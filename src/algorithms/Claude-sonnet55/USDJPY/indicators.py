"""Wskaźniki USD/JPY liczone na rzeczywistych świecach bez uzupełniania luk."""

from collections import defaultdict
from datetime import datetime, timedelta
from math import isfinite
from statistics import median
from typing import Any

import numpy as np
import pandas as pd

from config import (
    ATR_PERIOD,
    ATR_RATIO_LOOKBACK,
    BB_DEVIATIONS,
    BB_PERIOD,
    EFFICIENCY_PERIOD,
    EMA_FAST_PERIOD,
    EMA_MIDDLE_PERIOD,
    EMA_SLOW_PERIOD,
    M5_BUCKET_MINUTES,
    M5_EMA_PERIOD,
    M5_MINIMUM_CANDLES,
    PIP_SIZE,
    RELATIVE_VOLUME_PERIOD,
    RSI_PERIOD,
)
from schedule import UTC, segment_id, trading_day_start

Candle = dict[str, Any]


def ema(values: pd.Series, period: int) -> pd.Series:
    """Oblicza wykładniczą średnią; niepoprawny okres zgłasza ValueError."""
    if period < 1:
        raise ValueError("Okres EMA musi być dodatni.")
    return values.astype(float).ewm(span=period, adjust=False, min_periods=1).mean()


def true_range(candles: pd.DataFrame) -> pd.Series:
    """Oblicza True Range; pierwsza świeca segmentu używa zakresu high-low."""
    previous_close = candles["close"].shift(1)
    values = pd.concat(
        [
            candles["high"] - candles["low"],
            (candles["high"] - previous_close).abs(),
            (candles["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    if not values.empty:
        values.iloc[0] = candles["high"].iloc[0] - candles["low"].iloc[0]
    return values


def wilder_rsi(values: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Oblicza RSI metodą Wildera; przed rozgrzewką zwraca NaN."""
    if period < 1:
        raise ValueError("Okres RSI musi być dodatni.")
    prices = values.astype(float).to_numpy()
    result = np.full(len(prices), np.nan)
    if len(prices) <= period:
        return pd.Series(result, index=values.index)
    changes = np.diff(prices)
    gains = np.maximum(changes, 0)
    losses = np.maximum(-changes, 0)
    average_gain = float(gains[:period].mean())
    average_loss = float(losses[:period].mean())
    for index in range(period, len(prices)):
        if index > period:
            change_index = index - 1
            average_gain = (average_gain * (period - 1) + gains[change_index]) / period
            average_loss = (average_loss * (period - 1) + losses[change_index]) / period
        if average_gain == 0 and average_loss == 0:
            result[index] = 50.0
        elif average_loss == 0:
            result[index] = 100.0
        else:
            result[index] = 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return pd.Series(result, index=values.index)


def wilder_atr(candles: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """Oblicza ATR Wildera z inicjalizacją średnią pierwszych zakresów."""
    if period < 1:
        raise ValueError("Okres ATR musi być dodatni.")
    ranges = true_range(candles)
    if len(ranges) < period:
        return pd.Series(np.nan, index=ranges.index, dtype=float)
    seeded = ranges.copy()
    seeded.iloc[: period - 1] = np.nan
    seeded.iloc[period - 1] = ranges.iloc[:period].mean()
    return seeded.ewm(alpha=1.0 / period, adjust=False, min_periods=1).mean()


def efficiency_ratio(values: pd.Series, period: int = EFFICIENCY_PERIOD) -> pd.Series:
    """Oblicza Kaufman ER jako ruch netto podzielony przez całkowitą drogę."""
    if period < 1:
        raise ValueError("Okres ER musi być dodatni.")
    numbers = values.astype(float)
    net = numbers.diff(period).abs()
    path = numbers.diff().abs().rolling(period, min_periods=period).sum()
    return (net / path.replace(0, np.nan)).clip(0, 1)


def relative_volume(volume: pd.Series, period: int = RELATIVE_VOLUME_PERIOD) -> pd.Series:
    """Dzieli bieżący wolumen przez średnią z bieżącej i poprzednich świec."""
    if period < 1:
        raise ValueError("Okres wolumenu musi być dodatni.")
    numeric = pd.to_numeric(volume, errors="coerce")
    average = numeric.rolling(period, min_periods=period).mean()
    return numeric / average.replace(0, np.nan)


def candle_body_ratio(candles: pd.DataFrame) -> pd.Series:
    """Zwraca udział korpusu w pełnym zakresie świecy; zakres zerowy daje NaN."""
    spread = candles["high"] - candles["low"]
    body = (candles["close"] - candles["open"]).abs()
    return body / spread.replace(0, np.nan)


def build_m1_indicators(candles: pd.DataFrame) -> pd.DataFrame:
    """Dodaje wskaźniki M1 do kopii świec jednego segmentu."""
    result = candles.copy()
    close = result["close"].astype(float)
    for period in (EMA_FAST_PERIOD, EMA_MIDDLE_PERIOD, EMA_SLOW_PERIOD):
        result[f"ema{period}"] = ema(close, period)
    result["atr14"] = wilder_atr(result, ATR_PERIOD)
    result["atr14_pips"] = result["atr14"] / PIP_SIZE
    result["rsi5"] = wilder_rsi(close, RSI_PERIOD)
    middle = close.rolling(BB_PERIOD, min_periods=BB_PERIOD).mean()
    deviation = close.rolling(BB_PERIOD, min_periods=BB_PERIOD).std(ddof=0)
    result["bb_middle"] = middle
    result["bb_upper"] = middle + BB_DEVIATIONS * deviation
    result["bb_lower"] = middle - BB_DEVIATIONS * deviation
    result["bb_zscore"] = (close - middle) / deviation.replace(0, np.nan)
    result["efficiency_ratio_30"] = efficiency_ratio(close, EFFICIENCY_PERIOD)
    result["rel_volume"] = relative_volume(result["volume"])
    result["body_ratio"] = candle_body_ratio(result)
    median_atr = result["atr14"].rolling(
        ATR_RATIO_LOOKBACK, min_periods=1
    ).median()
    result["atr_ratio"] = result["atr14"] / median_atr.replace(0, np.nan)
    return result


def aggregate_m5(candles: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Agrega kubełki M5 zamknięte w chwili now, wymagając czterech świec M1."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Czas agregacji M5 musi zawierać strefę.")
    if candles.empty:
        return pd.DataFrame()
    source = candles.copy()
    source["bucket"] = source["timestamp_utc"].map(
        lambda value: value.replace(
            minute=value.minute - value.minute % M5_BUCKET_MINUTES,
            second=0,
            microsecond=0,
        )
    )
    rows = []
    now_utc = now.astimezone(UTC)
    for bucket, group in source.groupby("bucket", sort=True):
        if bucket + timedelta(minutes=M5_BUCKET_MINUTES) > now_utc:
            continue
        ordered = group.sort_values("timestamp_utc")
        if len(ordered) < M5_MINIMUM_CANDLES:
            continue
        volume = pd.to_numeric(ordered["volume"], errors="coerce")
        rows.append(
            {
                "timestamp_utc": bucket,
                "open": float(ordered["open"].iloc[0]),
                "high": float(ordered["high"].max()),
                "low": float(ordered["low"].min()),
                "close": float(ordered["close"].iloc[-1]),
                "volume": float(volume.sum()) if volume.notna().all() else np.nan,
            }
        )
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result["ema20_5m"] = ema(result["close"], M5_EMA_PERIOD)
    result["ema20_5m_slope"] = result["ema20_5m"].diff()
    return result


def anchored_vwap(
    candles: pd.DataFrame, latest_time: datetime
) -> tuple[float | None, datetime]:
    """Oblicza VWAP od początku dnia Forex o 17:00 ET."""
    if latest_time.tzinfo is None or latest_time.utcoffset() is None:
        raise ValueError("Czas VWAP musi zawierać strefę.")
    start = trading_day_start(latest_time)
    session = candles.loc[candles["timestamp_utc"] >= start].copy()
    if session.empty:
        return None, start
    volume = pd.to_numeric(session["volume"], errors="coerce").fillna(0).clip(lower=0)
    total = float(volume.sum())
    if total <= 0:
        return None, start
    typical = (session["high"] + session["low"] + session["close"]) / 3.0
    return float((typical * volume).sum() / total), start


def latest_valid(row: pd.Series, key: str) -> float | None:
    """Zwraca skończoną wartość wskaźnika albo None."""
    value = row.get(key)
    if value is None or pd.isna(value):
        return None
    converted = float(value)
    return converted if isfinite(converted) else None


def unique_segments(candles: list[Candle]) -> dict[str, list[Candle]]:
    """Grupuje świece według identyfikatora segmentu bez tworzenia syntetycznych wierszy."""
    groups: dict[str, list[Candle]] = defaultdict(list)
    for candle in candles:
        groups[segment_id(candle["timestamp_utc"])].append(candle)
    return dict(groups)


def median_or_none(values: list[float]) -> float | None:
    """Zwraca medianę niepustej listy skończonych liczb."""
    valid = [value for value in values if isfinite(value)]
    return float(median(valid)) if valid else None
