"""Czyste obliczenia wskaźników dla strategii BTC/USD."""

from __future__ import annotations

from math import isfinite
from typing import Sequence


# Backend
def ema_series(values: Sequence[float], period: int) -> list[float]:
    """Oblicza serię EMA, rozpoczynając od pierwszej dostępnej wartości.

    Args:
        values: Ceny zamknięcia w kolejności od najstarszej do najnowszej.
        period: Dodatnia liczba obserwacji używana do wygładzania.

    Returns:
        Wartość EMA dla każdej podanej ceny.

    Raises:
        ValueError: Gdy okres EMA jest mniejszy od jednego.
    """
    if period < 1:
        raise ValueError("Okres EMA musi być dodatni.")
    if not values:
        return []

    smoothing_factor = 2.0 / (period + 1)
    result = [float(values[0])]
    for value in values[1:]:
        previous = result[-1]
        result.append(previous + smoothing_factor * (float(value) - previous))
    return result


def rsi_wilder(values: Sequence[float], period: int) -> float | None:
    """Oblicza ostatnie RSI klasyczną metodą wygładzania Wildera.

    Args:
        values: Ceny zamknięcia w kolejności od najstarszej do najnowszej.
        period: Dodatnia liczba zmian ceny używana do obliczeń RSI.

    Returns:
        Ostatnie RSI albo None, jeśli historia nie zawiera pełnego okresu.

    Raises:
        ValueError: Gdy okres RSI jest mniejszy od jednego.
    """
    if period < 1:
        raise ValueError("Okres RSI musi być dodatni.")
    if len(values) <= period:
        return None

    changes = [
        float(values[index]) - float(values[index - 1])
        for index in range(1, len(values))
    ]
    average_gain = sum(max(change, 0.0) for change in changes[:period]) / period
    average_loss = sum(max(-change, 0.0) for change in changes[:period]) / period

    for change in changes[period:]:
        average_gain = (
            average_gain * (period - 1) + max(change, 0.0)
        ) / period
        average_loss = (
            average_loss * (period - 1) + max(-change, 0.0)
        ) / period

    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    if average_gain == 0:
        return 0.0
    relative_strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def volume_sma(values: Sequence[float], period: int) -> float | None:
    """Oblicza średnią wolumenu z ostatniego okna, razem z bieżącą świecą.

    Args:
        values: Wolumeny w kolejności od najstarszego do najnowszego.
        period: Dodatnia liczba wolumenów w średniej.

    Returns:
        Średnią z ostatniego okna albo None przy zbyt krótkiej historii.

    Raises:
        ValueError: Gdy okres jest mniejszy od jednego.
    """
    if period < 1:
        raise ValueError("Okres SMA wolumenu musi być dodatni.")
    if len(values) < period:
        return None

    window = [float(value) for value in values[-period:]]
    if not all(isfinite(value) for value in window):
        raise ValueError("Wolumen zawiera wartość niefinitywną.")
    return sum(window) / period


def atr_wilder(candles: Sequence[dict[str, float]], period: int) -> float | None:
    """Oblicza ostatni ATR metodą Wildera z kolejnych dostępnych świec.

    Args:
        candles: Świece OHLC w kolejności czasu; przerwy nie są uzupełniane.
        period: Dodatnia liczba zakresów używana do obliczeń ATR.

    Returns:
        Ostatnią wartość ATR albo None przy zbyt krótkiej historii.

    Raises:
        ValueError: Gdy okres ATR jest mniejszy od jednego.
    """
    if period < 1:
        raise ValueError("Okres ATR musi być dodatni.")
    if len(candles) < period:
        return None

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
    for true_range in true_ranges[period:]:
        average_true_range = (
            average_true_range * (period - 1) + true_range
        ) / period
    return average_true_range
