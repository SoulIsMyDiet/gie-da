"""Modele świec M1 i agregacja kompletnych świec M5."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite


@dataclass(frozen=True)
class Candle:
    """Przechowuje jedną zamkniętą świecę OHLCV ze znacznikiem początku UTC."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None

    def validate(self) -> None:
        """Sprawdza strefę czasu i spójność zakresu cen świecy."""
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("Znacznik czasu świecy musi zawierać strefę czasową.")
        if self.timestamp.utcoffset() != timedelta(0):
            raise ValueError("Strategia wymaga znaczników czasu zapisanych w UTC.")
        if self.timestamp.second != 0 or self.timestamp.microsecond != 0:
            raise ValueError("Świeca M1 musi zaczynać się równo w pełnej minucie.")
        price_values = (self.open, self.high, self.low, self.close)
        if (
            not all(isfinite(value) for value in price_values)
            or (self.volume is not None and not isfinite(self.volume))
            or self.open <= 0
            or self.high <= 0
            or self.low <= 0
            or self.close <= 0
            or self.high < max(self.open, self.close)
            or self.low > min(self.open, self.close)
            or self.low > self.high
            or (self.volume is not None and self.volume < 0)
        ):
            raise ValueError("Świeca zawiera nieprawidłowe wartości OHLCV.")


class FiveMinuteCandleAggregator:
    """Tworzy M5 tylko z pięciu kolejnych i kompletnych świec M1."""

    def __init__(self) -> None:
        """Przygotowuje pusty bufor dla bieżącego pięciominutowego przedziału."""
        self._bucket_start: datetime | None = None
        self._candles: list[Candle] = []

    def reset(self) -> None:
        """Usuwa niekompletną grupę M1 po wykryciu luki w danych."""
        self._bucket_start = None
        self._candles.clear()

    def add(self, candle: Candle) -> Candle | None:
        """Dodaje zamkniętą M1 i zwraca M5 po zebraniu kompletnych pięciu minut."""
        candle.validate()
        bucket_minute = candle.timestamp.minute - candle.timestamp.minute % 5
        bucket_start = candle.timestamp.replace(
            minute=bucket_minute, second=0, microsecond=0
        )
        if self._bucket_start != bucket_start:
            self._bucket_start = bucket_start
            self._candles = []

        expected_timestamp = bucket_start + timedelta(minutes=len(self._candles))
        if candle.timestamp != expected_timestamp:
            self._candles = []
            if candle.timestamp == bucket_start:
                self._candles.append(candle)
            return None

        self._candles.append(candle)
        if len(self._candles) != 5:
            return None

        volumes = [item.volume for item in self._candles]
        total_volume = (
            sum(volume for volume in volumes if volume is not None)
            if all(volume is not None for volume in volumes)
            else None
        )
        return Candle(
            timestamp=bucket_start.replace(tzinfo=timezone.utc),
            open=self._candles[0].open,
            high=max(item.high for item in self._candles),
            low=min(item.low for item in self._candles),
            close=self._candles[-1].close,
            volume=total_volume,
        )
