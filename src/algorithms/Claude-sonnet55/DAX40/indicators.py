"""Backend
Obliczanie wskaźników dla zamkniętych świec w jednym ciągłym segmencie danych.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from math import sqrt
from statistics import median
from typing import Any

from .config import (
    ATR_PERIOD,
    ATR_RATIO_LOOKBACK,
    BB_DEVIATIONS,
    BB_PERIOD,
    EFFICIENCY_RATIO_PERIOD,
    EMA_FAST_PERIOD,
    EMA_MID_PERIOD,
    EMA_SLOW_PERIOD,
    FIVE_MINUTE_BUCKET_SIZE,
    FIVE_MINUTE_EMA_PERIOD,
    FIVE_MINUTE_MIN_CANDLES,
    RSI_PERIOD,
    WARSAW_TIMEZONE,
)
from .schedule import expected_candle_opens, is_trading_minute


def _ema(values: list[float], period: int) -> list[float | None]:
    """Oblicza EMA z inicjalizacją średnią po pełnym okresie."""
    result: list[float | None] = [None] * len(values)
    if period < 1 or len(values) < period:
        return result
    current = sum(values[:period]) / period
    result[period - 1] = current
    alpha = 2.0 / (period + 1)
    for index in range(period, len(values)):
        current += alpha * (values[index] - current)
        result[index] = current
    return result


def _rsi_series(values: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    """Oblicza serię RSI metodą Wildera dla późniejszej weryfikacji warunków."""
    result: list[float | None] = [None] * len(values)
    if period < 1 or len(values) <= period:
        return result
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period

    def value(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain else 50.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    result[period] = value(average_gain, average_loss)
    for change_index in range(period, len(changes)):
        average_gain = (average_gain * (period - 1) + gains[change_index]) / period
        average_loss = (average_loss * (period - 1) + losses[change_index]) / period
        result[change_index + 1] = value(average_gain, average_loss)
    return result


def _rsi(values: list[float], period: int = RSI_PERIOD) -> float | None:
    """Zwraca ostatnią wartość RSI Wildera albo None przed rozgrzaniem."""
    return _rsi_series(values, period)[-1] if values else None


def _true_ranges(candles: list[dict[str, Any]]) -> list[float]:
    """Buduje TR; pierwsza świeca segmentu używa wyłącznie high minus low."""
    ranges = []
    previous_close = None
    for candle in candles:
        high, low = float(candle["high"]), float(candle["low"])
        ranges.append(
            high - low
            if previous_close is None
            else max(high - low, abs(high - previous_close), abs(low - previous_close))
        )
        previous_close = float(candle["close"])
    return ranges


def _atr(candles: list[dict[str, Any]], period: int = ATR_PERIOD) -> float | None:
    """Oblicza ATR Wildera z pełnego okresu."""
    ranges = _true_ranges(candles)
    if period < 1 or len(ranges) < period:
        return None
    current = sum(ranges[:period]) / period
    for true_range in ranges[period:]:
        current = (current * (period - 1) + true_range) / period
    return current


def _atr_series(candles: list[dict[str, Any]], period: int) -> list[float | None]:
    """Oblicza Wilder ATR w każdym punkcie segmentu."""
    ranges = _true_ranges(candles)
    result: list[float | None] = [None] * len(candles)
    if len(ranges) < period:
        return result
    current = sum(ranges[:period]) / period
    result[period - 1] = current
    for index in range(period, len(ranges)):
        current = (current * (period - 1) + ranges[index]) / period
        result[index] = current
    return result


def _efficiency_ratio(values: list[float], period: int) -> float | None:
    """Mierzy udział zmiany netto w całkowitym ruchu ceny z ostatnich N świec."""
    if len(values) <= period:
        return None
    movement = abs(values[-1] - values[-period - 1])
    total_movement = sum(
        abs(values[index] - values[index - 1])
        for index in range(len(values) - period, len(values))
    )
    return movement / total_movement if total_movement else 0.0


def completed_five_minute_candles(
    candles: list[dict[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    """Agreguje zamknięte 5M koszyki mające co najmniej 4 oczekiwane świece M1."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Bieżący czas musi zawierać strefę czasową.")
    now_utc = now.astimezone(timezone.utc)
    buckets: dict[int, dict[int, dict[str, Any]]] = {}
    for candle in candles:
        timestamp = candle["timestamp_utc"].astimezone(timezone.utc)
        minute_epoch = int(timestamp.timestamp()) // 60
        bucket_id = minute_epoch // FIVE_MINUTE_BUCKET_SIZE
        bucket_end = datetime.fromtimestamp(
            (bucket_id + 1) * FIVE_MINUTE_BUCKET_SIZE * 60, timezone.utc
        )
        if bucket_end <= now_utc and is_trading_minute(timestamp):
            buckets.setdefault(bucket_id, {})[minute_epoch] = candle

    aggregated = []
    for bucket_id, members_by_minute in sorted(buckets.items()):
        bucket_start = datetime.fromtimestamp(
            bucket_id * FIVE_MINUTE_BUCKET_SIZE * 60, timezone.utc
        )
        bucket_end = bucket_start + timedelta(minutes=FIVE_MINUTE_BUCKET_SIZE)
        expected = expected_candle_opens(bucket_start, bucket_end)
        eligible = [members_by_minute[int(item.timestamp()) // 60] for item in expected
                    if int(item.timestamp()) // 60 in members_by_minute]
        if len(eligible) < FIVE_MINUTE_MIN_CANDLES:
            continue
        eligible.sort(key=lambda item: item["timestamp_utc"])
        aggregated.append(
            {
                "timestamp_utc": bucket_start,
                "open": float(eligible[0]["open"]),
                "high": max(float(item["high"]) for item in eligible),
                "low": min(float(item["low"]) for item in eligible),
                "close": float(eligible[-1]["close"]),
                "volume": sum(float(item.get("volume") or 0) for item in eligible),
                "minute_candles": len(eligible),
            }
        )
    return aggregated


def calculate_indicators(
    candles: list[dict[str, Any]], now: datetime
) -> dict[str, Any]:
    """Oblicza wszystkie wskaźniki i dane diagnostyczne dla ostatniej świecy."""
    if not candles:
        return {}
    closes = [float(item["close"]) for item in candles]
    highs = [float(item["high"]) for item in candles]
    lows = [float(item["low"]) for item in candles]
    volumes = [float(item.get("volume") or 0) for item in candles]
    ema9 = _ema(closes, EMA_FAST_PERIOD)
    ema21 = _ema(closes, EMA_MID_PERIOD)
    ema55 = _ema(closes, EMA_SLOW_PERIOD)
    atr_values = _atr_series(candles, ATR_PERIOD)
    atr = atr_values[-1]
    recent_atr_values = [
        value for value in atr_values[-ATR_RATIO_LOOKBACK:] if value is not None
    ]
    atr_median = median(recent_atr_values) if recent_atr_values else None
    rsi_values = _rsi_series(closes)
    bb_values = closes[-BB_PERIOD:]
    bb_middle = sum(bb_values) / BB_PERIOD if len(bb_values) == BB_PERIOD else None
    bb_std = (
        sqrt(sum((value - bb_middle) ** 2 for value in bb_values) / BB_PERIOD)
        if bb_middle is not None
        else None
    )
    previous_bb_values = closes[-BB_PERIOD - 1 : -1]
    previous_bb_middle = (
        sum(previous_bb_values) / BB_PERIOD
        if len(previous_bb_values) == BB_PERIOD
        else None
    )
    previous_bb_std = (
        sqrt(
            sum((value - previous_bb_middle) ** 2 for value in previous_bb_values)
            / BB_PERIOD
        )
        if previous_bb_middle is not None
        else None
    )
    zscore = (
        (closes[-1] - bb_middle) / bb_std
        if bb_middle is not None and bb_std and bb_std > 0
        else None
    )
    previous_zscore = (
        (closes[-2] - previous_bb_middle) / previous_bb_std
        if len(closes) >= 2
        and previous_bb_middle is not None
        and previous_bb_std
        and previous_bb_std > 0
        else None
    )
    last_time = candles[-1]["timestamp_utc"].astimezone(timezone.utc)
    local_time = last_time.astimezone(WARSAW_TIMEZONE)
    vwap_candidates = []
    if local_time.timetz().replace(tzinfo=None) >= time(9, 0):
        vwap_candidates = [
            item
            for item in candles
            if item["timestamp_utc"].astimezone(WARSAW_TIMEZONE).date()
            == local_time.date()
            and item["timestamp_utc"].astimezone(WARSAW_TIMEZONE).timetz().replace(
                tzinfo=None
            )
            >= time(9, 0)
        ]
    vwap_volume = sum(float(item.get("volume") or 0) for item in vwap_candidates)
    vwap = (
        sum(
            (
                float(item["high"])
                + float(item["low"])
                + float(item["close"])
            )
            / 3
            * float(item.get("volume") or 0)
            for item in vwap_candidates
        )
        / vwap_volume
        if vwap_volume > 0
        else (
            sum(
                (float(item["high"]) + float(item["low"]) + float(item["close"])) / 3
                for item in vwap_candidates
            )
            / len(vwap_candidates)
            if vwap_candidates
            else None
        )
    )
    completed_5m = completed_five_minute_candles(candles, now)
    ema5_values = _ema(
        [float(item["close"]) for item in completed_5m], FIVE_MINUTE_EMA_PERIOD
    )
    volume_baseline = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else 0.0
    candle_range = highs[-1] - lows[-1]
    body_ratio = abs(closes[-1] - float(candles[-1]["open"])) / candle_range if candle_range else 0.0
    previous_rsi_values = [
        item for item in rsi_values[-4:-1] if item is not None
    ]
    last_two_rsi_values = [item for item in rsi_values[-2:] if item is not None]
    efficiency = _efficiency_ratio(closes, EFFICIENCY_RATIO_PERIOD)
    close_5m = float(completed_5m[-1]["close"]) if completed_5m else None
    ema20_5m = ema5_values[-1] if ema5_values else None
    trend_5m = (
        "UP"
        if close_5m is not None
        and ema20_5m is not None
        and close_5m > ema20_5m
        else "DOWN"
        if close_5m is not None
        and ema20_5m is not None
        and close_5m < ema20_5m
        else None
    )
    return {
        "atr14_points": atr,
        "atr14": atr,
        "atr_median_points": atr_median,
        "atr_ratio": atr / atr_median if atr is not None and atr_median else None,
        "rsi5": rsi_values[-1],
        "rsi5_previous_1": rsi_values[-2] if len(rsi_values) > 1 else None,
        "rsi5_previous_2": rsi_values[-3] if len(rsi_values) > 2 else None,
        "rsi5_previous_3": rsi_values[-4] if len(rsi_values) > 3 else None,
        "rsi5_prior_three": previous_rsi_values,
        "rsi5_last_two": last_two_rsi_values,
        "ema9": ema9[-1],
        "ema21": ema21[-1],
        "ema55": ema55[-1],
        "ema55_10_bars_ago": ema55[-11] if len(ema55) >= 11 else None,
        "efficiency_ratio_30": efficiency,
        "bb_middle": bb_middle,
        "bb_upper": bb_middle + BB_DEVIATIONS * bb_std if bb_middle is not None and bb_std is not None else None,
        "bb_lower": bb_middle - BB_DEVIATIONS * bb_std if bb_middle is not None and bb_std is not None else None,
        "bb_zscore": zscore,
        "bb_zscore_previous": previous_zscore,
        "vwap": vwap,
        "vwap_distance_atr": (
            (closes[-1] - vwap) / atr if vwap is not None and atr else None
        ),
        "ema20_5m": ema20_5m,
        "close_5m": close_5m,
        "ema20_5m_previous": ema5_values[-2] if len(ema5_values) > 1 else None,
        "ema20_5m_slope": (
            ema5_values[-1] - ema5_values[-2]
            if len(ema5_values) > 1
            and ema5_values[-1] is not None
            and ema5_values[-2] is not None
            else None
        ),
        "trend_5m": trend_5m,
        "completed_5m_count": len(completed_5m),
        "relative_volume": volumes[-1] / volume_baseline if volume_baseline > 0 else None,
        "rel_volume": volumes[-1] / volume_baseline if volume_baseline > 0 else None,
        "volume_baseline_20": volume_baseline or None,
        "candle_range": candle_range,
        "body_ratio": body_ratio,
        "segment_candles": len(candles),
        "atr_14_series": atr_values,
    }
