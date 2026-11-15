"""Backend: regresje diagnostyki sesji, braków danych i granic zakresu otwarcia."""

import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import signal_logic

UTC = timezone.utc


def _candle(opened: datetime) -> dict:
    """Tworzy poprawną świecę M1 dla świadomego czasu otwarcia UTC."""
    return {"timestamp_utc": opened, "open": 6000.0, "high": 6003.0,
            "low": 5997.0, "close": 6000.0, "volume": 100.0}


def _history(closed: datetime, count: int = 120) -> list[dict]:
    """Tworzy ciąg świec zakończonych w podanym świadomym czasie UTC."""
    return [_candle(closed - timedelta(minutes=count - index)) for index in range(count)]


def _indicators(direction: str) -> dict:
    """Zwraca wskaźniki sprzyjające wybiciu ORB w podanym kierunku."""
    long = direction == "LONG"
    return {"atr": 2.0, "atr_ratio": 1.0, "relative_volume": 1.5,
            "vwap": 5990.0 if long else 6010.0,
            "m5_close": 6002.0 if long else 5998.0, "m5_ema20": 6000.0,
            "m5_ema20_slope": 1.0 if long else -1.0,
            "ema9": 6002.0, "ema21": 6000.0, "ema55": 5998.0,
            "ema55_slope": 0.0, "ema21_series": [6000.0] * 5,
            "er": 0.5, "rsi": 50.0, "rsi_series": [35.0, 40.0, 42.0, 50.0],
            "body_ratio": 0.6, "lower_wick_ratio": 0.4, "upper_wick_ratio": 0.4,
            "bb_zscore": 0.0, "bb_zscore_previous": -2.0}


@pytest.mark.parametrize("opened", [
    datetime(2026, 11, 16, 22, 0, tzinfo=UTC),
    datetime(2026, 11, 21, 12, 0, tzinfo=UTC),
])
def test_candle_outside_delivery_schedule(opened: datetime) -> None:
    """Świeca otwarta w przerwie daje NONE bez wyjątku i bez fikcyjnej luki."""
    result = signal_logic.generate_signal([_candle(opened)], now=opened + timedelta(minutes=1))
    assert result["signal"] == "NONE"
    assert result["candle_open_epoch"] == int(opened.timestamp())
    assert "outside_session" in result["blocked_by"]
    assert "data_gap" not in result["blocked_by"]


def test_historical_off_schedule_rows_do_not_corrupt_current_segment() -> None:
    """Starsza świeca z przerwy nie trafia do segmentu prawidłowej świecy."""
    closed = datetime(2026, 11, 16, 23, 3, tzinfo=UTC)
    rows = [_candle(datetime(2026, 11, 16, 22, 30, tzinfo=UTC))] + _history(closed, 3)
    result = signal_logic.generate_signal(rows, now=closed)
    assert result["params"]["segment_candles"] == 3
    assert "data_gap" not in result["blocked_by"]
    assert "outside_session" in result["blocked_by"]


def test_delivery_boundary_uses_open_not_close() -> None:
    """Otwarcie 16:59 ET jest ostatnią minutą dostaw, otwarcie 17:00 już nie."""
    boundary = datetime(2026, 11, 16, 22, 0, tzinfo=UTC)
    last_valid = signal_logic.generate_signal([_candle(boundary - timedelta(minutes=1))], now=boundary)
    first_invalid = signal_logic.generate_signal([_candle(boundary)], now=boundary + timedelta(minutes=1))
    assert last_valid["params"]["segment_candles"] == 1
    assert first_invalid["params"] == {}
    assert "outside_session" in last_valid["blocked_by"]
    assert "outside_session" in first_invalid["blocked_by"]
    assert "data_gap" not in first_invalid["blocked_by"]


def test_holiday_does_not_report_data_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Święto nie blokuje przez braki, lecz zachowuje ich liczbę w diagnostyce."""
    closed = datetime(2026, 11, 16, 15, 20, tzinfo=UTC)
    rows = _history(closed)
    rows = rows[:-10] + rows[-8:]
    ordinary = signal_logic.generate_signal(rows, now=closed)
    assert "data_gap" in ordinary["blocked_by"]
    monkeypatch.setattr(config, "NO_TRADE_DATES", [date(2026, 11, 16)])
    result = signal_logic.generate_signal(rows, now=closed)
    assert result["signal"] == "NONE"
    assert "outside_session" in result["blocked_by"]
    assert "data_gap" not in result["blocked_by"]
    assert result["params"]["missing_candles_60m"] == 2


def test_four_separate_missing_minutes_exceed_gap_limit() -> None:
    """Cztery oddzielne braki przekraczają limit, mimo braku kolejnych luk."""
    closed = datetime(2026, 11, 16, 15, 20, tzinfo=UTC)
    rows = _history(closed)
    rows = [row for index, row in enumerate(rows) if index not in (80, 85, 90, 95)]
    result = signal_logic.generate_signal(rows, now=closed)
    assert result["params"]["missing_candles_60m"] == 4
    assert result["params"]["max_consecutive_missing"] == 1
    assert "data_gap" in result["blocked_by"]


def test_holiday_analysis_on_stale_previous_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zegar analizy wskazuje święto także wtedy, gdy dane pochodzą z piątku."""
    closed = datetime(2026, 11, 13, 15, 20, tzinfo=UTC)
    rows = _history(closed)
    monkeypatch.setattr(config, "NO_TRADE_DATES", [date(2026, 11, 16)])
    result = signal_logic.generate_signal(rows[:-10] + rows[-8:], now=datetime(2026, 11, 16, 15, 0, tzinfo=UTC))
    assert "outside_session" in result["blocked_by"]
    assert "stale_data" in result["blocked_by"]
    assert "data_gap" not in result["blocked_by"]


def test_weekend_clock_and_intentional_historical_analysis() -> None:
    """Ten sam zestaw danych jest historyczny lub przeterminowany zależnie od now."""
    closed = datetime(2026, 11, 13, 15, 20, tzinfo=UTC)
    rows = _history(closed)
    historical = signal_logic.generate_signal(rows, now=closed)
    weekend = signal_logic.generate_signal(rows, now=datetime(2026, 11, 14, 12, 0, tzinfo=UTC))
    assert "outside_session" not in historical["blocked_by"]
    assert "stale_data" not in historical["blocked_by"]
    assert "outside_session" in weekend["blocked_by"]
    assert "stale_data" in weekend["blocked_by"]


@pytest.mark.parametrize(("closed", "analysis_time", "stale"), [
    (datetime(2026, 11, 13, 15, 20, tzinfo=UTC), datetime(2026, 11, 14, 12, 0, tzinfo=UTC), True),
    (datetime(2026, 11, 16, 21, 0, tzinfo=UTC), datetime(2026, 11, 16, 21, 0, tzinfo=UTC), False),
])
def test_outside_session_keeps_gap_counts_without_gap_block(
        closed: datetime, analysis_time: datetime, stale: bool) -> None:
    """Weekend i zamknięcie sesji zachowują liczniki, ale nie blokadę luki."""
    rows = _history(closed)
    rows = rows[:-10] + rows[-8:]
    result = signal_logic.generate_signal(rows, now=analysis_time)
    assert result["signal"] == "NONE"
    assert "outside_session" in result["blocked_by"]
    assert ("stale_data" in result["blocked_by"]) is stale
    assert "data_gap" not in result["blocked_by"]
    assert result["params"]["missing_candles_60m"] == 2
    assert result["params"]["max_consecutive_missing"] == 2
    if stale:
        historical = signal_logic.generate_signal(rows, now=closed)
        assert "outside_session" not in historical["blocked_by"]
        assert "data_gap" in historical["blocked_by"]
        assert historical["params"]["missing_candles_60m"] == 2
        assert historical["params"]["max_consecutive_missing"] == 2


def test_recent_closing_candle_uses_closing_time_not_analysis_clock() -> None:
    """Świeca zamknięta na granicy okna pozostaje w nim przy niewielkim opóźnieniu."""
    closed = datetime(2026, 11, 16, 20, 50, tzinfo=UTC)
    result = signal_logic.generate_signal(_history(closed), now=closed + timedelta(minutes=1))
    assert "outside_session" not in result["blocked_by"]
    assert "stale_data" not in result["blocked_by"]


@pytest.mark.parametrize(("day", "close_hour", "early"), [
    (date(2026, 3, 9), 16, False),
    (date(2026, 11, 16), 16, False),
    (date(2026, 11, 16), 13, True),
])
def test_closing_blackout_is_inclusive(monkeypatch: pytest.MonkeyPatch, day: date,
                                       close_hour: int, early: bool) -> None:
    """Ostatnia świeca sesji jest objęta blokadą także dokładnie o 16 lub 13 ET."""
    monkeypatch.setattr(config, "EARLY_CLOSE_DATES", [day] if early else [])
    closed = datetime(day.year, day.month, day.day, close_hour, tzinfo=config.NEW_YORK).astimezone(UTC)
    assert not signal_logic._blackout(closed - timedelta(minutes=5, seconds=1))
    assert signal_logic._blackout(closed - timedelta(minutes=5))
    assert signal_logic._blackout(closed)
    assert not signal_logic._blackout(closed + timedelta(seconds=1))
    result = signal_logic.generate_signal(_history(closed), now=closed)
    assert "news_blackout" in result["blocked_by"]
    assert "outside_session" in result["blocked_by"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_first_breakout_after_opening_range(monkeypatch: pytest.MonkeyPatch, direction: str) -> None:
    """O 09:45 wybicie jest niemożliwe; od 09:46 nadal obowiązuje pierwsze przejście."""
    monkeypatch.setattr(signal_logic, "calculate_indicators",
                        lambda segment, session, cutoff: _indicators(direction))
    close_945 = datetime(2026, 11, 16, 14, 45, tzinfo=UTC)
    rows = _history(close_945)
    first_rows = [dict(row) for row in rows]
    if direction == "LONG":
        first_rows[-1].update(open=6003.0, low=6002.8, high=6004.0, close=6003.5)
    else:
        first_rows[-1].update(open=5997.0, low=5996.0, high=5997.2, close=5996.5)
    result = signal_logic.generate_signal(first_rows, now=close_945)
    assert result["params"]["or_count"] == 15
    assert result["params"]["or_low"] <= result["price"] <= result["params"]["or_high"]
    assert result["signal"] == "NONE"
    next_row = _candle(close_945)
    if direction == "LONG":
        next_row.update(open=6003.0, low=6002.8, high=6004.0, close=6003.5)
    else:
        next_row.update(open=5997.0, low=5996.0, high=5997.2, close=5996.5)
    result = signal_logic.generate_signal(rows + [next_row], now=close_945 + timedelta(minutes=1))
    assert result["signal"] == direction
    assert result["reasons"][0] == "OPENING_RANGE_BREAKOUT"
    assert len(result["reasons"]) == 6
    blocked = signal_logic.generate_signal(rows + [next_row], signals_today=config.MAX_SIGNALS_PER_DAY,
                                           now=close_945 + timedelta(minutes=1))
    assert blocked["signal"] == "NONE"
    assert blocked["reasons"] == result["reasons"]
    assert "daily_limit" in blocked["blocked_by"]
    rows[-1]["close"] = 6003.2 if direction == "LONG" else 5996.8
    rows[-1]["high"] = max(rows[-1]["high"], rows[-1]["close"])
    rows[-1]["low"] = min(rows[-1]["low"], rows[-1]["close"])
    # Poprzednia świeca należy jeszcze do OR; osobny test pierwszego przejścia
    # musi więc korzystać z kolejnej świecy poza tym zakresem.
    result = signal_logic.generate_signal(rows + [next_row, next_row | {
        "timestamp_utc": close_945 + timedelta(minutes=1)}], now=close_945 + timedelta(minutes=2))
    assert result["signal"] == "NONE"


def test_warmup_before_and_in_an_applicable_setup_window() -> None:
    """Przed oknem i w jego trakcie niewystarczająca historia oznacza rozgrzewkę."""
    closed = datetime(2026, 11, 16, 14, 42, tzinfo=UTC)
    result = signal_logic.generate_signal(_history(closed, 1), now=closed)
    assert "warmup" in result["blocked_by"]
    assert "no_setup" not in result["blocked_by"]
    result = signal_logic.generate_signal(_history(closed, config.WARMUP_ORB), now=closed)
    assert "warmup" not in result["blocked_by"]
    assert "no_setup" in result["blocked_by"]
    closed = datetime(2026, 11, 16, 15, 10, tzinfo=UTC)
    result = signal_logic.generate_signal(_history(closed, 1), now=closed)
    assert "warmup" in result["blocked_by"]


@pytest.mark.parametrize("setup", ["OPENING_RANGE_BREAKOUT", "TREND_PULLBACK", "VWAP_REVERSION"])
@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_score_reasons_match_every_bonus(setup: str, direction: str) -> None:
    """Opisy rdzenia i czterech premii odpowiadają punktom obu kierunków i wszystkich setupów."""
    params = _indicators(direction)
    score, reasons = signal_logic._score_details(setup, direction, params, time(9, 45))
    assert score == config.CORE_SCORE + 4 * config.BONUS_SCORE
    assert score == signal_logic._score(setup, direction, params, time(9, 45))
    assert len(reasons) == 5
    assert setup in reasons[0] and direction in reasons[0]
    assert "wolumen względny" in reasons[1]
    assert "płynności" in reasons[2]
    assert ("ER >" if setup == "TREND_PULLBACK" else "M5") in reasons[3]
    assert "korpusu" in reasons[4]


@pytest.mark.parametrize("setup", ["OPENING_RANGE_BREAKOUT", "TREND_PULLBACK", "VWAP_REVERSION"])
@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_score_reasons_exclude_unearned_bonuses(setup: str, direction: str) -> None:
    """Wartości poniżej progów, neutralne M5 i koniec okna nie dodają premii."""
    params = _indicators(direction)
    params.update(relative_volume=config.BONUS_VOLUME - 0.01, er=config.BONUS_ER,
                  m5_close=6000.0, m5_ema20=6000.0, body_ratio=config.BONUS_BODY - 0.01)
    score, reasons = signal_logic._score_details(setup, direction, params, time(11))
    assert score == config.CORE_SCORE
    assert len(reasons) == 1
    assert setup in reasons[0] and direction in reasons[0]


def test_score_bonus_boundaries_and_pullback_er() -> None:
    """Wolumen i korpus mają próg włącznie, natomiast premia ER wymaga przekroczenia."""
    params = _indicators("LONG")
    params.update(relative_volume=config.BONUS_VOLUME, body_ratio=config.BONUS_BODY,
                  er=config.BONUS_ER, m5_close=6001.0, m5_ema20=6000.0)
    score, reasons = signal_logic._score_details("TREND_PULLBACK", "LONG", params, time(11))
    assert score == config.CORE_SCORE + 2 * config.BONUS_SCORE
    assert len(reasons) == 3
    assert all("ER >" not in reason and "M5" not in reason for reason in reasons[1:])
    params["er"] += 0.01
    score, reasons = signal_logic._score_details("TREND_PULLBACK", "LONG", params, time(11))
    assert score == config.CORE_SCORE + 3 * config.BONUS_SCORE
    assert any("ER >" in reason for reason in reasons)


def test_no_setup_does_not_claim_core_or_bonus() -> None:
    """Brak kandydata nie tworzy opisów spełnionych warunków ani premii."""
    closed = datetime(2026, 11, 16, 15, 20, tzinfo=UTC)
    result = signal_logic.generate_signal(_history(closed), now=closed)
    assert result["signal"] == "NONE"
    assert result["reasons"] == []
