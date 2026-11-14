"""Wskaźniki techniczne obliczane wyłącznie z rzeczywistych świec."""

from __future__ import annotations

from math import isfinite
from statistics import fmean
from typing import Any, Sequence

from .config import (
    ADX_PERIOD,
    ATR_PERIOD,
    BREAKOUT_LOOKBACK,
    EMA_FAST_PERIOD,
    EMA_MIDDLE_PERIOD,
    EMA_SLOW_PERIOD,
    RSI_PERIOD,
)

Candle = dict[str, Any]
IndicatorSeries = list[float | None]


def _ema(values: Sequence[float], period: int) -> IndicatorSeries:
    """Oblicza EMA z alpha=2/(period+1), inicjalizując pierwszą ceną."""
    result: IndicatorSeries = [None] * len(values)
    if period < 1 or not values:
        return result
    alpha = 2.0 / (period + 1)
    current = float(values[0])
    result[0] = current
    for index in range(1, len(values)):
        current += alpha * (float(values[index]) - current)
        result[index] = current
    return result


def _rsi(values: Sequence[float], period: int) -> IndicatorSeries:
    """Oblicza RSI metodą Wildera po początkowej średniej z pełnego okresu."""
    result: IndicatorSeries = [None] * len(values)
    if period < 1 or len(values) <= period:
        return result

    changes = [
        float(values[index]) - float(values[index - 1])
        for index in range(1, len(values))
    ]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = fmean(gains[:period])
    average_loss = fmean(losses[:period])

    def rsi_value(gain: float, loss: float) -> float:
        """Przelicza wygładzone wzrosty i spadki na wartość RSI."""
        if loss == 0:
            return 50.0 if gain == 0 else 100.0
        if gain == 0:
            return 0.0
        relative_strength = gain / loss
        return 100.0 - 100.0 / (1.0 + relative_strength)

    result[period] = rsi_value(average_gain, average_loss)
    for change_index in range(period, len(changes)):
        average_gain = (
            average_gain * (period - 1) + gains[change_index]
        ) / period
        average_loss = (
            average_loss * (period - 1) + losses[change_index]
        ) / period
        result[change_index + 1] = rsi_value(average_gain, average_loss)
    return result


def _true_ranges(candles: Sequence[Candle]) -> list[float]:
    """Oblicza TR z poprzednim close; pierwsza świeca używa high-low."""
    true_ranges: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        if previous_close is None:
            true_ranges.append(high - low)
        else:
            true_ranges.append(
                max(
                    high - low,
                    abs(high - previous_close),
                    abs(low - previous_close),
                )
            )
        previous_close = close
    return true_ranges


def _atr(candles: Sequence[Candle], period: int) -> IndicatorSeries:
    """Oblicza ATR Wildera, nie podstawiając zera przed rozgrzewką."""
    ranges = _true_ranges(candles)
    result: IndicatorSeries = [None] * len(ranges)
    if period < 1 or len(ranges) < period:
        return result

    current = fmean(ranges[:period])
    result[period - 1] = current
    for index in range(period, len(ranges)):
        current = (current * (period - 1) + ranges[index]) / period
        result[index] = current
    return result


def _directional_movement(
    candles: Sequence[Candle], period: int
) -> tuple[IndicatorSeries, IndicatorSeries, IndicatorSeries]:
    """Oblicza +DI, -DI i ADX standardowym wygładzaniem Wildera."""
    count = len(candles)
    plus_di: IndicatorSeries = [None] * count
    minus_di: IndicatorSeries = [None] * count
    adx: IndicatorSeries = [None] * count
    if period < 1 or count <= period:
        return plus_di, minus_di, adx

    ranges = _true_ranges(candles)
    positive_moves = [0.0] * count
    negative_moves = [0.0] * count
    for index in range(1, count):
        upward_move = float(candles[index]["high"]) - float(candles[index - 1]["high"])
        downward_move = float(candles[index - 1]["low"]) - float(candles[index]["low"])
        if upward_move > downward_move and upward_move > 0:
            positive_moves[index] = upward_move
        elif downward_move > upward_move and downward_move > 0:
            negative_moves[index] = downward_move

    smoothed_range = sum(ranges[1 : period + 1])
    smoothed_positive = sum(positive_moves[1 : period + 1])
    smoothed_negative = sum(negative_moves[1 : period + 1])
    dx_values: IndicatorSeries = [None] * count

    for index in range(period, count):
        if index > period:
            smoothed_range += ranges[index] - smoothed_range / period
            smoothed_positive += (
                positive_moves[index] - smoothed_positive / period
            )
            smoothed_negative += (
                negative_moves[index] - smoothed_negative / period
            )

        if smoothed_range <= 0:
            positive = 0.0
            negative = 0.0
        else:
            positive = 100.0 * smoothed_positive / smoothed_range
            negative = 100.0 * smoothed_negative / smoothed_range
        plus_di[index] = positive
        minus_di[index] = negative
        di_sum = positive + negative
        dx_values[index] = (
            100.0 * abs(positive - negative) / di_sum if di_sum > 0 else 0.0
        )

        first_adx_index = period * 2 - 1
        if index == first_adx_index:
            initial_dx = [
                value
                for value in dx_values[period : first_adx_index + 1]
                if value is not None
            ]
            if len(initial_dx) == period:
                adx[index] = fmean(initial_dx)
        elif index > first_adx_index and adx[index - 1] is not None:
            adx[index] = (
                adx[index - 1] * (period - 1) + float(dx_values[index])
            ) / period
    return plus_di, minus_di, adx


def _breakout_levels(
    candles: Sequence[Candle], lookback: int
) -> tuple[IndicatorSeries, IndicatorSeries]:
    """Wyznacza poziomy z poprzednich świec, bez świecy aktualnie ocenianej."""
    highs: IndicatorSeries = [None] * len(candles)
    lows: IndicatorSeries = [None] * len(candles)
    if lookback < 1:
        return highs, lows
    for index in range(lookback, len(candles)):
        previous_candles = candles[index - lookback : index]
        highs[index] = max(float(item["high"]) for item in previous_candles)
        lows[index] = min(float(item["low"]) for item in previous_candles)
    return highs, lows


def calculate_indicator_series(
    candles: Sequence[Candle],
) -> dict[str, IndicatorSeries]:
    """Oblicza serie wszystkich wskaźników dla jednej ciągłej historii."""
    closes = [float(candle["close"]) for candle in candles]
    ema9 = _ema(closes, EMA_FAST_PERIOD)
    ema21 = _ema(closes, EMA_MIDDLE_PERIOD)
    ema50 = _ema(closes, EMA_SLOW_PERIOD)
    rsi14 = _rsi(closes, RSI_PERIOD)
    atr14 = _atr(candles, ATR_PERIOD)
    plus_di14, minus_di14, adx14 = _directional_movement(candles, ADX_PERIOD)
    breakout_high, breakout_low = _breakout_levels(candles, BREAKOUT_LOOKBACK)
    return {
        "ema9": ema9,
        "ema21": ema21,
        "ema50": ema50,
        "rsi14": rsi14,
        "atr14": atr14,
        "plus_di14": plus_di14,
        "minus_di14": minus_di14,
        "adx14": adx14,
        "breakout_high_10": breakout_high,
        "breakout_low_10": breakout_low,
    }


def finite_number(value: object) -> float | None:
    """Zwraca skończoną liczbę zmiennoprzecinkową lub None."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None
