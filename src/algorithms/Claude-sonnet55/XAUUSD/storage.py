"""Odczyt świec i transakcyjny zapis sygnałów strategii XAU/USD."""

import json
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import mysql.connector
from mysql.connector import Error as MySQLError

import config

WARSAW = ZoneInfo(config.LOCAL_TIME_ZONE)
UTC = timezone.utc


def utc_time(value: datetime) -> datetime:
    """Zwraca czas w UTC; nieświadomy lub błędny czas zgłasza ValueError."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Czas musi zawierać strefę czasową.")
    return value.astimezone(UTC)


def build_signal_parameters(result: dict[str, Any]) -> dict[str, Any]:
    """Buduje JSON z pięcioma polami wymaganymi w signals.signal_parameters."""
    required = ("price", "signal", "timestamp", "candle_open_time", "reason")
    if any(key not in result for key in required):
        raise ValueError("Wynik strategii nie zawiera wymaganych pól.")
    if not isinstance(result.get("params"), dict):
        raise ValueError("Wynik strategii nie zawiera słownika params.")
    return {
        "price": round(float(result["price"]), config.SIGNAL_PRICE_DECIMALS),
        "signal": str(result["signal"]).upper(),
        "timestamp": int(result["timestamp"]),
        "instrument": config.INSTRUMENT_PARAMS_NAME,
        "reason": str(result["reason"]),
    }


def _is_range_breakout_signal(payload: dict[str, Any]) -> bool:
    """Rozpoznaje wybicie w nowych i starszych zapisach sygnałów."""
    if payload.get("setup") == "RANGE_BREAKOUT":
        return True
    reason = payload.get("reason")
    return isinstance(reason, str) and reason.startswith(
        "Spełnione warunki setupu RANGE_BREAKOUT ("
    )


class XAUUSDStorage:
    """Udostępnia świece i sygnały XAU/USD z MySQL; błędy bazy są propagowane."""

    def __init__(self, settings: dict[str, Any] | None = None):
        """Zapamiętuje opcjonalne ustawienia MySQL bez otwierania połączenia."""
        self.settings = settings if settings is not None else {
            "host": os.getenv("MYSQL_HOST", "localhost"),
            "port": int(os.getenv("MYSQL_PORT", "3306")),
            "user": os.getenv("MYSQL_USER", "root"),
            "password": os.getenv("MYSQL_PASSWORD", ""),
            "database": os.getenv("MYSQL_DATABASE", "gielda"),
            "charset": "utf8mb4",
        }

    def _connect(self):
        """Otwiera połączenie MySQL; błędy sterownika pozostają widoczne."""
        return mysql.connector.connect(**self.settings)

    def _fetchone(self, query: str, values: tuple[Any, ...]) -> Any:
        """Wykonuje pojedynczy odczyt i zawsze zamyka kursor oraz połączenie."""
        connection = self._connect()
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(query, values)
            return cursor.fetchone()
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()

    def get_processed_signal(self, closed_epoch: int) -> dict[str, Any] | None:
        """Zwraca sygnał zapisany dla epoki zamknięcia albo None."""
        row = self._fetchone(
            """SELECT signal_parameters FROM signals
               WHERE product = %s AND algorithm_name = %s AND source_timestamp = %s
               LIMIT 1""",
            (config.DB_SYMBOL, config.STRATEGY_NAME, closed_epoch),
        )
        if row is None:
            return None
        value = row[0]
        payload = json.loads(value) if isinstance(value, (str, bytes, bytearray)) else value
        if not isinstance(payload, dict) or payload.get("timestamp") != closed_epoch:
            raise ValueError("Zapisany signal_parameters nie odpowiada zamkniętej świecy.")
        return payload

    def load_context(self, cutoff: datetime, limit: int | None = None) -> list[dict[str, Any]]:
        """Zwraca historię do otwarcia cutoff UTC, w kolejności chronologicznej."""
        cutoff = utc_time(cutoff)
        limit = config.HISTORY_LIMIT if limit is None else limit
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
            result = []
            for row in reversed(cursor.fetchall()):
                opened_utc = (
                    row["datetime"] - timedelta(minutes=int(row["utc_offset_minutes"]))
                ).replace(tzinfo=UTC)
                result.append(
                    {
                        "timestamp_utc": opened_utc,
                        **{
                            key: float(row[key])
                            for key in ("open", "high", "low", "close")
                        },
                        "volume": (
                            float(row["volume"]) if row["volume"] is not None else None
                        ),
                    }
                )
            return result
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()

    def has_candle(self, opened_epoch: int) -> bool:
        """Sprawdza istnienie świecy o otwarciu opened_epoch UTC."""
        opened = datetime.fromtimestamp(opened_epoch, UTC)
        row = self._fetchone(
            """SELECT 1 FROM candles
               WHERE symbol = %s
                 AND DATE_SUB(`datetime`, INTERVAL utc_offset_minutes MINUTE) = %s
               LIMIT 1""",
            (config.DB_SYMBOL, opened.replace(tzinfo=None)),
        )
        return row is not None

    def latest_candle_open_epoch(self) -> int | None:
        """Zwraca otwarcie najnowszej świecy XAU/USD jako epokę UTC."""
        row = self._fetchone(
            """SELECT `datetime`, utc_offset_minutes FROM candles
               WHERE symbol = %s
               ORDER BY DATE_SUB(`datetime`, INTERVAL utc_offset_minutes MINUTE) DESC
               LIMIT 1""",
            (config.DB_SYMBOL,),
        )
        if row is None:
            return None
        opened = (row[0] - timedelta(minutes=int(row[1]))).replace(tzinfo=UTC)
        return int(opened.timestamp())

    def latest_processed_close_epoch(self) -> int | None:
        """Zwraca epokę zamknięcia ostatniej świecy tej strategii."""
        row = self._fetchone(
            """SELECT MAX(source_timestamp) FROM signals
               WHERE product = %s AND algorithm_name = %s""",
            (config.DB_SYMBOL, config.STRATEGY_NAME),
        )
        return int(row[0]) if row and row[0] is not None else None

    def signal_history(self, instant: datetime) -> dict[str, Any]:
        """Zwraca cooldown oraz limity sygnałów dla bieżącego dnia lokalnego."""
        instant = utc_time(instant)
        local_day = instant.astimezone(WARSAW).date()
        start = datetime.combine(local_day, time.min, tzinfo=WARSAW).astimezone(UTC)
        end = datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=WARSAW).astimezone(UTC)
        instant_epoch = int(instant.timestamp())
        connection = self._connect()
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(
                """SELECT source_timestamp, CAST(signal_parameters AS CHAR)
                   FROM signals
                   WHERE product = %s AND algorithm_name = %s
                     AND `signal` IN ('long', 'short')
                     AND source_timestamp <= %s AND source_timestamp < %s
                     AND (
                         source_timestamp >= %s
                         OR source_timestamp = (
                             SELECT MAX(source_timestamp) FROM signals
                             WHERE product = %s AND algorithm_name = %s
                               AND `signal` IN ('long', 'short')
                               AND source_timestamp <= %s
                         )
                     )
                   ORDER BY source_timestamp DESC""",
                (
                    config.DB_SYMBOL,
                    config.STRATEGY_NAME,
                    instant_epoch,
                    int(end.timestamp()),
                    int(start.timestamp()),
                    config.DB_SYMBOL,
                    config.STRATEGY_NAME,
                    instant_epoch,
                ),
            )
            rows = cursor.fetchall()
            directions = []
            today_count = 0
            for source_timestamp, serialized in rows:
                if int(source_timestamp) < int(start.timestamp()):
                    continue
                today_count += 1
                payload = json.loads(serialized) if isinstance(serialized, (str, bytes)) else serialized
                if _is_range_breakout_signal(payload):
                    directions.append(payload.get("signal"))
            return {
                "last_signal_epoch": int(rows[0][0]) if rows else None,
                "signals_today": today_count,
                "range_breakout_directions_today": directions,
            }
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()

    def save_signal(self, result: dict[str, Any], candles_count: int) -> bool:
        """Zapisuje sygnał w pojedynczej transakcji; duplikat zwraca False.

        Błędne dane zgłaszają ValueError, a błędy MySQL są cofane i propagowane.
        """
        signal = result.get("signal")
        if not isinstance(signal, str) or signal.upper() not in ("LONG", "SHORT", "NONE"):
            raise ValueError("Nieznany rodzaj sygnału.")
        signal = signal.upper()
        if signal == "NONE" and not config.SAVE_NONE_SIGNALS:
            return False
        if result.get("instrument") != config.INSTRUMENT_NAME:
            raise ValueError("Nieznany instrument w wyniku strategii.")
        if isinstance(candles_count, bool) or not isinstance(candles_count, int) or candles_count < 1:
            raise ValueError("Liczba świec musi być dodatnią liczbą całkowitą.")
        try:
            price = Decimal(str(result["price"])).quantize(
                Decimal(1).scaleb(-config.SIGNAL_PRICE_DECIMALS),
                rounding=ROUND_HALF_UP,
            )
            if not price.is_finite() or price <= 0:
                raise ValueError("Cena musi być dodatnia i skończona.")
            opened = utc_time(datetime.fromisoformat(result["candle_open_time"]))
            closed = opened + timedelta(minutes=1)
            closed_epoch = int(result["timestamp"])
            if (
                opened.second
                or opened.microsecond
                or closed_epoch != int(closed.timestamp())
            ):
                raise ValueError("Czas świecy nie odpowiada zamkniętej minucie.")
        except (KeyError, TypeError, InvalidOperation, OverflowError) as error:
            raise ValueError("Nieprawidłowa cena lub czas wyniku.") from error
        payload = build_signal_parameters(result)
        serialized = json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        connection = self._connect()
        cursor = None
        try:
            connection.start_transaction()
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """INSERT INTO signals
                       (`datetime`, product, price, `signal`, algorithm_name,
                        ai_decision, transaction_id, candles_count,
                        signal_parameters, source_timestamp)
                       VALUES (%s, %s, %s, %s, %s, NULL, NULL, %s, %s, %s)""",
                    (
                        closed.astimezone(WARSAW).replace(tzinfo=None),
                        config.DB_SYMBOL,
                        price,
                        signal.lower(),
                        config.STRATEGY_NAME,
                        candles_count,
                        serialized,
                        closed_epoch,
                    ),
                )
            except MySQLError as error:
                if error.errno == 1062:
                    connection.rollback()
                    return False
                raise
            if signal in {"LONG", "SHORT"}:
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


def default_state_path() -> Path:
    """Zwraca ścieżkę pliku stanu pętli z możliwością nadpisania zmienną env."""
    override = os.getenv("XAUUSD_STATE_FILE")
    return Path(override) if override else Path.home() / ".cache" / config.STATE_FILE_NAME


def read_loop_state(path: Path) -> int | None:
    """Odczytuje ostatnie przetworzone otwarcie z pliku stanu."""
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    epoch = value.get("last_processed_open_epoch") if isinstance(value, dict) else None
    if isinstance(epoch, bool) or not isinstance(epoch, int):
        raise ValueError("Plik stanu strategii zawiera nieprawidłową epokę.")
    return epoch


def write_loop_state(path: Path, opened_epoch: int) -> None:
    """Zapisuje ostatnie przetworzone otwarcie atomową podmianą pliku."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"last_processed_open_epoch": opened_epoch}),
        encoding="utf-8",
    )
    temporary.replace(path)
