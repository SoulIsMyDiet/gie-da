"""Czyste obliczenia wskaźników dla strategii Grok-45/USDMXN."""

from __future__ import annotations

from math import isfinite, sqrt
from statistics import fmean
from typing import Sequence

from .config import (
    ATR_PERIOD,
    BB_PERIOD,
    BB_STANDARD_DEVIATIONS,
    EMA_FAST_PERIOD,
    EMA_SLOW_PERIOD,
    MOMENTUM_LOOKBACK,
    RSI_PERIOD,
)

CandleValues = dict[str, object]


def ema_series(values: Sequence[float], period: int) -> list[float]:
    """Oblicza serię EMA, inicjując ją pierwszą dostępną ceną zamknięcia."""
    if period < 1:
        raise ValueError("Okres EMA musi być dodatni.")
    if not values:
        return []

    multiplier = 2.0 / (period + 1)
    result = [float(values[0])]
    for value in values[1:]:
        previous = result[-1]
        result.append(previous + multiplier * (float(value) - previous))
    return result


def rsi_series(values: Sequence[float], period: int) -> list[float | None]:
    """Oblicza RSI metodą Wildera; pozycje przed rozgrzewką mają wartość None."""
    if period < 1:
        raise ValueError("Okres RSI musi być dodatni.")
    result: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return result

    changes = [
        float(values[index]) - float(values[index - 1])
        for index in range(1, len(values))
    ]
    average_gain = sum(max(change, 0.0) for change in changes[:period]) / period
    average_loss = sum(max(-change, 0.0) for change in changes[:period]) / period
    result[period] = _rsi_value(average_gain, average_loss)

    for index in range(period + 1, len(values)):
        change = changes[index - 1]
        average_gain = (
            average_gain * (period - 1) + max(change, 0.0)
        ) / period
        average_loss = (
            average_loss * (period - 1) + max(-change, 0.0)
        ) / period
        result[index] = _rsi_value(average_gain, average_loss)
    return result


def _rsi_value(average_gain: float, average_loss: float) -> float:
    """Oblicza RSI i bezpiecznie obsługuje serie bez wzrostów lub spadków."""
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    if average_gain == 0:
        return 0.0
    relative_strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def atr_series(
    candles: Sequence[CandleValues], period: int
) -> list[float | None]:
    """Oblicza ATR metodą Wildera na kolejnych dostępnych świecach."""
    if period < 1:
        raise ValueError("Okres ATR musi być dodatni.")
    result: list[float | None] = [None] * len(candles)
    if len(candles) < period:
        return result

    true_ranges = []
    for index, candle in enumerate(candles):
        high = float(candle["high"])
        low = float(candle["low"])
        if index == 0:
            true_range = high - low
        else:
            previous_close = float(candles[index - 1]["close"])
            true_range = max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        true_ranges.append(true_range)

    average_true_range = sum(true_ranges[:period]) / period
    result[period - 1] = average_true_range
    for index in range(period, len(candles)):
        average_true_range = (
            average_true_range * (period - 1) + true_ranges[index]
        ) / period
        result[index] = average_true_range
    return result


def bollinger_series(
    values: Sequence[float],
    period: int,
    standard_deviations: float,
) -> tuple[
    list[float | None],
    list[float | None],
    list[float | None],
    list[float | None],
]:
    """Zwraca środkowe, górne i dolne pasmo oraz ich szerokość procentową."""
    if period < 1:
        raise ValueError("Okres pasm Bollingera musi być dodatni.")
    if standard_deviations < 0:
        raise ValueError("Odchylenie standardowe nie może być ujemne.")

    middle: list[float | None] = [None] * len(values)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    width: list[float | None] = [None] * len(values)
    for index in range(period - 1, len(values)):
        window = [
            float(value)
            for value in values[index - period + 1 : index + 1]
        ]
        average = fmean(window)
        deviation = sqrt(fmean((value - average) ** 2 for value in window))
        band_upper = average + standard_deviations * deviation
        band_lower = average - standard_deviations * deviation
        middle[index] = average
        upper[index] = band_upper
        lower[index] = band_lower
        width[index] = (
            (band_upper - band_lower) / average * 100.0
            if average != 0
            else None
        )
    return middle, upper, lower, width


def momentum_series(values: Sequence[float], lookback: int) -> list[float | None]:
    """Oblicza momentum procentowe względem Close sprzed wskazanej liczby świec."""
    if lookback < 1:
        raise ValueError("Okres momentum musi być dodatni.")
    result: list[float | None] = [None] * len(values)
    for index in range(lookback, len(values)):
        previous_close = float(values[index - lookback])
        if previous_close != 0:
            result[index] = (
                (float(values[index]) - previous_close) / previous_close * 100.0
            )
    return result


def percentile(values: Sequence[float], percentile_value: float) -> float | None:
    """Oblicza percentyl metodą interpolacji liniowej."""
    if not 0 <= percentile_value <= 100:
        raise ValueError("Percentyl musi należeć do przedziału 0–100.")
    sorted_values = sorted(
        float(value) for value in values if isfinite(float(value))
    )
    if not sorted_values:
        return None

    position = (len(sorted_values) - 1) * percentile_value / 100.0
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    fraction = position - lower_index
    return sorted_values[lower_index] + (
        sorted_values[upper_index] - sorted_values[lower_index]
    ) * fraction


def calculate_indicator_series(
    candles: Sequence[CandleValues],
) -> dict[str, list[float | None]]:
    """Oblicza wszystkie serie wskaźników potrzebne do oceny strategii."""
    closes = [float(candle["close"]) for candle in candles]
    bb_middle, bb_upper, bb_lower, bb_width = bollinger_series(
        closes, BB_PERIOD, BB_STANDARD_DEVIATIONS
    )
    return {
        "ema_fast": ema_series(closes, EMA_FAST_PERIOD),
        "ema_slow": ema_series(closes, EMA_SLOW_PERIOD),
        "rsi": rsi_series(closes, RSI_PERIOD),
        "bb_middle": bb_middle,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "bb_width": bb_width,
        "atr": atr_series(candles, ATR_PERIOD),
        "momentum_5": momentum_series(closes, MOMENTUM_LOOKBACK),
    }
