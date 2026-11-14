"""
Backend
Odczyt świec z bazy MySQL i zapis sygnałów.
"""

import os
import logging
import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, List
import mysql.connector

from config import (
    INSTRUMENT,
    WARSAW_TIMEZONE,
    UTC_TIMEZONE,
    STRATEGY_NAME,
    SAVE_NONE_SIGNALS,
)

logger = logging.getLogger(__name__)


class CandleDatabase:
    """Backend - Zarządza połączeniem z bazą i pobieraniem świec."""

    def __init__(self) -> None:
        """Inicjalizuje konfigurację połączenia z MySQL."""
        self.database_settings = {
            "host": os.environ.get("MYSQL_HOST", "localhost"),
            "port": int(os.environ.get("MYSQL_PORT", "3306")),
            "user": os.environ.get("MYSQL_USER", "root"),
            "password": os.environ.get("MYSQL_PASSWORD", ""),
            "database": os.environ.get("MYSQL_DATABASE", "gielda"),
        }

    def fetch_candles(self, symbol: str, limit: int = 500) -> List[dict]:
        """
        Backend
        Pobiera ostatnie N zamkniętych świec 1M z bazy.
        Zwraca listę słowników z kluczami: datetime_utc, open, high, low, close, volume
        """
        try:
            connection = mysql.connector.connect(**self.database_settings)
            cursor = connection.cursor(dictionary=True)

            query = """
                SELECT 
                    datetime,
                    utc_offset_minutes,
                    open,
                    high,
                    low,
                    close,
                    volume
                FROM candles
                WHERE symbol = %s
                ORDER BY datetime DESC
                LIMIT %s
            """

            cursor.execute(query, (symbol, limit))
            rows = cursor.fetchall()
            cursor.close()
            connection.close()

            if not rows:
                logger.warning(f"Brak świec dla symbolu {symbol} w bazie.")
                return []

            # Konwertuj do UTC i odwróć kolejność (od najstarszej do najnowszej)
            candles = []
            for row in reversed(rows):
                datetime_local = row["datetime"]
                offset_minutes = row["utc_offset_minutes"]

                # Konwersja z czasu lokalnego (Warszawa) na UTC
                offset = timedelta(minutes=offset_minutes)
                dt_with_tz = datetime_local.replace(tzinfo=WARSAW_TIMEZONE)
                dt_utc = dt_with_tz.astimezone(UTC_TIMEZONE)

                candles.append(
                    {
                        "datetime_utc": dt_utc,
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]) if row["volume"] else None,
                    }
                )

            return candles

        except mysql.connector.Error as error:
            logger.error(f"Błąd połączenia MySQL: {error}")
            raise
        except Exception as error:
            logger.error(f"Błąd przy pobieraniu świec: {error}")
            raise

    def save_signal(
        self,
        signal_dict: dict,
        candles_count: int,
    ) -> bool:
        """
        Backend
        Zapisuje sygnał do bazy (lub pomija NONE jeśli SAVE_NONE_SIGNALS=False).
        Zwraca True jeśli sygnał został zapisany, False jeśli pominięty.
        """
        signal_type = signal_dict.get("signal", "NONE")

        # Filtruj sygnały NONE jeśli SAVE_NONE_SIGNALS=False
        if signal_type == "NONE" and not SAVE_NONE_SIGNALS:
            logger.debug(f"Sygnał NONE pominięty (SAVE_NONE_SIGNALS=False)")
            return False

        try:
            connection = mysql.connector.connect(**self.database_settings)
            cursor = connection.cursor()

            # Konwertuj timestamp do czasu lokalnego (Warszawa)
            timestamp_utc = signal_dict.get("timestamp_utc")
            if isinstance(timestamp_utc, str):
                timestamp_utc = datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00"))
            if timestamp_utc.tzinfo is None:
                timestamp_utc = timestamp_utc.replace(tzinfo=UTC_TIMEZONE)

            timestamp_local = timestamp_utc.astimezone(WARSAW_TIMEZONE).replace(tzinfo=None)
            offset_minutes = int(timestamp_local.astimezone(WARSAW_TIMEZONE).utcoffset().total_seconds() // 60)

            # Przygotuj parametry do zapisu
            price = Decimal(str(signal_dict.get("price", 0)))
            signal_params = {
                k: v
                for k, v in signal_dict.get("params", {}).items()
                if k not in ["signal", "timestamp", "price"]
            }

            # Dodaj inne interesujące pola do params jeśli istnieją
            if "setup" in signal_dict:
                signal_params["setup"] = signal_dict["setup"]
            if "score" in signal_dict:
                signal_params["score"] = signal_dict["score"]
            if "reasons" in signal_dict:
                signal_params["reasons"] = signal_dict["reasons"]
            if "blocked_by" in signal_dict:
                signal_params["blocked_by"] = signal_dict["blocked_by"]
            if "tp_price" in signal_dict:
                signal_params["tp_price"] = signal_dict["tp_price"]
            if "sl_price" in signal_dict:
                signal_params["sl_price"] = signal_dict["sl_price"]

            signal_parameters_json = json.dumps(signal_params)

            # Upewnij się że tabela zawiera kolumnę dla signal (jeśli nie zawiera NONE)
            # Dla teraz: wstaw normalnie, a base obsłuży UPSERT
            insert_query = """
                INSERT INTO signals
                    (`datetime`, product, price, `signal`, algorithm_name,
                     candles_count, signal_parameters, source_timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    `signal` = VALUES(`signal`),
                    price = VALUES(price),
                    signal_parameters = VALUES(signal_parameters),
                    candles_count = VALUES(candles_count)
            """

            cursor.execute(
                insert_query,
                (
                    timestamp_local,
                    INSTRUMENT,
                    price,
                    signal_type.lower(),  # 'long', 'short', 'none'
                    STRATEGY_NAME,
                    candles_count,
                    signal_parameters_json,
                    int(timestamp_utc.timestamp()),  # source_timestamp
                ),
            )

            connection.commit()

            # Sprawdź czy to był INSERT (nowy sygnał)
            was_inserted = cursor.rowcount > 0

            cursor.close()
            connection.close()

            if was_inserted:
                logger.info(
                    f"Sygnał {signal_type} zapisany dla {INSTRUMENT} "
                    f"o {timestamp_local} UTC{offset_minutes:+d}"
                )
            else:
                logger.debug(f"Sygnał {signal_type} (duplikat) nie zapisany")

            return was_inserted

        except mysql.connector.Error as error:
            logger.error(f"Błąd MySQL przy zapisie sygnału: {error}")
            raise
        except Exception as error:
            logger.error(f"Błąd przy zapisie sygnału: {error}")
            raise
