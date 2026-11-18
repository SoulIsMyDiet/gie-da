"""Odczyt świec i transakcyjny zapis wyników USD/JPY w MySQL."""

import json
import logging
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import mysql.connector
from mysql.connector import Error as MySQLError

import config
from schedule import TOKYO

UTC = timezone.utc
WARSAW = ZoneInfo(config.LOCAL_TIME_ZONE)
logger = logging.getLogger(__name__)


def _read_project_environment() -> dict[str, str]:
    """Odczytuje MYSQL_* z najbliższego pliku .env, jeśli plik istnieje."""
    values = {}
    env_path = next(
        (
            parent / ".env"
            for parent in Path(__file__).resolve().parents
            if (parent / ".env").is_file()
        ),
        None,
    )
    if env_path is None:
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key.startswith("MYSQL_"):
            values[key] = value.strip().strip("\"'")
    return values


def _database_settings() -> dict[str, Any]:
    """Łączy ustawienia .env z nadpisaniami środowiska procesu."""
    file_values = _read_project_environment()

    def setting(name: str, default: str) -> str:
        return os.environ.get(name, file_values.get(name, default))

    return {
        "host": setting("MYSQL_HOST", "127.0.0.1"),
        "port": int(setting("MYSQL_PORT", "3306")),
        "database": setting("MYSQL_DATABASE", "gie_da"),
        "user": setting("MYSQL_USER", "gie_da_app"),
        "password": setting("MYSQL_PASSWORD", ""),
        "charset": "utf8mb4",
        "use_unicode": True,
    }


def _json_value(value: Any) -> Any:
    """Przygotowuje płaskie dane do JSON i zastępuje NaN oraz inf wartością null."""
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if hasattr(value, "item"):
        return _json_value(value.item())
    return value


def _round_price(value: Any) -> float:
    """Zaokrągla cenę do skali projektu, zgodnie z regułą połowy w górę."""
    quantum = Decimal(1).scaleb(-config.PRICE_DECIMALS)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def build_signal_parameters(result: dict[str, Any]) -> dict[str, Any]:
    """Buduje uporządkowany, płaski dokument JSON dla signals.signal_parameters."""
    required = ("price", "signal", "timestamp", "candle_open_time", "reason")
    if any(key not in result for key in required):
        raise ValueError("Wynik nie zawiera wszystkich wymaganych pól sygnału.")
    params = result.get("params")
    if not isinstance(params, dict):
        raise ValueError("Wynik strategii nie zawiera słownika params.")
    payload = {
        "price": _round_price(result["price"]),
        "signal": str(result["signal"]).upper(),
        "timestamp": int(result["timestamp"]),
        "instrument": config.INSTRUMENT_PARAMS_NAME,
        "reason": str(result["reason"]),
        "setup": result.get("setup"),
        "score": int(result.get("score", 0)),
        "tp_price": (
            _round_price(result["tp_price"])
            if result.get("tp_price") is not None
            else None
        ),
        "sl_price": (
            _round_price(result["sl_price"])
            if result.get("sl_price") is not None
            else None
        ),
        "max_hold_minutes": int(result["max_hold_minutes"]),
        "candle_open_time": str(result["candle_open_time"]),
        "blocked_by": list(result.get("blocked_by", [])),
        "reasons": list(result.get("reasons", [])),
    }
    for key, value in params.items():
        if key in payload:
            raise ValueError(f"Parametr {key} powtarza klucz wyniku.")
        if isinstance(value, (dict, list, tuple)):
            raise ValueError(f"Parametr {key} musi być wartością płaską.")
        payload[key] = _json_value(value)
    return payload


def _decode_json(value: Any) -> dict[str, Any]:
    """Odczytuje dokument JSON z MySQL i sprawdza jego podstawowy kształt."""
    if isinstance(value, (str, bytes, bytearray)):
        decoded = json.loads(value)
    else:
        decoded = value
    if not isinstance(decoded, dict):
        raise ValueError("Zapisane signal_parameters nie jest obiektem JSON.")
    return decoded


class USDJPYStorage:
    """Udostępnia świece i sygnały USD/JPY; błędy MySQL są propagowane."""

    def __init__(self, settings: dict[str, Any] | None = None):
        """Zapamiętuje ustawienia MySQL lub domyślną konfigurację projektu."""
        self.settings = settings if settings is not None else _database_settings()

    def _connect(self):
        """Otwiera połączenie MySQL kodujące tekst jako UTF-8 mb4."""
        return mysql.connector.connect(**self.settings)

    def _fetchone(self, query: str, values: tuple[Any, ...]) -> Any:
        """Wykonuje odczyt pojedynczego wiersza i zawsze zamyka zasoby."""
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
        """Zwraca wynik zapisany dla epoki zamknięcia albo None."""
        row = self._fetchone(
            """SELECT signal_parameters FROM signals
               WHERE product = %s AND algorithm_name = %s AND source_timestamp = %s
               LIMIT 1""",
            (config.DB_SYMBOL, config.STRATEGY_NAME, closed_epoch),
        )
        if row is None:
            return None
        payload = _decode_json(row[0])
        if payload.get("timestamp") != closed_epoch:
            raise ValueError("Zapisany JSON nie odpowiada zamkniętej świecy.")
        return payload

    def load_context(
        self, cutoff: datetime, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Pobiera ostatnie świece do odcięcia, korygując UTC po pobraniu."""
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("Czas odcięcia historii musi zawierać strefę.")
        history_limit = config.HISTORY_LIMIT if limit is None else limit
        if (
            isinstance(history_limit, bool)
            or not isinstance(history_limit, int)
            or history_limit < 1
        ):
            raise ValueError("Limit historii musi być dodatnią liczbą całkowitą.")
        cutoff_utc = cutoff.astimezone(UTC).replace(tzinfo=None)
        local_cutoff_bound = cutoff_utc + timedelta(
            minutes=config.MAX_LOCAL_UTC_OFFSET_MINUTES
        )
        connection = self._connect()
        cursor = None
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT `datetime`, utc_offset_minutes, `open`, high, low, `close`, volume
                   FROM candles
                   WHERE symbol = %s
                     AND `datetime` <= %s
                     AND utc_offset_minutes BETWEEN %s AND %s
                   ORDER BY `datetime` DESC, utc_offset_minutes DESC
                   LIMIT %s""",
                (
                    config.DB_SYMBOL,
                    local_cutoff_bound,
                    -config.MAX_VALID_UTC_OFFSET_MINUTES,
                    config.MAX_VALID_UTC_OFFSET_MINUTES,
                    history_limit + config.CANDLE_QUERY_OVERFETCH,
                ),
            )
            result = []
            for row in cursor.fetchall():
                opened_utc = (
                    row["datetime"] - timedelta(
                        minutes=int(row["utc_offset_minutes"])
                    )
                ).replace(tzinfo=UTC)
                if opened_utc.replace(tzinfo=None) > cutoff_utc:
                    continue
                result.append(
                    {
                        "timestamp_utc": opened_utc,
                        **{
                            key: float(row[key])
                            for key in ("open", "high", "low", "close")
                        },
                        "volume": (
                            float(row["volume"])
                            if row["volume"] is not None
                            else None
                        ),
                    }
                )
            result.sort(key=lambda candle: candle["timestamp_utc"])
            return result[-history_limit:]
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()

    def has_candle(self, opened_epoch: int) -> bool:
        """Sprawdza, czy baza zawiera świecę o podanym otwarciu UTC."""
        opened_utc = datetime.fromtimestamp(opened_epoch, UTC)
        opened_local = opened_utc.astimezone(WARSAW)
        row = self._fetchone(
            """SELECT 1 FROM candles
               WHERE symbol = %s
                 AND `datetime` = %s
                 AND utc_offset_minutes = %s
               LIMIT 1""",
            (
                config.DB_SYMBOL,
                opened_local.replace(tzinfo=None),
                int(opened_local.utcoffset().total_seconds() // 60),
            ),
        )
        return row is not None

    def latest_candle_open_epoch(self) -> int | None:
        """Zwraca otwarcie najnowszej świecy USD/JPY jako epokę UTC."""
        candles = self.load_context(datetime.now(UTC), limit=1)
        return (
            int(candles[-1]["timestamp_utc"].timestamp())
            if candles
            else None
        )

    def latest_processed_close_epoch(self) -> int | None:
        """Zwraca epokę zamknięcia ostatniej oceny tej strategii."""
        row = self._fetchone(
            """SELECT MAX(source_timestamp) FROM signals
               WHERE product = %s AND algorithm_name = %s""",
            (config.DB_SYMBOL, config.STRATEGY_NAME),
        )
        return int(row[0]) if row and row[0] is not None else None

    def signal_history(self, instant: datetime) -> dict[str, Any]:
        """Pobiera cooldown, dzienny limit i wybicia dla japońskiego dnia handlu."""
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("Czas historii sygnałów musi zawierać strefę.")
        current = instant.astimezone(UTC)
        tokyo_day = current.astimezone(TOKYO).date()
        start = datetime.combine(tokyo_day, time.min, tzinfo=TOKYO)
        end = datetime.combine(
            tokyo_day + timedelta(days=1), time.min, tzinfo=TOKYO
        )
        current_epoch = int(current.timestamp())
        start_epoch = int(start.timestamp())
        end_epoch = int(end.timestamp())
        connection = self._connect()
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(
                """SELECT source_timestamp, CAST(signal_parameters AS CHAR)
                   FROM signals
                   WHERE product = %s AND algorithm_name = %s
                     AND `signal` IN ('long', 'short')
                     AND source_timestamp <= %s
                     AND (
                         source_timestamp >= %s
                         OR source_timestamp = (
                             SELECT MAX(source_timestamp) FROM signals
                             WHERE product = %s AND algorithm_name = %s
                               AND `signal` IN ('long', 'short')
                               AND source_timestamp < %s
                         )
                     )
                   ORDER BY source_timestamp DESC""",
                (
                    config.DB_SYMBOL,
                    config.STRATEGY_NAME,
                    current_epoch,
                    start_epoch,
                    config.DB_SYMBOL,
                    config.STRATEGY_NAME,
                    start_epoch,
                ),
            )
            rows = cursor.fetchall()
            today_count = 0
            breakout_directions = []
            for source_timestamp, serialized in rows:
                if start_epoch <= int(source_timestamp) < end_epoch:
                    today_count += 1
                    payload = _decode_json(serialized)
                    if payload.get("setup") == "RANGE_BREAKOUT":
                        breakout_directions.append(payload.get("signal"))
            return {
                "last_signal_epoch": int(rows[0][0]) if rows else None,
                "signals_today": today_count,
                "range_breakout_directions_today": breakout_directions,
            }
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()

    def save_signal(self, result: dict[str, Any], candles_count: int) -> bool:
        """Zapisuje sygnał transakcyjnie; zwraca False dla NONE wyłączonego lub duplikatu.

        Błędne dane zgłaszają ValueError, a błędy bazy są cofane i propagowane.
        """
        signal = result.get("signal")
        if not isinstance(signal, str) or signal.upper() not in {
            "LONG", "SHORT", "NONE"
        }:
            raise ValueError("Nieznany rodzaj sygnału.")
        signal = signal.upper()
        if signal == "NONE" and not config.SAVE_NONE_SIGNALS:
            return False
        if result.get("instrument") != config.INSTRUMENT_NAME:
            raise ValueError("Wynik dotyczy innego instrumentu.")
        if (
            isinstance(candles_count, bool)
            or not isinstance(candles_count, int)
            or candles_count < 1
        ):
            raise ValueError("Liczba świec musi być dodatnią liczbą całkowitą.")
        try:
            price = Decimal(str(result["price"])).quantize(
                Decimal(1).scaleb(-config.PRICE_DECIMALS),
                rounding=ROUND_HALF_UP,
            )
            if not price.is_finite() or price <= 0:
                raise ValueError("Cena musi być dodatnia i skończona.")
            opened = datetime.fromisoformat(result["candle_open_time"])
            if opened.tzinfo is None or opened.utcoffset() is None:
                raise ValueError("Czas otwarcia świecy musi zawierać strefę.")
            opened = opened.astimezone(UTC)
            closed_epoch = int(result["timestamp"])
            if (
                opened.second
                or opened.microsecond
                or closed_epoch
                != int((opened + timedelta(seconds=config.CANDLE_DURATION_SECONDS)).timestamp())
            ):
                raise ValueError("Czas wyniku nie odpowiada zamkniętej świecy M1.")
        except (KeyError, TypeError, InvalidOperation, OverflowError) as error:
            raise ValueError("Cena lub czas wyniku są nieprawidłowe.") from error

        payload = build_signal_parameters(result)
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
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
                        opened.astimezone(WARSAW).replace(tzinfo=None),
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
    """Zwraca ścieżkę pliku stanu z możliwością nadpisania zmienną środowiska."""
    override = os.getenv("USDJPY_STATE_FILE")
    return (
        Path(override)
        if override
        else Path.home() / ".cache" / config.STATE_FILE_NAME
    )


def read_loop_state(path: Path) -> int | None:
    """Odczytuje ostatnie przetworzone otwarcie albo zwraca None dla braku pliku."""
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    epoch = state.get("last_processed_open_epoch") if isinstance(state, dict) else None
    if isinstance(epoch, bool) or not isinstance(epoch, int):
        raise ValueError("Plik stanu zawiera nieprawidłową epokę.")
    return epoch


def write_loop_state(path: Path, opened_epoch: int) -> None:
    """Zapisuje epokę otwarcia atomową podmianą pliku UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"last_processed_open_epoch": opened_epoch}),
        encoding="utf-8",
    )
    temporary.replace(path)
