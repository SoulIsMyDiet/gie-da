"""Backend: testy stronicowanego odczytu świec i kolejności UTC."""

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import storage

UTC = timezone.utc


def candle(wall, offset, price=1):
    """Tworzy wiersz świecy o zadanym czasie lokalnym, przesunięciu i cenie."""
    return {"datetime": wall, "utc_offset_minutes": offset, "open": price,
            "high": price, "low": price, "close": price, "volume": None}


class Cursor:
    """Naśladuje zakresowy odczyt indeksu i zapytania o stan strategii."""

    def __init__(self, rows):
        """Przyjmuje wiersze świec; przygotowuje rejestr zapytań i stan zamknięcia."""
        self.rows = rows
        self.queries = []
        self.result = []
        self.closed = False

    def execute(self, query, params):
        """Wykonuje symulowany zakres klucza albo zwraca pusty stan sygnałów."""
        self.queries.append((query, params))
        if "FROM candles" not in query:
            self.result = []
            return
        assert "DATE_SUB" not in query
        assert "ORDER BY `datetime` DESC, utc_offset_minutes DESC" in query
        upper = params[1]
        selected = [r for r in self.rows if r["datetime"] <= upper]
        if len(params) == 6:
            key = (params[2], params[4])
            selected = [r for r in selected if (r["datetime"], r["utc_offset_minutes"]) < key]
        selected.sort(key=lambda r: (r["datetime"], r["utc_offset_minutes"]), reverse=True)
        self.result = selected[:params[-1]]

    def fetchall(self):
        """Zwraca wszystkie wiersze ostatniego zapytania."""
        return self.result

    def fetchone(self):
        """Zwraca pierwszy wiersz stanu albo None."""
        return self.result[0] if self.result else None

    def close(self):
        """Oznacza kursor jako zamknięty."""
        self.closed = True


class Connection:
    """Naśladuje połączenie, zapamiętując transakcję i zamknięcie."""

    def __init__(self, rows):
        """Przyjmuje wiersze świec i tworzy kursor."""
        self.candle_cursor = Cursor(rows)
        self.transaction = None
        self.closed = False

    def start_transaction(self, **kwargs):
        """Zapamiętuje ustawienia spójnej transakcji odczytu."""
        self.transaction = kwargs

    def cursor(self, **kwargs):
        """Zwraca kursor słownikowy; niepoprawne ustawienia kończą się błędem."""
        assert kwargs == {"dictionary": True}
        return self.candle_cursor

    def close(self):
        """Oznacza połączenie jako zamknięte."""
        self.closed = True


def utc(row):
    """Zwraca oczekiwany czas UTC poprawnego wiersza świecy."""
    return (row["datetime"] - timedelta(minutes=row["utc_offset_minutes"])).replace(tzinfo=UTC)


def read(rows, cutoff, limit):
    """Wykonuje load_context na imitacji bazy i zwraca wynik oraz połączenie."""
    connection = Connection(rows)
    with patch.object(storage.SP500Storage, "_connect", return_value=connection):
        result, state = storage.SP500Storage({}).load_context(cutoff, limit)
    assert state["signals_today"] == 0
    assert connection.closed and connection.candle_cursor.closed
    assert connection.transaction == {
        "consistent_snapshot": True, "isolation_level": "REPEATABLE READ", "readonly": True}
    return result, connection


def test_dst_cutoff_limit_and_subminute(monkeypatch):
    """Cofnięta godzina, granica cutoff i sekundy nie zmieniają kolejności UTC."""
    monkeypatch.setattr(storage, "_CANDLE_PAGE_SIZE", 2)
    wall = datetime(2026, 10, 25, 2, 30)
    rows = [candle(wall, 60, 1), candle(wall, 120, 2),
            candle(wall.replace(minute=29, second=59), 60, 3),
            candle(wall.replace(second=1), 60, 4)]
    cutoff = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    actual, connection = read(rows, cutoff, 2)
    assert [r["timestamp_utc"] for r in actual] == [
        datetime(2026, 10, 25, 1, 29, 59, tzinfo=UTC), cutoff]
    assert len([q for q, _ in connection.candle_cursor.queries if "FROM candles" in q]) >= 2
    with patch.object(storage.SP500Storage, "_connect", return_value=Connection(rows)):
        assert storage.SP500Storage({}).latest_closed_open(cutoff + timedelta(seconds=60)) == cutoff


def test_random_sparse_history_matches_full_utc_sort(monkeypatch):
    """Losowe odstępy, nietypowe poprawne strefy i strony odpowiadają pełnemu sortowaniu."""
    monkeypatch.setattr(storage, "_CANDLE_PAGE_SIZE", 7)
    rng = random.Random(913)
    base = datetime(2026, 10, 25)
    keys = set()
    while len(keys) < 350:
        keys.add((base + timedelta(seconds=rng.randrange(-4_000_000, 4_000_000)),
                  rng.choice([-1439, -720, -1, 0, 60, 120, 330, 1439])))
    rows = [candle(wall, offset, index + 1) for index, (wall, offset) in enumerate(sorted(keys))]
    for cutoff in [base.replace(tzinfo=UTC), (base + timedelta(days=12, seconds=17)).replace(tzinfo=UTC)]:
        for limit in (1, 2, 37, 500):
            expected = sorted((r for r in rows if utc(r) <= cutoff),
                              key=lambda r: (utc(r), r["datetime"], r["utc_offset_minutes"]))[-limit:]
            actual, _ = read(rows, cutoff, limit)
            assert [(r["timestamp_utc"], r["open"]) for r in actual] == [
                (utc(r), float(r["open"])) for r in expected]


def test_smallint_extremes_cannot_be_ignored(monkeypatch):
    """Skrajny nieprawidłowy offset może wyprzedzić inne świece i musi zgłosić błąd."""
    monkeypatch.setattr(storage, "_CANDLE_PAGE_SIZE", 1)
    cutoff = datetime(2026, 11, 1, tzinfo=UTC)
    rows = [candle(cutoff.replace(tzinfo=None) - timedelta(minutes=1), 0),
            candle(cutoff.replace(tzinfo=None) - timedelta(minutes=32768), -32768)]
    connection = Connection(rows)
    with patch.object(storage.SP500Storage, "_connect", return_value=connection):
        with pytest.raises(ValueError, match="przesunięcie"):
            storage.SP500Storage({}).load_context(cutoff, 1)
    assert connection.closed and connection.candle_cursor.closed


def test_upper_bound_handles_positive_smallint_extreme(monkeypatch):
    """Górna granica obejmuje także offset 32767, nawet poza zasięgiem timezone."""
    monkeypatch.setattr(storage, "_CANDLE_PAGE_SIZE", 1)
    cutoff = datetime(2026, 11, 1, tzinfo=UTC)
    rows = [candle(cutoff.replace(tzinfo=None) + timedelta(minutes=32767), 32767)]
    connection = Connection(rows)
    with patch.object(storage.SP500Storage, "_connect", return_value=connection):
        with pytest.raises(ValueError, match="przesunięcie"):
            storage.SP500Storage({}).latest_closed_open(cutoff + timedelta(minutes=1))
    assert connection.closed and connection.candle_cursor.closed


def test_range_stops_before_old_history(monkeypatch):
    """Po dowiedzeniu granicy UTC starsza historia nie jest odczytywana."""
    monkeypatch.setattr(storage, "_CANDLE_PAGE_SIZE", 2)
    cutoff = datetime(2026, 11, 1, tzinfo=UTC)
    wall = cutoff.replace(tzinfo=None)
    rows = [candle(wall, 0), candle(wall - timedelta(days=23), 0)]
    rows += [candle(wall - timedelta(days=100 + index), 0) for index in range(50)]
    actual, connection = read(rows, cutoff, 1)
    assert actual[0]["timestamp_utc"] == cutoff
    assert len([q for q, _ in connection.candle_cursor.queries if "FROM candles" in q]) == 1


def test_equal_utc_ties_are_stable_across_pages(monkeypatch):
    """Remisy UTC są rozstrzygane kluczem lokalnym, również na granicy strony."""
    monkeypatch.setattr(storage, "_CANDLE_PAGE_SIZE", 1)
    cutoff = datetime(2026, 11, 1, tzinfo=UTC)
    wall = cutoff.replace(tzinfo=None)
    rows = [candle(wall, 0, 1), candle(wall + timedelta(minutes=60), 60, 2),
            candle(wall - timedelta(minutes=60), -60, 3)]
    actual, _ = read(rows, cutoff, 2)
    assert [r["open"] for r in actual] == [1, 2]
    assert [r["timestamp_utc"] for r in actual] == [cutoff, cutoff]


@pytest.mark.parametrize("limit", [0, -1, 1.5, True, "2"])
def test_invalid_limit_rejected_before_connection(limit):
    """Niecałkowity lub niedodatni limit nie otwiera połączenia."""
    with patch.object(storage.SP500Storage, "_connect") as connect:
        with pytest.raises(ValueError, match="Limit"):
            storage.SP500Storage({}).load_context(datetime(2026, 1, 1, tzinfo=UTC), limit)
    connect.assert_not_called()


@pytest.mark.parametrize("failure_point", ["transaction", "query", "cursor_close"])
def test_failures_close_connection(failure_point):
    """Błędy transakcji, zapytania i zamknięcia kursora nie pozostawiają połączenia."""
    connection = Connection([])
    target = {"transaction": (connection, "start_transaction"),
              "query": (connection.candle_cursor, "execute"),
              "cursor_close": (connection.candle_cursor, "close")}[failure_point]
    with patch.object(storage.SP500Storage, "_connect", return_value=connection), \
            patch.object(*target, side_effect=RuntimeError("Błąd testowy")):
        with pytest.raises(RuntimeError, match="Błąd testowy"):
            storage.SP500Storage({}).load_context(datetime(2026, 1, 1, tzinfo=UTC))
    assert connection.closed


def test_datetime_max_does_not_overflow_upper_bound():
    """Cutoff bliski końca zakresu datetime ogranicza górną granicę indeksu."""
    cutoff = datetime.max.replace(tzinfo=UTC)
    cursor = Cursor([candle(datetime.max, 0)])
    actual = storage.SP500Storage._select_candles(cursor, cutoff, 1)
    assert storage.SP500Storage._candle_utc(actual[0]) == cutoff
    assert cursor.queries[0][1][1] == datetime.max
