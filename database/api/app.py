"""Lokalne API, które zapisuje historię cen produktów w MySQL."""

# Backend
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import mysql.connector
from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    """Opis ceny produktu przesyłanej przez aplikację desktopową."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9./_-]+$")
    price: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=10)
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def require_timezone(cls, recorded_at: datetime) -> datetime:
        """Odrzuca czasy bez strefy, aby nie pomylić czasu lokalnego z UTC."""
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("recorded_at musi zawierać strefę czasową.")
        return recorded_at

    # :)


class PriceHistoryRepository:
    """Zarządza tabelami produktów i zapisami ich historycznych cen."""

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
                        zlote_rady VARCHAR(64) NULL,
                        created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                        INDEX idx_price_history_symbol_recorded_at (symbol, recorded_at),
                        CONSTRAINT fk_price_history_product
                            FOREIGN KEY (symbol) REFERENCES products(symbol)
                    ) ENGINE=InnoDB
                    """
                )
                self._fill_random_nouns(cursor)
                connection.commit()
            finally:
                cursor.close()
        finally:
            connection.close()

    def _fill_random_nouns(self, cursor: mysql.connector.cursor.MySQLCursor) -> None:
        """Dodaje kolumnę i losowe rzeczowniki do wierszy, które jeszcze ich nie mają."""
        cursor.execute("SHOW COLUMNS FROM price_history LIKE %s", ("zlote_rady",))
        column = cursor.fetchone()
        if column is None:
            cursor.execute(
                "ALTER TABLE price_history ADD COLUMN zlote_rady VARCHAR(64) NULL"
            )
            column_is_nullable = True
        else:
            column_is_nullable = column[2] == "YES"

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

        if column_is_nullable:
            cursor.execute(
                "ALTER TABLE price_history MODIFY COLUMN zlote_rady VARCHAR(64) NOT NULL"
            )

    def save(self, record: PriceRecord) -> int:
        """Zapisuje produkt, cenę i losowy rzeczownik; zwraca identyfikator wpisu."""
        recorded_at_local = (
            record.recorded_at.astimezone(WARSAW_TIME_ZONE).replace(tzinfo=None)
        )
        golden_advice = secrets.choice(RANDOM_NOUNS)
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
                (record.symbol, record.price, recorded_at_local, golden_advice),
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

    # :)


repository = PriceHistoryRepository()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Przygotowuje tabele przed obsługą żądań i kończy start przy błędzie bazy."""
    repository.initialize()
    yield


app = FastAPI(title="gie-da price history API", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    """Zwraca prostą odpowiedź kontrolną dla lokalnego API."""
    return {"status": "ok"}


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
