"""Obliczenia wskaźników dla strategii Chat-gpt6 BTC/USD."""

from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite
from typing import Sequence

Candle = dict[str, object]


def ema_series(values: Sequence[float], period: int) -> list[float]:
    """Oblicza EMA, rozpoczynając od pierwszej wartości szeregu."""
    if period < 1:
        raise ValueError("Okres EMA musi być dodatni.")
    if not values:
        return []
    alpha = 2.0 / (period + 1)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(alpha * float(value) + (1.0 - alpha) * result[-1])
    return result


def rsi_wilder_series(
    values: Sequence[float], period: int
) -> list[float | None]:
    """Oblicza RSI metodą Wildera i zwraca None przed rozgrzewką."""
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
    """Obsługuje serie RSI bez wzrostów albo bez spadków."""
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    if average_gain == 0:
        return 0.0
    relative_strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def atr_wilder_series(
    candles: Sequence[Candle], period: int
) -> list[float | None]:
    """Oblicza ATR metodą Wildera, używając high-low dla pierwszego TR."""
    if period < 1:
        raise ValueError("Okres ATR musi być dodatni.")
    result: list[float | None] = [None] * len(candles)
    if len(candles) < period:
        return result

    true_ranges: list[float] = []
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


def macd_series(
    closes: Sequence[float],
    fast_period: int,
    slow_period: int,
    signal_period: int,
) -> tuple[list[float], list[float], list[float]]:
    """Zwraca MACD, linię sygnałową i histogram."""
    if fast_period < 1 or slow_period < 1 or signal_period < 1:
        raise ValueError("Okresy MACD muszą być dodatnie.")
    fast = ema_series(closes, fast_period)
    slow = ema_series(closes, slow_period)
    line = [fast_value - slow_value for fast_value, slow_value in zip(fast, slow)]
    signal = ema_series(line, signal_period)
    histogram = [line_value - signal_value for line_value, signal_value in zip(line, signal)]
    return line, signal, histogram


def volume_average(
    candles: Sequence[Candle], index: int, period: int
) -> tuple[float | None, float | None]:
    """Liczy SMA i ratio z ostatnich dostępnych świec, łącznie z bieżącą."""
    if period < 1 or index < 0 or index >= len(candles):
        raise ValueError("Nieprawidłowy indeks lub okres wolumenu.")
    window = candles[max(0, index - period + 1) : index + 1]
    if len(window) < period:
        return None, None
    volumes: list[float] = []
    for candle in window:
        value = candle.get("volume")
        if value is None:
            return None, None
        volume = float(value)
        if not isfinite(volume) or volume <= 0:
            return None, None
        volumes.append(volume)
    average = sum(volumes) / period
    return average, volumes[-1] / average


def aggregate_complete_m5(candles: Sequence[Candle]) -> list[dict[str, object]]:
    """Agreguje wyłącznie pięć rzeczywistych świec M1 w jednym kubełku M5."""
    buckets: dict[datetime, list[Candle]] = {}
    for candle in candles:
        opened = candle["timestamp_utc"]
        if not isinstance(opened, datetime):
            raise ValueError("Świeca M1 musi mieć świadomy czas UTC.")
        minute = opened.replace(second=0, microsecond=0)
        bucket_open = minute - timedelta(minutes=minute.minute % 5)
        buckets.setdefault(bucket_open, []).append(candle)

    complete: list[dict[str, object]] = []
    for bucket_open, bucket_candles in sorted(buckets.items()):
        by_open = {
            candle["timestamp_utc"]: candle for candle in bucket_candles
        }
        expected_opens = [
            bucket_open + timedelta(minutes=offset) for offset in range(5)
        ]
        if any(opened not in by_open for opened in expected_opens):
            continue
        ordered = [by_open[opened] for opened in expected_opens]
        volumes = [candle.get("volume") for candle in ordered]
        volume = (
            sum(float(value) for value in volumes)
            if all(value is not None for value in volumes)
            else None
        )
        complete.append(
            {
                "timestamp_utc": bucket_open,
                "close_time_utc": bucket_open + timedelta(minutes=5),
                "open": float(ordered[0]["open"]),
                "high": max(float(candle["high"]) for candle in ordered),
                "low": min(float(candle["low"]) for candle in ordered),
                "close": float(ordered[-1]["close"]),
                "volume": volume,
            }
        )
    return complete
