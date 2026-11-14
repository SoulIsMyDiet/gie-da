"""Backend
Walidacja danych, filtrów rynkowych, setupów i punktacji sygnałów GER30.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any

from .config import (
    ASSUMED_COST_POINTS,
    BONUS_CANDLE_BODY_RATIO_MIN,
    BONUS_RELATIVE_VOLUME_MIN,
    ATR_MAX_POINTS,
    ATR_MIN_POINTS,
    ATR_RATIO_MAX,
    ATR_RATIO_MIN,
    BONUS_CANDLE_QUALITY_POINTS,
    BONUS_SESSION_POINTS,
    BONUS_TREND_POINTS,
    BONUS_VOLUME_POINTS,
    COOLDOWN_MINUTES,
    CORE_SETUP_POINTS,
    COST_MULTIPLE,
    GAP_LOOKBACK_TRADING_MINUTES,
    MAX_CANDLE_RANGE_ATR,
    MAX_HOLD_MINUTES,
    MAX_MISSING_CONSECUTIVE,
    MAX_MISSING_IN_60_TRADING_MINUTES,
    MAX_SIGNALS_PER_DAY,
    MAX_STALE_DATA_MINUTES,
    HIGH_LIQUIDITY_WINDOWS,
    MIN_RELATIVE_VOLUME,
    MIN_SCORE,
    MIN_TP_POINTS,
    US_EVENT_BLACKOUT_MINUTES,
    NEWS_BLACKOUTS,
    NEW_YORK_TIMEZONE,
    US_DATA_EVENT_TIMES,
    WARSAW_OPENING_BLACKOUT,
    WARSAW_CLOSING_BLACKOUT,
    ORB_SIGNAL_START,
    ORB_SIGNAL_END,
    ORB_MINIMUM_SEGMENT_CANDLES,
    ORB_BREAKOUT_BUFFER_ATR,
    ORB_MAX_EXTENSION_ATR,
    ORB_MAX_WIDTH_POINTS,
    ORB_MIN_RELATIVE_VOLUME,
    ORB_MIN_BODY_RATIO,
    ORB_MIN_WIDTH_POINTS,
    OPENING_RANGE_END,
    OPENING_RANGE_MIN_CANDLES,
    OPENING_RANGE_START,
    PULLBACK_ER_MIN,
    PULLBACK_MAX_EMA_DISTANCE_ATR,
    PULLBACK_LONG_RSI_MIN,
    PULLBACK_LONG_PRIOR_RSI_MAX,
    PULLBACK_MIN_BODY_RATIO,
    PULLBACK_SHORT_PRIOR_RSI_MIN,
    PULLBACK_SHORT_RSI_MAX,
    PULLBACK_WARMUP_CANDLES,
    TREND_PULLBACK_WINDOWS,
    REVERSION_BB_ZSCORE,
    REVERSION_ER_MAX,
    REVERSION_MAX_EMA55_SLOPE_ATR,
    REVERSION_RSI_LONG_MAX,
    REVERSION_RSI_SHORT_MIN,
    REVERSION_VWAP_DISTANCE_ATR,
    REJECTION_WICK_MIN_RATIO,
    REVERSION_WARMUP_CANDLES,
    RANGE_REVERSION_WINDOW,
    SIGNAL_WINDOW_END,
    SIGNAL_WINDOW_START,
    US_OVERLAP_START,
    SL_ATR_MULTIPLIER,
    STRONG_TREND_ER,
    TP_ATR_MULTIPLIER,
    WARSAW_TIMEZONE,
)
from .indicators import calculate_indicators
from .schedule import (
    expected_candle_opens,
    is_trading_minute,
    new_york_session_time,
    segment_id,
)

def _candle_time(candle: dict[str, Any]) -> datetime:
    """Zwraca otwarcie świecy jako świadomy znacznik UTC."""
    value = candle.get("timestamp_utc", candle.get("candle_open_time"))
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Każda świeca musi zawierać świadomy timestamp_utc.")
    return value.astimezone(timezone.utc)


def _gap_status(
    candles: list[dict[str, Any]],
) -> tuple[int, int, list[dict[str, Any]]]:
    """Liczy braki w oczekiwanych minutach i zwraca najnowszy ciągły segment."""
    ordered = sorted(candles, key=_candle_time)
    if not ordered:
        return 0, 0, []
    latest = _candle_time(ordered[-1])
    first = _candle_time(ordered[0])
    known_opens = {_candle_time(item) for item in ordered}
    expected = expected_candle_opens(first, latest + timedelta(minutes=1))
    missing = [opening for opening in expected if opening not in known_opens]
    recent = expected[-GAP_LOOKBACK_TRADING_MINUTES:]
    missing_recent = [opening for opening in recent if opening not in known_opens]
    longest = current = 0
    for opening in recent:
        if opening in known_opens:
            current = 0
        else:
            current += 1
            longest = max(longest, current)

    last_missing = missing[-1] if missing else None
    last_segment_id = segment_id(latest)
    segment = [
        item
        for item in ordered
        if _candle_time(item) > last_missing
        and segment_id(_candle_time(item)) == last_segment_id
    ] if last_missing else [
        item for item in ordered
        if segment_id(_candle_time(item)) == last_segment_id
    ]
    return len(missing_recent), longest, segment


def _opening_range(
    candles: list[dict[str, Any]], local_day: object
) -> tuple[float | None, float | None, int]:
    """Oblicza zakres 09:00–09:15 z danego dnia, nie uzupełniając braków."""
    members = []
    for item in candles:
        local_open = _candle_time(item).astimezone(WARSAW_TIMEZONE)
        if (
            local_open.date() == local_day
            and OPENING_RANGE_START <= local_open.timetz().replace(tzinfo=None)
            < OPENING_RANGE_END
        ):
            members.append(item)
    if len(members) < OPENING_RANGE_MIN_CANDLES:
        return None, None, len(members)
    return (
        max(float(item["high"]) for item in members),
        min(float(item["low"]) for item in members),
        len(members),
    )


def _in_blackout(local_close: datetime, close_utc: datetime) -> list[str]:
    """Zwraca nazwy aktywnych wyłączeń czasowych dla czasu zamknięcia świecy."""
    wall = local_close.timetz().replace(tzinfo=None)
    blocked = []
    if WARSAW_OPENING_BLACKOUT[0] <= wall <= WARSAW_OPENING_BLACKOUT[1]:
        blocked.append("warsaw_opening_blackout")
    if WARSAW_CLOSING_BLACKOUT[0] <= wall <= WARSAW_CLOSING_BLACKOUT[1]:
        blocked.append("warsaw_closing_blackout")
    ny_local = close_utc.astimezone(NEW_YORK_TIMEZONE)
    ny_minute = ny_local.hour * 60 + ny_local.minute
    for event_time in US_DATA_EVENT_TIMES:
        center = event_time.hour * 60 + event_time.minute
        # Odległość kołowa utrzymuje poprawne działanie również blisko północy.
        distance = min((ny_minute - center) % 1440, (center - ny_minute) % 1440)
        if distance <= US_EVENT_BLACKOUT_MINUTES:
            blocked.append("us_data_blackout")
            break
    for start, end in NEWS_BLACKOUTS:
        start_dt, end_dt = _as_local_datetime(start), _as_local_datetime(end)
        if start_dt <= local_close.replace(tzinfo=None) <= end_dt:
            blocked.append("news_blackout")
            break
    return blocked


def _as_local_datetime(value: object) -> datetime:
    """Normalizuje koniec przedziału informacyjnego do naiwnego czasu Warszawy."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise ValueError("NEWS_BLACKOUTS wymaga wartości datetime lub ISO.")
    if value.tzinfo is None:
        return value
    return value.astimezone(WARSAW_TIMEZONE).replace(tzinfo=None)


def _setup_candidates(
    candles: list[dict[str, Any]],
    indicators: dict[str, Any],
    close_time: datetime,
    opening_range: tuple[float | None, float | None, int],
) -> list[dict[str, Any]]:
    """Sprawdza dokładne warunki ORB, pullback i reversion."""
    current = candles[-1]
    previous = candles[-2] if len(candles) >= 2 else None
    open_price, close = float(current["open"]), float(current["close"])
    high, low = float(current["high"]), float(current["low"])
    atr = indicators["atr14_points"]
    if atr is None:
        return []
    local_time = close_time.astimezone(WARSAW_TIMEZONE).timetz().replace(tzinfo=None)
    relative_volume = indicators["relative_volume"]
    vwap = indicators["vwap"]
    ema5 = indicators.get("ema20_5m")
    ema5_previous = indicators.get("ema20_5m_previous")
    close5 = indicators.get("close_5m")
    candidates: list[dict[str, Any]] = []
    or_high, or_low, or_count = opening_range

    # Front/mix — ORB wymaga kompletnego zakresu, wolumenu i potwierdzeń M1/5M.
    if (
        or_high is not None
        and or_low is not None
        and ORB_SIGNAL_START <= local_time <= ORB_SIGNAL_END
        and or_count >= OPENING_RANGE_MIN_CANDLES
        and len(candles) >= ORB_MINIMUM_SEGMENT_CANDLES
    ):
        width = or_high - or_low
        if ORB_MIN_WIDTH_POINTS <= width <= ORB_MAX_WIDTH_POINTS:
            buffer = ORB_BREAKOUT_BUFFER_ATR * atr
            body_ratio = indicators["body_ratio"]
            if (
                previous is not None
                and relative_volume is not None
                and relative_volume >= ORB_MIN_RELATIVE_VOLUME
                and body_ratio >= ORB_MIN_BODY_RATIO
                and vwap is not None
                and ema5 is not None
                and close5 is not None
            ):
                if (
                    close > or_high + buffer
                    and float(previous["close"]) <= or_high
                    and close > open_price
                    and close > vwap
                    and close5 > ema5
                    and close - or_high <= ORB_MAX_EXTENSION_ATR * atr
                ):
                    candidates.append(
                        {"direction": "LONG", "setup": "OPENING_RANGE_BREAKOUT"}
                    )
                if (
                    close < or_low - buffer
                    and float(previous["close"]) >= or_low
                    and close < open_price
                    and close < vwap
                    and close5 < ema5
                    and or_low - close <= ORB_MAX_EXTENSION_ATR * atr
                ):
                    candidates.append(
                        {"direction": "SHORT", "setup": "OPENING_RANGE_BREAKOUT"}
                    )

    # Backend — trend pullback oceniany wyłącznie w dwóch dozwolonych oknach.
    in_pullback_window = any(
        start <= local_time <= end for start, end in TREND_PULLBACK_WINDOWS
    )
    efficiency = indicators.get("efficiency_ratio_30")
    ema9, ema21, ema55 = (
        indicators.get("ema9"),
        indicators.get("ema21"),
        indicators.get("ema55"),
    )
    if (
        in_pullback_window
        and len(candles) >= PULLBACK_WARMUP_CANDLES
        and efficiency is not None
        and efficiency >= PULLBACK_ER_MIN
        and previous is not None
        and all(
            value is not None
            for value in (ema9, ema21, ema55, ema5, ema5_previous, close5, vwap, atr)
        )
    ):
        recent_five = candles[-5:]
        near_long = min(abs(float(item["low"]) - ema21) for item in recent_five) <= atr * PULLBACK_MAX_EMA_DISTANCE_ATR
        near_short = min(abs(float(item["high"]) - ema21) for item in recent_five) <= atr * PULLBACK_MAX_EMA_DISTANCE_ATR
        prior_rsi = indicators["rsi5_prior_three"]
        rsi = indicators["rsi5"]
        body_ratio = indicators["body_ratio"]
        if (
            ema9 > ema21 > ema55
            and ema5 > ema5_previous
            and close5 > ema5
            and close > vwap
            and near_long
            and len(prior_rsi) == 3
            and any(value < PULLBACK_LONG_PRIOR_RSI_MAX for value in prior_rsi)
            and rsi is not None
            and rsi > PULLBACK_LONG_RSI_MIN
            and close > open_price
            and body_ratio >= PULLBACK_MIN_BODY_RATIO
            and close > float(previous["close"])
        ):
            candidates.append({"direction": "LONG", "setup": "TREND_PULLBACK"})
        if (
            ema9 < ema21 < ema55
            and ema5 < ema5_previous
            and close5 < ema5
            and close < vwap
            and near_short
            and len(prior_rsi) == 3
            and any(value > PULLBACK_SHORT_PRIOR_RSI_MIN for value in prior_rsi)
            and rsi is not None
            and rsi < PULLBACK_SHORT_RSI_MAX
            and close < open_price
            and body_ratio >= PULLBACK_MIN_BODY_RATIO
            and close < float(previous["close"])
        ):
            candidates.append({"direction": "SHORT", "setup": "TREND_PULLBACK"})

    # Backend — reversion wymaga powrotu do pasma po wcześniejszym wyjściu poza nie.
    if (
        RANGE_REVERSION_WINDOW[0] <= local_time <= RANGE_REVERSION_WINDOW[1]
        and len(candles) >= REVERSION_WARMUP_CANDLES
        and efficiency is not None
        and efficiency <= REVERSION_ER_MAX
        and previous is not None
        and atr is not None
        and vwap is not None
        and indicators["ema55"] is not None
        and indicators["ema55_10_bars_ago"] is not None
    ):
        slope = abs(indicators["ema55"] - indicators["ema55_10_bars_ago"])
        flat = slope <= atr * REVERSION_MAX_EMA55_SLOPE_ATR
        prior_z = indicators["bb_zscore_previous"]
        current_z = indicators["bb_zscore"]
        rsi_last_two = indicators["rsi5_last_two"]
        lower_wick = min(open_price, close) - low
        upper_wick = high - max(open_price, close)
        range_points = high - low
        if flat and prior_z is not None and current_z is not None and range_points > 0:
            if (
                prior_z <= -REVERSION_BB_ZSCORE
                and -REVERSION_BB_ZSCORE < current_z < REVERSION_BB_ZSCORE
                and len(rsi_last_two) == 2
                and all(value <= REVERSION_RSI_LONG_MAX for value in rsi_last_two)
                and close > open_price
                and close < vwap
                and (vwap - close) >= atr * REVERSION_VWAP_DISTANCE_ATR
                and lower_wick / range_points >= REJECTION_WICK_MIN_RATIO
            ):
                candidates.append({"direction": "LONG", "setup": "RANGE_REVERSION"})
            if (
                prior_z >= REVERSION_BB_ZSCORE
                and -REVERSION_BB_ZSCORE < current_z < REVERSION_BB_ZSCORE
                and len(rsi_last_two) == 2
                and all(value >= REVERSION_RSI_SHORT_MIN for value in rsi_last_two)
                and close < open_price
                and close > vwap
                and (close - vwap) >= atr * REVERSION_VWAP_DISTANCE_ATR
                and upper_wick / range_points >= REJECTION_WICK_MIN_RATIO
            ):
                candidates.append({"direction": "SHORT", "setup": "RANGE_REVERSION"})
    return candidates


def _score_candidate(
    candidate: dict[str, Any], indicators: dict[str, Any], local_time: datetime
) -> tuple[int, list[str]]:
    """Przyznaje bonusy dopiero po spełnieniu pełnych warunków setupu."""
    score = CORE_SETUP_POINTS
    reasons = ["setup_core_conditions_met"]
    if (
        indicators["relative_volume"] is not None
        and indicators["relative_volume"] >= BONUS_RELATIVE_VOLUME_MIN
    ):
        score += BONUS_VOLUME_POINTS
        reasons.append("bonus_relative_volume")
    wall = local_time.timetz().replace(tzinfo=None)
    if any(start <= wall <= end for start, end in HIGH_LIQUIDITY_WINDOWS):
        score += BONUS_SESSION_POINTS
        reasons.append("bonus_prime_session")
    direction = candidate["direction"]
    if candidate["setup"] == "TREND_PULLBACK":
        if indicators["efficiency_ratio_30"] is not None and indicators["efficiency_ratio_30"] > STRONG_TREND_ER:
            score += BONUS_TREND_POINTS
            reasons.append("bonus_strong_trend")
    else:
        expected_trend = "UP" if direction == "LONG" else "DOWN"
        if indicators["trend_5m"] == expected_trend:
            score += BONUS_TREND_POINTS
            reasons.append("bonus_five_minute_alignment")
    if indicators["body_ratio"] >= BONUS_CANDLE_BODY_RATIO_MIN:
        score += BONUS_CANDLE_QUALITY_POINTS
        reasons.append("bonus_candle_quality")
    return score, reasons


def generate_signal(
    candles: list[dict[str, Any]],
    now: datetime | None = None,
    last_signal_time: datetime | None = None,
    signals_today: int = 0,
    orb_directions_today: list[str] | None = None,
) -> dict[str, Any]:
    """Generuje pojedynczy wynik dla najnowszej zamkniętej minuty."""
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ValueError("Bieżący czas musi zawierać strefę czasową.")
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ordered = sorted(candles, key=_candle_time)
    if not is_trading_minute(now_utc):
        return _none_signal(
            ordered[-1] if ordered else None,
            "Bieżący czas wypada poza harmonogramem handlu.",
            ["outside_session"],
            now_utc,
        )
    if not ordered:
        return _none_signal(None, "Brak danych do rozgrzewki strategii.", ["warmup"], now_utc)

    last = ordered[-1]
    candle_open = _candle_time(last)
    candle_close = candle_open + timedelta(minutes=1)
    age = (now_utc - candle_close).total_seconds() / 60
    local_close = candle_close.astimezone(WARSAW_TIMEZONE)
    wall_time = local_close.timetz().replace(tzinfo=None)

    def blocked(reason: str, keys: list[str], params: dict[str, Any] | None = None, score: int = 0) -> dict[str, Any]:
        """Buduje wspólny format NONE dla każdego zablokowanego przypadku."""
        return _none_signal(last, reason, keys, now_utc, params, score)

    if now_utc < candle_close:
        return blocked("Świeca nie jest jeszcze zamknięta.", ["forming_candle"])
    if age > MAX_STALE_DATA_MINUTES:
        return blocked("Dane są starsze niż dozwolone 3 minuty.", ["stale_data"])
    if not is_trading_minute(candle_open):
        return blocked("Otwarcie świecy wypada poza harmonogramem.", ["outside_trading_hours"])
    if local_close.weekday() >= 5:
        return blocked("Sygnały są wyłączone w weekend.", ["weekend"])
    blackout_keys = _in_blackout(local_close, candle_close)
    if blackout_keys:
        return blocked("Aktywne wyłączenie czasowe.", blackout_keys)
    if wall_time < SIGNAL_WINDOW_START or wall_time > SIGNAL_WINDOW_END:
        return blocked("Poza warszawskim oknem sygnałów 09:05–17:25.", ["outside_signal_window"])

    missing_count, consecutive_missing, segment = _gap_status(ordered)
    if consecutive_missing > MAX_MISSING_CONSECUTIVE or missing_count > MAX_MISSING_IN_60_TRADING_MINUTES:
        return blocked(
            "Liczba brakujących minut przekracza dozwolony limit.",
            ["data_gap"],
            {
                "missing_candles_60m": missing_count,
                "maximum_consecutive_missing": consecutive_missing,
            },
        )
    if not segment or _candle_time(segment[-1]) != candle_open:
        return blocked("Brak bieżącej świecy w segmencie danych.", ["current_segment_missing"])

    session_candles = [
        item
        for item in ordered
        if _candle_time(item).astimezone(WARSAW_TIMEZONE).date()
        == local_close.date()
    ]
    indicators = calculate_indicators(
        segment,
        now_utc,
        vwap_candles=session_candles,
    )
    indicators["missing_candles_60m"] = missing_count
    indicators["maximum_consecutive_missing"] = consecutive_missing
    indicators["data_age_minutes"] = round(age, 4)
    indicators["segment_candles"] = len(segment)
    opening_time = local_close.replace(
        hour=OPENING_RANGE_START.hour,
        minute=OPENING_RANGE_START.minute,
        second=0,
        microsecond=0,
    )
    indicators["minutes_since_open"] = max(
        0, int((local_close - opening_time).total_seconds() // 60)
    )
    indicators["session_phase"] = (
        "PRE_OPENING_RANGE"
        if wall_time < ORB_SIGNAL_START
        else "OPENING_RANGE_BREAKOUT"
        if wall_time <= ORB_SIGNAL_END
        else "MID_SESSION"
        if wall_time < US_OVERLAP_START
        else "US_OVERLAP"
    )
    indicators["or_high"], indicators["or_low"], indicators["or_count"] = _opening_range(
        ordered, local_close.date()
    )
    indicators["or_width_points"] = (
        indicators["or_high"] - indicators["or_low"]
        if indicators["or_high"] is not None and indicators["or_low"] is not None
        else None
    )
    warmup_reasons = []
    if (
        ORB_SIGNAL_START <= wall_time <= ORB_SIGNAL_END
        and (
            len(segment) < ORB_MINIMUM_SEGMENT_CANDLES
            or indicators["or_count"] < OPENING_RANGE_MIN_CANDLES
        )
    ):
        warmup_reasons.append("opening_range")
    if (
        any(start <= wall_time <= end for start, end in TREND_PULLBACK_WINDOWS)
        and len(segment) < PULLBACK_WARMUP_CANDLES
    ):
        warmup_reasons.append("trend_pullback")
    if (
        RANGE_REVERSION_WINDOW[0] <= wall_time <= RANGE_REVERSION_WINDOW[1]
        and len(segment) < REVERSION_WARMUP_CANDLES
    ):
        warmup_reasons.append("range_reversion")
    if warmup_reasons:
        return blocked(
            "Brakuje świec wymaganych do rozgrzewki setupu.",
            ["warmup"],
            indicators,
        )
    if indicators.get("atr14_points") is None:
        return blocked("Za mało danych do wyliczenia ATR(14).", ["insufficient_atr_history"], indicators)

    hard_filters = []
    atr = indicators["atr14_points"]
    ratio = indicators["atr_ratio"]
    if not ATR_MIN_POINTS <= atr <= ATR_MAX_POINTS:
        hard_filters.append("atr_out_of_range")
    if ratio is None or not ATR_RATIO_MIN <= ratio <= ATR_RATIO_MAX:
        hard_filters.append("atr_ratio_out_of_range")
    if indicators["candle_range"] > atr * MAX_CANDLE_RANGE_ATR:
        hard_filters.append("candle_range_spike")
    if indicators["relative_volume"] is None or indicators["relative_volume"] < MIN_RELATIVE_VOLUME:
        hard_filters.append("relative_volume_below_minimum")
    if hard_filters:
        return blocked("Twardy filtr rynkowy nie został spełniony.", hard_filters, indicators)
    if signals_today >= MAX_SIGNALS_PER_DAY:
        return blocked("Osiągnięto dzienny limit sygnałów.", ["daily_signal_limit"], indicators)
    if last_signal_time is not None:
        if last_signal_time.tzinfo is None or last_signal_time.utcoffset() is None:
            raise ValueError("Czas ostatniego sygnału musi zawierać strefę czasową.")
        if (candle_close - last_signal_time.astimezone(timezone.utc)).total_seconds() < COOLDOWN_MINUTES * 60:
            return blocked("Aktywny jest cooldown po poprzednim sygnale.", ["cooldown_active"], indicators)

    efficiency = indicators["efficiency_ratio_30"]
    regime_unclear = (
        efficiency is not None
        and REVERSION_ER_MAX < efficiency < PULLBACK_ER_MIN
    )
    opening_range = _opening_range(ordered, local_close.date())
    candidates = _setup_candidates(segment, indicators, candle_close, opening_range)
    used_orb_directions = {direction.upper() for direction in (orb_directions_today or [])}
    orb_was_blocked = any(
        candidate["setup"] == "OPENING_RANGE_BREAKOUT"
        and candidate["direction"] in used_orb_directions
        for candidate in candidates
    )
    candidates = [
        candidate
        for candidate in candidates
        if not (
            candidate["setup"] == "OPENING_RANGE_BREAKOUT"
            and candidate["direction"] in used_orb_directions
        )
    ]
    if not candidates:
        if orb_was_blocked:
            return blocked(
                "Ten kierunek ORB został już wykorzystany w bieżącym dniu lokalnym.",
                ["orb_direction_already_used"],
                indicators,
            )
        setup_two_or_three_is_active = (
            any(start <= wall_time <= end for start, end in TREND_PULLBACK_WINDOWS)
            or RANGE_REVERSION_WINDOW[0] <= wall_time <= RANGE_REVERSION_WINDOW[1]
        )
        if regime_unclear and setup_two_or_three_is_active:
            return blocked(
                "Reżim rynku jest niejednoznaczny.",
                ["regime_unclear"],
                indicators,
            )
        return blocked("Nie spełniono warunków żadnego setupu.", ["no_setup"], indicators)
    directions = {item["direction"] for item in candidates}
    if len(directions) != 1:
        return blocked("Setupy wskazują przeciwne kierunki.", ["conflicting_setups"], indicators)

    scored = []
    for candidate in candidates:
        score, bonus_reasons = _score_candidate(candidate, indicators, local_close)
        scored.append((score, candidate, bonus_reasons))
    score, candidate, bonus_reasons = max(scored, key=lambda item: item[0])
    if score < MIN_SCORE:
        return blocked("Wynik nie osiąga progu 70 punktów.", ["score_below_minimum"], indicators, score)

    direction = candidate["direction"]
    sign = 1 if direction == "LONG" else -1
    price = float(last["close"])
    target_points = max(atr * TP_ATR_MULTIPLIER, MIN_TP_POINTS)
    if candidate["setup"] == "RANGE_REVERSION":
        target_points = min(target_points, abs(price - indicators["vwap"]))
    if target_points < COST_MULTIPLE * ASSUMED_COST_POINTS:
        return blocked("Proponowany cel nie pokrywa trzykrotności kosztu.", ["target_below_cost_multiple"], indicators, score)
    stop_points = atr * SL_ATR_MULTIPLIER
    close_local = candle_close.astimezone(WARSAW_TIMEZONE)
    open_local = candle_open.astimezone(WARSAW_TIMEZONE)
    params = _clean_json(
        {
            **indicators,
            "atr14_points": indicators["atr14_points"],
            "atr_ratio": indicators["atr_ratio"],
            "rsi5": indicators["rsi5"],
            "efficiency_ratio_30": indicators["efficiency_ratio_30"],
            "ema20_5m_slope": indicators["ema20_5m_slope"],
            "trend_5m": indicators["trend_5m"],
            "rel_volume": indicators["relative_volume"],
            "body_ratio": indicators["body_ratio"],
            "or_high": indicators["or_high"],
            "or_low": indicators["or_low"],
            "or_width_points": indicators["or_width_points"],
            "minutes_since_open": indicators["minutes_since_open"],
            "session_phase": indicators["session_phase"],
            "missing_candles_60m": missing_count,
            "data_age_minutes": round(age, 4),
            "segment_candles": len(segment),
            "assumed_cost_points": ASSUMED_COST_POINTS,
            "opening_range_candles": indicators["or_count"],
        }
    )
    reasons = [f"setup_{candidate['setup'].lower()}", *bonus_reasons]
    return {
        "instrument": "DAX40",
        "market_name": "DAX40",
        "symbol": "GER30",
        "signal": direction,
        "price": price,
        "timestamp": close_local.isoformat(),
        "timestamp_epoch": int(candle_close.timestamp()),
        "candle_open_time": open_local.isoformat(),
        "candle_open_epoch": int(candle_open.timestamp()),
        "candle_open_time_epoch": int(candle_open.timestamp()),
        "setup": candidate["setup"],
        "score": score,
        "reasons": reasons,
        "blocked_by": [],
        "params": params,
        "max_hold_minutes": MAX_HOLD_MINUTES,
        "tp_points": round(target_points, 4),
        "sl_points": round(stop_points, 4),
        "tp_price": round(price + sign * target_points, 4),
        "sl_price": round(price - sign * stop_points, 4),
    }


def _clean_json(value: Any) -> Any:
    """Usuwa nieobsługiwane wskaźniki i zaokrągla liczby do bezpiecznego JSON."""
    if isinstance(value, dict):
        return {key: _clean_json(item) for key, item in value.items() if key != "atr_14_series"}
    if isinstance(value, list):
        return [_clean_json(item) for item in value]
    if isinstance(value, float):
        return round(value, 6)
    return value


def _none_signal(
    candle: dict[str, Any] | None,
    reason: str,
    blocked_by: list[str],
    now: datetime,
    params: dict[str, Any] | None = None,
    score: int = 0,
) -> dict[str, Any]:
    """Buduje pełny format odpowiedzi NONE również dla pustej historii."""
    opened = _candle_time(candle) if candle else None
    closed = opened + timedelta(minutes=1) if opened else None
    close_local = closed.astimezone(WARSAW_TIMEZONE) if closed else None
    age = (
        round((now.astimezone(timezone.utc) - closed).total_seconds() / 60, 4)
        if closed
        else None
    )
    required_parameters = {
        "atr14_points": None,
        "atr_ratio": None,
        "rsi5": None,
        "ema9": None,
        "ema21": None,
        "ema55": None,
        "ema20_5m": None,
        "ema20_5m_slope": None,
        "trend_5m": None,
        "efficiency_ratio_30": None,
        "bb_zscore": None,
        "vwap": None,
        "vwap_distance_atr": None,
        "rel_volume": None,
        "body_ratio": None,
        "or_high": None,
        "or_low": None,
        "or_width_points": None,
        "minutes_since_open": None,
        "session_phase": None,
        "missing_candles_60m": 0,
        "data_age_minutes": age,
        "segment_candles": 0,
    }
    required_parameters.update(params or {})
    return {
        "instrument": "DAX40",
        "market_name": "DAX40",
        "symbol": "GER30",
        "signal": "NONE",
        "price": float(candle["close"]) if candle else 0.0,
        "timestamp": close_local.isoformat() if close_local else None,
        "timestamp_epoch": int(closed.timestamp()) if closed else None,
        "candle_open_time": opened.astimezone(WARSAW_TIMEZONE).isoformat() if opened else None,
        "candle_open_epoch": int(opened.timestamp()) if opened else None,
        "candle_open_time_epoch": int(opened.timestamp()) if opened else None,
        "setup": None,
        "score": score,
        "reasons": [reason],
        "blocked_by": blocked_by,
        "params": _clean_json(required_parameters),
        "data_age_minutes_from_close": age,
        "max_hold_minutes": MAX_HOLD_MINUTES,
        "tp_points": None,
        "sl_points": None,
        "tp_price": None,
        "sl_price": None,
    }
