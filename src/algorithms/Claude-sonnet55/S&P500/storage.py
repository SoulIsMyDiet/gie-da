"""Backend: odczyt świec ES i atomowy, idempotentny zapis wyników MySQL."""

import json
import os
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

import config
from schedule import as_utc


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
            cursor = connection.cursor()
            cursor.execute(
                """SELECT `datetime`, utc_offset_minutes FROM candles
                   WHERE symbol = %s
                     AND DATE_SUB(`datetime`, INTERVAL utc_offset_minutes MINUTE) <= %s
                   ORDER BY DATE_SUB(`datetime`, INTERVAL utc_offset_minutes MINUTE) DESC
                   LIMIT 1""",
                (config.DB_SYMBOL, cutoff.replace(tzinfo=None)),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return row[0].replace(
                tzinfo=timezone(timedelta(minutes=int(row[1])))
            ).astimezone(timezone.utc)
        finally:
            if cursor is not None:
                cursor.close()
            connection.close()

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
        if limit < 1:
            raise ValueError("Limit historii musi być dodatni.")
        connection = self._connect()
        cursor = None
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT `datetime`, utc_offset_minutes, `open`, high, low, `close`, volume
                   FROM candles WHERE symbol = %s
                   AND DATE_SUB(`datetime`, INTERVAL utc_offset_minutes MINUTE) <= %s
                   ORDER BY DATE_SUB(`datetime`, INTERVAL utc_offset_minutes MINUTE) DESC LIMIT %s""",
                (config.DB_SYMBOL, cutoff.replace(tzinfo=None), limit))
            candles = []
            for row in reversed(cursor.fetchall()):
                stamp = row["datetime"].replace(
                    tzinfo=timezone(timedelta(minutes=int(row["utc_offset_minutes"])))).astimezone(timezone.utc)
                candles.append({"timestamp_utc": stamp, **{key: float(row[key]) for key in ("open", "high", "low", "close")},
                                "volume": float(row["volume"]) if row["volume"] is not None else None})
            state = self._load_state(cursor, cutoff)
            return candles, state
        finally:
            if cursor is not None:
                cursor.close()
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
                    "INSERT INTO signal_potention (signal_id, spread) VALUES (%s, %s)",
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
