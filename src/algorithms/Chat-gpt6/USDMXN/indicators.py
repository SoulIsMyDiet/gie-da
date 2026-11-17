"""Obliczenia wskaźników z zamkniętych, chronologicznych świec OHLC."""

from __future__ import annotations

from datetime import timedelta
from math import isfinite
from statistics import fmean
from typing import Any, Sequence

from .config import (
    ADX_M5_PERIOD,
    ATR_M1_PERIOD,
    EMA_FAST_M1_PERIOD,
    EMA_FAST_M5_PERIOD,
    EMA_SLOW_M1_PERIOD,
    EMA_SLOW_M5_PERIOD,
    RSI_M1_PERIOD,
)

Candle = dict[str, Any]
IndicatorSeries = list[float | None]


def _ema(closes: Sequence[float], period: int) -> IndicatorSeries:
    """Oblicza EMA od pierwszej ceny zamknięcia; zgłasza ValueError dla złego okresu."""
    if period < 1:
        raise ValueError("Okres EMA musi być dodatni.")
    values: IndicatorSeries = [None] * len(closes)
    if not closes:
        return values
    alpha = 2.0 / (period + 1)
    current = float(closes[0])
    values[0] = current
    for index, close in enumerate(closes[1:], start=1):
        current += alpha * (float(close) - current)
        values[index] = current
    return values


def _rsi(closes: Sequence[float], period: int) -> IndicatorSeries:
    """Oblicza RSI metodą Wildera po pierwszych pełnych zmianach ceny."""
    values: IndicatorSeries = [None] * len(closes)
    if period < 1:
        raise ValueError("Okres RSI musi być dodatni.")
    if len(closes) <= period:
        return values

    changes = [
        float(closes[index]) - float(closes[index - 1])
        for index in range(1, len(closes))
    ]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = fmean(gains[:period])
    average_loss = fmean(losses[:period])

    def rsi_value(gain: float, loss: float) -> float:
        """Zamienia średnie wzrosty i spadki na wartość z przedziału 0–100."""
        if gain == 0 and loss == 0:
            return 50.0
        if loss == 0:
            return 100.0
        if gain == 0:
            return 0.0
        relative_strength = gain / loss
        return 100.0 - 100.0 / (1.0 + relative_strength)

    values[period] = rsi_value(average_gain, average_loss)
    for change_index in range(period, len(changes)):
        average_gain = (
            average_gain * (period - 1) + gains[change_index]
        ) / period
        average_loss = (
            average_loss * (period - 1) + losses[change_index]
        ) / period
        values[change_index + 1] = rsi_value(average_gain, average_loss)
    return values


def _true_ranges(candles: Sequence[Candle]) -> list[float]:
    """Oblicza True Range, używając poprzedniego zamknięcia od drugiej świecy."""
    ranges: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        high, low, close = (
            float(candle["high"]),
            float(candle["low"]),
            float(candle["close"]),
        )
        ranges.append(
            high - low
            if previous_close is None
            else max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )
        previous_close = close
    return ranges


def _atr(candles: Sequence[Candle], period: int) -> IndicatorSeries:
    """Oblicza ATR metodą Wildera, pozostawiając warm-up jako None."""
    if period < 1:
        raise ValueError("Okres ATR musi być dodatni.")
    ranges = _true_ranges(candles)
    values: IndicatorSeries = [None] * len(ranges)
    if len(ranges) < period:
        return values
    current = fmean(ranges[:period])
    values[period - 1] = current
    for index in range(period, len(ranges)):
        current = (current * (period - 1) + ranges[index]) / period
        values[index] = current
    return values


def _adx(candles: Sequence[Candle], period: int) -> IndicatorSeries:
    """Oblicza ADX metodą Wildera, z pierwszym ADX po dwóch pełnych okresach."""
    values: IndicatorSeries = [None] * len(candles)
    if period < 1:
        raise ValueError("Okres ADX musi być dodatni.")
    if len(candles) <= period:
        return values

    ranges = _true_ranges(candles)
    positive_moves = [0.0] * len(candles)
    negative_moves = [0.0] * len(candles)
    for index in range(1, len(candles)):
        upward = float(candles[index]["high"]) - float(candles[index - 1]["high"])
        downward = float(candles[index - 1]["low"]) - float(candles[index]["low"])
        if upward > downward and upward > 0:
            positive_moves[index] = upward
        elif downward > upward and downward > 0:
            negative_moves[index] = downward

    smoothed_range = sum(ranges[1 : period + 1])
    smoothed_positive = sum(positive_moves[1 : period + 1])
    smoothed_negative = sum(negative_moves[1 : period + 1])
    dx_values: IndicatorSeries = [None] * len(candles)
    first_adx_index = period * 2 - 1

    for index in range(period, len(candles)):
        if index > period:
            smoothed_range += ranges[index] - smoothed_range / period
            smoothed_positive += (
                positive_moves[index] - smoothed_positive / period
            )
            smoothed_negative += (
                negative_moves[index] - smoothed_negative / period
            )
        if smoothed_range <= 0:
            dx = 0.0
        else:
            positive_di = 100.0 * smoothed_positive / smoothed_range
            negative_di = 100.0 * smoothed_negative / smoothed_range
            di_sum = positive_di + negative_di
            dx = (
                100.0 * abs(positive_di - negative_di) / di_sum
                if di_sum > 0
                else 0.0
            )
        dx_values[index] = dx

        if index == first_adx_index:
            initial_dx = [
                float(value)
                for value in dx_values[period : first_adx_index + 1]
                if value is not None
            ]
            if len(initial_dx) == period:
                values[index] = fmean(initial_dx)
        elif index > first_adx_index and values[index - 1] is not None:
            values[index] = (
                values[index - 1] * (period - 1) + dx
            ) / period
    return values


def aggregate_m5(candles: Sequence[Candle]) -> list[Candle]:
    """Tworzy M5 tylko z pięciu kolejnych świec M1 w tym samym przedziale UTC."""
    completed: list[Candle] = []
    bucket_start = None
    bucket: list[Candle] = []

    def save_complete_bucket() -> None:
        """Dodaje do wyniku grupę wyłącznie wtedy, gdy zawiera pełne pięć minut."""
        if len(bucket) != 5:
            return
        volumes = [item.get("volume") for item in bucket]
        volume = (
            sum(float(value) for value in volumes)
            if all(value is not None for value in volumes)
            else None
        )
        completed.append(
            {
                "timestamp_utc": bucket_start,
                "open": float(bucket[0]["open"]),
                "high": max(float(item["high"]) for item in bucket),
                "low": min(float(item["low"]) for item in bucket),
                "close": float(bucket[-1]["close"]),
                "volume": volume,
            }
        )

    for candle in candles:
        timestamp = candle["timestamp_utc"]
        start = timestamp.replace(minute=timestamp.minute - timestamp.minute % 5)
        if start != bucket_start:
            save_complete_bucket()
            bucket_start = start
            bucket = []
        expected = bucket_start + timedelta(minutes=len(bucket))
        if timestamp != expected:
            bucket = []
            if timestamp == bucket_start:
                bucket.append(candle)
            continue
        bucket.append(candle)
    save_complete_bucket()
    return completed


def calculate_indicator_series(
    m1_candles: Sequence[Candle],
    m5_candles: Sequence[Candle],
) -> dict[str, IndicatorSeries]:
    """Oblicza wszystkie serie M1 i M5 z chronologicznych, zamkniętych świec."""
    return {
        "ema9_m1": _ema(
            [float(candle["close"]) for candle in m1_candles],
            EMA_FAST_M1_PERIOD,
        ),
        "ema21_m1": _ema(
            [float(candle["close"]) for candle in m1_candles],
            EMA_SLOW_M1_PERIOD,
        ),
        "rsi14_m1": _rsi(
            [float(candle["close"]) for candle in m1_candles],
            RSI_M1_PERIOD,
        ),
        "atr14_m1": _atr(m1_candles, ATR_M1_PERIOD),
        "ema20_m5": _ema(
            [float(candle["close"]) for candle in m5_candles],
            EMA_FAST_M5_PERIOD,
        ),
        "ema50_m5": _ema(
            [float(candle["close"]) for candle in m5_candles],
            EMA_SLOW_M5_PERIOD,
        ),
        "adx14_m5": _adx(m5_candles, ADX_M5_PERIOD),
    }


def finite_number(value: object) -> float | None:
    """Zwraca skończoną liczbę zmiennoprzecinkową lub None dla braku wartości."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None
