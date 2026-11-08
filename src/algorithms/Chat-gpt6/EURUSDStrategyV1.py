"""Strategia EMA/RSI dla EUR/USD w interwale M1 z potwierdzeniem M5.

Spread jest obecnie wartością testową 3 pipsy, równą limitowi 3 pipsów.
Strategia nie otrzymuje jeszcze rzeczywistych notowań BID/ASK.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from indicators import (
    ExponentialMovingAverage,
    WilderAverageTrueRange,
    WilderRelativeStrengthIndex,
)
from market_data import Candle, FiveMinuteCandleAggregator
from signal_serializer import Signal

STRATEGY_NAME = "Chat-gpt6/EURUSDStrategyV1"
MAX_SPREAD_PIPS = 3.0
ASSUMED_SPREAD_PIPS = 3.0
MINIMUM_M1_CANDLES = 50
MINIMUM_M5_CANDLES = 50
MAX_M5_AGE = timedelta(minutes=5)


class EURUSDStrategyV1:
    """Analizuje zamknięte świece i generuje nowe sygnały LONG albo SHORT."""

    def __init__(self) -> None:
        """Tworzy wskaźniki i stan ponawiania dla jednego ciągłego segmentu danych."""
        self._m1_ema9 = ExponentialMovingAverage(9)
        self._m1_ema21 = ExponentialMovingAverage(21)
        self._m5_ema9 = ExponentialMovingAverage(9)
        self._m5_ema21 = ExponentialMovingAverage(21)
        self._rsi14 = WilderRelativeStrengthIndex(14)
        self._atr14 = WilderAverageTrueRange(14)
        self._m5_aggregator = FiveMinuteCandleAggregator()
        self._last_m1_timestamp: datetime | None = None
        self._last_m5_timestamp: datetime | None = None
        self._m1_candle_count = 0
        self._m5_candle_count = 0
        self._long_armed = True
        self._short_armed = True

    @property
    def candles_count(self) -> int:
        """Zwraca liczbę nieprzerwanych świec M1 w aktualnym segmencie danych."""
        return self._m1_candle_count

    def reset(self) -> None:
        """Resetuje wskaźniki i rearm, aby nie łączyć danych przed i po luce."""
        self._m1_ema9.reset()
        self._m1_ema21.reset()
        self._m5_ema9.reset()
        self._m5_ema21.reset()
        self._rsi14.reset()
        self._atr14.reset()
        self._m5_aggregator.reset()
        self._last_m1_timestamp = None
        self._last_m5_timestamp = None
        self._m1_candle_count = 0
        self._m5_candle_count = 0
        self._long_armed = True
        self._short_armed = True

    def warm_up(self, candles: list[Candle]) -> None:
        """Odtwarza stan wskaźników z historii bez generowania starych sygnałów."""
        self.reset()
        for candle in candles:
            self.process_candle(candle, emit_signal=False)

    def process_candle(
        self,
        candle: Candle,
        now_utc: datetime | None = None,
        emit_signal: bool = True,
    ) -> Signal | None:
        """Analizuje jedną zamkniętą M1 i zwraca nowy sygnał albo brak wyniku."""
        candle.validate()
        current_time = now_utc or datetime.now(timezone.utc)
        if current_time.tzinfo is None or current_time.utcoffset() is None:
            raise ValueError("Bieżący czas musi zawierać strefę czasową.")
        if candle.timestamp + timedelta(minutes=1) > current_time.astimezone(timezone.utc):
            return None

        if self._last_m1_timestamp is not None:
            if candle.timestamp <= self._last_m1_timestamp:
                if candle.timestamp == self._last_m1_timestamp:
                    return None
                raise ValueError("Świece muszą być przekazywane w kolejności czasu UTC.")
            if candle.timestamp - self._last_m1_timestamp != timedelta(minutes=1):
                self.reset()

        self._last_m1_timestamp = candle.timestamp

        completed_m5 = self._m5_aggregator.add(candle)
        if completed_m5 is not None:
            self._last_m5_timestamp = completed_m5.timestamp
            self._m5_ema9.update(completed_m5.close)
            self._m5_ema21.update(completed_m5.close)
            self._m5_candle_count += 1

        ema9_1m = self._m1_ema9.update(candle.close)
        ema21_1m = self._m1_ema21.update(candle.close)
        previous_rsi = self._rsi14.value
        rsi14_1m = self._rsi14.update(candle.close)
        atr14_1m = self._atr14.update(candle)
        self._m1_candle_count += 1

        if not self._indicators_ready(
            ema9_1m, ema21_1m, rsi14_1m, atr14_1m
        ):
            self._long_armed = True
            self._short_armed = True
            return None

        m5_age = (
            candle.timestamp + timedelta(minutes=1)
            - (self._last_m5_timestamp + timedelta(minutes=5))
        )
        if m5_age < timedelta(0) or m5_age > MAX_M5_AGE:
            self._long_armed = True
            self._short_armed = True
            return None

        ema9_5m = self._m5_ema9.value
        ema21_5m = self._m5_ema21.value
        if ema9_5m is None or ema21_5m is None:
            self._long_armed = True
            self._short_armed = True
            return None

        spread_is_acceptable = ASSUMED_SPREAD_PIPS <= MAX_SPREAD_PIPS
        long_setup = (
            spread_is_acceptable
            and ema9_1m > ema21_1m
            and ema9_5m > ema21_5m
            and candle.low <= ema9_1m
            and candle.close > ema9_1m
            and rsi14_1m > 50
        )
        short_setup = (
            spread_is_acceptable
            and ema9_1m < ema21_1m
            and ema9_5m < ema21_5m
            and candle.high >= ema9_1m
            and candle.close < ema9_1m
            and rsi14_1m < 50
        )
        long_cross = previous_rsi is not None and previous_rsi <= 50 < rsi14_1m
        short_cross = previous_rsi is not None and previous_rsi >= 50 > rsi14_1m

        if not long_setup:
            self._long_armed = True
        if not short_setup:
            self._short_armed = True

        direction = None
        if long_setup and long_cross and self._long_armed:
            direction = "LONG"
            self._long_armed = False
        elif short_setup and short_cross and self._short_armed:
            direction = "SHORT"
            self._short_armed = False
        if direction is None or not emit_signal:
            return None

        return Signal(
            direction=direction,
            timestamp=candle.timestamp + timedelta(minutes=1),
            price=candle.close,
            ema9_1m=ema9_1m,
            ema21_1m=ema21_1m,
            ema9_5m=ema9_5m,
            ema21_5m=ema21_5m,
            rsi14_1m=rsi14_1m,
            atr14_1m=atr14_1m,
            candles_count=self._m1_candle_count,
        )

    def _indicators_ready(
        self,
        ema9_1m: float | None,
        ema21_1m: float | None,
        rsi14_1m: float | None,
        atr14_1m: float | None,
    ) -> bool:
        """Wymaga 50 kolejnych świec każdego interwału i gotowych wskaźników."""
        return (
            self._m1_candle_count >= MINIMUM_M1_CANDLES
            and self._m5_candle_count >= MINIMUM_M5_CANDLES
            and ema9_1m is not None
            and ema21_1m is not None
            and rsi14_1m is not None
            and atr14_1m is not None
        )
