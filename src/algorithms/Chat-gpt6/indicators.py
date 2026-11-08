"""Wskaźniki techniczne używane przez strategię EUR/USD."""

from __future__ import annotations

from dataclasses import dataclass

from market_data import Candle


@dataclass
class ExponentialMovingAverage:
    """Oblicza EMA z ziarnem będącym średnią arytmetyczną pierwszego okresu."""

    period: int
    value: float | None = None
    _seed_total: float = 0.0
    _seed_count: int = 0

    def update(self, price: float) -> float | None:
        """Dodaje cenę i zwraca EMA, gdy dostępne są dane rozgrzewkowe."""
        if self.period < 1:
            raise ValueError("Okres EMA musi być dodatni.")
        if self.value is None:
            self._seed_total += price
            self._seed_count += 1
            if self._seed_count == self.period:
                self.value = self._seed_total / self.period
            return self.value

        smoothing_factor = 2.0 / (self.period + 1)
        self.value += smoothing_factor * (price - self.value)
        return self.value

    def reset(self) -> None:
        """Czyści stan wskaźnika przed rozpoczęciem nowego ciągłego segmentu."""
        self.value = None
        self._seed_total = 0.0
        self._seed_count = 0


@dataclass
class WilderRelativeStrengthIndex:
    """Oblicza RSI metodą wygładzania Wildera na podstawie cen zamknięcia."""

    period: int = 14
    value: float | None = None
    previous_close: float | None = None
    previous_value: float | None = None
    _gain_total: float = 0.0
    _loss_total: float = 0.0
    _change_count: int = 0
    _average_gain: float | None = None
    _average_loss: float | None = None

    def update(self, close: float) -> float | None:
        """Dodaje cenę zamknięcia i zwraca najnowszą wartość RSI."""
        if self.period < 1:
            raise ValueError("Okres RSI musi być dodatni.")
        if self.previous_close is None:
            self.previous_close = close
            return None

        change = close - self.previous_close
        self.previous_close = close
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        self.previous_value = self.value

        if self._average_gain is None or self._average_loss is None:
            self._gain_total += gain
            self._loss_total += loss
            self._change_count += 1
            if self._change_count < self.period:
                return None
            self._average_gain = self._gain_total / self.period
            self._average_loss = self._loss_total / self.period
        else:
            self._average_gain = (
                (self._average_gain * (self.period - 1) + gain) / self.period
            )
            self._average_loss = (
                (self._average_loss * (self.period - 1) + loss) / self.period
            )

        if self._average_gain == 0 and self._average_loss == 0:
            self.value = 50.0
        elif self._average_loss == 0:
            self.value = 100.0
        elif self._average_gain == 0:
            self.value = 0.0
        else:
            relative_strength = self._average_gain / self._average_loss
            self.value = 100.0 - 100.0 / (1.0 + relative_strength)
        return self.value

    def reset(self) -> None:
        """Czyści stan RSI przed rozpoczęciem nowego ciągłego segmentu."""
        self.value = None
        self.previous_close = None
        self.previous_value = None
        self._gain_total = 0.0
        self._loss_total = 0.0
        self._change_count = 0
        self._average_gain = None
        self._average_loss = None


@dataclass
class WilderAverageTrueRange:
    """Oblicza ATR metodą Wildera z zakresu świecy i poprzedniego zamknięcia."""

    period: int = 14
    value: float | None = None
    previous_close: float | None = None
    _true_range_total: float = 0.0
    _true_range_count: int = 0

    def update(self, candle: Candle) -> float | None:
        """Dodaje świecę i zwraca ATR po zgromadzeniu pełnego okresu."""
        if self.period < 1:
            raise ValueError("Okres ATR musi być dodatni.")
        if self.previous_close is None:
            true_range = candle.high - candle.low
        else:
            true_range = max(
                candle.high - candle.low,
                abs(candle.high - self.previous_close),
                abs(candle.low - self.previous_close),
            )
        self.previous_close = candle.close

        if self.value is None:
            self._true_range_total += true_range
            self._true_range_count += 1
            if self._true_range_count == self.period:
                self.value = self._true_range_total / self.period
            return self.value

        self.value = (
            self.value * (self.period - 1) + true_range
        ) / self.period
        return self.value

    def reset(self) -> None:
        """Czyści stan ATR przed rozpoczęciem nowego ciągłego segmentu."""
        self.value = None
        self.previous_close = None
        self._true_range_total = 0.0
        self._true_range_count = 0
