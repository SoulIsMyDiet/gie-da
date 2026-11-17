"""Wskaźniki techniczne wyliczane wyłącznie na rzeczywistych świecach."""

from collections import defaultdict
from datetime import datetime, timedelta
from math import isfinite
from statistics import median
from typing import Any

import numpy as np
import pandas as pd

from config import (
    ATR_PERIOD,
    BB_DEVIATIONS,
    BB_PERIOD,
    EFFICIENCY_PERIOD,
    EMA_FAST_PERIOD,
    EMA_MIDDLE_PERIOD,
    M5_EMA_PERIOD,
    EMA_SLOW_PERIOD,
    M5_BUCKET_MINUTES,
    M5_MINIMUM_CANDLES,
    RELATIVE_VOLUME_PERIOD,
    RSI_PERIOD,
    ATR_RATIO_LOOKBACK,
    DAILY_SESSION_START_ET,
)
from schedule import NEW_YORK, UTC, segment_id

Candle = dict[str, Any]


def ema(values: pd.Series, period: int) -> pd.Series:
    """Oblicza EMA z jawnym okresem; niepoprawny okres zgłasza ValueError."""
    if period < 1:
        raise ValueError("Okres EMA musi być dodatni.")
    return values.astype(float).ewm(span=period, adjust=False, min_periods=1).mean()


def true_range(candles: pd.DataFrame) -> pd.Series:
    """Oblicza TR; pierwsza świeca segmentu używa wyłącznie high minus low."""
    previous_close = candles["close"].shift(1)
    result = pd.concat(
        [
            candles["high"] - candles["low"],
            (candles["high"] - previous_close).abs(),
            (candles["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    if not result.empty:
        result.iloc[0] = candles["high"].iloc[0] - candles["low"].iloc[0]
    return result


def wilder_rsi(values: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Oblicza RSI Wildera; wartości są puste przed zakończeniem rozgrzewki."""
    if period < 1:
        raise ValueError("Okres RSI musi być dodatni.")
    prices = values.astype(float).to_numpy()
    result_values = np.full(len(prices), np.nan)
    if len(prices) <= period:
        return pd.Series(result_values, index=values.index)
    changes = np.diff(prices)
    gains = np.maximum(changes, 0)
    losses = np.maximum(-changes, 0)
    average_gain = float(gains[:period].mean())
    average_loss = float(losses[:period].mean())
    for index in range(period, len(prices)):
        if index > period:
            change_index = index - 1
            average_gain = (
                average_gain * (period - 1) + gains[change_index]
            ) / period
            average_loss = (
                average_loss * (period - 1) + losses[change_index]
            ) / period
        if average_gain == 0 and average_loss == 0:
            result_values[index] = 50
        elif average_loss == 0:
            result_values[index] = 100
        else:
            result_values[index] = 100 - 100 / (1 + average_gain / average_loss)
    result = pd.Series(result_values, index=values.index)
    return result


def wilder_atr(candles: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """Oblicza ATR Wildera dla jednej historii segmentu."""
    if period < 1:
        raise ValueError("Okres ATR musi być dodatni.")
    ranges = true_range(candles)
    if len(ranges) < period:
        return pd.Series(np.nan, index=ranges.index, dtype=float)

    seeded = ranges.copy()
    seeded.iloc[:period - 1] = np.nan
    seeded.iloc[period - 1] = ranges.iloc[:period].mean()
    return seeded.ewm(alpha=1 / period, adjust=False, min_periods=1).mean()


def efficiency_ratio(values: pd.Series, period: int = EFFICIENCY_PERIOD) -> pd.Series:
    """Oblicza Kaufman ER jako ruch netto podzielony przez drogę bezwzględną."""
    if period < 1:
        raise ValueError("Okres ER musi być dodatni.")
    net = values.astype(float).diff(period).abs()
    path = values.astype(float).diff().abs().rolling(period, min_periods=period).sum()
    return (net / path.replace(0, np.nan)).clip(0, 1)


def build_m1_indicators(candles: pd.DataFrame) -> pd.DataFrame:
    """Dodaje wszystkie wymagane wskaźniki M1 do kopii świec segmentu."""
    result = candles.copy()
    close = result["close"].astype(float)
    for period in (EMA_FAST_PERIOD, EMA_MIDDLE_PERIOD, EMA_SLOW_PERIOD):
        result[f"ema{period}"] = ema(close, period)
    result["atr14_usd"] = wilder_atr(result, ATR_PERIOD)
    result["rsi5"] = wilder_rsi(close, RSI_PERIOD)
    middle = close.rolling(BB_PERIOD, min_periods=BB_PERIOD).mean()
    deviation = close.rolling(BB_PERIOD, min_periods=BB_PERIOD).std(ddof=0)
    result["bb_middle"] = middle
    result["bb_upper"] = middle + BB_DEVIATIONS * deviation
    result["bb_lower"] = middle - BB_DEVIATIONS * deviation
    result["bb_zscore"] = (close - middle) / deviation.replace(0, np.nan)
    result["efficiency_ratio_30"] = efficiency_ratio(close, EFFICIENCY_PERIOD)
    result["rel_volume"] = relative_volume(result["volume"], RELATIVE_VOLUME_PERIOD)
    result["body_ratio"] = candle_body_ratio(result)
    median_atr = result["atr14_usd"].rolling(
        ATR_RATIO_LOOKBACK, min_periods=1
    ).median()
    result["atr_ratio"] = result["atr14_usd"] / median_atr.replace(0, np.nan)
    result["atr14_bps"] = result["atr14_usd"] / close * 10000
    return result


def relative_volume(volume: pd.Series, period: int = RELATIVE_VOLUME_PERIOD) -> pd.Series:
    """Dzieli wolumen świecy przez średnią z poprzednich świec, łącznie z bieżącą."""
    if period < 1:
        raise ValueError("Okres wolumenu musi być dodatni.")
    values = pd.to_numeric(volume, errors="coerce")
    average = values.rolling(period, min_periods=period).mean()
    return values / average.replace(0, np.nan)


def candle_body_ratio(candles: pd.DataFrame) -> pd.Series:
    """Zwraca udział korpusu w zakresie świecy; zakres zerowy daje NaN."""
    spread = candles["high"] - candles["low"]
    return (candles["close"] - candles["open"]).abs() / spread.replace(0, np.nan)


def aggregate_m5(candles: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Buduje zamknięte kubełki M5 z co najmniej czterech rzeczywistych świec."""
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
        bucket_end = bucket + timedelta(minutes=M5_BUCKET_MINUTES)
        if bucket_end > now_utc or len(group) < M5_MINIMUM_CANDLES:
            continue
        ordered = group.sort_values("timestamp_utc")
        volumes = ordered["volume"]
        rows.append(
            {
                "timestamp_utc": bucket,
                "open": float(ordered["open"].iloc[0]),
                "high": float(ordered["high"].max()),
                "low": float(ordered["low"].min()),
                "close": float(ordered["close"].iloc[-1]),
                "volume": float(volumes.sum()) if volumes.notna().all() else np.nan,
            }
        )
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result["ema20_5m"] = ema(result["close"], M5_EMA_PERIOD)
    result["ema20_5m_slope"] = result["ema20_5m"].diff()
    return result


def anchored_vwap(candles: pd.DataFrame, latest_time: datetime) -> tuple[float | None, datetime]:
    """Oblicza VWAP od początku bieżącego dnia handlowego 18:00 ET."""
    if latest_time.tzinfo is None or latest_time.utcoffset() is None:
        raise ValueError("Czas VWAP musi zawierać strefę.")
    new_york = latest_time.astimezone(NEW_YORK)
    trade_date = (
        new_york.date()
        if new_york.time() >= DAILY_SESSION_START_ET
        else new_york.date() - timedelta(days=1)
    )
    start = datetime.combine(
        trade_date, DAILY_SESSION_START_ET, tzinfo=NEW_YORK
    ).astimezone(UTC)
    session = candles.loc[candles["timestamp_utc"] >= start].copy()
    if session.empty:
        return None, start
    volume = pd.to_numeric(session["volume"], errors="coerce").fillna(0).clip(lower=0)
    total = float(volume.sum())
    if total <= 0:
        return None, start
    typical = (session["high"] + session["low"] + session["close"]) / 3
    return float((typical * volume).sum() / total), start


def latest_valid(row: pd.Series, key: str) -> float | None:
    """Zwraca skończoną wartość wskaźnika albo None."""
    value = row.get(key)
    if value is None or pd.isna(value):
        return None
    converted = float(value)
    return converted if isfinite(converted) else None


def unique_segments(candles: list[Candle]) -> dict[str, list[Candle]]:
    """Grupuje świece według identyfikatora segmentu, bez uzupełniania braków."""
    groups: dict[str, list[Candle]] = defaultdict(list)
    for candle in candles:
        groups[segment_id(candle["timestamp_utc"])].append(candle)
    return dict(groups)


def median_or_none(values: list[float]) -> float | None:
    """Zwraca medianę z niepustej listy skończonych wartości."""
    valid = [value for value in values if isfinite(value)]
    return float(median(valid)) if valid else None
