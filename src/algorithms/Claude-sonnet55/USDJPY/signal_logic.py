"""Filtry, setupy, punktacja i wynik strategii USD/JPY."""

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

import config
from indicators import aggregate_m5, anchored_vwap, build_m1_indicators
from schedule import (
    LONDON,
    NEW_YORK,
    TOKYO,
    UTC,
    WARSAW,
    blackout_codes,
    expected_candle_opens,
    is_early_close,
    is_holiday,
    is_trading_minute,
    minutes_since_london_open,
    minutes_since_ny_open,
    minutes_since_tokyo_open,
    minutes_until_market_close,
    segment_id,
    session_name,
    signal_window_open,
)

Candle = dict[str, Any]


def _normalize_candles(candles: list[Candle], now: datetime) -> list[Candle]:
    """Sprawdza OHLCV, odrzuca świece formowane i porządkuje dane chronologicznie."""
    if not isinstance(candles, list):
        raise ValueError("Dane wejściowe muszą być listą świec.")
    normalized = []
    seen = set()
    for row in candles:
        if not isinstance(row, dict):
            raise ValueError("Każda świeca musi być słownikiem.")
        stamp = row.get("timestamp_utc")
        if (
            not isinstance(stamp, datetime)
            or stamp.tzinfo is None
            or stamp.utcoffset() is None
        ):
            raise ValueError("Czas otwarcia świecy musi zawierać strefę czasową.")
        stamp = stamp.astimezone(UTC)
        if stamp.second or stamp.microsecond or stamp in seen:
            raise ValueError("Czas świecy musi być unikalną pełną minutą.")
        seen.add(stamp)
        try:
            values = {
                key: float(row[key]) for key in ("open", "high", "low", "close")
            }
            volume = None if row.get("volume") is None else float(row["volume"])
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise ValueError("Niepoprawne OHLC lub wolumen świecy.") from error
        if (
            not all(isfinite(value) and value > 0 for value in values.values())
            or values["high"] < max(values["open"], values["close"], values["low"])
            or values["low"] > min(values["open"], values["close"])
            or (volume is not None and (not isfinite(volume) or volume < 0))
        ):
            raise ValueError("Niepoprawny zakres OHLC lub wolumen.")
        if stamp + timedelta(seconds=config.CANDLE_DURATION_SECONDS) <= now:
            normalized.append(
                {"timestamp_utc": stamp, **values, "volume": volume}
            )
    return sorted(normalized, key=lambda candle: candle["timestamp_utc"])


def _finite(value: Any) -> float | None:
    """Zamienia pustą lub nieskończoną wartość liczbową na None."""
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _rounded(value: Any, decimals: int = config.INDICATOR_DECIMALS) -> float | None:
    """Zaokrągla skończoną liczbę; pustą wartość pozostawia jako None."""
    number = _finite(value)
    return round(number, decimals) if number is not None else None


def _round_price(value: float) -> float:
    """Zaokrągla cenę tak samo jak zapis MySQL, połową w górę."""
    quantum = Decimal(1).scaleb(-config.PRICE_DECIMALS)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def _missing_state(candles: list[Candle], latest_open: datetime) -> tuple[int, int]:
    """Liczy łączne luki i najdłuższą serię w ostatnich 60 minutach handlu."""
    start = latest_open - timedelta(minutes=config.GAP_LOOKBACK_MINUTES - 1)
    expected = expected_candle_opens(
        start, latest_open + timedelta(minutes=config.CANDLE_DURATION_SECONDS // 60)
    )
    present = {candle["timestamp_utc"] for candle in candles}
    missing = {stamp for stamp in expected if stamp not in present}
    longest = current = 0
    for stamp in expected:
        if stamp in present:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return len(missing), longest


def _tokyo_range(
    candles: list[Candle], tokyo_day
) -> tuple[float | None, float | None, float | None, int, float]:
    """Buduje zakres 09:00–12:00 JST, jeśli pokrycie minut wynosi co najmniej 80%."""
    start = datetime.combine(tokyo_day, config.TOKYO_RANGE_START_JST, tzinfo=TOKYO)
    end = datetime.combine(tokyo_day, config.TOKYO_RANGE_END_JST, tzinfo=TOKYO)
    expected = expected_candle_opens(start, end)
    selected = [
        candle
        for candle in candles
        if start.astimezone(UTC) <= candle["timestamp_utc"] < end.astimezone(UTC)
    ]
    coverage = (
        len({candle["timestamp_utc"] for candle in selected}) / len(expected)
        if expected
        else 0.0
    )
    if not selected or coverage < config.TOKYO_RANGE_MIN_COVERAGE:
        return None, None, None, len(selected), coverage
    high = max(candle["high"] for candle in selected)
    low = min(candle["low"] for candle in selected)
    return high, low, high - low, len(selected), coverage


def _range_spike_series(rows: pd.DataFrame) -> pd.Series:
    """Liczy średni zakres trzech świec względem mediany z 60 świec."""
    candle_ranges = rows["high"] - rows["low"]
    recent_average = candle_ranges.rolling(
        config.RANGE_SPIKE_LOOKBACK,
        min_periods=config.RANGE_SPIKE_LOOKBACK,
    ).mean()
    baseline = candle_ranges.rolling(
        config.RANGE_SPIKE_BASELINE,
        min_periods=config.RANGE_SPIKE_BASELINE,
    ).median()
    result = recent_average / baseline.replace(0, np.nan)
    result.index = pd.DatetimeIndex(rows["timestamp_utc"])
    return result


def _time_filters(opened: datetime) -> list[str]:
    """Zwraca wszystkie harmonogramowe blokady dla czasu zamknięcia świecy."""
    closed = opened + timedelta(seconds=config.CANDLE_DURATION_SECONDS)
    codes = []
    if is_holiday(opened):
        codes.append("holiday")
    if is_early_close(opened):
        codes.append("early_close")
    if not is_trading_minute(opened):
        codes.append("scheduled_break")
    if not signal_window_open(closed):
        codes.append("outside_session")
    warsaw = closed.astimezone(WARSAW)
    if (
        warsaw.weekday() == 4
        and warsaw.time() >= config.FRIDAY_LAST_SIGNAL_PL
    ):
        codes.append("friday_cutoff")
    minutes_to_close = minutes_until_market_close(opened)
    if minutes_to_close is not None and 0 <= minutes_to_close <= config.PRE_BREAK_BUFFER_MIN:
        codes.append("pre_break_buffer")
    codes.extend(blackout_codes(opened))
    return list(dict.fromkeys(codes))


def _reason(codes: list[str], direction: str, setup: str | None) -> str:
    """Buduje jedno zdanie po polsku opisujące sygnał albo blokadę."""
    if codes == ["no_setup"]:
        return "Warunki LONG ani SHORT nie zostały spełnione."
    if codes:
        descriptions = [
            config.BLOCKED_REASON_PL.get(code, "wystąpiła skonfigurowana blokada")
            for code in codes
        ]
        return "Zablokowano: " + "; ".join(dict.fromkeys(descriptions)) + "."
    if direction == "NONE" or setup is None:
        return "Warunki LONG ani SHORT nie zostały spełnione."
    descriptions = {
        "RANGE_BREAKOUT": "wybicie zakresu tokijskiego i potwierdzenie M5",
        "TREND_PULLBACK": "odbicie od EMA21 i potwierdzenie RSI(5)",
        "MEAN_REVERSION": "powrót ceny do wnętrza wstęg i w stronę VWAP",
    }
    return (
        f"Spełnione warunki setupu {setup} ({direction}): "
        f"{descriptions[setup]}."
    )


def _score_candidate(
    setup: str,
    direction: str,
    current: pd.Series,
    m5: pd.Series | None,
) -> tuple[int, list[str]]:
    """Punktuje poprawny rdzeń setupu dodatkowymi warunkami jakości."""
    score = config.SCORE_CORE
    reasons = []
    relative_volume = _finite(current.get("rel_volume"))
    if (
        relative_volume is not None
        and relative_volume >= config.RELATIVE_VOLUME_BONUS_MIN
    ):
        score += config.SCORE_RELATIVE_VOLUME_BONUS
        reasons.append("wolumen względny co najmniej 1,2")
    stamp = current["timestamp_utc"]
    london = stamp.astimezone(LONDON)
    new_york = stamp.astimezone(NEW_YORK)
    overlap = (
        config.NEW_YORK_DATA_OPEN
        <= new_york.time()
        < config.LIQUIDITY_OVERLAP_END_ET
        and config.LONDON_OPEN <= london.time() < config.LONDON_SESSION_END
    )
    london_window = (
        config.RANGE_BREAKOUT_LONDON_START
        <= london.time()
        < config.RANGE_BREAKOUT_LONDON_END
    )
    if overlap or london_window:
        score += config.SCORE_LIQUIDITY_BONUS
        reasons.append("okno najwyższej płynności")
    if m5 is not None:
        close_5m = float(m5["close"])
        ema_5m = _finite(m5.get("ema20_5m"))
        if setup == "TREND_PULLBACK":
            er = _finite(current.get("efficiency_ratio_30"))
            aligned = er is not None and er > config.PULLBACK_ER_BONUS_MIN
        else:
            aligned = ema_5m is not None and (
                close_5m > ema_5m if direction == "LONG" else close_5m < ema_5m
            )
        if aligned:
            score += config.SCORE_CONFIRMATION_BONUS
            reasons.append("zgodność z trendem lub reżimem")
    body_ratio = _finite(current.get("body_ratio"))
    if (
        body_ratio is not None
        and body_ratio >= config.CANDLE_QUALITY_BODY_RATIO_MIN
    ):
        score += config.SCORE_CANDLE_QUALITY_BONUS
        reasons.append("mocny korpus świecy")
    return score, reasons


def _detect_setups(
    rows: pd.DataFrame,
    m5_rows: pd.DataFrame,
    vwap: float | None,
    tokyo_range: tuple[float | None, float | None, float | None, int, float],
    tokyo_day,
) -> list[dict[str, Any]]:
    """Wykrywa zgodne z regułami setupy LONG i SHORT."""
    current = rows.iloc[-1]
    if len(rows) < 2:
        return []
    previous = rows.iloc[-2]
    atr = _finite(current.get("atr14"))
    er = _finite(current.get("efficiency_ratio_30"))
    if atr is None:
        return []
    price = float(current["close"])
    m5 = m5_rows.iloc[-1] if not m5_rows.empty else None
    close_5m = _finite(m5.get("close")) if m5 is not None else None
    ema_5m = _finite(m5.get("ema20_5m")) if m5 is not None else None
    slope_5m = _finite(m5.get("ema20_5m_slope")) if m5 is not None else None
    candle_close = current["timestamp_utc"] + timedelta(minutes=1)
    m5_age = (
        (
            candle_close
            - (m5["timestamp_utc"] + timedelta(minutes=config.M5_BUCKET_MINUTES))
        ).total_seconds()
        / config.SECONDS_PER_MINUTE
        if m5 is not None
        else None
    )
    m5_fresh = (
        m5_age is not None
        and 0 <= m5_age <= config.M5_TREND_STALE_MAX_MINUTES
    )
    m5_up = (
        m5_fresh and close_5m is not None and ema_5m is not None
        and close_5m > ema_5m
    )
    m5_down = (
        m5_fresh and close_5m is not None and ema_5m is not None
        and close_5m < ema_5m
    )
    tokyo_time = current["timestamp_utc"].astimezone(TOKYO).time()
    london_time = current["timestamp_utc"].astimezone(LONDON).time()
    new_york_time = current["timestamp_utc"].astimezone(NEW_YORK).time()
    range_high, range_low, range_width, _, _ = tokyo_range
    candidates = []

    if (
        config.RANGE_BREAKOUT_LONDON_START
        <= london_time
        < config.RANGE_BREAKOUT_LONDON_END
        and tokyo_day <= current["timestamp_utc"].astimezone(TOKYO).date()
        and range_high is not None
        and range_low is not None
        and range_width is not None
        and config.RANGE_MIN_PIPS
        <= range_width / config.PIP_SIZE
        <= config.RANGE_MAX_PIPS
    ):
        for direction in ("LONG", "SHORT"):
            is_long = direction == "LONG"
            boundary = range_high if is_long else range_low
            broke_first = (
                price > boundary + config.RANGE_BREAKOUT_ATR_BUFFER * atr
                and float(previous["close"]) <= boundary
                if is_long
                else price < boundary - config.RANGE_BREAKOUT_ATR_BUFFER * atr
                and float(previous["close"]) >= boundary
            )
            directional = (
                price > float(current["open"])
                if is_long
                else price < float(current["open"])
            )
            extension = price - boundary if is_long else boundary - price
            valid = (
                broke_first
                and directional
                and float(current["body_ratio"])
                >= config.RANGE_BREAKOUT_BODY_RATIO_MIN
                and _finite(current.get("rel_volume")) is not None
                and float(current["rel_volume"])
                >= config.RANGE_BREAKOUT_REL_VOLUME_MIN
                and vwap is not None
                and (price > vwap if is_long else price < vwap)
                and (m5_up if is_long else m5_down)
                and extension <= config.RANGE_BREAKOUT_MAX_EXTENSION_ATR * atr
            )
            if valid:
                score, bonuses = _score_candidate(
                    "RANGE_BREAKOUT", direction, current, m5 if m5_fresh else None
                )
                candidates.append(
                    {
                        "setup": "RANGE_BREAKOUT",
                        "direction": direction,
                        "score": score,
                        "reasons": [
                            "pierwsze wybicie zakresu Tokio",
                            "potwierdzenie trendu 5M",
                            *bonuses,
                        ],
                    }
                )

    pullback_window = (
        config.PULLBACK_TOKYO_START <= tokyo_time < config.PULLBACK_TOKYO_END
        or config.PULLBACK_NEW_YORK_START
        <= new_york_time
        < config.PULLBACK_NEW_YORK_END
    )
    if (
        pullback_window
        and er is not None
        and er >= config.PULLBACK_ER_MIN
        and len(rows) >= config.WARMUP_PULLBACK
    ):
        recent = rows.tail(config.PULLBACK_LOOKBACK_CANDLES)
        prior_rsi = (
            rows["rsi5"]
            .iloc[-(config.PULLBACK_PRIOR_RSI_CANDLES + 1) : -1]
            .dropna()
        )
        fast = _finite(current.get(f"ema{config.EMA_FAST_PERIOD}"))
        middle = _finite(current.get(f"ema{config.EMA_MIDDLE_PERIOD}"))
        slow = _finite(current.get(f"ema{config.EMA_SLOW_PERIOD}"))
        near_ema_long = bool(
            (
                (recent["low"] - recent[f"ema{config.EMA_MIDDLE_PERIOD}"]).abs()
                <= config.PULLBACK_PROXIMITY_ATR * atr
            ).any()
        )
        near_ema_short = bool(
            (
                (recent["high"] - recent[f"ema{config.EMA_MIDDLE_PERIOD}"]).abs()
                <= config.PULLBACK_PROXIMITY_ATR * atr
            ).any()
        )
        long_valid = (
            m5_up
            and slope_5m is not None
            and slope_5m > 0
            and fast is not None
            and middle is not None
            and slow is not None
            and fast > middle > slow
            and vwap is not None
            and price > vwap
            and near_ema_long
            and not prior_rsi.empty
            and float(prior_rsi.min()) < config.PULLBACK_RSI_LOW
            and _finite(current.get("rsi5")) is not None
            and float(current["rsi5"]) > config.PULLBACK_RSI_HIGH
            and price > float(current["open"])
            and float(current["body_ratio"]) >= config.PULLBACK_BODY_RATIO_MIN
            and price > float(previous["close"])
        )
        short_valid = (
            m5_down
            and slope_5m is not None
            and slope_5m < 0
            and fast is not None
            and middle is not None
            and slow is not None
            and fast < middle < slow
            and vwap is not None
            and price < vwap
            and near_ema_short
            and not prior_rsi.empty
            and float(prior_rsi.max()) > config.PULLBACK_RSI_SHORT_HIGH
            and _finite(current.get("rsi5")) is not None
            and float(current["rsi5"]) < config.PULLBACK_RSI_SHORT_LOW
            and price < float(current["open"])
            and float(current["body_ratio"]) >= config.PULLBACK_BODY_RATIO_MIN
            and price < float(previous["close"])
        )
        for direction, valid in (("LONG", long_valid), ("SHORT", short_valid)):
            if valid:
                score, bonuses = _score_candidate(
                    "TREND_PULLBACK", direction, current, m5 if m5_fresh else None
                )
                candidates.append(
                    {
                        "setup": "TREND_PULLBACK",
                        "direction": direction,
                        "score": score,
                        "reasons": [
                            "uporządkowane EMA na M1 i trend 5M",
                            "odbicie i zwrot RSI(5)",
                            *bonuses,
                        ],
                    }
                )

    if (
        er is not None
        and er <= config.REVERSION_ER_MAX
        and len(rows) >= config.WARMUP_REVERSION
    ):
        slow_slope = _finite(
            rows[f"ema{config.EMA_SLOW_PERIOD}"].diff(
                config.EMA_SLOPE_LOOKBACK
            ).iloc[-1]
        )
        previous_z = _finite(previous.get("bb_zscore"))
        current_z = _finite(current.get("bb_zscore"))
        lower = _finite(current.get("bb_lower"))
        upper = _finite(current.get("bb_upper"))
        distance_vwap = abs(vwap - price) if vwap is not None else 0.0
        recent_rsi = rows["rsi5"].tail(config.REVERSION_RSI_LOOKBACK).dropna()
        candle_range = float(current["high"] - current["low"])
        lower_wick = min(float(current["open"]), price) - float(current["low"])
        upper_wick = float(current["high"]) - max(float(current["open"]), price)
        long_valid = (
            slow_slope is not None
            and abs(slow_slope) < config.REVERSION_SLOPE_ATR_MAX * atr
            and previous_z is not None
            and previous_z <= -config.REVERSION_ZSCORE_THRESHOLD
            and current_z is not None
            and lower is not None
            and upper is not None
            and lower < price < upper
            and len(recent_rsi) == config.REVERSION_RSI_LOOKBACK
            and float(recent_rsi.max()) <= config.REVERSION_RSI_THRESHOLD
            and vwap is not None
            and vwap - price >= config.REVERSION_VWAP_DISTANCE_ATR * atr
            and price > float(current["open"])
            and candle_range > 0
            and lower_wick / candle_range >= config.REVERSION_WICK_RATIO_MIN
        )
        short_valid = (
            slow_slope is not None
            and abs(slow_slope) < config.REVERSION_SLOPE_ATR_MAX * atr
            and previous_z is not None
            and previous_z >= config.REVERSION_ZSCORE_THRESHOLD
            and current_z is not None
            and lower is not None
            and upper is not None
            and lower < price < upper
            and len(recent_rsi) == config.REVERSION_RSI_LOOKBACK
            and float(recent_rsi.min()) >= config.REVERSION_RSI_SHORT_THRESHOLD
            and vwap is not None
            and price - vwap >= config.REVERSION_VWAP_DISTANCE_ATR * atr
            and price < float(current["open"])
            and candle_range > 0
            and upper_wick / candle_range >= config.REVERSION_WICK_RATIO_MIN
        )
        for direction, valid in (("LONG", long_valid), ("SHORT", short_valid)):
            if valid:
                score, bonuses = _score_candidate(
                    "MEAN_REVERSION", direction, current, m5 if m5_fresh else None
                )
                candidates.append(
                    {
                        "setup": "MEAN_REVERSION",
                        "direction": direction,
                        "score": score,
                        "reasons": [
                            "powrót ceny do wnętrza wstęg",
                            "skrajny RSI(5) i dystans do VWAP",
                            *bonuses,
                        ],
                    }
                )
    return candidates


def _build_params(
    rows: pd.DataFrame,
    m5_rows: pd.DataFrame,
    now: datetime,
    vwap: float | None,
    vwap_start: datetime,
    tokyo_range: tuple[float | None, float | None, float | None, int, float],
    missing_count: int,
    longest_missing: int,
    spike_series: pd.Series,
) -> dict[str, Any]:
    """Buduje wszystkie wskaźniki i dane diagnostyczne wyniku."""
    current = rows.iloc[-1]
    stamp = current["timestamp_utc"]
    closed = stamp + timedelta(minutes=1)
    tokyo = stamp.astimezone(TOKYO)
    london = stamp.astimezone(LONDON)
    new_york = stamp.astimezone(NEW_YORK)
    atr = _finite(current.get("atr14"))
    atr_median = rows["atr14"].dropna().tail(config.ATR_RATIO_LOOKBACK)
    median_atr = float(atr_median.median()) if not atr_median.empty else None
    m5 = m5_rows.iloc[-1] if not m5_rows.empty else None
    m5_end = (
        m5["timestamp_utc"] + timedelta(minutes=config.M5_BUCKET_MINUTES)
        if m5 is not None
        else None
    )
    m5_age = (
        (closed - m5_end).total_seconds() / config.SECONDS_PER_MINUTE
        if m5_end is not None
        else None
    )
    trend = "UNKNOWN"
    if m5 is not None and m5_age is not None and 0 <= m5_age <= config.M5_TREND_STALE_MAX_MINUTES:
        if m5["close"] > m5["ema20_5m"]:
            trend = "UP"
        elif m5["close"] < m5["ema20_5m"]:
            trend = "DOWN"
        else:
            trend = "FLAT"
    range_high, range_low, range_width, tokyo_count, tokyo_coverage = tokyo_range
    recent_spikes = spike_series.loc[
        spike_series.index >= stamp - timedelta(minutes=config.SPIKE_COOLDOWN_MINUTES)
    ]
    last_spike = [
        spike_time
        for spike_time, ratio in recent_spikes.items()
        if _finite(ratio) is not None and float(ratio) > config.RANGE_SPIKE_MAX_RATIO
    ]
    current_range_ratio = _finite(spike_series.iloc[-1]) if len(spike_series) else None
    params = {
        "atr14_pips": atr / config.PIP_SIZE if atr is not None else None,
        "atr_ratio": _finite(current.get("atr_ratio")),
        "atr_median_200": median_atr,
        "rsi5": _finite(current.get("rsi5")),
        "ema9": _finite(current.get(f"ema{config.EMA_FAST_PERIOD}")),
        "ema21": _finite(current.get(f"ema{config.EMA_MIDDLE_PERIOD}")),
        "ema55": _finite(current.get(f"ema{config.EMA_SLOW_PERIOD}")),
        "ema20_5m": _finite(m5.get("ema20_5m")) if m5 is not None else None,
        "ema20_5m_slope": _finite(m5.get("ema20_5m_slope")) if m5 is not None else None,
        "close5m": _finite(m5.get("close")) if m5 is not None else None,
        "trend_5m": trend,
        "efficiency_ratio_30": _finite(current.get("efficiency_ratio_30")),
        "bb_zscore": _finite(current.get("bb_zscore")),
        "bb_upper": _finite(current.get("bb_upper")),
        "bb_middle": _finite(current.get("bb_middle")),
        "bb_lower": _finite(current.get("bb_lower")),
        "vwap": vwap,
        "vwap_distance_atr": (
            (float(current["close"]) - vwap) / atr
            if vwap is not None and atr not in (None, 0)
            else None
        ),
        "vwap_session_start": vwap_start.isoformat(),
        "rel_volume": _finite(current.get("rel_volume")),
        "body_ratio": _finite(current.get("body_ratio")),
        "range_high": range_high,
        "range_low": range_low,
        "range_width_pips": (
            range_width / config.PIP_SIZE if range_width is not None else None
        ),
        "tokyo_range_candles": tokyo_count,
        "tokyo_range_coverage": tokyo_coverage,
        "session_name": session_name(stamp),
        "minutes_since_tokyo_open": minutes_since_tokyo_open(stamp),
        "minutes_since_london_open": minutes_since_london_open(stamp),
        "minutes_since_ny_open": minutes_since_ny_open(stamp),
        "missing_candles_60m": missing_count,
        "max_consecutive_missing": longest_missing,
        "data_age_minutes": (
            now - closed
        ).total_seconds() / config.SECONDS_PER_MINUTE,
        "m5_data_age_minutes": m5_age,
        "segment_candles": len(rows),
        "segment_start": rows.iloc[0]["timestamp_utc"].astimezone(WARSAW).isoformat(),
        "segment_start_time": rows.iloc[0]["timestamp_utc"].astimezone(WARSAW).strftime("%H:%M:%S"),
        "warmup_breakout_required": config.WARMUP_BREAKOUT,
        "warmup_breakout_ready": len(rows) >= config.WARMUP_BREAKOUT,
        "breakout_available_from": (
            rows.iloc[config.WARMUP_BREAKOUT - 1]["timestamp_utc"]
            .astimezone(WARSAW)
            .isoformat()
            if len(rows) >= config.WARMUP_BREAKOUT
            else None
        ),
        "warmup_pullback_required": config.WARMUP_PULLBACK,
        "warmup_pullback_ready": len(rows) >= config.WARMUP_PULLBACK,
        "pullback_available_from": (
            rows.iloc[config.WARMUP_PULLBACK - 1]["timestamp_utc"]
            .astimezone(WARSAW)
            .isoformat()
            if len(rows) >= config.WARMUP_PULLBACK
            else None
        ),
        "warmup_reversion_required": config.WARMUP_REVERSION,
        "warmup_reversion_ready": len(rows) >= config.WARMUP_REVERSION,
        "reversion_available_from": (
            rows.iloc[config.WARMUP_REVERSION - 1]["timestamp_utc"]
            .astimezone(WARSAW)
            .isoformat()
            if len(rows) >= config.WARMUP_REVERSION
            else None
        ),
        "tokyo_date": tokyo.date().isoformat(),
        "london_date": london.date().isoformat(),
        "new_york_date": new_york.date().isoformat(),
        "candle_open": float(current["open"]),
        "candle_high": float(current["high"]),
        "candle_low": float(current["low"]),
        "candle_close": float(current["close"]),
        "candle_volume": _finite(current.get("volume")),
        "candle_range_pips": (
            float(current["high"] - current["low"]) / config.PIP_SIZE
        ),
        "range_spike_ratio": current_range_ratio,
        "spike_cooldown_active": bool(last_spike),
        "spike_last_time": (
            last_spike[-1].astimezone(WARSAW).isoformat() if last_spike else None
        ),
        "yield_correlation_available": False,
        "yield_correlation": None,
        "us_yield_data_note": "Brak danych rentowności; wejściem są wyłącznie świece USD/JPY.",
        "previous_close": (
            _finite(rows.iloc[-2].get("close")) if len(rows) > 1 else None
        ),
        "previous_rsi5": (
            _finite(rows.iloc[-2].get("rsi5")) if len(rows) > 1 else None
        ),
    }
    return {
        key: _rounded(value)
        if isinstance(value, (float, np.floating))
        else value
        for key, value in params.items()
    }


def generate_signal(
    candles: list[Candle],
    now: datetime | None = None,
    signal_history: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Tworzy JSON wyniku dla ostatniej zamkniętej świecy M1.

    Args:
        candles: Świece OHLCV z czasem otwarcia świadomym strefy.
        now: Świadomy czas UTC do testów lub domyślnie bieżący czas.
        signal_history: Ostatni sygnał i limity pobrane ze storage.

    Returns:
        Słownik z sygnałem, poziomami, wskaźnikami i powodami.

    Raises:
        ValueError: Gdy wejściowe świece lub czas są niepoprawne.
    """
    now = datetime.now(UTC) if now is None else now
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise ValueError("Czas analizy musi zawierać strefę czasową.")
    now = now.astimezone(UTC)
    if not isinstance(candles, list):
        raise ValueError("Dane wejściowe muszą być listą świec.")
    normalized = _normalize_candles(candles[-config.HISTORY_LIMIT :], now)
    if not normalized:
        raise ValueError("Brak zamkniętych świec do analizy.")
    latest = normalized[-1]
    opened = latest["timestamp_utc"]
    closed = opened + timedelta(seconds=config.CANDLE_DURATION_SECONDS)
    segment = segment_id(opened)
    segment_candles = [
        candle
        for candle in normalized
        if segment_id(candle["timestamp_utc"]) == segment
    ]
    rows = pd.DataFrame(segment_candles).sort_values("timestamp_utc").reset_index(drop=True)
    rows["volume"] = pd.to_numeric(rows["volume"], errors="coerce")
    rows = build_m1_indicators(rows)
    m5_rows = aggregate_m5(rows, now)
    vwap, vwap_start = anchored_vwap(rows, opened)
    tokyo_day = opened.astimezone(TOKYO).date()
    tokyo_range = _tokyo_range(normalized, tokyo_day)
    missing_count, longest_missing = _missing_state(normalized, opened)
    spike_series = _range_spike_series(rows)
    params = _build_params(
        rows,
        m5_rows,
        now,
        vwap,
        vwap_start,
        tokyo_range,
        missing_count,
        longest_missing,
        spike_series,
    )

    blocked = _time_filters(opened)
    if params["data_age_minutes"] is None or params["data_age_minutes"] > config.STALE_MAX_MINUTES:
        blocked.append("stale_data")
    if (
        longest_missing > config.MAX_CONSECUTIVE_MISSING
        or missing_count > config.MAX_MISSING_IN_60
    ):
        blocked.append("data_gap")
    current = rows.iloc[-1]
    atr_pips = _finite(current.get("atr14_pips"))
    atr_ratio = _finite(current.get("atr_ratio"))
    if atr_pips is None or not config.ATR_MIN_PIPS <= atr_pips <= config.ATR_MAX_PIPS:
        blocked.append("atr_out_of_range")
    if atr_ratio is None or not config.ATR_RATIO_MIN <= atr_ratio <= config.ATR_RATIO_MAX:
        blocked.append("atr_ratio_out_of_range")
    atr = _finite(current.get("atr14"))
    if (
        atr is not None
        and float(current["high"] - current["low"])
        > config.CANDLE_RANGE_ATR_MAX * atr
    ):
        blocked.append("candle_anomaly")
    relative_volume = _finite(current.get("rel_volume"))
    if relative_volume is None or relative_volume < config.RELATIVE_VOLUME_HARD_MIN:
        blocked.append("low_relative_volume")
    if params["spike_cooldown_active"]:
        blocked.append("spike_cooldown")

    history = signal_history or {}
    close_epoch = int(closed.timestamp())
    last_signal_epoch = history.get("last_signal_epoch")
    if (
        isinstance(last_signal_epoch, int)
        and not isinstance(last_signal_epoch, bool)
        and close_epoch - last_signal_epoch
        < config.COOLDOWN_MINUTES * config.SECONDS_PER_MINUTE
    ):
        blocked.append("cooldown")
    if int(history.get("signals_today", 0)) >= config.MAX_SIGNALS_PER_DAY:
        blocked.append("daily_limit")
    blocked = list(dict.fromkeys(blocked))

    london_time = opened.astimezone(LONDON).time()
    new_york_time = opened.astimezone(NEW_YORK).time()
    breakout_window = (
        config.RANGE_BREAKOUT_LONDON_START
        <= london_time
        < config.RANGE_BREAKOUT_LONDON_END
    )
    pullback_window = (
        config.PULLBACK_TOKYO_START
        <= opened.astimezone(TOKYO).time()
        < config.PULLBACK_TOKYO_END
        or config.PULLBACK_NEW_YORK_START
        <= new_york_time
        < config.PULLBACK_NEW_YORK_END
    )
    required_warmups = []
    if breakout_window:
        required_warmups.append(config.WARMUP_BREAKOUT)
    if pullback_window:
        required_warmups.append(config.WARMUP_PULLBACK)
    required_warmups.append(config.WARMUP_REVERSION)
    if not blocked and len(rows) < min(required_warmups):
        blocked.append("warmup")

    candidates = []
    if not blocked:
        candidates = _detect_setups(rows, m5_rows, vwap, tokyo_range, tokyo_day)
        allowed_breakouts = history.get("range_breakout_directions_today", [])
        if candidates:
            retained = [
                candidate
                for candidate in candidates
                if candidate["setup"] != "RANGE_BREAKOUT"
                or candidate["direction"] not in allowed_breakouts
            ]
            if not retained and len(retained) != len(candidates):
                blocked.append("setup_limit")
            candidates = retained
        if not candidates:
            er = _finite(current.get("efficiency_ratio_30"))
            if "setup_limit" in blocked:
                pass
            elif (
                pullback_window
                and len(rows) < config.WARMUP_PULLBACK
                and (er is None or er > config.REVERSION_ER_MAX)
            ):
                blocked.append("warmup")
            elif er is not None and config.UNCLEAR_ER_MIN < er < config.UNCLEAR_ER_MAX:
                blocked.append("regime_unclear")
            elif breakout_window and tokyo_range[4] < config.TOKYO_RANGE_MIN_COVERAGE:
                blocked.append("warmup")
            else:
                blocked.append("no_setup")
        else:
            directions = {candidate["direction"] for candidate in candidates}
            if len(directions) > 1:
                blocked.append("conflicting_setups")
                candidates = []
            elif len(candidates) > 1:
                candidates.sort(key=lambda candidate: candidate["score"], reverse=True)

    chosen = candidates[0] if candidates else None
    direction = chosen["direction"] if chosen else "NONE"
    setup = chosen["setup"] if chosen else None
    score = int(chosen["score"]) if chosen else 0
    reasons = list(chosen["reasons"]) if chosen else []
    tp_price = sl_price = None
    if chosen is not None:
        atr = _finite(current.get("atr14"))
        if atr is None:
            blocked.append("warmup")
            chosen = None
            direction = "NONE"
            setup = None
            score = 0
        else:
            tp_distance = config.TAKE_PROFIT_ATR_MULTIPLE * atr
            if setup == "MEAN_REVERSION" and vwap is not None:
                tp_distance = min(tp_distance, abs(vwap - float(current["close"])))
            minimum_profit = max(
                config.MIN_TAKE_PROFIT_PIPS,
                config.COST_MULTIPLE * config.ASSUMED_COST_PIPS,
            ) * config.PIP_SIZE
            if tp_distance / config.PIP_SIZE < minimum_profit / config.PIP_SIZE:
                blocked.append("cost_filter")
                chosen = None
                direction = "NONE"
                setup = None
                score = 0
            elif score < config.MIN_SCORE:
                blocked.append("low_score")
                chosen = None
                direction = "NONE"
                setup = None
                score = 0
            else:
                price = float(current["close"])
                if direction == "LONG":
                    tp_price = price + tp_distance
                    sl_price = price - config.STOP_LOSS_ATR_MULTIPLE * atr
                else:
                    tp_price = price - tp_distance
                    sl_price = price + config.STOP_LOSS_ATR_MULTIPLE * atr
    if chosen is None:
        reasons = []
    blocked = list(dict.fromkeys(blocked))
    reason = _reason(blocked, direction, setup)
    if chosen is not None:
        reason = _reason([], direction, setup)
    local_open = opened.astimezone(WARSAW)
    output = {
        "instrument": config.INSTRUMENT_NAME,
        "price": _round_price(float(current["close"])),
        "timestamp": close_epoch,
        "candle_open_time": local_open.isoformat(),
        "signal": direction,
        "setup": setup,
        "score": score,
        "tp_price": (
            _round_price(tp_price) if tp_price is not None else None
        ),
        "sl_price": (
            _round_price(sl_price) if sl_price is not None else None
        ),
        "max_hold_minutes": config.MAX_HOLD_MINUTES,
        "params": params,
        "reasons": reasons,
        "blocked_by": blocked,
        "reason": reason,
    }
    return output
