"""Backend: wskaźniki M1, sesyjny VWAP i wyłącznie zamknięte koszyki M5."""

from datetime import datetime, timedelta, timezone
from statistics import median

import config
import numpy as np
import pandas as pd
from schedule import as_utc, is_trading_minute


def ema(values: list[float], period: int) -> list[float]:
    """Oblicza rekurencyjną EMA; niepoprawny okres zgłasza ValueError."""
    if period < 1:
        raise ValueError("Okres EMA musi być dodatni.")
    if not values:
        return []
    return pd.Series(values, dtype="float64").ewm(span=period, adjust=False).mean().tolist()


def rsi(values: list[float], period: int = config.RSI_PERIOD) -> list[float | None]:
    """Zwraca serię RSI Wildera; przed rozgrzaniem wartości są None."""
    if period < 1:
        raise ValueError("Okres RSI musi być dodatni.")
    output = [None] * len(values)
    if len(values) <= period:
        return output
    changes = [b - a for a, b in zip(values, values[1:])]
    gain = sum(max(x, 0) for x in changes[:period]) / period
    loss = sum(max(-x, 0) for x in changes[:period]) / period
    for index in range(period, len(values)):
        if index > period:
            change = changes[index - 1]
            gain = (gain * (period - 1) + max(change, 0)) / period
            loss = (loss * (period - 1) + max(-change, 0)) / period
        output[index] = (100 - 100 / (1 + gain / loss)) if loss else (100.0 if gain else 50.0)
    return output


def completed_five_minute_candles(candles: list[dict], cutoff: datetime) -> list[dict]:
    """Agreguje ukończone koszyki UTC z co najmniej czterech rzeczywistych minut."""
    cutoff = as_utc(cutoff)
    buckets = {}
    for candle in candles:
        stamp = as_utc(candle["timestamp_utc"])
        minute = int(stamp.timestamp()) // 60
        bucket = minute // config.FIVE_MINUTE_PERIOD
        if (bucket + 1) * config.FIVE_MINUTE_PERIOD * 60 <= cutoff.timestamp() and is_trading_minute(stamp):
            buckets.setdefault(bucket, {})[minute] = candle
    result = []
    for bucket, members in sorted(buckets.items()):
        if len(members) < config.FIVE_MINUTE_MIN_CANDLES:
            continue
        rows = [members[key] for key in sorted(members)]
        volumes = [x.get("volume") for x in rows]
        result.append({"timestamp_utc": datetime.fromtimestamp(bucket * config.FIVE_MINUTE_PERIOD * 60, timezone.utc),
                       "open": float(rows[0]["open"]), "high": max(float(x["high"]) for x in rows),
                       "low": min(float(x["low"]) for x in rows), "close": float(rows[-1]["close"]),
                       "volume": sum(float(x) for x in volumes) if all(x is not None for x in volumes) else None})
    return result


def _bollinger(values: list[float]) -> tuple[float | None, float | None, float | None, float | None]:
    """Zwraca średnią, górne i dolne pasmo oraz z-score; brak rozgrzania daje None."""
    if len(values) < config.BB_PERIOD:
        return None, None, None, None
    recent = values[-config.BB_PERIOD:]
    middle = float(np.mean(recent))
    deviation = float(np.std(recent, ddof=0))
    return (middle, middle + config.BB_DEVIATIONS * deviation, middle - config.BB_DEVIATIONS * deviation,
            (recent[-1] - middle) / deviation if deviation else None)


def calculate_indicators(segment: list[dict], session: list[dict], cutoff: datetime) -> dict:
    """Oblicza wskaźniki segmentu do cutoff i VWAP dnia kasowego; pusta lista daje {}."""
    cutoff = as_utc(cutoff)
    segment = [x for x in segment if as_utc(x["timestamp_utc"]) + timedelta(minutes=1) <= cutoff]
    session = [x for x in session if as_utc(x["timestamp_utc"]) + timedelta(minutes=1) <= cutoff]
    if not segment:
        return {}
    closes = [float(x["close"]) for x in segment]
    fast, mid, slow = (ema(closes, n) for n in (config.EMA_FAST, config.EMA_MID, config.EMA_SLOW))
    ranges = []
    for index, row in enumerate(segment):
        high, low = float(row["high"]), float(row["low"])
        ranges.append(high - low if index == 0 else max(high - low, abs(high - closes[index - 1]), abs(low - closes[index - 1])))
    atrs = []
    if len(ranges) >= config.ATR_PERIOD:
        current = sum(ranges[:config.ATR_PERIOD]) / config.ATR_PERIOD
        atrs.append(current)
        for value in ranges[config.ATR_PERIOD:]:
            current = (current * (config.ATR_PERIOD - 1) + value) / config.ATR_PERIOD
            atrs.append(current)
    atr = atrs[-1] if atrs else None
    atr_median = median(atrs[-config.ATR_MEDIAN_PERIOD:]) if atrs else None
    middle, upper, lower, zscore = _bollinger(closes)
    _, _, _, previous_zscore = _bollinger(closes[:-1])
    movement = (sum(abs(b - a) for a, b in zip(closes[-config.ER_PERIOD - 1:-1], closes[-config.ER_PERIOD:]))
                if len(closes) > config.ER_PERIOD else None)
    er = abs(closes[-1] - closes[-config.ER_PERIOD - 1]) / movement if movement else (0.0 if movement == 0 else None)
    # SMA20 obejmuje świecę bieżącą; brak dowolnego wolumenu wyłącza wskaźnik.
    recent_volumes = [x.get("volume") for x in segment[-config.VOLUME_PERIOD:]]
    average_volume = (sum(float(x) for x in recent_volumes) / config.VOLUME_PERIOD
                      if len(recent_volumes) == config.VOLUME_PERIOD and all(x is not None for x in recent_volumes) else None)
    relative_volume = (float(recent_volumes[-1]) / average_volume if average_volume and average_volume > 0 else None)
    day = cutoff.astimezone(config.NEW_YORK).date()
    vwap_rows = [x for x in session if as_utc(x["timestamp_utc"]).astimezone(config.NEW_YORK).date() == day
                 and config.CASH_OPEN <= as_utc(x["timestamp_utc"]).astimezone(config.NEW_YORK).time().replace(tzinfo=None)
                 < (config.EARLY_CASH_CLOSE if day in config.EARLY_CLOSE_DATES else config.CASH_CLOSE)]
    total_volume = (sum(float(x["volume"]) for x in vwap_rows)
                    if vwap_rows and all(x.get("volume") is not None for x in vwap_rows) else 0)
    vwap = (sum((float(x["high"]) + float(x["low"]) + float(x["close"])) / 3 * float(x["volume"])
                for x in vwap_rows) / total_volume if total_volume > 0 else None)
    five = completed_five_minute_candles(segment, cutoff)
    five_closes = [x["close"] for x in five]
    five_ema = ema(five_closes, config.FIVE_MINUTE_EMA)
    m5_ready = len(five_closes) >= config.FIVE_MINUTE_EMA
    rsi_values = rsi(closes)
    last = segment[-1]
    candle_range = float(last["high"]) - float(last["low"])
    body = abs(float(last["close"]) - float(last["open"])) / candle_range if candle_range else 0.0
    return {"ema9": fast[-1], "ema21": mid[-1], "ema55": slow[-1],
            "ema55_slope": slow[-1] - slow[-config.EMA_SLOPE_PERIOD - 1] if len(slow) > config.EMA_SLOPE_PERIOD else None,
            "ema21_series": mid[-config.PULLBACK_TOUCH_LOOKBACK:],
            "atr": atr, "atr_bps": atr / closes[-1] * 10000 if atr is not None else None,
            "atr_ratio": atr / atr_median if atr_median else None,
            "rsi": rsi_values[-1], "rsi_previous": rsi_values[-2] if len(rsi_values) > 1 else None,
            "rsi_series": rsi_values[-(config.PULLBACK_RSI_LOOKBACK + 1):],
            "bb_middle": middle, "bb_upper": upper, "bb_lower": lower,
            "bb_zscore": zscore, "bb_zscore_previous": previous_zscore,
            "er": er, "relative_volume": relative_volume, "volume_sma20": average_volume,
            "vwap": vwap, "body_ratio": body,
            "lower_wick_ratio": (min(float(last["open"]), closes[-1]) - float(last["low"])) / candle_range if candle_range else 0.0,
            "upper_wick_ratio": (float(last["high"]) - max(float(last["open"]), closes[-1])) / candle_range if candle_range else 0.0,
            "m5_close": five_closes[-1] if m5_ready else None,
            "m5_ema20": five_ema[-1] if m5_ready else None,
            "m5_ema20_slope": five_ema[-1] - five_ema[-2] if m5_ready else None,
            "m5_count": len(five), "segment_candles": len(segment)}
