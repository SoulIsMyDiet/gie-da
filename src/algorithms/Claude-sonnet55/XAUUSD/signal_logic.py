"""Filtry, setupy, punktacja oraz wynik strategii XAU/USD."""

from datetime import datetime, time, timedelta
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

from config import (
    ATR_MAX_USD,
    ATR_MIN_USD,
    ATR_RATIO_MAX,
    ATR_RATIO_MIN,
    ATR_RATIO_LOOKBACK,
    ASSUMED_COST_USD,
    BLOCKED_REASON_PL,
    CANDLE_QUALITY_BODY_RATIO_MIN,
    CANDLE_RANGE_ATR_MAX,
    COOLDOWN_MINUTES,
    COST_MULTIPLE,
    CANDLE_DURATION_SECONDS,
    EARLY_CLOSE_DATES,
    LIQUIDITY_LONDON_END,
    LIQUIDITY_LONDON_START,
    LIQUIDITY_OVERLAP_END,
    LIQUIDITY_OVERLAP_START,
    EMA_FAST_PERIOD,
    EMA_MIDDLE_PERIOD,
    EMA_SLOPE_LOOKBACK,
    EMA_SLOW_PERIOD,
    FRIDAY_LAST_SIGNAL_PL,
    GAP_LOOKBACK_MINUTES,
    HISTORY_LIMIT,
    INDICATOR_DECIMALS,
    INSTRUMENT_NAME,
    MAX_CONSECUTIVE_MISSING,
    MAX_HOLD_MINUTES,
    MAX_MISSING_IN_60,
    MAX_SIGNALS_PER_DAY,
    M5_BUCKET_MINUTES,
    M5_TREND_STALE_MAX_MINUTES,
    MIN_SCORE,
    MIN_TAKE_PROFIT_USD,
    PULLBACK_BODY_RATIO_MIN,
    PULLBACK_ER_MIN,
    PULLBACK_LONDON_END,
    PULLBACK_LONDON_START,
    PULLBACK_LOOKBACK_CANDLES,
    PULLBACK_NEW_YORK_END,
    PULLBACK_NEW_YORK_START,
    PULLBACK_PRIOR_RSI_CANDLES,
    PULLBACK_PROXIMITY_ATR,
    PULLBACK_RSI_HIGH,
    PULLBACK_RSI_LOW,
    PULLBACK_RSI_SHORT_HIGH,
    PULLBACK_RSI_SHORT_LOW,
    PRICE_DECIMALS,
    PRE_BREAK_BUFFER_MIN,
    MARKET_CLOSE_ET,
    RANGE_BREAKOUT_BODY_RATIO_MIN,
    RANGE_BREAKOUT_LONDON_END,
    RANGE_BREAKOUT_LONDON_START,
    RANGE_BREAKOUT_REL_VOLUME_MIN,
    RANGE_ASIA_END,
    RANGE_ASIA_MIN_COVERAGE,
    RANGE_ASIA_START,
    RANGE_BREAKOUT_ATR_BUFFER,
    RANGE_BREAKOUT_MAX_EXTENSION_ATR,
    RANGE_MAX_USD,
    RANGE_MIN_USD,
    RELATIVE_VOLUME_HARD_MIN,
    RELATIVE_VOLUME_BONUS_MIN,
    REVERSION_ER_MAX,
    REVERSION_RSI_SHORT_THRESHOLD,
    REVERSION_RSI_LOOKBACK,
    REVERSION_RSI_THRESHOLD,
    REVERSION_SLOPE_ATR_MAX,
    REVERSION_VWAP_DISTANCE_ATR,
    REVERSION_WICK_RATIO_MIN,
    REVERSION_ZSCORE_THRESHOLD,
    SCORE_BONUS,
    SCORE_CORE,
    SECONDS_PER_MINUTE,
    STALE_MAX_MINUTES,
    WARMUP_BREAKOUT,
    WARMUP_PULLBACK,
    WARMUP_REVERSION,
    UNCLEAR_ER_MAX,
    UNCLEAR_ER_MIN,
    PULLBACK_ER_BONUS_MIN,
    TAKE_PROFIT_ATR_MULTIPLE,
    STOP_LOSS_ATR_MULTIPLE,
    TREND_PULLBACK_SCORE_DESCRIPTION,
    MEAN_REVERSION_SCORE_DESCRIPTION,
)
from indicators import (
    aggregate_m5,
    anchored_vwap,
    build_m1_indicators,
)
from schedule import (
    LONDON,
    NEW_YORK,
    UTC,
    WARSAW,
    blackout_codes,
    expected_candle_opens,
    is_early_close,
    is_holiday,
    is_trading_minute,
    minutes_since_london_open,
    minutes_since_ny_open,
    session_name,
    signal_window_open,
)

Candle = dict[str, Any]


def _normalize_candles(candles: list[Candle], now: datetime) -> list[Candle]:
    """Odrzuca świece formowane; zachowuje najnowszą zamkniętą do diagnostyki."""
    if not isinstance(candles, list):
        raise ValueError("Dane wejściowe muszą być listą świec.")
    normalized = []
    latest_outside_schedule = None
    seen = set()
    for row in candles:
        if not isinstance(row, dict):
            raise ValueError("Każda świeca musi być słownikiem.")
        stamp = row.get("timestamp_utc")
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("Czas otwarcia świecy musi zawierać strefę czasową.")
        stamp = stamp.astimezone(UTC)
        if stamp.second or stamp.microsecond or stamp in seen:
            raise ValueError("Czas świecy musi być unikalną pełną minutą.")
        seen.add(stamp)
        try:
            values = {key: float(row[key]) for key in ("open", "high", "low", "close")}
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
        if stamp + timedelta(minutes=1) <= now:
            candle = {"timestamp_utc": stamp, **values, "volume": volume}
            if is_trading_minute(stamp):
                normalized.append(candle)
            elif (
                latest_outside_schedule is None
                or stamp > latest_outside_schedule["timestamp_utc"]
            ):
                latest_outside_schedule = candle
    latest_trading_open = max(
        (candle["timestamp_utc"] for candle in normalized),
        default=None,
    )
    if latest_outside_schedule is not None and (
        latest_trading_open is None
        or latest_outside_schedule["timestamp_utc"] > latest_trading_open
    ):
        normalized.append(latest_outside_schedule)
    return sorted(normalized, key=lambda candle: candle["timestamp_utc"])


def _missing_state(candles: list[Candle], latest_open: datetime) -> tuple[int, int]:
    """Liczy brakujące minuty handlu i najdłuższą serię z ostatnich 60 minut."""
    window_start = latest_open - timedelta(minutes=GAP_LOOKBACK_MINUTES - 1)
    expected = expected_candle_opens(window_start, latest_open + timedelta(minutes=1))
    present = {candle["timestamp_utc"] for candle in candles}
    missing = [stamp for stamp in expected if stamp not in present]
    longest = current = 0
    for stamp in expected:
        if stamp in present:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return len(missing), longest


def _asian_range(
    candles: list[Candle], london_day: datetime.date
) -> tuple[float | None, float | None, float | None, int, float]:
    """Wyznacza dzienny zakres Azji i pokrycie minut zgodnych z harmonogramem."""
    start = datetime.combine(london_day, RANGE_ASIA_START, tzinfo=LONDON)
    end = datetime.combine(london_day, RANGE_ASIA_END, tzinfo=LONDON)
    expected = expected_candle_opens(start, end)
    selected = [
        candle
        for candle in candles
        if start.astimezone(UTC) <= candle["timestamp_utc"] < end.astimezone(UTC)
    ]
    coverage = len({candle["timestamp_utc"] for candle in selected}) / len(expected) if expected else 0.0
    if not selected or coverage < RANGE_ASIA_MIN_COVERAGE:
        return None, None, None, len(selected), coverage
    high = max(candle["high"] for candle in selected)
    low = min(candle["low"] for candle in selected)
    return high, low, high - low, len(selected), coverage


def _finite_or_none(value: Any) -> float | None:
    """Zamienia pustą lub nieskończoną wartość liczbową na None."""
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _reason(codes: list[str], direction: str, setup: str | None) -> str:
    """Buduje pojedyncze zdanie po polsku opisujące wynik lub blokadę."""
    if codes == ["no_setup"]:
        return "Warunki LONG ani SHORT nie zostały spełnione."
    if codes:
        descriptions = [BLOCKED_REASON_PL.get(code, "wystąpiła skonfigurowana blokada") for code in codes]
        return "Zablokowano: " + "; ".join(dict.fromkeys(descriptions)) + "."
    if setup is None or direction == "NONE":
        return "Warunki LONG ani SHORT nie zostały spełnione."
    labels = {
        "RANGE_BREAKOUT": "wybicie zakresu Azji",
        "TREND_PULLBACK": TREND_PULLBACK_SCORE_DESCRIPTION,
        "MEAN_REVERSION": MEAN_REVERSION_SCORE_DESCRIPTION,
    }
    return f"Spełnione warunki setupu {setup} ({direction}): {labels[setup]}."


def _time_filters(instant: datetime) -> list[str]:
    """Zwraca wszystkie blokady harmonogramu dla czasu zamknięcia świecy."""
    codes = []
    if is_holiday(instant):
        codes.append("holiday")
    if is_early_close(instant):
        codes.append("early_close")
    if not is_trading_minute(instant):
        codes.append("scheduled_break")
    if not signal_window_open(instant):
        codes.append("outside_session")
    if instant.astimezone(WARSAW).weekday() == 4 and instant.astimezone(WARSAW).time() > FRIDAY_LAST_SIGNAL_PL:
        codes.append("friday_cutoff")
    new_york = instant.astimezone(NEW_YORK)
    market_close = EARLY_CLOSE_DATES.get(new_york.date(), MARKET_CLOSE_ET)
    current_minute = new_york.hour * 60 + new_york.minute
    market_close_minute = market_close.hour * 60 + market_close.minute
    if (
        market_close_minute - PRE_BREAK_BUFFER_MIN
        <= current_minute
        < market_close_minute
    ):
        codes.append("pre_break_buffer")
    configured = blackout_codes(instant)
    codes.extend(configured)
    return list(dict.fromkeys(codes))


def _body_is_bullish(row: pd.Series) -> bool:
    """Sprawdza, czy świeca zamknęła się powyżej otwarcia."""
    return float(row["close"]) > float(row["open"])


def _candidate_score(
    setup: str,
    direction: str,
    row: pd.Series,
    m5_row: pd.Series | None,
) -> tuple[int, list[str]]:
    """Liczy rdzeń 60 punktów i cztery możliwe premie po spełnieniu setupu."""
    score = SCORE_CORE
    bonuses = []
    rel_volume = _finite_or_none(row.get("rel_volume"))
    if rel_volume is not None and rel_volume >= RELATIVE_VOLUME_BONUS_MIN:
        score += SCORE_BONUS
        bonuses.append("wolumen względny co najmniej 1,2")
    london = row["timestamp_utc"].astimezone(LONDON)
    overlap = (
        LIQUIDITY_OVERLAP_START
        <= row["timestamp_utc"].astimezone(NEW_YORK).time()
        < LIQUIDITY_OVERLAP_END
    )
    london_liquidity = LIQUIDITY_LONDON_START <= london.time() < LIQUIDITY_LONDON_END
    if overlap or london_liquidity:
        score += SCORE_BONUS
        bonuses.append("okno najwyższej płynności")
    if m5_row is not None:
        m5_close = float(m5_row["close"])
        m5_ema = _finite_or_none(m5_row.get("ema20_5m"))
        if setup == "TREND_PULLBACK":
            er = _finite_or_none(row.get("efficiency_ratio_30"))
            trend_bonus = er is not None and er > PULLBACK_ER_BONUS_MIN
        else:
            trend_bonus = m5_ema is not None and (
                m5_close > m5_ema if direction == "LONG" else m5_close < m5_ema
            )
        if trend_bonus:
            score += SCORE_BONUS
            bonuses.append("zgodność z trendem")
    body = _finite_or_none(row.get("body_ratio"))
    if body is not None and body >= CANDLE_QUALITY_BODY_RATIO_MIN:
        score += SCORE_BONUS
        bonuses.append("mocny korpus świecy")
    return score, bonuses


def _detect_setups(
    rows: pd.DataFrame,
    m5_rows: pd.DataFrame,
    vwap: float | None,
    range_high: float | None,
    range_low: float | None,
    range_width: float | None,
) -> list[dict[str, Any]]:
    """Zwraca kandydatów LONG/SHORT spełniających pełne warunki setupów."""
    current = rows.iloc[-1]
    previous = rows.iloc[-2] if len(rows) > 1 else None
    atr = _finite_or_none(current.get("atr14_usd"))
    if atr is None or previous is None:
        return []
    price = float(current["close"])
    rsi_now = _finite_or_none(current.get("rsi5"))
    er = _finite_or_none(current.get("efficiency_ratio_30"))
    candidates = []
    m5 = m5_rows.iloc[-1] if not m5_rows.empty else None
    m5_close = _finite_or_none(m5.get("close")) if m5 is not None else None
    m5_ema = _finite_or_none(m5.get("ema20_5m")) if m5 is not None else None
    m5_slope = _finite_or_none(m5.get("ema20_5m_slope")) if m5 is not None else None
    m5_age = (
        (
            current["timestamp_utc"] + timedelta(seconds=CANDLE_DURATION_SECONDS)
            - (m5["timestamp_utc"] + timedelta(minutes=M5_BUCKET_MINUTES))
        ).total_seconds() / SECONDS_PER_MINUTE
        if m5 is not None
        else None
    )
    m5_fresh = (
        m5_age is not None
        and 0 <= m5_age <= M5_TREND_STALE_MAX_MINUTES
    )
    m5_up = m5_fresh and m5_close is not None and m5_ema is not None and m5_close > m5_ema
    m5_down = m5_fresh and m5_close is not None and m5_ema is not None and m5_close < m5_ema

    london_time = current["timestamp_utc"].astimezone(LONDON).time()
    if (
        RANGE_BREAKOUT_LONDON_START <= london_time < RANGE_BREAKOUT_LONDON_END
        and len(rows) >= WARMUP_BREAKOUT
        and range_high is not None
        and range_low is not None
        and range_width is not None
        and RANGE_MIN_USD <= range_width <= RANGE_MAX_USD
    ):
        for direction in ("LONG", "SHORT"):
            long = direction == "LONG"
            boundary = range_high if long else range_low
            first_break = (
                price > boundary + RANGE_BREAKOUT_ATR_BUFFER * atr
                and float(previous["close"]) <= boundary
                if long
                else price < boundary - RANGE_BREAKOUT_ATR_BUFFER * atr
                and float(previous["close"]) >= boundary
            )
            directional_candle = _body_is_bullish(current) if long else float(current["close"]) < float(current["open"])
            trend_aligned = m5_up if long else m5_down
            extension = (price - boundary) if long else (boundary - price)
            valid = (
                first_break
                and directional_candle
                and float(current["body_ratio"]) >= RANGE_BREAKOUT_BODY_RATIO_MIN
                and _finite_or_none(current.get("rel_volume")) is not None
                and float(current["rel_volume"]) >= RANGE_BREAKOUT_REL_VOLUME_MIN
                and vwap is not None
                and (price > vwap if long else price < vwap)
                and trend_aligned
                and extension <= RANGE_BREAKOUT_MAX_EXTENSION_ATR * atr
            )
            if valid:
                score, bonuses = _candidate_score(
                    "RANGE_BREAKOUT", direction, current, m5 if m5_fresh else None
                )
                candidates.append({"setup": "RANGE_BREAKOUT", "direction": direction,
                                   "score": score, "bonuses": bonuses,
                                   "conditions": ["pierwsze wybicie zakresu Azji", "potwierdzenie trendu M5"]})

    ny_time = current["timestamp_utc"].astimezone(NEW_YORK).time()
    pullback_window = (
        PULLBACK_LONDON_START <= london_time < PULLBACK_LONDON_END
        or PULLBACK_NEW_YORK_START <= ny_time < PULLBACK_NEW_YORK_END
    )
    if pullback_window and er is not None and er >= PULLBACK_ER_MIN and len(rows) >= WARMUP_PULLBACK:
        last_five = rows.tail(PULLBACK_LOOKBACK_CANDLES)
        prior_rsi_start = -(PULLBACK_PRIOR_RSI_CANDLES + 1)
        last_three_rsi = rows["rsi5"].iloc[prior_rsi_start:-1].dropna()
        fast = _finite_or_none(current.get(f"ema{EMA_FAST_PERIOD}"))
        middle = _finite_or_none(current.get(f"ema{EMA_MIDDLE_PERIOD}"))
        slow = _finite_or_none(current.get(f"ema{EMA_SLOW_PERIOD}"))
        near_ema_long = bool(
            ((last_five["low"] - last_five[f"ema{EMA_MIDDLE_PERIOD}"]).abs()
             <= PULLBACK_PROXIMITY_ATR * atr).any()
        )
        near_ema_short = bool(
            ((last_five["high"] - last_five[f"ema{EMA_MIDDLE_PERIOD}"]).abs()
             <= PULLBACK_PROXIMITY_ATR * atr).any()
        )
        long_core = (
            m5_up and m5_slope is not None and m5_slope > 0
            and fast is not None and middle is not None and slow is not None
            and fast > middle > slow and price > (vwap if vwap is not None else float("inf"))
            and near_ema_long and len(last_three_rsi) > 0 and float(last_three_rsi.min()) < PULLBACK_RSI_LOW
            and rsi_now is not None and rsi_now > PULLBACK_RSI_HIGH
            and _body_is_bullish(current) and float(current["body_ratio"]) >= PULLBACK_BODY_RATIO_MIN
            and price > float(previous["close"])
        )
        short_core = (
            m5_down and m5_slope is not None and m5_slope < 0
            and fast is not None and middle is not None and slow is not None
            and fast < middle < slow and price < (vwap if vwap is not None else float("-inf"))
            and near_ema_short and len(last_three_rsi) > 0 and float(last_three_rsi.max()) > PULLBACK_RSI_SHORT_HIGH
            and rsi_now is not None and rsi_now < PULLBACK_RSI_SHORT_LOW
            and float(current["close"]) < float(current["open"])
            and float(current["body_ratio"]) >= PULLBACK_BODY_RATIO_MIN
            and price < float(previous["close"])
        )
        for direction, valid in (("LONG", long_core), ("SHORT", short_core)):
            if valid:
                score, bonuses = _candidate_score(
                    "TREND_PULLBACK", direction, current, m5 if m5_fresh else None
                )
                candidates.append({"setup": "TREND_PULLBACK", "direction": direction,
                                   "score": score, "bonuses": bonuses,
                                   "conditions": ["uporządkowane EMA na M1 i trend M5", "odbicie i zwrot RSI(5)"]})

    if er is not None and er <= REVERSION_ER_MAX and len(rows) >= WARMUP_REVERSION:
        slope_values = rows[f"ema{EMA_SLOW_PERIOD}"].diff(EMA_SLOPE_LOOKBACK)
        slope = _finite_or_none(slope_values.iloc[-1])
        previous_z = _finite_or_none(previous.get("bb_zscore"))
        current_z = _finite_or_none(current.get("bb_zscore"))
        lower = _finite_or_none(current.get("bb_lower"))
        upper = _finite_or_none(current.get("bb_upper"))
        vwap_distance = abs(vwap - price) if vwap is not None else 0.0
        recent_rsi = rows["rsi5"].tail(REVERSION_RSI_LOOKBACK).dropna()
        body_low = min(float(current["open"]), float(current["close"])) - float(current["low"])
        body_high = float(current["high"]) - max(float(current["open"]), float(current["close"]))
        candle_range = float(current["high"]) - float(current["low"])
        long_core = (
            slope is not None and abs(slope) < REVERSION_SLOPE_ATR_MAX * atr
            and previous_z is not None and previous_z <= -REVERSION_ZSCORE_THRESHOLD
            and current_z is not None and lower is not None and upper is not None
            and lower < price < upper
            and len(recent_rsi) == 2 and float(recent_rsi.max()) <= REVERSION_RSI_THRESHOLD
            and vwap is not None and vwap - price >= REVERSION_VWAP_DISTANCE_ATR * atr
            and _body_is_bullish(current)
            and candle_range > 0 and body_low / candle_range >= REVERSION_WICK_RATIO_MIN
        )
        short_core = (
            slope is not None and abs(slope) < REVERSION_SLOPE_ATR_MAX * atr
            and previous_z is not None and previous_z >= REVERSION_ZSCORE_THRESHOLD
            and current_z is not None and upper is not None and lower is not None
            and lower < price < upper
            and len(recent_rsi) == 2 and float(recent_rsi.min()) >= REVERSION_RSI_SHORT_THRESHOLD
            and vwap is not None and price - vwap >= REVERSION_VWAP_DISTANCE_ATR * atr
            and float(current["close"]) < float(current["open"])
            and candle_range > 0 and body_high / candle_range >= REVERSION_WICK_RATIO_MIN
        )
        for direction, valid in (("LONG", long_core), ("SHORT", short_core)):
            if valid:
                score, bonuses = _candidate_score(
                    "MEAN_REVERSION", direction, current, m5 if m5_fresh else None
                )
                candidates.append({"setup": "MEAN_REVERSION", "direction": direction,
                                   "score": score, "bonuses": bonuses,
                                   "conditions": ["powrót ceny do wnętrza wstęg", "skrajny RSI(5) i dystans do VWAP"]})
    return candidates


def _round_result(value: Any, decimals: int = INDICATOR_DECIMALS) -> float | None:
    """Zaokrągla skończoną liczbę; puste wartości pozostawia jako None."""
    number = _finite_or_none(value)
    return round(number, decimals) if number is not None else None


def _build_params(
    rows: pd.DataFrame,
    m5_rows: pd.DataFrame,
    now: datetime,
    vwap: float | None,
    vwap_start: datetime,
    asian: tuple[float | None, float | None, float | None, int, float],
    missing_count: int,
    longest_missing: int,
) -> dict[str, Any]:
    """Buduje płaski zestaw wskaźników i diagnostyki dla wyniku."""
    current = rows.iloc[-1]
    previous = rows.iloc[-2] if len(rows) > 1 else None
    stamp = current["timestamp_utc"]
    london = stamp.astimezone(LONDON)
    new_york = stamp.astimezone(NEW_YORK)
    atr = _finite_or_none(current.get("atr14_usd"))
    median_atr = rows["atr14_usd"].dropna().tail(ATR_RATIO_LOOKBACK)
    median_atr_value = float(median_atr.median()) if not median_atr.empty else None
    m5 = m5_rows.iloc[-1] if not m5_rows.empty else None
    m5_age = (
        (now - (m5["timestamp_utc"] + timedelta(minutes=M5_BUCKET_MINUTES))).total_seconds()
        / SECONDS_PER_MINUTE
        if m5 is not None
        else None
    )
    range_high, range_low, range_width, asia_count, asia_coverage = asian
    params = {
        "atr14_usd": atr,
        "atr14_bps": _finite_or_none(current.get("atr14_bps")),
        "atr_ratio": _finite_or_none(current.get("atr_ratio")),
        "atr_median_200": median_atr_value,
        "rsi5": _finite_or_none(current.get("rsi5")),
        "ema9": _finite_or_none(current.get(f"ema{EMA_FAST_PERIOD}")),
        "ema21": _finite_or_none(current.get(f"ema{EMA_MIDDLE_PERIOD}")),
        "ema55": _finite_or_none(current.get(f"ema{EMA_SLOW_PERIOD}")),
        "ema20_5m": _finite_or_none(m5.get("ema20_5m")) if m5 is not None else None,
        "ema20_5m_slope": _finite_or_none(m5.get("ema20_5m_slope")) if m5 is not None else None,
        "close5m": _finite_or_none(m5.get("close")) if m5 is not None else None,
        "trend_5m": (
            "UP" if m5 is not None and m5_age is not None
            and m5_age <= M5_TREND_STALE_MAX_MINUTES
            and m5["close"] > m5["ema20_5m"]
            else "DOWN" if m5 is not None and m5_age is not None
            and m5_age <= M5_TREND_STALE_MAX_MINUTES
            and m5["close"] < m5["ema20_5m"]
            else "FLAT" if m5 is not None and m5_age is not None
            and m5_age <= M5_TREND_STALE_MAX_MINUTES
            else "UNKNOWN"
        ),
        "efficiency_ratio_30": _finite_or_none(current.get("efficiency_ratio_30")),
        "bb_zscore": _finite_or_none(current.get("bb_zscore")),
        "bb_upper": _finite_or_none(current.get("bb_upper")),
        "bb_middle": _finite_or_none(current.get("bb_middle")),
        "bb_lower": _finite_or_none(current.get("bb_lower")),
        "vwap": vwap,
        "vwap_distance_atr": (
            (float(current["close"]) - vwap) / atr
            if vwap is not None and atr not in (None, 0)
            else None
        ),
        "vwap_session_start": vwap_start.isoformat(),
        "rel_volume": _finite_or_none(current.get("rel_volume")),
        "body_ratio": _finite_or_none(current.get("body_ratio")),
        "range_high": range_high,
        "range_low": range_low,
        "range_width_usd": range_width,
        "asia_candles": asia_count,
        "asia_coverage": asia_coverage,
        "session_name": session_name(stamp),
        "minutes_since_london_open": minutes_since_london_open(stamp),
        "minutes_since_ny_open": minutes_since_ny_open(stamp),
        "missing_candles_60m": missing_count,
        "max_consecutive_missing": longest_missing,
        "data_age_minutes": (
            now - (stamp + timedelta(seconds=CANDLE_DURATION_SECONDS))
        ).total_seconds() / SECONDS_PER_MINUTE,
        "m5_data_age_minutes": m5_age,
        "segment_candles": len(rows),
        "segment_start": rows.iloc[0]["timestamp_utc"].astimezone(WARSAW).isoformat(),
        "segment_start_utc": rows.iloc[0]["timestamp_utc"].isoformat(),
        "segment_start_time": rows.iloc[0]["timestamp_utc"].astimezone(WARSAW).strftime(
            "%H:%M:%S"
        ),
        "warmup_breakout_required": WARMUP_BREAKOUT,
        "warmup_breakout_ready": len(rows) >= WARMUP_BREAKOUT,
        "breakout_available_from": (
            rows.iloc[WARMUP_BREAKOUT - 1]["timestamp_utc"].astimezone(WARSAW).isoformat()
            if len(rows) >= WARMUP_BREAKOUT
            else None
        ),
        "warmup_pullback_required": WARMUP_PULLBACK,
        "warmup_pullback_ready": len(rows) >= WARMUP_PULLBACK,
        "pullback_available_from": (
            rows.iloc[WARMUP_PULLBACK - 1]["timestamp_utc"].astimezone(WARSAW).isoformat()
            if len(rows) >= WARMUP_PULLBACK
            else None
        ),
        "warmup_reversion_required": WARMUP_REVERSION,
        "warmup_reversion_ready": len(rows) >= WARMUP_REVERSION,
        "reversion_available_from": (
            rows.iloc[WARMUP_REVERSION - 1]["timestamp_utc"].astimezone(WARSAW).isoformat()
            if len(rows) >= WARMUP_REVERSION
            else None
        ),
        "london_date": london.date().isoformat(),
        "new_york_date": new_york.date().isoformat(),
        "candle_range_usd": float(current["high"] - current["low"]),
        "candle_open": float(current["open"]),
        "candle_high": float(current["high"]),
        "candle_low": float(current["low"]),
        "candle_close": float(current["close"]),
        "candle_volume": _finite_or_none(current.get("volume")),
        "previous_close": _finite_or_none(previous.get("close")) if previous is not None else None,
        "previous_rsi5": _finite_or_none(previous.get("rsi5")) if previous is not None else None,
    }
    return {key: _round_result(value) if isinstance(value, (float, np.floating)) else value
            for key, value in params.items()}


def generate_signal(
    candles: list[Candle],
    now: datetime | None = None,
    signal_history: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Tworzy wynik strategii dla ostatniej zamkniętej świecy M1.

    Args:
        candles: Świece OHLCV z czasem otwarcia `timestamp_utc`.
        now: Świadomy czas testu lub bieżący czas UTC.
        signal_history: Stan wcześniejszych sygnałów LONG/SHORT z tej strategii.

    Returns:
        Słownik JSON z kierunkiem, poziomami ryzyka, parametrami i powodami.

    Raises:
        ValueError: Gdy czas albo wejściowe świece są niepoprawne.
    """
    if now is None:
        now = datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Czas analizy musi zawierać strefę czasową.")
    now = now.astimezone(UTC)
    rows_list = _normalize_candles(candles[-HISTORY_LIMIT:], now)
    if not rows_list:
        raise ValueError("Brak zamkniętych świec w godzinach handlu.")
    latest = rows_list[-1]
    opened = latest["timestamp_utc"]
    closed = opened + timedelta(minutes=1)
    from schedule import segment_id

    segment_key = segment_id(opened)
    segment_list = [
        candle for candle in rows_list
        if segment_id(candle["timestamp_utc"]) == segment_key
    ]
    rows = pd.DataFrame(segment_list)
    rows["volume"] = pd.to_numeric(rows["volume"], errors="coerce")
    rows = build_m1_indicators(rows)
    m5_rows = aggregate_m5(rows, now)
    vwap, vwap_start = anchored_vwap(rows, opened)
    asian = _asian_range(rows_list, opened.astimezone(LONDON).date())
    missing_count, longest_missing = _missing_state(rows_list, opened)
    params = _build_params(
        rows, m5_rows, now, vwap, vwap_start, asian,
        missing_count, longest_missing,
    )
    blocked = _time_filters(closed)
    current = rows.iloc[-1]
    if params["data_age_minutes"] is None or params["data_age_minutes"] > STALE_MAX_MINUTES:
        blocked.append("stale_data")
    if longest_missing > MAX_CONSECUTIVE_MISSING or missing_count > MAX_MISSING_IN_60:
        blocked.append("data_gap")
    london_now = opened.astimezone(LONDON).time()
    ny_now = opened.astimezone(NEW_YORK).time()
    breakout_window = (
        RANGE_BREAKOUT_LONDON_START <= london_now < RANGE_BREAKOUT_LONDON_END
    )
    pullback_window = (
        PULLBACK_LONDON_START <= london_now < PULLBACK_LONDON_END
        or PULLBACK_NEW_YORK_START <= ny_now < PULLBACK_NEW_YORK_END
    )
    required_warmup = (
        WARMUP_BREAKOUT
        if breakout_window
        else WARMUP_PULLBACK
        if pullback_window
        else WARMUP_REVERSION
    )
    if not blocked and len(rows) < required_warmup:
        blocked.append("warmup")
    atr = _finite_or_none(current.get("atr14_usd"))
    if not blocked:
        atr_ratio = _finite_or_none(current.get("atr_ratio"))
        if atr is None or not ATR_MIN_USD <= atr <= ATR_MAX_USD:
            blocked.append("atr_out_of_range")
        if atr_ratio is None or not ATR_RATIO_MIN <= atr_ratio <= ATR_RATIO_MAX:
            blocked.append("atr_ratio_out_of_range")
        if atr is not None and params["candle_range_usd"] > CANDLE_RANGE_ATR_MAX * atr:
            blocked.append("candle_anomaly")
        rel_volume = _finite_or_none(current.get("rel_volume"))
        if rel_volume is None or rel_volume < RELATIVE_VOLUME_HARD_MIN:
            blocked.append("low_relative_volume")
    history = signal_history or {}
    close_epoch = int(closed.timestamp())
    last_signal_epoch = history.get("last_signal_epoch")
    if (
        isinstance(last_signal_epoch, int)
        and not isinstance(last_signal_epoch, bool)
        and close_epoch - last_signal_epoch < COOLDOWN_MINUTES * SECONDS_PER_MINUTE
    ):
        blocked.append("cooldown")
    if int(history.get("signals_today", 0)) >= MAX_SIGNALS_PER_DAY:
        blocked.append("daily_limit")
    blocked = list(dict.fromkeys(blocked))

    candidates = []
    if not blocked:
        candidates = _detect_setups(
            rows, m5_rows, vwap, asian[0], asian[1], asian[2]
        )
        if not candidates:
            er = _finite_or_none(current.get("efficiency_ratio_30"))
            if er is not None and UNCLEAR_ER_MIN < er < UNCLEAR_ER_MAX:
                blocked.append("regime_unclear")
            elif (
                breakout_window
                and asian[4] < RANGE_ASIA_MIN_COVERAGE
            ):
                blocked.append("warmup")
            else:
                blocked.append("no_setup")
        else:
            directions = {item["direction"] for item in candidates}
            if len(directions) > 1:
                blocked.append("conflicting_setups")
            elif not any(item["score"] >= MIN_SCORE for item in candidates):
                blocked.append("low_score")

    direction = "NONE"
    setup = None
    score = 0
    tp_price = sl_price = None
    selected: dict[str, Any] | None = None
    if not blocked and candidates:
        qualifying = [item for item in candidates if item["score"] >= MIN_SCORE]
        used_directions = set(history.get("range_breakout_directions_today", []))
        available = [
            item for item in qualifying
            if item["setup"] != "RANGE_BREAKOUT"
            or item["direction"] not in used_directions
        ]
        if qualifying and not available:
            blocked.append("setup_limit")
        qualifying = available
        if not qualifying:
            blocked.append("low_score" if candidates else "no_setup")
        directions = {item["direction"] for item in qualifying}
        if len(directions) > 1:
            blocked.append("conflicting_setups")
        if not blocked:
            selected = max(qualifying, key=lambda item: item["score"])
            direction, setup, score = (
                selected["direction"], selected["setup"], selected["score"]
            )
            sign = 1 if direction == "LONG" else -1
            close = float(latest["close"])
            take_profit_distance = TAKE_PROFIT_ATR_MULTIPLE * float(atr)
            if setup == "MEAN_REVERSION" and vwap is not None:
                take_profit_distance = min(take_profit_distance, abs(vwap - close))
            minimum_distance = max(MIN_TAKE_PROFIT_USD, COST_MULTIPLE * ASSUMED_COST_USD)
            if take_profit_distance < minimum_distance:
                blocked.append("cost_filter")
            else:
                tp_price = round(close + sign * take_profit_distance, PRICE_DECIMALS)
                if abs(tp_price - close) < minimum_distance:
                    tp_price = round(
                        tp_price + sign * 10 ** -PRICE_DECIMALS,
                        PRICE_DECIMALS,
                    )
                sl_price = round(
                    close - sign * STOP_LOSS_ATR_MULTIPLE * float(atr),
                    PRICE_DECIMALS,
                )
                if min(tp_price, sl_price) <= 0:
                    blocked.append("candle_anomaly")
    if blocked:
        direction, setup, score, tp_price, sl_price = "NONE", None, 0, None, None
    reasons = []
    if selected is not None and not blocked:
        reasons.extend(selected["conditions"])
        reasons.extend(selected["bonuses"])
    result = {
        "instrument": INSTRUMENT_NAME,
        "price": round(float(latest["close"]), PRICE_DECIMALS),
        "timestamp": close_epoch,
        "candle_open_time": opened.astimezone(WARSAW).isoformat(),
        "signal": direction,
        "setup": setup,
        "score": score,
        "tp_price": tp_price,
        "sl_price": sl_price,
        "max_hold_minutes": MAX_HOLD_MINUTES,
        "params": params,
        "reasons": reasons,
        "blocked_by": blocked,
        "reason": _reason(blocked, direction, setup),
    }
    return _json_safe(result)


def _json_safe(value: Any) -> Any:
    """Zamienia typy numpy i NaN/inf na wartości do poprawnego JSON."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if isfinite(number) else None
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value
