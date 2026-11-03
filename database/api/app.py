"""Lokalne API, które zapisuje historię cen produktów w MySQL."""

# Backend
import os
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import mysql.connector
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

WARSAW_TIME_ZONE = ZoneInfo("Europe/Warsaw")


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
                        created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                        INDEX idx_price_history_symbol_recorded_at (symbol, recorded_at),
                        CONSTRAINT fk_price_history_product
                            FOREIGN KEY (symbol) REFERENCES products(symbol)
                    ) ENGINE=InnoDB
                    """
                )
                connection.commit()
            finally:
                cursor.close()
        finally:
            connection.close()

    def save(self, record: PriceRecord) -> int:
        """Zapisuje produkt i jego cenę; zwraca identyfikator nowego wpisu."""
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
                INSERT INTO price_history (symbol, price, recorded_at)
                VALUES (%s, %s, %s)
                """,
                (record.symbol, record.price, recorded_at_local),
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


app.state.repository = repository
