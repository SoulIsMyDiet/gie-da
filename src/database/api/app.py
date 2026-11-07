"""Lokalne API, które zapisuje historię cen i świec rynkowych w MySQL."""

# Backend
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import mysql.connector
from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WARSAW_TIME_ZONE = ZoneInfo("Europe/Warsaw")
RANDOM_NOUNS = (
    "kot",
    "kompas",
    "latarnia",
    "ogrod",
    "chmura",
    "zegarek",
    "most",
    "ksiazka",
    "rower",
    "kaktus",
    "peron",
    "lusterko",
    "wiatrak",
    "szuflada",
    "statek",
    "jabłko",
    "żaba",
    "źródło",
    "pociąg",
    "księżyc",
)


class PriceRecord(BaseModel):
    """Opis ceny i sentencji przesyłanych przez aplikację desktopową."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9./_-]+$")
    price: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=10)
    recorded_at: datetime
    golden_advice: str = Field(min_length=1, max_length=512)

    @field_validator("recorded_at")
    @classmethod
    def require_timezone(cls, recorded_at: datetime) -> datetime:
        """Odrzuca czasy bez strefy, aby nie pomylić czasu lokalnego z UTC."""
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("recorded_at musi zawierać strefę czasową.")
        return recorded_at

    @field_validator("golden_advice")
    @classmethod
    def require_nonempty_advice(cls, golden_advice: str) -> str:
        """Odrzuca sentencje składające się wyłącznie z białych znaków."""
        if not golden_advice.strip():
            raise ValueError("golden_advice nie może być puste.")
        return golden_advice.strip()

    # :)


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


class MarketDataRepository:
    """Zarządza produktami, historią cen oraz pobranymi świecami."""

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
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS price_history (
                        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        symbol VARCHAR(32) NOT NULL,
                        price DECIMAL(20, 10) NOT NULL,
                        recorded_at DATETIME(6) NOT NULL,
                        zlote_rady VARCHAR(512) NULL,
                        created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                        INDEX idx_price_history_symbol_recorded_at (symbol, recorded_at),
                        CONSTRAINT fk_price_history_product
                            FOREIGN KEY (symbol) REFERENCES products(symbol)
                    ) ENGINE=InnoDB
                    """
                )
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
                self._migrate_candle_timestamps(cursor)
                self._migrate_golden_advice(cursor)
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

    def _migrate_golden_advice(self, cursor: mysql.connector.cursor.MySQLCursor) -> None:
        """Poszerza kolumnę i uzupełnia brakujące sentencje w starszych wpisach."""
        cursor.execute("SHOW COLUMNS FROM price_history LIKE %s", ("zlote_rady",))
        column = cursor.fetchone()
        needs_column_update = (
            column is None
            or column[1].lower() != "varchar(512)"
            or column[2] == "YES"
        )
        if column is None:
            cursor.execute(
                "ALTER TABLE price_history ADD COLUMN zlote_rady VARCHAR(512) NULL"
            )

        cursor.execute("SELECT id FROM price_history WHERE zlote_rady IS NULL")
        for (record_id,) in cursor.fetchall():
            cursor.execute(
                """
                UPDATE price_history
                SET zlote_rady = %s
                WHERE id = %s AND zlote_rady IS NULL
                """,
                (secrets.choice(RANDOM_NOUNS), record_id),
            )

        if needs_column_update:
            cursor.execute(
                "ALTER TABLE price_history MODIFY COLUMN zlote_rady VARCHAR(512) NOT NULL"
            )

    def save(self, record: PriceRecord) -> int:
        """Zapisuje produkt, cenę i sentencję; zwraca identyfikator wpisu."""
        recorded_at_local = (
            record.recorded_at.astimezone(WARSAW_TIME_ZONE).replace(tzinfo=None)
        )
        connection = mysql.connector.connect(**self.database_settings)
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO products (symbol) VALUES (%s)
                ON DUPLICATE KEY UPDATE symbol = %s
                """,
                (record.symbol, record.symbol),
            )
            cursor.execute(
                """
                INSERT INTO price_history (symbol, price, recorded_at, zlote_rady)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    record.symbol,
                    record.price,
                    recorded_at_local,
                    record.golden_advice,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)
        except mysql.connector.Error:
            connection.rollback()
            raise
        finally:
            if cursor is not None:
                cursor.close()
            connection.close()

    def fetch_recent_before(self, cutoff: datetime) -> list[tuple[int, str, str, str, str]]:
        """Pobiera do 20 najnowszych wpisów sprzed wskazanego czasu lokalnego."""
        cutoff_local = cutoff.astimezone(WARSAW_TIME_ZONE).replace(tzinfo=None)
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT
                        id,
                        symbol,
                        CAST(price AS CHAR),
                        recorded_at,
                        zlote_rady
                    FROM price_history
                    WHERE recorded_at < %s
                    ORDER BY recorded_at DESC, id DESC
                    LIMIT 20
                    """,
                    (cutoff_local,),
                )
                rows = cursor.fetchall()
                return [
                    (
                        record_id,
                        symbol,
                        price,
                        recorded_at.strftime("%Y-%m-%d %H:%M:%S"),
                        golden_advice,
                    )
                    for record_id, symbol, price, recorded_at, golden_advice in rows
                ]
            finally:
                cursor.close()
        finally:
            connection.close()

    def save_candle(self, candle: CandleRecord) -> None:
        """Zapisuje świecę lub aktualizuje jej wartości dla tego samego symbolu i czasu."""
        candle_datetime_local = candle.datetime.astimezone(WARSAW_TIME_ZONE)
        local_datetime = candle_datetime_local.replace(tzinfo=None)
        offset_minutes = int(candle_datetime_local.utcoffset().total_seconds() // 60)
        connection = mysql.connector.connect(**self.database_settings)
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO products (symbol) VALUES (%s)
                ON DUPLICATE KEY UPDATE symbol = %s
                """,
                (candle.symbol, candle.symbol),
            )
            cursor.execute(
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
                (
                    candle.symbol,
                    local_datetime,
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                    offset_minutes,
                ),
            )
            connection.commit()
        except mysql.connector.Error:
            connection.rollback()
            raise
        finally:
            if cursor is not None:
                cursor.close()
            connection.close()

    # :)


repository = MarketDataRepository()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Przygotowuje tabele przed obsługą żądań i kończy start przy błędzie bazy."""
    repository.initialize()
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
        request.app.state.repository.save_candle(candle)
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się zapisać świecy w bazie MySQL.",
        ) from error


@app.post("/api/prices", status_code=201)
def save_price(
    record: PriceRecord,
    request: Request,
) -> dict[str, int]:
    """Waliduje i zapisuje pojedynczy odczyt ceny produktu."""
    try:
        record_id = request.app.state.repository.save(record)
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się zapisać ceny w bazie MySQL.",
        ) from error
    return {"id": record_id}


@app.get("/api/prices/report", response_class=Response)
def price_report(
    request: Request,
    before_utc_us: int = Query(ge=0, le=253402300799999999),
) -> Response:
    """Zwraca do 20 wpisów sprzed chwili kliknięcia w formacie TSV dla aplikacji."""
    cutoff_utc = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
        microseconds=before_utc_us
    )
    try:
        rows = request.app.state.repository.fetch_recent_before(cutoff_utc)
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się odczytać raportu z bazy MySQL.",
        ) from error

    report_lines = [
        "\t".join((str(record_id), recorded_at, symbol, price, golden_advice))
        for record_id, symbol, price, recorded_at, golden_advice in rows
    ]
    return Response(
        content="\n".join(report_lines),
        media_type="text/tab-separated-values; charset=utf-8",
    )


app.state.repository = repository
