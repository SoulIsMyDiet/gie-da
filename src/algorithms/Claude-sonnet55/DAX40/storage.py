"""Backend
Pobieranie historii GER30 i transakcyjne, idempotentne zapisy sygnałów.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

import mysql.connector

from .config import (
    ASSUMED_COST_POINTS,
    INSTRUMENT,
    SAVE_NONE_SIGNALS,
    STRATEGY_NAME,
    WARSAW_TIMEZONE,
)

logger = logging.getLogger(__name__)


class DAX40Storage:
    """Udostępnia wymagane odczyty i zapisy MySQL dla algorytmu DAX40."""

    def __init__(self, database_settings: dict[str, Any] | None = None) -> None:
        """Czyta konfigurację MySQL z tych samych zmiennych co lokalne API."""
        self.database_settings = database_settings or {
            "host": os.environ.get("MYSQL_HOST", "localhost"),
            "port": int(os.environ.get("MYSQL_PORT", "3306")),
            "user": os.environ.get("MYSQL_USER", "root"),
            "password": os.environ.get("MYSQL_PASSWORD", ""),
            "database": os.environ.get("MYSQL_DATABASE", "gielda"),
        }

    def load_context(self, limit: int = 500) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Ładuje świece oraz stan cooldownu i limitu dziennego z trwałej bazy."""
        connection = mysql.connector.connect(**self.database_settings)
        cursor = None
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT datetime, utc_offset_minutes, open, high, low, close, volume
                FROM candles
                WHERE symbol = %s
                ORDER BY DATE_SUB(datetime, INTERVAL utc_offset_minutes MINUTE) DESC
                LIMIT %s
                """,
                (INSTRUMENT, limit),
            )
            rows = list(reversed(cursor.fetchall()))
            candles = []
            for row in rows:
                utc_time = row["datetime"].replace(
                    tzinfo=timezone(timedelta(minutes=int(row["utc_offset_minutes"])))
                ).astimezone(timezone.utc)
                candles.append(
                    {
                        "timestamp_utc": utc_time,
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]) if row["volume"] is not None else None,
                    }
                )
            state = self._load_signal_state(cursor, datetime.now(timezone.utc))
            return candles, state
        finally:
            if cursor is not None:
                cursor.close()
            connection.close()

    @staticmethod
    def _load_signal_state(cursor: Any, reference_time: datetime) -> dict[str, Any]:
        """Odczytuje ostatni LONG/SHORT i liczbę takich sygnałów w dniu lokalnym."""
        local_now = reference_time.astimezone(WARSAW_TIMEZONE)
        local_day = local_now.date()
        local_start = datetime.combine(local_day, time.min, WARSAW_TIMEZONE)
        local_end = datetime.combine(local_day + timedelta(days=1), time.min, WARSAW_TIMEZONE)
        cursor.execute(
            """
            SELECT source_timestamp
            FROM signals
            WHERE product = %s AND algorithm_name = %s
                AND `signal` IN ('long', 'short')
                AND source_timestamp IS NOT NULL
            ORDER BY source_timestamp DESC
            LIMIT 1
            """,
            (INSTRUMENT, STRATEGY_NAME),
        )
        latest = cursor.fetchone()
        cursor.execute(
            """
            SELECT COUNT(*) AS signal_count
            FROM signals
            WHERE product = %s AND algorithm_name = %s
                AND `signal` IN ('long', 'short')
                AND source_timestamp >= %s AND source_timestamp < %s
            """,
            (
                INSTRUMENT,
                STRATEGY_NAME,
                int(local_start.timestamp()),
                int(local_end.timestamp()),
            ),
        )
        count_row = cursor.fetchone()
        cursor.execute(
            """
            SELECT `signal`
            FROM signals
            WHERE product = %s AND algorithm_name = %s
                AND `signal` IN ('long', 'short')
                AND source_timestamp >= %s AND source_timestamp < %s
                AND JSON_UNQUOTE(JSON_EXTRACT(signal_parameters, '$.setup'))
                    = 'OPENING_RANGE_BREAKOUT'
            """,
            (
                INSTRUMENT,
                STRATEGY_NAME,
                int(local_start.timestamp()),
                int(local_end.timestamp()),
            ),
        )
        orb_rows = cursor.fetchall()
        return {
            "last_signal_time": (
                datetime.fromtimestamp(
                    int(latest["source_timestamp"]) + 60, timezone.utc
                )
                if latest and latest["source_timestamp"] is not None
                else None
            ),
            "signals_today": int(count_row["signal_count"] or 0),
            "orb_directions_today": [
                str(row["signal"]).upper() for row in orb_rows
            ],
        }

    def save_signal(self, signal: dict[str, Any], candles_count: int) -> bool:
        """Zapisuje sygnał i jego parametry w jednej transakcji; duplikat zwraca False."""
        signal_type = str(signal.get("signal", "NONE")).upper()
        if signal_type == "NONE" and not SAVE_NONE_SIGNALS:
            logger.debug("Pominięto sygnał NONE zgodnie z konfiguracją.")
            return False

        source_time = signal.get("candle_open_time")
        close_time = signal.get("timestamp")
        if isinstance(source_time, str):
            source_time = datetime.fromisoformat(source_time.replace("Z", "+00:00"))
        if isinstance(close_time, str):
            close_time = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        if (
            not isinstance(source_time, datetime)
            or source_time.tzinfo is None
            or not isinstance(close_time, datetime)
            or close_time.tzinfo is None
        ):
            raise ValueError(
                "Sygnał musi zawierać świadome candle_open_time i timestamp."
            )
        source_time = source_time.astimezone(timezone.utc)
        close_time = close_time.astimezone(timezone.utc)
        local_time = close_time.astimezone(WARSAW_TIMEZONE)
        local_naive = local_time.replace(tzinfo=None)
        offset_minutes = int(local_time.utcoffset().total_seconds() // 60)
        parameters = dict(signal)
        parameters["utc_offset_minutes"] = offset_minutes
        serialized_parameters = json.dumps(
            parameters, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )

        connection = mysql.connector.connect(**self.database_settings)
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT IGNORE INTO signals
                    (`datetime`, product, price, `signal`, algorithm_name,
                     candles_count, signal_parameters, source_timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    local_naive,
                    INSTRUMENT,
                    Decimal(str(signal.get("price", 0))),
                    signal_type.lower(),
                    STRATEGY_NAME,
                    max(0, int(candles_count)),
                    serialized_parameters,
                    int(source_time.timestamp()),
                ),
            )
            inserted = cursor.rowcount == 1
            if inserted and signal_type in {"LONG", "SHORT"}:
                cursor.execute(
                    """
                    INSERT INTO signal_potention (signal_id, average_spread)
                    VALUES (%s, %s)
                    """,
                    (
                        cursor.lastrowid,
                        Decimal(str(ASSUMED_COST_POINTS)),
                    ),
                )
            connection.commit()
            return inserted
        except Exception:
            connection.rollback()
            raise
        finally:
            if cursor is not None:
                cursor.close()
            connection.close()
