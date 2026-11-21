"""Backend: odczyt świec i transakcyjny zapis wyników XAUUSD."""

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import mysql.connector
from mysql.connector import Error as MySQLError

from . import config

WARSAW = ZoneInfo("Europe/Warsaw")


def utc_time(value: datetime) -> datetime:
    """Zwraca czas value w UTC; ValueError oznacza brak świadomej strefy."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Czas musi zawierać strefę czasową.")
    return value.astimezone(timezone.utc)


def _compact_signal_payload(result: dict, price: Decimal, opened_epoch: int) -> dict:
    """Buduje mały JSON do kolumny signal_parameters z podstawowymi wskaźnikami.

    result zawiera pełny wynik algorytmu, price jest zweryfikowaną ceną, a
    opened_epoch epoką UTC otwarcia świecy. Brak wymaganych wskaźników lub
    powodów zgłasza ValueError zamiast zapisywać niekompletny wynik.
    """
    details = result.get("signal_parameters")
    indicators = details.get("indicators") if isinstance(details, dict) else None
    m1_indicators = (
        indicators.get("m1") if isinstance(indicators, dict) else None
    )
    reasons = details.get("reasons") if isinstance(details, dict) else None
    if not isinstance(m1_indicators, dict):
        raise ValueError("Wynik XAU/USD nie zawiera wskaźników M1.")
    if not isinstance(reasons, list) or not all(
        isinstance(reason, str) for reason in reasons
    ):
        raise ValueError("Wynik XAU/USD nie zawiera poprawnych powodów sygnału.")

    signal = result["signal"].upper()
    reason = next((item.strip() for item in reasons if item.strip()), "")
    if not reason:
        reason = (
            f"Warunki {signal} zostały spełnione."
            if signal in ("LONG", "SHORT")
            else "Brak kwalifikującego się setupu."
        )

    configuration = config.CONFIG
    return {
        "price": float(price),
        "signal": signal,
        "timestamp": opened_epoch,
        "instrument": config.DB_SYMBOL,
        "parameters": {
            f"ema{configuration.ema_fast}": m1_indicators.get(
                f"ema_{configuration.ema_fast}"
            ),
            f"atr{configuration.atr_period}": m1_indicators.get("atr"),
            f"ema{configuration.ema_middle}": m1_indicators.get(
                f"ema_{configuration.ema_middle}"
            ),
            f"rsi{configuration.rsi_period}": m1_indicators.get("rsi"),
            "reason": reason,
            "bb_lower": m1_indicators.get("bb_lower"),
            "bb_upper": m1_indicators.get("bb_upper"),
            "bb_middle": m1_indicators.get("bb_middle"),
        },
    }


class XAUUSDStorage:
    """Udostępnia świece i sygnały XAUUSD w MySQL; błędy bazy są propagowane."""

    def __init__(self, settings: dict | None = None):
        """Zapamiętuje opcjonalne ustawienia połączenia, bez łączenia z bazą."""
        self.settings = settings if settings is not None else {
            "host": os.getenv("MYSQL_HOST", "localhost"),
            "port": int(os.getenv("MYSQL_PORT", "3306")),
            "user": os.getenv("MYSQL_USER", "root"),
            "password": os.getenv("MYSQL_PASSWORD", ""),
            "database": os.getenv("MYSQL_DATABASE", "gielda"),
        }

    def _connect(self):
        """Otwiera połączenie MySQL; przekazuje błędy sterownika."""
        return mysql.connector.connect(**self.settings)

    def get_processed_signal(self, opened: datetime) -> dict | None:
        """Zwraca zapisany wynik dla otwarcia opened albo None; błędy propaguje."""
        opened = utc_time(opened)
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
            result = json.loads(row[0]) if isinstance(row[0], (str, bytes, bytearray)) else row[0]
            if not isinstance(result, dict) or not (
                isinstance(result.get("signal_parameters"), dict)
                or isinstance(result.get("parameters"), dict)
            ):
                raise ValueError("Zapisany wynik strategii nie jest kompletnym obiektem JSON.")
            epoch = result.get("candle_open_epoch", result.get("timestamp"))
            if isinstance(epoch, bool) or not isinstance(epoch, int):
                raise ValueError("Zapisany wynik strategii nie zawiera epoki świecy.")
            result.setdefault("candle_open_epoch", epoch)
            return result
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()

    def load_context(self, cutoff: datetime, limit: int | None = None) -> list[dict]:
        """Zwraca do limit świec do otwarcia cutoff UTC włącznie; błędy propaguje."""
        cutoff = utc_time(cutoff)
        if limit is None:
            limit = config.HISTORY_LIMIT
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Limit historii musi być dodatnią liczbą całkowitą.")
        connection = self._connect()
        cursor = None
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT `datetime`, utc_offset_minutes, `open`, high, low, `close`, volume
                   FROM candles
                   WHERE symbol = %s AND utc_offset_minutes BETWEEN 60 AND 120
                     AND DATE_SUB(`datetime`, INTERVAL utc_offset_minutes MINUTE) <= %s
                   ORDER BY DATE_SUB(`datetime`, INTERVAL utc_offset_minutes MINUTE) DESC
                   LIMIT %s""",
                (config.DB_SYMBOL, cutoff.replace(tzinfo=None), limit),
            )
            candles = []
            for row in reversed(cursor.fetchall()):
                opened = (row["datetime"] - timedelta(minutes=int(row["utc_offset_minutes"]))).replace(
                    tzinfo=timezone.utc
                )
                candles.append({
                    "timestamp_utc": opened,
                    **{key: float(row[key]) for key in ("open", "high", "low", "close")},
                    "volume": float(row["volume"]) if row["volume"] is not None else None,
                })
            return candles
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()

    def save_signal(self, result: dict, candles_count: int) -> bool:
        """Zapisuje wynik i zwraca True tylko dla nowego rekordu.

        Błędne dane zgłaszają ValueError; błędy bazy są cofane i propagowane.
        Dla nowego sygnału LONG/SHORT zapisuje także założony spread XAU/USD.
        """
        signal = result.get("signal")
        if not isinstance(signal, str) or signal.upper() not in ("LONG", "SHORT", "NONE"):
            raise ValueError("Nieznany rodzaj sygnału.")
        signal = signal.lower()
        if signal == "none" and not config.SAVE_NONE_SIGNALS:
            return False
        if result.get("instrument") != "XAUUSD" or not isinstance(result.get("signal_parameters"), dict):
            raise ValueError("Wynik nie zawiera instrumentu lub parametrów XAUUSD.")
        if isinstance(candles_count, bool) or not isinstance(candles_count, int) or candles_count < 1:
            raise ValueError("Liczba świec musi być dodatnią liczbą całkowitą.")
        try:
            if isinstance(result.get("price"), bool):
                raise ValueError("Cena nie może być wartością logiczną.")
            price = Decimal(str(result["price"]))
            if not price.is_finite() or price <= 0:
                raise ValueError("Cena musi być dodatnia i skończona.")
        except (KeyError, InvalidOperation, TypeError) as error:
            raise ValueError("Nieprawidłowa cena wyniku.") from error
        opened = utc_time(datetime.fromisoformat(result["candle_open_time"]))
        closed = utc_time(datetime.fromisoformat(result["timestamp"]))
        epoch = result.get("candle_open_epoch")
        if (opened.second or opened.microsecond or closed != opened + timedelta(minutes=1)
                or isinstance(epoch, bool) or not isinstance(epoch, int) or epoch != int(opened.timestamp())):
            raise ValueError("Czas lub epoka wyniku nie odpowiada zamkniętej świecy M1.")
        payload = _compact_signal_payload(result, price, epoch)
        serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
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
                    (closed.astimezone(WARSAW).replace(tzinfo=None), config.DB_SYMBOL, price,
                     signal, config.STRATEGY_NAME, candles_count, serialized, epoch),
                )
            except MySQLError as error:
                if error.errno == 1062:
                    connection.rollback()
                    return False
                raise
            if signal in ("long", "short"):
                cursor.execute(
                    """
                    INSERT INTO signal_potention (signal_id, average_spread)
                    VALUES (%s, %s)
                    """,
                    (
                        cursor.lastrowid,
                        Decimal(str(config.SIGNAL_POTENTIAL_SPREAD)),
                    ),
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
