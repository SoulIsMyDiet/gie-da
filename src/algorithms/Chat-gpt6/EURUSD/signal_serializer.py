"""Serializuje sygnały strategii do wymaganego formatu JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite


@dataclass(frozen=True)
class Signal:
    """Przechowuje wynik strategii i wartości wskaźników dla pojedynczej świecy."""

    direction: str
    timestamp: datetime
    price: float
    ema9_1m: float
    ema21_1m: float
    ema9_5m: float
    ema21_5m: float
    rsi14_1m: float
    atr14_1m: float
    candles_count: int

    def __post_init__(self) -> None:
        """Sprawdza kierunek, czas i wartości przed utworzeniem pakietu sygnału."""
        if self.direction not in ("LONG", "SHORT"):
            raise ValueError("Kierunek sygnału musi mieć wartość LONG albo SHORT.")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("Czas sygnału musi zawierać strefę czasową.")
        if not all(
            isfinite(value)
            for value in (
                self.price,
                self.ema9_1m,
                self.ema21_1m,
                self.ema9_5m,
                self.ema21_5m,
                self.rsi14_1m,
                self.atr14_1m,
            )
        ):
            raise ValueError("Wartości sygnału muszą być liczbami skończonymi.")
        if self.candles_count < 1:
            raise ValueError("Liczba świec użytych do sygnału musi być dodatnia.")

    def to_payload(self) -> dict[str, str | float | None]:
        """Zwraca dokładnie pola kontraktu JSON bez metadanych dodatkowych."""
        utc_timestamp = self.timestamp.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        return {
            "signal": self.direction,
            "instrument": "EURUSD",
            "timestamp": utc_timestamp,
            "price": round(self.price, 10),
            "ema9_1m": round(self.ema9_1m, 10),
            "ema21_1m": round(self.ema21_1m, 10),
            "ema9_5m": round(self.ema9_5m, 10),
            "ema21_5m": round(self.ema21_5m, 10),
            "rsi14_1m": round(self.rsi14_1m, 6),
            "atr14_1m": round(self.atr14_1m, 10),
            "vwap": None,
        }

    def to_json(self) -> str:
        """Zwraca sygnał jako poprawny JSON, odrzucając wartości niefinitywne."""
        return json.dumps(self.to_payload(), allow_nan=False, separators=(",", ":"))
