"""Lokalne API zapisujące świece rynkowe w MySQL."""

# Backend
import copy
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

import mysql.connector
from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

algorithm_directory = Path(__file__).resolve().parent / "algorithm"
if not algorithm_directory.is_dir():
    algorithm_directory = (
        Path(__file__).resolve().parents[2] / "algorithms" / "Chat-gpt6"
    )
sys.path.insert(0, str(algorithm_directory))

from EURUSDStrategyV1 import EURUSDStrategyV1, STRATEGY_NAME  # noqa: E402
from market_data import Candle  # noqa: E402
from signal_serializer import Signal  # noqa: E402

WARSAW_TIME_ZONE = ZoneInfo("Europe/Warsaw")
NEW_YORK_TIME_ZONE = ZoneInfo("America/New_York")
MAX_BACKFILL_CANDLES = 1000


class CandleRecord(BaseModel):
    """Opis jednej świecy rynkowej przesyłanej przez aplikację desktopową."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9./_-]+$")
    datetime: datetime
    open: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=10)
    high: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=10)
    low: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=10)
    close: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=10)
    volume: Decimal | None = Field(
        default=None, ge=Decimal("0"), max_digits=30, decimal_places=10
    )

    @field_validator("datetime")
    @classmethod
    def require_timezone(cls, candle_datetime: datetime) -> datetime:
        """Wymaga strefy czasowej, aby zapisywać czas świecy jednoznacznie w UTC."""
        if candle_datetime.tzinfo is None or candle_datetime.utcoffset() is None:
            raise ValueError("datetime musi zawierać strefę czasową.")
        if candle_datetime.second != 0 or candle_datetime.microsecond != 0:
            raise ValueError("datetime świecy M1 musi zaczynać się równo w pełnej minucie.")
        return candle_datetime

    @model_validator(mode="after")
    def validate_ohlc_range(self) -> "CandleRecord":
        """Sprawdza, czy zakres świecy zawiera ceny otwarcia i zamknięcia."""
        if (
            self.high < self.open
            or self.high < self.close
            or self.low > self.open
            or self.low > self.close
            or self.low > self.high
        ):
            raise ValueError("Wartości OHLC nie tworzą prawidłowego zakresu świecy.")
        return self


class CandleBatch(BaseModel):
    """Opis paczki świec zapisywanych podczas uzupełniania historii."""

    candles: list[CandleRecord] = Field(min_length=1, max_length=MAX_BACKFILL_CANDLES)


class MarketDataRepository:
    """Zarządza produktami i pobranymi świecami."""

    def __init__(self) -> None:
        """Pobiera ustawienia połączenia z bazy ze zmiennych środowiskowych."""
        self.database_settings = {
            "host": os.environ["MYSQL_HOST"],
            "port": int(os.environ.get("MYSQL_PORT", "3306")),
            "user": os.environ["MYSQL_USER"],
            "password": os.environ["MYSQL_PASSWORD"],
            "database": os.environ["MYSQL_DATABASE"],
        }

    def initialize(self) -> None:
        """Tworzy tabele, jeśli nie istnieją; błędy połączenia przerywają start API."""
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS products (
                        symbol VARCHAR(32) PRIMARY KEY,
                        created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                    ) ENGINE=InnoDB
                    """
                )
                cursor.execute("DROP TABLE IF EXISTS price_history")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS candles (
                        symbol VARCHAR(32) NOT NULL,
                        datetime DATETIME(6) NOT NULL,
                        open DECIMAL(20, 10) NOT NULL,
                        high DECIMAL(20, 10) NOT NULL,
                        low DECIMAL(20, 10) NOT NULL,
                        close DECIMAL(20, 10) NOT NULL,
                        volume DECIMAL(30, 10) NULL,
                        utc_offset_minutes SMALLINT NOT NULL DEFAULT 32767,
                        PRIMARY KEY (symbol, datetime, utc_offset_minutes),
                        CONSTRAINT fk_candles_product
                            FOREIGN KEY (symbol) REFERENCES products(symbol)
                    ) ENGINE=InnoDB
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS signals (
                        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        `datetime` DATETIME(6) NOT NULL,
                        product VARCHAR(32) NOT NULL,
                        price DECIMAL(20, 10) NOT NULL,
                        `signal` ENUM('short', 'long') NOT NULL,
                        algorithm_name VARCHAR(255) NOT NULL,
                        ai_decision JSON NULL,
                        transaction_id BIGINT UNSIGNED NULL,
                        candles_count INT UNSIGNED NOT NULL,
                        signal_parameters JSON NOT NULL,
                        INDEX idx_signals_product_datetime (product, `datetime`),
                        INDEX idx_signals_algorithm_datetime (algorithm_name, `datetime`),
                        CONSTRAINT fk_signals_product
                            FOREIGN KEY (product) REFERENCES products(symbol)
                    ) ENGINE=InnoDB
                    """
                )
                self._migrate_candle_timestamps(cursor)
                connection.commit()
            finally:
                cursor.close()
        finally:
            connection.close()

    def _migrate_candle_timestamps(
        self, cursor: mysql.connector.cursor.MySQLCursor
    ) -> None:
        """Przekształca wcześniej zapisane znaczniki UTC na czas Europe/Warsaw."""
        cursor.execute("SHOW COLUMNS FROM candles LIKE %s", ("utc_offset_minutes",))
        offset_column = cursor.fetchone()
        if offset_column is None:
            cursor.execute(
                """
                ALTER TABLE candles
                ADD COLUMN utc_offset_minutes SMALLINT NOT NULL DEFAULT 32767
                """
            )

        cursor.execute("SHOW KEYS FROM candles WHERE Key_name = 'PRIMARY'")
        primary_key_columns = [key[4] for key in cursor.fetchall()]
        expected_primary_key = ["symbol", "datetime", "utc_offset_minutes"]
        if primary_key_columns != expected_primary_key:
            cursor.execute(
                """
                ALTER TABLE candles
                DROP PRIMARY KEY,
                ADD PRIMARY KEY (symbol, datetime, utc_offset_minutes)
                """
            )

        cursor.execute(
            """
            SELECT symbol, datetime
            FROM candles
            WHERE utc_offset_minutes = 32767
            """
        )
        for symbol, previous_utc_datetime in cursor.fetchall():
            utc_datetime = previous_utc_datetime.replace(tzinfo=timezone.utc)
            warsaw_datetime = utc_datetime.astimezone(WARSAW_TIME_ZONE)
            local_datetime = warsaw_datetime.replace(tzinfo=None)
            offset_minutes = int(warsaw_datetime.utcoffset().total_seconds() // 60)
            cursor.execute(
                """
                UPDATE candles
                SET datetime = %s, utc_offset_minutes = %s
                WHERE symbol = %s AND datetime = %s
                    AND utc_offset_minutes = 32767
                """,
                (local_datetime, offset_minutes, symbol, previous_utc_datetime),
            )

    def save_candle(
        self, candle: CandleRecord, signal: Signal | None = None
    ) -> None:
        """Zapisuje świecę lub aktualizuje jej wartości dla tego samego symbolu i czasu."""
        self.save_candles([candle], signal)

    def save_candles(
        self, candles: list[CandleRecord], signal: Signal | None = None
    ) -> None:
        """Zapisuje paczkę świec i opcjonalny sygnał w jednej transakcji."""
        candle_values = []
        product_symbols = set()
        for candle in candles:
            candle_datetime_local = candle.datetime.astimezone(WARSAW_TIME_ZONE)
            local_datetime = candle_datetime_local.replace(tzinfo=None)
            offset_minutes = int(candle_datetime_local.utcoffset().total_seconds() // 60)
            product_symbols.add(candle.symbol)
            candle_values.append(
                (
                    candle.symbol,
                    local_datetime,
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                    offset_minutes,
                )
            )

        connection = mysql.connector.connect(**self.database_settings)
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.executemany(
                """
                INSERT INTO products (symbol) VALUES (%s)
                ON DUPLICATE KEY UPDATE symbol = VALUES(symbol)
                """,
                [(symbol,) for symbol in product_symbols],
            )
            cursor.executemany(
                """
                INSERT INTO candles
                    (symbol, datetime, open, high, low, close, volume,
                     utc_offset_minutes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    open = VALUES(open),
                    high = VALUES(high),
                    low = VALUES(low),
                    close = VALUES(close),
                    volume = VALUES(volume)
                """,
                candle_values,
            )
            if signal is not None:
                self._insert_signal(cursor, signal)
            connection.commit()
        except mysql.connector.Error:
            connection.rollback()
            raise
        finally:
            if cursor is not None:
                cursor.close()
            connection.close()

    def fetch_candle_history(self, symbol: str) -> list[Candle]:
        """Pobiera świece symbolu w kolejności UTC do odtworzenia stanu strategii."""
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT datetime, utc_offset_minutes, open, high, low, close, volume
                    FROM candles
                    WHERE symbol = %s
                    ORDER BY DATE_SUB(
                        datetime, INTERVAL utc_offset_minutes MINUTE
                    ) ASC
                    """,
                    (symbol,),
                )
                rows = cursor.fetchall()
                return [
                    Candle(
                        timestamp=candle_datetime.replace(
                            tzinfo=timezone(timedelta(minutes=offset_minutes))
                        ).astimezone(timezone.utc),
                        open=float(open_price),
                        high=float(high_price),
                        low=float(low_price),
                        close=float(close_price),
                        volume=float(volume) if volume is not None else None,
                    )
                    for (
                        candle_datetime,
                        offset_minutes,
                        open_price,
                        high_price,
                        low_price,
                        close_price,
                        volume,
                    ) in rows
                ]
            finally:
                cursor.close()
        finally:
            connection.close()

    @staticmethod
    def _insert_signal(cursor: mysql.connector.cursor.MySQLCursor, signal: Signal) -> None:
        """Dodaje sygnał do bieżącej transakcji wraz z jego pakietem JSON."""
        signal_payload = signal.to_payload()
        cursor.execute(
            """
            INSERT INTO signals
                (`datetime`, product, price, `signal`, algorithm_name,
                 ai_decision, transaction_id, candles_count, signal_parameters)
            VALUES (%s, %s, %s, %s, %s, NULL, NULL, %s, %s)
            """,
            (
                datetime.now(WARSAW_TIME_ZONE).replace(tzinfo=None),
                "EUR/USD",
                Decimal(str(signal_payload["price"])),
                signal.direction.lower(),
                STRATEGY_NAME,
                signal.candles_count,
                json.dumps(
                    signal_payload, allow_nan=False, separators=(",", ":")
                ),
            ),
        )

    def fetch_missing_candle_times(
        self, symbol: str, limit: int
    ) -> tuple[bool, list[str]]:
        """Zwraca do limitu brakujących minut rynku Forex, od najnowszej wstecz."""
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT datetime, utc_offset_minutes
                    FROM candles
                    WHERE symbol = %s
                    ORDER BY datetime DESC, utc_offset_minutes DESC
                    """,
                    (symbol,),
                )
                stored_times = {
                    candle_datetime.replace(
                        tzinfo=timezone(timedelta(minutes=offset_minutes))
                    ).astimezone(timezone.utc)
                    for candle_datetime, offset_minutes in cursor.fetchall()
                }
            finally:
                cursor.close()
        finally:
            connection.close()

        if not stored_times:
            return False, []

        latest_closed_minute = (
            datetime.now(timezone.utc).replace(second=0, microsecond=0)
            - timedelta(minutes=1)
        )
        upper_bound = latest_closed_minute
        missing_times = []
        for stored_time in sorted(stored_times, reverse=True):
            if stored_time > latest_closed_minute:
                continue
            current_minute = upper_bound
            while current_minute > stored_time and len(missing_times) < limit:
                if (
                    current_minute not in stored_times
                    and self._is_forex_market_open(current_minute)
                ):
                    missing_times.append(
                        current_minute.strftime("%Y-%m-%d %H:%M:%S")
                    )
                current_minute -= timedelta(minutes=1)
            if len(missing_times) == limit:
                break
            upper_bound = stored_time - timedelta(minutes=1)

        missing_times.reverse()
        return True, missing_times

    @staticmethod
    def _is_forex_market_open(utc_time: datetime) -> bool:
        """Pomija weekendowe zamknięcie rynku Forex przy wyznaczaniu luk."""
        new_york_time = utc_time.astimezone(NEW_YORK_TIME_ZONE)
        weekday = new_york_time.weekday()
        minute_of_day = new_york_time.hour * 60 + new_york_time.minute
        friday_close = 17 * 60
        sunday_open = 17 * 60
        return (
            weekday in (0, 1, 2, 3)
            or (weekday == 4 and minute_of_day < friday_close)
            or (weekday == 6 and minute_of_day >= sunday_open)
        )

    def fetch_candle_page(
        self, page: int
    ) -> tuple[list[tuple[str, str, str, str, str, str, str | None]], bool]:
        """Pobiera 20 świec od najnowszych i sygnalizuje, czy istnieje kolejna strona."""
        page_size = 20
        offset = page * page_size
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT
                        symbol,
                        datetime,
                        CAST(open AS CHAR),
                        CAST(high AS CHAR),
                        CAST(low AS CHAR),
                        CAST(close AS CHAR),
                        CAST(volume AS CHAR)
                    FROM candles
                    ORDER BY datetime DESC, symbol ASC, utc_offset_minutes DESC
                    LIMIT %s OFFSET %s
                    """,
                    (page_size + 1, offset),
                )
                rows = cursor.fetchall()
                has_next_page = len(rows) > page_size
                page_rows = rows[:page_size]
                return [
                    (
                        symbol,
                        candle_datetime.strftime("%Y-%m-%d %H:%M:%S"),
                        open_price,
                        high_price,
                        low_price,
                        close_price,
                        volume if volume is not None else "",
                    )
                    for (
                        symbol,
                        candle_datetime,
                        open_price,
                        high_price,
                        low_price,
                        close_price,
                        volume,
                    ) in page_rows
                ], has_next_page
            finally:
                cursor.close()
        finally:
            connection.close()

    # :)


repository = MarketDataRepository()
eurusd_strategy = EURUSDStrategyV1()
strategy_lock = Lock()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Przygotowuje tabele przed obsługą żądań i kończy start przy błędzie bazy."""
    repository.initialize()
    with strategy_lock:
        eurusd_strategy.warm_up(repository.fetch_candle_history("EUR/USD"))
        app.state.strategy_ready = True
    yield


app = FastAPI(title="gie-da market data API", lifespan=lifespan)

@app.get("/health")
def health() -> dict[str, str]:
    """Zwraca prostą odpowiedź kontrolną dla lokalnego API."""
    return {"status": "ok"}


@app.post("/api/candles", status_code=204)
def save_candle(candle: CandleRecord, request: Request) -> None:
    """Waliduje i zapisuje świecę rynkową; ten sam symbol i czas aktualizują wpis."""
    try:
        with strategy_lock:
            if not request.app.state.strategy_ready:
                recovered_strategy = copy.deepcopy(
                    request.app.state.eurusd_strategy
                )
                recovered_strategy.warm_up(
                    request.app.state.repository.fetch_candle_history("EUR/USD")
                )
                request.app.state.eurusd_strategy = recovered_strategy
                request.app.state.strategy_ready = True
            current_strategy = request.app.state.eurusd_strategy
            updated_strategy = copy.deepcopy(current_strategy)
            signal = None
            if candle.symbol == "EUR/USD":
                signal = updated_strategy.process_candle(
                    Candle(
                        timestamp=candle.datetime.astimezone(timezone.utc),
                        open=float(candle.open),
                        high=float(candle.high),
                        low=float(candle.low),
                        close=float(candle.close),
                        volume=(
                            float(candle.volume)
                            if candle.volume is not None
                            else None
                        ),
                    )
                )
            request.app.state.repository.save_candle(candle, signal)
            request.app.state.eurusd_strategy = updated_strategy
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się zapisać świecy w bazie MySQL.",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail="Świeca jest nieprawidłowa lub nie zachowuje kolejności czasu.",
        ) from error


@app.post("/api/candles/batch", status_code=204)
def save_candle_batch(batch: CandleBatch, request: Request) -> None:
    """Waliduje i zapisuje maksymalnie 1000 świec w jednej transakcji."""
    try:
        with strategy_lock:
            request.app.state.strategy_ready = False
            request.app.state.repository.save_candles(batch.candles)
            updated_strategy = copy.deepcopy(request.app.state.eurusd_strategy)
            updated_strategy.warm_up(
                request.app.state.repository.fetch_candle_history("EUR/USD")
            )
            request.app.state.eurusd_strategy = updated_strategy
            request.app.state.strategy_ready = True
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się zapisać świec w bazie MySQL.",
        ) from error


@app.get("/api/candles/missing", response_class=Response)
def missing_candles(
    request: Request,
    symbol: str = Query(min_length=1, max_length=32, pattern=r"^[A-Z0-9./_-]+$"),
) -> Response:
    """Zwraca status bazy i do 1000 najnowszych brakujących minut dla symbolu."""
    try:
        has_existing_candles, missing_times = (
            request.app.state.repository.fetch_missing_candle_times(
                symbol, MAX_BACKFILL_CANDLES
            )
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się sprawdzić brakujących świec w bazie MySQL.",
        ) from error

    report_lines = ["1" if has_existing_candles else "0"]
    report_lines.extend(missing_times)
    return Response(
        content="\n".join(report_lines),
        media_type="text/plain; charset=utf-8",
    )


@app.get("/api/candles/report", response_class=Response)
def candle_report(
    request: Request,
    page: int = Query(ge=0, le=1000000),
) -> Response:
    """Zwraca stronę 20 najnowszych świec jako TSV z flagą kolejnej strony."""
    try:
        rows, has_next_page = request.app.state.repository.fetch_candle_page(page)
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się odczytać świec z bazy MySQL.",
        ) from error

    report_lines = ["1" if has_next_page else "0"]
    report_lines.extend("\t".join(row) for row in rows)
    return Response(
        content="\n".join(report_lines),
        media_type="text/tab-separated-values; charset=utf-8",
    )


app.state.repository = repository
app.state.eurusd_strategy = eurusd_strategy
app.state.strategy_ready = False
