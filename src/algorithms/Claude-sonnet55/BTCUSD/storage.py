"""Backend: odczyt świec IBKR i idempotentny zapis do istniejących tabel."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from typing import Any

try:
    from . import config
    from .schedule import as_utc
except ImportError:
    import config
    from schedule import as_utc

class BTCUSDStorage:
    """Udostępnia MySQL w trybie samodzielnym albo istniejące repozytorium API."""

    def __init__(self, repository: Any | None = None, settings: dict | None = None):
        """Przyjmuje istniejące repozytorium lub ustawienia z MYSQL_*."""
        self.repository = repository
        self.settings = settings if settings is not None else {
            "host": os.getenv("MYSQL_HOST", "localhost"),
            "port": int(os.getenv("MYSQL_PORT", "3306")),
            "user": os.getenv("MYSQL_USER", "root"),
            "password": os.getenv("MYSQL_PASSWORD", ""),
            "database": os.getenv("MYSQL_DATABASE", "gielda"),
        }

    def _connect(self):
        """Otwiera połączenie MySQL; błędy sterownika są przekazywane wyżej."""
        import mysql.connector

        return mysql.connector.connect(**self.settings)

    @staticmethod
    def _row_timestamp(row: dict) -> datetime:
        """Odtwarza UTC z lokalnego DATETIME i jawnego przesunięcia UTC."""
        try:
            offset_minutes = int(row["utc_offset_minutes"])
            if (
                not -config.MAX_UTC_OFFSET_MINUTES
                <= offset_minutes
                <= config.MAX_UTC_OFFSET_MINUTES
            ):
                raise ValueError("Offset UTC świecy jest poza dozwolonym zakresem.")
            offset = timezone(timedelta(minutes=offset_minutes))
            return row["datetime"].replace(tzinfo=offset).astimezone(timezone.utc)
        except (KeyError, AttributeError, TypeError, ValueError, OverflowError) as error:
            raise ValueError("Nie można odtworzyć czasu UTC świecy BTC/USD.") from error

    def load_context(
        self, cutoff: datetime, limit: int = config.HISTORY_LIMIT
    ) -> list[dict]:
        """Wczytuje maksymalnie limit rzeczywistych świec zamkniętych do cutoff."""
        cutoff_utc = as_utc(cutoff)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Limit historii musi być dodatnią liczbą całkowitą.")
        if self.repository is not None:
            return self.repository.fetch_recent_candle_history(
                config.DB_SYMBOL, limit, cutoff_utc
            )

        connection = self._connect()
        cursor = None
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT `datetime`, utc_offset_minutes, `open`, high, low, `close`, volume,
                          bid, ask
                   FROM candles_IBKR
                   WHERE symbol = %s
                     AND DATE_SUB(`datetime`, INTERVAL utc_offset_minutes MINUTE) <= %s
                   ORDER BY `datetime` DESC, utc_offset_minutes DESC
                   LIMIT %s""",
                (
                    config.DB_SYMBOL,
                    cutoff_utc.replace(tzinfo=None),
                    limit + config.CANDLE_QUERY_LIMIT_PADDING,
                ),
            )
            normalized = []
            for row in cursor.fetchall():
                timestamp = self._row_timestamp(row)
                if timestamp > cutoff_utc:
                    continue
                normalized.append(
                    {
                        "timestamp_utc": timestamp,
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": (
                            float(row["volume"])
                            if row["volume"] is not None
                            else None
                        ),
                        "bid": float(row["bid"]) if row["bid"] is not None else None,
                        "ask": float(row["ask"]) if row["ask"] is not None else None,
                    }
                )
            normalized.sort(key=lambda candle: candle["timestamp_utc"])
            deduplicated = {
                candle["timestamp_utc"]: candle for candle in normalized
            }
            return [
                deduplicated[timestamp]
                for timestamp in sorted(deduplicated)[-limit:]
            ]
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()

    def latest_closed_open(self, now: datetime) -> datetime | None:
        """Zwraca otwarcie najnowszej zamkniętej minuty lub None."""
        current = as_utc(now)
        cutoff = current.replace(second=0, microsecond=0) - config.CANDLE_DURATION
        candles = self.load_context(cutoff, 1)
        return (
            as_utc(candles[-1]["timestamp_utc"])
            if candles
            and as_utc(candles[-1]["timestamp_utc"]) + config.CANDLE_DURATION <= current
            else None
        )

    def has_processed(self, opened: datetime) -> bool:
        """Sprawdza marker ewaluacji i starszy sygnał tej strategii dla świecy."""
        source_timestamp = int(as_utc(opened).timestamp())
        if self.repository is not None:
            return self.repository.has_algorithm_evaluation(
                config.DB_SYMBOL, config.STRATEGY_NAME, source_timestamp
            )
        connection = self._connect()
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(
                """SELECT 1 FROM algorithm_evaluations
                   WHERE product = %s AND algorithm_name = %s AND source_timestamp = %s
                   UNION ALL
                   SELECT 1 FROM signals
                   WHERE product = %s AND algorithm_name = %s AND source_timestamp = %s
                   LIMIT 1""",
                (
                    config.DB_SYMBOL,
                    config.STRATEGY_NAME,
                    source_timestamp,
                    config.DB_SYMBOL,
                    config.STRATEGY_NAME,
                    source_timestamp,
                ),
            )
            return cursor.fetchone() is not None
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()

    def latest_processed_open(self) -> datetime | None:
        """Zwraca najnowszą trwale ocenioną świecę, także zapis legacy."""
        if self.repository is not None:
            return None
        connection = self._connect()
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(
                """SELECT MAX(source_timestamp) FROM (
                       SELECT source_timestamp FROM algorithm_evaluations
                       WHERE product = %s AND algorithm_name = %s
                       UNION ALL
                       SELECT source_timestamp FROM signals
                       WHERE product = %s AND algorithm_name = %s
                   ) AS processed_candles""",
                (
                    config.DB_SYMBOL,
                    config.STRATEGY_NAME,
                    config.DB_SYMBOL,
                    config.STRATEGY_NAME,
                ),
            )
            row = cursor.fetchone()
            return (
                datetime.fromtimestamp(int(row[0]), timezone.utc)
                if row and row[0] is not None
                else None
            )
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()

    def last_signal_timestamp(
        self, opened: datetime, lookback_seconds: int
    ) -> int | None:
        """Zwraca otwarcie ostatniego LONG/SHORT w podanym oknie cooldownu."""
        source_timestamp = int(as_utc(opened).timestamp())
        if self.repository is not None:
            return self.repository.fetch_recent_algorithm_signal_timestamp(
                config.DB_SYMBOL,
                config.STRATEGY_NAME,
                source_timestamp,
                lookback_seconds,
            )
        connection = self._connect()
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(
                """SELECT MAX(source_timestamp) FROM signals
                   WHERE product = %s AND algorithm_name = %s
                     AND `signal` IN ('long', 'short')
                     AND source_timestamp >= %s AND source_timestamp < %s""",
                (
                    config.DB_SYMBOL,
                    config.STRATEGY_NAME,
                    source_timestamp - lookback_seconds,
                    source_timestamp,
                ),
            )
            row = cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else None
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()

    def save(
        self,
        result: dict,
        candles_count: int,
        *,
        enqueue_notification: bool = False,
    ) -> bool:
        """Zapisuje sygnał przez repozytorium API albo bezpośrednio w MySQL."""
        direction = str(result.get("signal", "")).upper()
        if direction not in {"LONG", "SHORT", "NONE"}:
            raise ValueError("Sygnał musi mieć wartość LONG, SHORT albo NONE.")
        # TYMCZASOWE: przełącznik pozwala czasowo zapisywać także oceny NONE.
        save_none_signals = config.SAVE_NONE_SIGNALS
        required = (
            "price",
            "timestamp",
            "candle_close_time",
            "candle_open_epoch",
        )
        if any(result.get(key) is None for key in required):
            raise ValueError("Wynik nie zawiera danych wymaganych do zapisu.")
        parameters = result.get("signal_parameters")
        if not isinstance(parameters, dict):
            raise ValueError("Brakuje kompaktowego JSON-u sygnału.")
        serialized = json.dumps(parameters, ensure_ascii=False, allow_nan=False)
        if len(serialized.encode("utf-8")) > config.SIGNAL_PARAMS_MAX_BYTES:
            raise ValueError("JSON sygnału przekracza limit rozmiaru 512 bajtów.")

        repository_signal = {
            "signal": direction,
            "price": result["price"],
            "timestamp": result["candle_close_time"],
            "candle_open_epoch": int(result["candle_open_epoch"]),
            "signal_parameters": parameters,
        }
        if self.repository is not None:
            return self.repository.save_algorithm_signal(
                config.DB_SYMBOL,
                config.STRATEGY_NAME,
                repository_signal,
                candles_count,
                record_evaluation=True,
                save_signal=(direction != "NONE" or save_none_signals),
                save_none_signal=(
                    direction == "NONE" and save_none_signals
                ),
                save_signal_potential=False,
                enqueue_notification=(
                    enqueue_notification and direction in {"LONG", "SHORT"}
                ),
            )

        import mysql.connector

        connection = self._connect()
        cursor = None
        try:
            cursor = connection.cursor()
            source_timestamp = int(result["candle_open_epoch"])
            cursor.execute(
                """INSERT IGNORE INTO algorithm_evaluations
                   (product, algorithm_name, source_timestamp, `signal`)
                   VALUES (%s, %s, %s, %s)""",
                (
                    config.DB_SYMBOL,
                    config.STRATEGY_NAME,
                    source_timestamp,
                    direction.lower(),
                ),
            )
            if cursor.rowcount != 1:
                connection.commit()
                return False
            if direction != "NONE" or save_none_signals:
                close_time = datetime.fromisoformat(
                    str(result["candle_close_time"]).replace("Z", "+00:00")
                )
                if close_time.tzinfo is None or close_time.utcoffset() is None:
                    raise ValueError("Czas sygnału musi zawierać strefę czasową.")
                local_close = close_time.astimezone(config.WARSAW).replace(
                    tzinfo=None
                )
                cursor.execute(
                    """INSERT IGNORE INTO signals
                       (`datetime`, product, price, `signal`, algorithm_name,
                        ai_decision, transaction_id, candles_count,
                        signal_parameters, source_timestamp)
                       VALUES (%s, %s, %s, %s, %s, NULL, NULL, %s, %s, %s)""",
                    (
                        local_close,
                        config.DB_SYMBOL,
                        Decimal(str(result["price"])),
                        direction.lower(),
                        config.STRATEGY_NAME,
                        max(0, int(candles_count)),
                        serialized,
                        source_timestamp,
                    ),
                )
                inserted = cursor.rowcount == 1
            else:
                inserted = False
            connection.commit()
            return inserted
        except (
            mysql.connector.Error,
            ValueError,
            TypeError,
            KeyError,
            OverflowError,
        ):
            connection.rollback()
            raise
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()
