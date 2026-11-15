"""Wskaźniki liczone niezależnie dla rzeczywistych świec M1 i M5."""

from collections import defaultdict
from datetime import datetime, timedelta
from math import sqrt
from statistics import fmean
from typing import Any, Sequence

from .config import CONFIG
from .schedule import session_start

Candle = dict[str, Any]
Series = list[float | None]


def ema(values: Sequence[float], period: int) -> Series:
    """Zwraca EMA z pierwszą ceną jako ziarnem; niepoprawny okres daje ValueError."""
    if period < 1:
        raise ValueError("Okres EMA musi być dodatni.")
    if not values:
        return []
    result = [float(values[0])]
    alpha = 2.0 / (period + 1)
    for value in values[1:]:
        result.append(result[-1] + alpha * (value - result[-1]))
    return result


def rsi(values: Sequence[float], period: int) -> Series:
    """Zwraca RSI Wildera; pierwsza wartość wymaga period zmian ceny."""
    if period < 1:
        raise ValueError("Okres RSI musi być dodatni.")
    result: Series = [None] * len(values)
    if len(values) <= period:
        return result
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gain = fmean(max(change, 0.0) for change in changes[:period])
    loss = fmean(max(-change, 0.0) for change in changes[:period])
    for index in range(period, len(values)):
        if index > period:
            change = changes[index - 1]
            gain = (gain * (period - 1) + max(change, 0.0)) / period
            loss = (loss * (period - 1) + max(-change, 0.0)) / period
        result[index] = (50.0 if gain == loss == 0 else
                         100.0 if loss == 0 else
                         100.0 - 100.0 / (1.0 + gain / loss))
    return result


def atr(candles: Sequence[Candle], period: int) -> Series:
    """Zwraca ATR Wildera; pierwszy TR to high-low, a ziarno to średnia TR."""
    if period < 1:
        raise ValueError("Okres ATR musi być dodatni.")
    result: Series = [None] * len(candles)
    ranges = []
    for index, candle in enumerate(candles):
        high, low = candle["high"], candle["low"]
        previous = candles[index - 1]["close"] if index else None
        ranges.append(high - low if previous is None else
                      max(high - low, abs(high - previous), abs(low - previous)))
    if len(ranges) >= period:
        result[period - 1] = fmean(ranges[:period])
        for index in range(period, len(ranges)):
            result[index] = (result[index - 1] * (period - 1) + ranges[index]) / period
    return result


def bollinger(values: Sequence[float], period: int, deviations: float) -> dict[str, Series]:
    """Zwraca średnią i pasma BB oparte na populacyjnym odchyleniu standardowym."""
    if period < 1 or deviations < 0:
        raise ValueError("Niepoprawne parametry pasm Bollingera.")
    result: dict[str, Series] = {key: [None] * len(values) for key in ("middle", "upper", "lower", "width")}
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        middle = fmean(window)
        deviation = sqrt(fmean((value - middle) ** 2 for value in window))
        upper, lower = middle + deviations * deviation, middle - deviations * deviation
        result["middle"][index], result["upper"][index], result["lower"][index] = middle, upper, lower
        result["width"][index] = (upper - lower) / middle if middle else None
    return result


def aggregate_m5(candles: Sequence[Candle], now: datetime) -> list[Candle]:
    """Agreguje tylko pięć kolejnych minut UTC w już zamkniętym kubełku M5."""
    buckets: dict[datetime, list[Candle]] = defaultdict(list)
    for candle in candles:
        stamp = candle["timestamp_utc"]
        start = stamp.replace(minute=stamp.minute - stamp.minute % 5, second=0, microsecond=0)
        buckets[start].append(candle)
    result = []
    for start, group in sorted(buckets.items()):
        group.sort(key=lambda candle: candle["timestamp_utc"])
        if start + timedelta(minutes=5) > now or [
            item["timestamp_utc"] for item in group
        ] != [start + timedelta(minutes=i) for i in range(5)]:
            continue
        volumes = [item["volume"] for item in group]
        result.append({
            "timestamp_utc": start, "open": group[0]["open"],
            "high": max(item["high"] for item in group),
            "low": min(item["low"] for item in group), "close": group[-1]["close"],
            "volume": sum(volumes) if all(value is not None for value in volumes) else None,
        })
    return result


def session_vwap(candles: Sequence[Candle]) -> dict[str, Any]:
    """Zwraca VWAP typowej ceny bieżącej sesji i informacje o brakach wolumenu.

    Przekazana historia musi być niepusta. Brak dowolnego wolumenu lub suma
    wolumenów równa zero uniemożliwia obliczenie VWAP i daje wartość None.
    """
    start = session_start(candles[-1]["timestamp_utc"])
    session = [candle for candle in candles if candle["timestamp_utc"] >= start]
    missing = sum(candle["volume"] is None for candle in session)
    volume = sum(candle["volume"] for candle in session if candle["volume"] is not None)
    weighted = sum((candle["high"] + candle["low"] + candle["close"]) / 3 * candle["volume"]
                   for candle in session if candle["volume"] is not None)
    return {"value": weighted / volume if volume > 0 and not missing else None,
            "session_start_utc": start.isoformat(), "volume": volume,
            "weighted_price_volume_sum": weighted,
            "missing_volume_candles": missing, "candles": len(session)}


def indicator_series(candles: Sequence[Candle]) -> dict[str, Series]:
    """Zwraca pełne szeregi wskaźników dla pojedynczej, niezależnej historii."""
    closes = [candle["close"] for candle in candles]
    result = {f"ema_{period}": ema(closes, period) for period in
              (CONFIG.ema_fast, CONFIG.ema_middle, CONFIG.ema_slow)}
    result["rsi"] = rsi(closes, CONFIG.rsi_period)
    result["atr"] = atr(candles, CONFIG.atr_period)
    result.update({f"bb_{key}": values for key, values in
                   bollinger(closes, CONFIG.bb_period, CONFIG.bb_deviations).items()})
    return result
