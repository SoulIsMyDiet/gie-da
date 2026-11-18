"""Backend: odczyt świec ES i atomowy zapis sygnałów strategii."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import pandas as pd
from mysql.connector import Error as MySQLError
import mysql.connector

import config

logger = logging.getLogger(__name__)


class InvalidSignalPrice(ValueError):
    """Oznacza, że wynik nie ma ceny umożliwiającej zapis w tabeli signals."""


def validated_price(result: dict) -> Decimal:
    """Zwraca dodatnią, skończoną cenę wyniku; brak lub błędna cena zgłasza ValueError."""
    value = result.get("price")
    try:
        if value is None or isinstance(value, bool):
            raise ValueError("Brak prawidłowej ceny.")
        price = Decimal(str(value))
        if not price.is_finite() or price <= 0:
            raise ValueError("Cena musi być dodatnia i skończona.")
        return price
    except (InvalidOperation, ValueError, TypeError) as error:
        logger.error("Nie można zapisać wyniku świecy %s: brak prawidłowej ceny; powód: %s.",
                     result.get("candle_open_epoch"),
                     result.get("signal_parameters", {}).get("reason"))
        raise InvalidSignalPrice("Brak prawidłowej ceny wyniku strategii.") from error


class SP500Storage:
    """Obsługuje bazę strategii; ustawienia pochodzą z MYSQL_* lub argumentu.

    Argumenty:
        settings: Opcjonalny słownik parametrów połączenia MySQL.
    Wyjątki:
        ValueError: Gdy port ze środowiska nie jest liczbą.
    """

    def __init__(self, settings: dict | None = None):
        """Zapamiętuje ustawienia połączenia; nie otwiera bazy ani nic nie zwraca."""
        self.settings = settings if settings is not None else {
            "host": os.getenv("MYSQL_HOST", "localhost"),
            "port": int(os.getenv("MYSQL_PORT", "3306")),
            "user": os.getenv("MYSQL_USER", "root"),
            "password": os.getenv("MYSQL_PASSWORD", ""),
            "database": os.getenv("MYSQL_DATABASE", "gielda"),
        }

    def _connect(self):
        """Otwiera i zwraca połączenie MySQL; przekazuje błędy sterownika."""
        return mysql.connector.connect(**self.settings)

    def recent_closed_candles(self, now: datetime, minutes: int = 15) -> list[datetime]:
        """Zwraca otwarcia zamkniętych świec ES z ostatnich minut w kolejności UTC.

        now musi mieć strefę, a minutes być dodatnie. Błędy bazy są przekazywane
        dalej; zakres dat lokalnych ogranicza skan indeksu mimo przeliczenia DST.
        """
        now = _utc(now)
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1:
            raise ValueError("Okno odczytu musi być dodatnią liczbą minut.")
        lower = now - timedelta(minutes=minutes + 1)
        upper = now - timedelta(minutes=1)
        connection = self._connect()
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(
                """SELECT `datetime`, utc_offset_minutes FROM candles
                   WHERE symbol = %s AND `datetime` BETWEEN %s AND %s
                     AND utc_offset_minutes BETWEEN 60 AND 120
                     AND DATE_SUB(`datetime`, INTERVAL utc_offset_minutes MINUTE)
                         BETWEEN %s AND %s
                   ORDER BY DATE_SUB(`datetime`, INTERVAL utc_offset_minutes MINUTE)""",
                (config.DB_SYMBOL, lower.replace(tzinfo=None),
                 (upper + timedelta(hours=2)).replace(tzinfo=None),
                 lower.replace(tzinfo=None), upper.replace(tzinfo=None)),
            )
            return [
                (stamp - timedelta(minutes=int(offset))).replace(tzinfo=timezone.utc)
                for stamp, offset in cursor.fetchall()
            ]
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()

    def get_processed_signal(self, opened: datetime) -> dict | None:
        """Zwraca wynik dla otwarcia UTC albo None; błędy bazy i JSON propaguje.

        Płaskie parametry z bazy przywraca do zagnieżdżonej struktury wyniku,
        której używa generator oraz obsługa wcześniej zapisanych sygnałów.
        """
        opened = _utc(opened)
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
                raise ValueError("Zapisane parametry nie są obiektem JSON.")
            result = dict(result)
            result.pop("audio_notification", None)
            if "signal_parameters" not in result:
                result_fields = {
                    "instrument", "algorithm_name", "price", "timestamp",
                    "candle_open_time", "candle_open_epoch", "signal", "max_hold_minutes",
                }
                result = {
                    **{key: value for key, value in result.items() if key in result_fields},
                    "signal_parameters": {key: value for key, value in result.items() if key not in result_fields},
                }
            return result
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()

    def load_context(self, cutoff: datetime, limit: int = config.HISTORY_LIMIT) -> pd.DataFrame:
        """Zwraca ramkę do otwarcia cutoff UTC włącznie, chronologicznie.

        Argumenty: cutoff to świadomy czas otwarcia; limit to maksymalna liczba świec.
        Wyjątki: ValueError dla limitu; błędy danych i bazy są przekazywane dalej.

        Obliczenie UTC w WHERE i ORDER BY poprawnie rozstrzyga zmianę DST,
        ale może wymagać filesort i nie wykorzystać indeksu do sortowania.
        """
        cutoff = _utc(cutoff)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Limit historii musi być dodatnią liczbą całkowitą.")
        connection = self._connect()
        cursor = None
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT `datetime`, utc_offset_minutes, `open`, high, low, `close`, volume
                   FROM candles
                   WHERE symbol = %s
                     AND DATE_SUB(`datetime`, INTERVAL utc_offset_minutes MINUTE) <= %s
                   ORDER BY DATE_SUB(`datetime`, INTERVAL utc_offset_minutes MINUTE) DESC
                   LIMIT %s""",
                (config.DB_SYMBOL, cutoff.replace(tzinfo=None), limit),
            )
            rows = cursor.fetchall()
            candles = []
            for row in reversed(rows):
                offset = int(row["utc_offset_minutes"])
                stamp = (row["datetime"] - timedelta(minutes=offset)).replace(tzinfo=timezone.utc)
                candles.append({
                    "timestamp_utc": stamp,
                    **{key: float(row[key]) for key in ("open", "high", "low", "close")},
                    "volume": float(row["volume"]) if row["volume"] is not None else None,
                })
            return pd.DataFrame(candles, columns=["timestamp_utc", "open", "high", "low", "close", "volume"])
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()

    def save_signal(self, result: dict, candles_count: int, audio_notification: bool = False) -> bool:
        """Zapisuje wynik i potencjał w jednej transakcji; zwraca False dla duplikatu/NONE.

        Argumenty: result to pełny wynik generate_signal, candles_count to liczba świec.
        Wyjątki: ValueError dla błędnego wyniku, pozostałe błędy bazy są propagowane.
        Flaga konfiguracji pomija wynik NONE, gdy zapis jest wyłączony.
        """
        price = validated_price(result)
        signal = result["signal"].upper()
        if signal not in ("LONG", "SHORT", "NONE"):
            raise ValueError("Nieznany rodzaj sygnału.")
        if signal == "NONE" and not config.SAVE_NONE_SIGNALS:
            return False
        opened = _utc(datetime.fromisoformat(result["candle_open_time"]))
        closed = _utc(datetime.fromisoformat(result["timestamp"]))
        if opened.second or opened.microsecond or closed != opened + timedelta(minutes=1):
            raise ValueError("Czasy muszą opisywać zamkniętą świecę M1.")
        if result["candle_open_epoch"] != int(opened.timestamp()):
            raise ValueError("Epoka otwarcia nie zgadza się z czasem świecy.")
        local = closed.astimezone(config.WARSAW)
        parameters = dict(result["signal_parameters"])
        parameters.update({key: value for key, value in result.items() if key != "signal_parameters"})
        parameters["audio_notification"] = bool(audio_notification)
        payload = json.dumps(parameters, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        connection = self._connect()
        cursor = None
        try:
            connection.start_transaction()
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """INSERT INTO signals (`datetime`, product, price, `signal`, algorithm_name,
                       candles_count, signal_parameters, source_timestamp)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (local.replace(tzinfo=None), config.DB_SYMBOL, price,
                     signal.lower(), config.STRATEGY_NAME, candles_count, payload, int(opened.timestamp())),
                )
            except MySQLError as error:
                if error.errno == 1062:
                    connection.rollback()
                    return False
                raise
            if signal != "NONE":
                cursor.execute(
                    "INSERT INTO signal_potention (signal_id, spread) VALUES (%s, %s)",
                    (cursor.lastrowid, Decimal(str(config.ASSUMED_COST_POINTS))),
                )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()


def _utc(value: datetime) -> datetime:
    """Zwraca świadomy czas value w UTC; ValueError dla czasu bez strefy."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Czas musi zawierać strefę czasową.")
    return value.astimezone(timezone.utc)
