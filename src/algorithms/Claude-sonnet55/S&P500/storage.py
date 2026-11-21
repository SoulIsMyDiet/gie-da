"""Backend: odczyt świec ES i atomowy, idempotentny zapis wyników MySQL."""

import json
import os
from heapq import heappush, heapreplace
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

import config
from schedule import as_utc


# Backend: granice wynikają z podpisanej kolumny SMALLINT, nie ze strefy Warszawy.
_MIN_OFFSET_MINUTES = -32768
_MAX_OFFSET_MINUTES = 32767
_MICROSECONDS_PER_MINUTE = 60_000_000
_CANDLE_PAGE_SIZE = 1024


def _microseconds(value: datetime) -> int:
    """Zamienia datetime bez strefy na liczbę mikrosekund bez użycia epoki."""
    return (((value.toordinal() * 24 + value.hour) * 60 + value.minute) * 60
            + value.second) * 1_000_000 + value.microsecond


class SP500Storage:
    """Udostępnia historię, stan i zapis strategii; ustawienia pochodzą ze zmiennych MYSQL_*."""

    def __init__(self, settings: dict | None = None):
        """Przyjmuje opcjonalne ustawienia połączenia; domyślnie czyta środowisko."""
        self.settings = settings if settings is not None else {
            "host": os.getenv("MYSQL_HOST", "localhost"),
            "port": int(os.getenv("MYSQL_PORT", "3306")),
            "user": os.getenv("MYSQL_USER", "root"),
            "password": os.getenv("MYSQL_PASSWORD", ""),
            "database": os.getenv("MYSQL_DATABASE", "gielda"),
        }

    def _connect(self):
        """Otwiera połączenie MySQL; błędy sterownika i połączenia są przekazywane dalej."""
        import mysql.connector
        return mysql.connector.connect(**self.settings)

    def latest_closed_open(self, now: datetime) -> datetime | None:
        """Zwraca otwarcie najnowszej zamkniętej świecy ES lub None, gdy jej nie ma."""
        cutoff = as_utc(now).replace(second=0, microsecond=0) - timedelta(minutes=1)
        connection = self._connect()
        cursor = None
        try:
            connection.start_transaction(consistent_snapshot=True, isolation_level="REPEATABLE READ", readonly=True)
            cursor = connection.cursor(dictionary=True)
            rows = self._select_candles(cursor, cutoff, 1)
            return self._candle_utc(rows[0]) if rows else None
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()

    @staticmethod
    def _candle_utc(row: dict) -> datetime:
        """Zwraca czas świecy w UTC; nieprawidłowe przesunięcie lub data zgłasza ValueError."""
        try:
            offset = timezone(timedelta(minutes=int(row["utc_offset_minutes"])))
            return row["datetime"].replace(tzinfo=offset).astimezone(timezone.utc)
        except (ValueError, OverflowError) as error:
            raise ValueError("Nieprawidłowa data lub przesunięcie UTC świecy.") from error

    @staticmethod
    def _select_candles(cursor, cutoff: datetime, limit: int) -> list[dict]:
        """Czyta najwyżej limit świec do cutoff włącznie, od najstarszej do najnowszej.

        Stronicuje malejąco po kluczu indeksu i porządkuje wybrane wiersze po UTC.
        Nie zakłada odstępów świec ani konkretnych przesunięć strefy. Błędy bazy
        oraz nieprawidłowe dane są przekazywane wywołującemu.
        """
        cutoff_naive = cutoff.replace(tzinfo=None)
        cutoff_us = _microseconds(cutoff_naive)
        try:
            upper = cutoff_naive + timedelta(minutes=_MAX_OFFSET_MINUTES)
        except OverflowError:
            upper = datetime.max
        candidates = []
        last_key = None
        while True:
            if last_key is None:
                cursor.execute(
                    """SELECT `datetime`, utc_offset_minutes, `open`, high, low, `close`, volume
                       FROM candles WHERE symbol = %s AND `datetime` <= %s
                       ORDER BY `datetime` DESC, utc_offset_minutes DESC LIMIT %s""",
                    (config.DB_SYMBOL, upper, _CANDLE_PAGE_SIZE))
            else:
                cursor.execute(
                    """SELECT `datetime`, utc_offset_minutes, `open`, high, low, `close`, volume
                       FROM candles WHERE symbol = %s AND `datetime` <= %s
                       AND (`datetime` < %s OR (`datetime` = %s AND utc_offset_minutes < %s))
                       ORDER BY `datetime` DESC, utc_offset_minutes DESC LIMIT %s""",
                    (config.DB_SYMBOL, upper, last_key[0], last_key[0], last_key[1], _CANDLE_PAGE_SIZE))
            rows = cursor.fetchall()
            if not rows:
                break
            for row in rows:
                wall = row["datetime"]
                offset = int(row["utc_offset_minutes"])
                if not _MIN_OFFSET_MINUTES <= offset <= _MAX_OFFSET_MINUTES:
                    raise ValueError("Przesunięcie UTC świecy wykracza poza zakres SMALLINT.")
                utc_us = _microseconds(wall) - offset * _MICROSECONDS_PER_MINUTE
                if utc_us <= cutoff_us:
                    entry = (utc_us, wall, offset, row)
                    if len(candidates) < limit:
                        heappush(candidates, entry)
                    elif entry[:3] > candidates[0][:3]:
                        heapreplace(candidates, entry)
            last_row = rows[-1]
            last_key = (last_row["datetime"], int(last_row["utc_offset_minutes"]))
            # Nawet najmniejsze SMALLINT nie przesunie niewczytanej świecy dalej w UTC.
            if (len(rows) < _CANDLE_PAGE_SIZE or
                    (len(candidates) == limit and
                     _microseconds(last_key[0]) - _MIN_OFFSET_MINUTES * _MICROSECONDS_PER_MINUTE
                     < candidates[0][0])):
                break
        return [entry[3] for entry in sorted(candidates)]

    def latest_processed_open(self) -> datetime | None:
        """Odczytuje otwarcie ostatniej zapisanej świecy strategii lub None."""
        connection = self._connect()
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(
                """SELECT MAX(source_timestamp) FROM signals
                   WHERE product = %s AND algorithm_name = %s""",
                (config.DB_SYMBOL, config.STRATEGY_NAME),
            )
            row = cursor.fetchone()
            return (
                datetime.fromtimestamp(int(row[0]), timezone.utc)
                if row and row[0] is not None else None
            )
        finally:
            if cursor is not None:
                cursor.close()
            connection.close()

    def get_processed_signal(self, opened: datetime) -> dict | None:
        """Zwraca wcześniej zapisany wynik dla świecy UTC lub None, gdy go nie ma."""
        opened = as_utc(opened)
        connection = self._connect()
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(
                """SELECT signal_parameters FROM signals
                   WHERE product = %s AND algorithm_name = %s AND source_timestamp = %s
                   LIMIT 1""",
                (config.DB_SYMBOL, config.STRATEGY_NAME, int(opened.timestamp())),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            value = row[0]
            result = json.loads(value) if isinstance(value, (str, bytes, bytearray)) else value
            if not isinstance(result, dict):
                raise ValueError("Zapisane parametry sygnału nie są obiektem JSON.")
            return result
        finally:
            if cursor is not None:
                cursor.close()
            connection.close()

    def load_context(self, cutoff: datetime, limit: int = config.HISTORY_LIMIT) -> tuple[list[dict], dict]:
        """Wczytuje świece do otwarcia cutoff włącznie i wyłącznie wcześniejszy stan sygnałów."""
        cutoff = as_utc(cutoff)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Limit historii musi być dodatnią liczbą całkowitą.")
        connection = self._connect()
        cursor = None
        try:
            connection.start_transaction(consistent_snapshot=True, isolation_level="REPEATABLE READ", readonly=True)
            cursor = connection.cursor(dictionary=True)
            candles = []
            for row in self._select_candles(cursor, cutoff, limit):
                stamp = self._candle_utc(row)
                candles.append({"timestamp_utc": stamp, **{key: float(row[key]) for key in ("open", "high", "low", "close")},
                                "volume": float(row["volume"]) if row["volume"] is not None else None})
            state = self._load_state(cursor, cutoff)
            return candles, state
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()

    @staticmethod
    def _load_state(cursor, cutoff: datetime) -> dict:
        """Czyta wcześniejszy cooldown i sygnały dnia ET, bez używania przyszłego stanu."""
        cutoff = as_utc(cutoff)
        day = (cutoff + timedelta(minutes=1)).astimezone(config.NEW_YORK).date()
        start = datetime.combine(day, time.min, config.NEW_YORK)
        end = datetime.combine(day + timedelta(days=1), time.min, config.NEW_YORK)
        cursor.execute(
            """SELECT source_timestamp FROM signals
               WHERE product = %s AND algorithm_name = %s AND `signal` IN ('long','short')
               AND source_timestamp < %s ORDER BY source_timestamp DESC LIMIT 1""",
            (config.DB_SYMBOL, config.STRATEGY_NAME, int(cutoff.timestamp())))
        latest = cursor.fetchone()
        cursor.execute(
            """SELECT `signal`, signal_parameters FROM signals
               WHERE product = %s AND algorithm_name = %s AND `signal` IN ('long','short')
               AND source_timestamp >= %s AND source_timestamp < %s AND source_timestamp < %s""",
            (config.DB_SYMBOL, config.STRATEGY_NAME, int(start.timestamp()), int(end.timestamp()), int(cutoff.timestamp())))
        rows = cursor.fetchall()
        directions = []
        for row in rows:
            parameters = row["signal_parameters"]
            if isinstance(parameters, (str, bytes, bytearray)):
                parameters = json.loads(parameters)
            if parameters and parameters.get("setup") == "OPENING_RANGE_BREAKOUT":
                directions.append(row["signal"].upper())
        return {"last_signal_time": datetime.fromtimestamp(int(latest["source_timestamp"]) + 60, timezone.utc) if latest else None,
                "signals_today": len(rows), "orb_directions_today": directions}

    def save_signal(self, result: dict, candles_count: int) -> bool:
        """Zapisuje sygnał i potencjał w transakcji; duplikat sygnału zwraca False, błędy są zgłaszane."""
        import mysql.connector
        signal = result["signal"].upper()
        if signal not in ("LONG", "SHORT", "NONE"):
            raise ValueError("Nieznany rodzaj sygnału.")
        if signal == "NONE" and not config.SAVE_NONE_SIGNALS:
            return False
        opened = as_utc(datetime.fromisoformat(result["candle_open_time"]))
        closed = as_utc(datetime.fromisoformat(result["timestamp"]))
        if closed != opened + timedelta(minutes=1):
            raise ValueError("Zamknięcie musi następować minutę po otwarciu.")
        local = closed.astimezone(config.WARSAW)
        parameters = dict(result)
        parameters["timestamp"] = local.isoformat()
        parameters["utc_offset_minutes"] = int(local.utcoffset().total_seconds() // 60)
        payload = json.dumps(parameters, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        connection = self._connect()
        cursor = None
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """INSERT INTO signals (`datetime`, product, price, `signal`, algorithm_name,
                       candles_count, signal_parameters, source_timestamp) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (local.replace(tzinfo=None), config.DB_SYMBOL, Decimal(str(result["price"])),
                     signal.lower(), config.STRATEGY_NAME, int(candles_count), payload, int(opened.timestamp())))
            except mysql.connector.Error as error:
                if error.errno == 1062:
                    connection.rollback()
                    return False
                raise
            if signal != "NONE":
                cursor.execute(
                    "INSERT INTO signal_potention (signal_id, average_spread) VALUES (%s, %s)",
                    (cursor.lastrowid, Decimal(str(config.ASSUMED_COST_POINTS))))
            connection.commit()
            return True
        except (mysql.connector.Error, ValueError, TypeError):
            connection.rollback()
            raise
        finally:
            if cursor is not None:
                cursor.close()
            connection.close()
