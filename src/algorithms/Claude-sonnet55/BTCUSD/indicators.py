"""Backend: wskaźniki M1/M5 i kontrola rzeczywistych luk w danych."""

from datetime import datetime, timedelta, timezone
from math import isfinite
from statistics import median

try:
    from . import config
    from .schedule import as_utc
except ImportError:
    import config
    from schedule import as_utc


def ema(values: list[float], period: int) -> list[float]:
    """Oblicza EMA z inicjalizacją pierwszą wartością; okres musi być dodatni."""
    if period < 1:
        raise ValueError("Okres EMA musi być dodatni.")
    if not values:
        return []
    alpha = 2.0 / (period + 1)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(alpha * float(value) + (1.0 - alpha) * result[-1])
    return result


def rsi_wilder(values: list[float], period: int = config.RSI_PERIOD) -> list[float | None]:
    """Oblicza RSI Wildera; zwraca None przed uzyskaniem period zmian cen."""
    if period < 1:
        raise ValueError("Okres RSI musi być dodatni.")
    result: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return result
    changes = [right - left for left, right in zip(values, values[1:])]
    average_gain = sum(max(change, 0.0) for change in changes[:period]) / period
    average_loss = sum(max(-change, 0.0) for change in changes[:period]) / period
    for index in range(period, len(values)):
        if index > period:
            change = changes[index - 1]
            average_gain = (average_gain * (period - 1) + max(change, 0.0)) / period
            average_loss = (average_loss * (period - 1) + max(-change, 0.0)) / period
        if average_loss == 0:
            result[index] = 100.0 if average_gain > 0 else 50.0
        else:
            result[index] = 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return result


def true_ranges(candles: list[dict]) -> list[float]:
    """Oblicza True Range, używając poprzedniego rzeczywistego zamknięcia."""
    ranges = []
    for index, candle in enumerate(candles):
        high, low = float(candle["high"]), float(candle["low"])
        if index == 0:
            ranges.append(high - low)
        else:
            previous_close = float(candles[index - 1]["close"])
            ranges.append(
                max(
                    high - low,
                    abs(high - previous_close),
                    abs(low - previous_close),
                )
            )
    return ranges


def atr_wilder(candles: list[dict], period: int = config.ATR_PERIOD) -> list[float | None]:
    """Oblicza ATR Wildera; wartości przed pierwszym pełnym okresem są None."""
    if period < 1:
        raise ValueError("Okres ATR musi być dodatni.")
    ranges = true_ranges(candles)
    result: list[float | None] = [None] * len(ranges)
    if len(ranges) < period:
        return result
    current = sum(ranges[:period]) / period
    result[period - 1] = current
    for index in range(period, len(ranges)):
        current = (current * (period - 1) + ranges[index]) / period
        result[index] = current
    return result


def completed_five_minute_candles(
    candles: list[dict], cutoff: datetime
) -> list[dict]:
    """Składa zamknięte M5 z co najmniej czterech rzeczywistych świec M1."""
    cutoff_utc = as_utc(cutoff)
    buckets: dict[int, dict[int, dict]] = {}
    for candle in candles:
        opened = as_utc(candle["timestamp_utc"])
        if opened + config.CANDLE_DURATION > cutoff_utc:
            continue
        minute_epoch = (
            int(opened.timestamp()) // config.SECONDS_PER_MINUTE
        )
        bucket_id = minute_epoch // config.FIVE_MINUTE_PERIOD
        bucket_close = (
            (bucket_id + 1)
            * config.FIVE_MINUTE_PERIOD
            * config.SECONDS_PER_MINUTE
        )
        if bucket_close > cutoff_utc.timestamp():
            continue
        buckets.setdefault(bucket_id, {})[minute_epoch] = candle

    completed = []
    for bucket_id, members in sorted(buckets.items()):
        first_minute = bucket_id * config.FIVE_MINUTE_PERIOD
        expected = [
            first_minute + offset
            for offset in range(config.FIVE_MINUTE_PERIOD)
        ]
        present_minutes = [
            minute for minute in expected if minute in members
        ]
        if len(present_minutes) < config.MINIMUM_M1_CANDLES_PER_M5:
            continue
        rows = [members[minute] for minute in present_minutes]
        volumes = [row.get("volume") for row in rows]
        completed.append(
            {
                "timestamp_utc": datetime.fromtimestamp(
                    first_minute * config.SECONDS_PER_MINUTE, timezone.utc
                ),
                "open": float(rows[0]["open"]),
                "high": max(float(row["high"]) for row in rows),
                "low": min(float(row["low"]) for row in rows),
                "close": float(rows[-1]["close"]),
                "volume": (
                    sum(float(value) for value in volumes)
                    if all(value is not None for value in volumes)
                    else None
                ),
            }
        )
    return completed


def split_segments(candles: list[dict]) -> list[list[dict]]:
    """Dzieli historię po lukach obejmujących co najmniej dwie brakujące minuty."""
    if not candles:
        return []
    segments = [[candles[0]]]
    for candle in candles[1:]:
        previous = as_utc(segments[-1][-1]["timestamp_utc"])
        current = as_utc(candle["timestamp_utc"])
        if current - previous > 2 * config.CANDLE_DURATION:
            segments.append([candle])
        else:
            segments[-1].append(candle)
    return segments


def _bollinger_widths(closes: list[float]) -> list[float | None]:
    """Oblicza szerokość pasm Bollingera dla zakończonych okien BB_PERIOD."""
    widths: list[float | None] = [None] * len(closes)
    for index in range(config.BB_PERIOD - 1, len(closes)):
        window = closes[index - config.BB_PERIOD + 1 : index + 1]
        average = sum(window) / config.BB_PERIOD
        variance = sum((value - average) ** 2 for value in window) / config.BB_PERIOD
        deviation = variance**0.5
        widths[index] = (
            2.0 * config.BB_DEVIATIONS * deviation / average if average else None
        )
    return widths


def calculate_indicators(
    candles: list[dict], now: datetime
) -> tuple[dict, list[dict]]:
    """Oblicza ostatnie wskaźniki bieżącego segmentu i liczniki braków danych."""
    if not candles:
        return {}, []
    current_open = as_utc(candles[-1]["timestamp_utc"])
    current_close = current_open + config.CANDLE_DURATION
    segments = split_segments(candles)
    segment = segments[-1]
    closes = [float(row["close"]) for row in segment]
    highs = [float(row["high"]) for row in segment]
    lows = [float(row["low"]) for row in segment]
    fast = ema(closes, config.EMA_FAST)
    mid = ema(closes, config.EMA_MID)
    slow = ema(closes, config.EMA_SLOW)
    m1_rsi = rsi_wilder(closes)
    m1_atr = atr_wilder(segment)
    atr = m1_atr[-1]
    m5 = completed_five_minute_candles(segment, current_close)
    m5_closes = [float(row["close"]) for row in m5]
    m5_ema = ema(m5_closes, config.FIVE_MINUTE_EMA)
    widths = _bollinger_widths(closes)
    valid_widths = [
        value
        for value in widths[-config.BB_PERCENTILE_LOOKBACK :]
        if value is not None
    ]
    current_width = widths[-1]
    width_percentile = (
        100.0
        * sum(value <= current_width for value in valid_widths)
        / len(valid_widths)
        if current_width is not None and valid_widths
        else None
    )
    movement_window = closes[-config.ER_PERIOD - 1 :]
    path_length = sum(
        abs(right - left)
        for left, right in zip(movement_window, movement_window[1:])
    )
    efficiency_ratio = (
        abs(movement_window[-1] - movement_window[0]) / path_length
        if len(movement_window) == config.ER_PERIOD + 1 and path_length > 0
        else None
    )
    donchian_closes = closes[-config.DONCHIAN_PERIOD - 1 : -1]
    donchian_highs = highs[-config.DONCHIAN_PERIOD - 1 : -1]
    donchian_lows = lows[-config.DONCHIAN_PERIOD - 1 : -1]
    volume_values = [row.get("volume") for row in segment[-config.VOLUME_PERIOD :]]
    volume_sma = (
        sum(float(value) for value in volume_values) / config.VOLUME_PERIOD
        if config.VOLUME_RELIABLE
        and len(volume_values) == config.VOLUME_PERIOD
        and all(value is not None and isfinite(float(value)) for value in volume_values)
        else None
    )
    relative_volume = (
        float(volume_values[-1]) / volume_sma
        if volume_sma and volume_values and volume_values[-1] is not None
        else None
    )
    range_values = [float(row["high"]) - float(row["low"]) for row in segment]
    recent_ranges = range_values[-config.RANGE_MEDIAN_PERIOD - 1 : -1]
    range_median = median(recent_ranges) if recent_ranges else None
    range_spike_ratio = (
        range_values[-1] / range_median if range_median and range_median > 0 else None
    )
    now_utc = as_utc(now)
    minute_epoch = (
        int(current_open.timestamp()) // config.SECONDS_PER_MINUTE
    )
    expected_last_hour = {
        minute_epoch - offset
        for offset in range(1, config.GAP_LOOKBACK_MINUTES + 1)
    }
    present_last_hour = {
        int(as_utc(row["timestamp_utc"]).timestamp())
        // config.SECONDS_PER_MINUTE
        for row in candles
        if minute_epoch - config.GAP_LOOKBACK_MINUTES
        <= int(as_utc(row["timestamp_utc"]).timestamp())
        // config.SECONDS_PER_MINUTE
        < minute_epoch
    }
    missing_minutes = sorted(expected_last_hour - present_last_hour)
    max_consecutive_missing = consecutive = 0
    for minute in sorted(expected_last_hour):
        if minute in missing_minutes:
            consecutive += 1
            max_consecutive_missing = max(max_consecutive_missing, consecutive)
        else:
            consecutive = 0

    current = segment[-1]
    candle_range = float(current["high"]) - float(current["low"])
    body_ratio = (
        abs(float(current["close"]) - float(current["open"])) / candle_range
        if candle_range > 0
        else 0.0
    )
    typical_price = (
        float(current["high"]) + float(current["low"]) + float(current["close"])
    ) / 3.0
    session_open = current_close.replace(hour=0, minute=0, second=0, microsecond=0)
    session_rows = [
        row
        for row in candles
        if session_open <= as_utc(row["timestamp_utc"]) < current_close
    ]
    vwap = None
    if config.VOLUME_RELIABLE and session_rows and all(
        row.get("volume") is not None for row in session_rows
    ):
        total_volume = sum(float(row["volume"]) for row in session_rows)
        if total_volume > 0:
            vwap = sum(
                (
                    float(row["high"])
                    + float(row["low"])
                    + float(row["close"])
                )
                / 3.0
                * float(row["volume"])
                for row in session_rows
            ) / total_volume

    params = {
        "atr14_usd": atr,
        "atr14_bps": atr / closes[-1] * 10000 if atr is not None and closes[-1] else None,
        "atr_ratio": (
            atr
            / median(
                [
                    value
                    for value in m1_atr[-config.ATR_MEDIAN_PERIOD :]
                    if value is not None
                ]
            )
            if atr is not None
            and any(
                value is not None
                for value in m1_atr[-config.ATR_MEDIAN_PERIOD :]
            )
            else None
        ),
        "rsi5": m1_rsi[-1],
        "ema9": fast[-1],
        "ema21": mid[-1],
        "ema55": slow[-1],
        "ema20_5m": m5_ema[-1] if len(m5_ema) >= config.FIVE_MINUTE_EMA else None,
        "ema20_5m_slope": (
            m5_ema[-1] - m5_ema[-2] if len(m5_ema) >= config.FIVE_MINUTE_EMA + 1 else None
        ),
        "trend_5m": (
            "UP"
            if len(m5_ema) >= config.FIVE_MINUTE_EMA
            and m5_closes[-1] > m5_ema[-1]
            else "DOWN"
            if len(m5_ema) >= config.FIVE_MINUTE_EMA
            and m5_closes[-1] < m5_ema[-1]
            else "NEUTRAL"
            if len(m5_ema) >= config.FIVE_MINUTE_EMA
            else None
        ),
        "efficiency_ratio_30": efficiency_ratio,
        "bb_zscore": (
            (closes[-1] - sum(closes[-config.BB_PERIOD :]) / config.BB_PERIOD)
            / (
                sum(
                    (
                        value
                        - sum(closes[-config.BB_PERIOD :]) / config.BB_PERIOD
                    )
                    ** 2
                    for value in closes[-config.BB_PERIOD :]
                )
                / config.BB_PERIOD
            )
            ** 0.5
            if len(closes) >= config.BB_PERIOD
            and any(closes[-config.BB_PERIOD :])
            and sum(
                (
                    value
                    - sum(closes[-config.BB_PERIOD :]) / config.BB_PERIOD
                )
                ** 2
                for value in closes[-config.BB_PERIOD :]
            )
            > 0
            else None
        ),
        "bb_width_percentile": width_percentile,
        "vwap": vwap,
        "vwap_distance_atr": (
            (closes[-1] - vwap) / atr if vwap is not None and atr else None
        ),
        "rel_volume": relative_volume,
        "body_ratio": body_ratio,
        "donchian_high_20": max(donchian_highs) if len(donchian_highs) == config.DONCHIAN_PERIOD else None,
        "donchian_low_20": min(donchian_lows) if len(donchian_lows) == config.DONCHIAN_PERIOD else None,
        "range_spike_ratio": range_spike_ratio,
        "missing_candles_60m": len(missing_minutes),
        "max_consecutive_missing": max_consecutive_missing,
        "data_age_minutes": max(0.0, (now_utc - current_close).total_seconds() / 60.0),
        "segment_candles": len(segment),
        "m5_candles": len(m5),
        "volume_sma20": volume_sma,
        "macd_line": None,
        "macd_signal": None,
        "macd_histogram": None,
        "candle_range": candle_range,
        "typical_price": typical_price,
        "_ema9_series": fast[-config.PULLBACK_LOOKBACK :],
        "_ema21_series": mid[-config.PULLBACK_LOOKBACK :],
        "_rsi_series": m1_rsi[-config.PULLBACK_LOOKBACK :],
        "_macd_histogram_previous": None,
    }
    if len(closes) >= config.MACD_SLOW:
        macd_line = [
            fast_value - slow_value
            for fast_value, slow_value in zip(
                ema(closes, config.MACD_FAST),
                ema(closes, config.MACD_SLOW),
            )
        ]
        macd_signal = ema(macd_line, config.MACD_SIGNAL)
        macd_histograms = [
            line - signal for line, signal in zip(macd_line, macd_signal)
        ]
        params.update(
            macd_line=macd_line[-1],
            macd_signal=macd_signal[-1],
            macd_histogram=macd_histograms[-1],
            _macd_histogram_previous=(
                macd_histograms[-2] if len(macd_histograms) > 1 else None
            ),
        )
    if len(m1_rsi) > 1:
        params["rsi5_previous"] = m1_rsi[-2]
    else:
        params["rsi5_previous"] = None
    params["previous_close"] = closes[-2] if len(closes) > 1 else None
    params["previous_ema9"] = fast[-2] if len(fast) > 1 else None
    params["previous_ema21"] = mid[-2] if len(mid) > 1 else None
    params["recent_lows"] = lows[-config.PULLBACK_LOOKBACK :]
    params["recent_highs"] = highs[-config.PULLBACK_LOOKBACK :]
    params["bb_widths_recent"] = valid_widths
    return params, segment
