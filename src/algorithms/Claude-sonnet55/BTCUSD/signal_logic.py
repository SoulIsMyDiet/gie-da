"""Backend: walidacja danych, setupy BTC/USD i kompaktowy JSON sygnału."""

from datetime import datetime, timedelta, timezone
import json
import logging
from math import isfinite

try:
    from . import config
    from .indicators import calculate_indicators
    from .schedule import as_utc, event_time_utc, is_weekend, session_name
    from .strategy_context import STRATEGY_CONTEXT
except ImportError:
    import config
    from indicators import calculate_indicators
    from schedule import as_utc, event_time_utc, is_weekend, session_name
    from strategy_context import STRATEGY_CONTEXT

STRATEGY_NAME = "Claude-sonnet55/BTCUSD"


def _normalize_timestamp(row: dict) -> datetime:
    """Interpretuje czas świecy jako UTC lub czas lokalny Warszawy z offsetem."""
    key = next(
        (
            name
            for name in ("timestamp_utc", "datetime", "open_time", "timestamp")
            if row.get(name) is not None
        ),
        None,
    )
    raw = row.get(key) if key else None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return datetime.fromtimestamp(float(raw), timezone.utc)
    if isinstance(raw, str):
        try:
            raw = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Nie można odczytać czasu świecy.") from error
    if not isinstance(raw, datetime):
        raise ValueError("Świeca nie zawiera prawidłowego czasu otwarcia.")
    if raw.tzinfo is not None and raw.utcoffset() is not None:
        return raw.astimezone(timezone.utc)
    offset_minutes = row.get("utc_offset_minutes")
    if offset_minutes is not None:
        try:
            offset_value = float(offset_minutes)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("Nieprawidłowy offset UTC świecy.") from error
        if (
            not isfinite(offset_value)
            or not offset_value.is_integer()
            or not -config.MAX_UTC_OFFSET_MINUTES
            <= offset_value
            <= config.MAX_UTC_OFFSET_MINUTES
        ):
            raise ValueError("Offset UTC świecy wykracza poza dopuszczalny zakres.")
        offset = int(offset_value)
        return raw.replace(tzinfo=timezone(timedelta(minutes=offset))).astimezone(
            timezone.utc
        )
    if key == "timestamp_utc":
        raise ValueError("timestamp_utc musi zawierać strefę czasową.")

    first = raw.replace(tzinfo=config.WARSAW, fold=0)
    second = raw.replace(tzinfo=config.WARSAW, fold=1)
    first_round_trip = first.astimezone(timezone.utc).astimezone(config.WARSAW)
    second_round_trip = second.astimezone(timezone.utc).astimezone(config.WARSAW)
    if (
        first_round_trip.replace(tzinfo=None) != raw
        and second_round_trip.replace(tzinfo=None) != raw
    ):
        raise ValueError("Lokalny czas świecy nie istnieje w Europe/Warsaw.")
    if first.utcoffset() != second.utcoffset():
        raise ValueError(
            "Lokalny czas świecy jest niejednoznaczny bez utc_offset_minutes."
        )
    return first.astimezone(timezone.utc)


def _normalize_candles(
    candles: list[dict], now: datetime
) -> tuple[list[dict], int, datetime | None]:
    """Sortuje, deduplikuje, waliduje i odrzuca świece niezamknięte."""
    latest_by_open: dict[datetime, dict] = {}
    invalid_count = 0
    latest_invalid_open = None
    for source in candles:
        opened = None
        try:
            row = dict(source)
            instrument = row.get("instrument", row.get("symbol"))
            if (
                instrument is not None
                and str(instrument).replace("/", "").upper()
                != config.INSTRUMENT_NAME
            ):
                raise ValueError("Strategia przyjmuje wyłącznie BTC/USD.")
            opened = _normalize_timestamp(row)
            if opened.second or opened.microsecond:
                raise ValueError("Otwarcie świecy M1 nie wypada na pełną minutę.")
            if opened + config.CANDLE_DURATION > now:
                continue
            prices = tuple(float(row[key]) for key in ("open", "high", "low", "close"))
            if (
                not all(isfinite(value) and value > 0 for value in prices)
                or prices[2] > min(prices[0], prices[3])
                or prices[1] < max(prices[0], prices[3])
                or prices[2] > prices[1]
            ):
                raise ValueError("Świeca zawiera nieprawidłowe wartości OHLC.")
            row.update(
                timestamp_utc=opened,
                open=prices[0],
                high=prices[1],
                low=prices[2],
                close=prices[3],
            )
            latest_by_open[opened] = row
        except (KeyError, TypeError, ValueError, OverflowError):
            invalid_count += 1
            if (
                opened is not None
                and opened + config.CANDLE_DURATION <= now
                and (latest_invalid_open is None or opened > latest_invalid_open)
            ):
                latest_invalid_open = opened
    ordered = [latest_by_open[key] for key in sorted(latest_by_open)]
    return ordered, invalid_count, latest_invalid_open


def _blackout_reasons(closed: datetime) -> list[str]:
    """Zwraca aktywne blokady otwarcia USA i ręcznie wskazanych wydarzeń."""
    local = closed.astimezone(config.NEW_YORK)
    day = local.date()
    reasons = []
    opening = event_time_utc(day, config.US_CASH_OPEN)
    if abs((closed - opening).total_seconds()) <= (
        config.US_CASH_OPEN_BLACKOUT_MINUTES * config.SECONDS_PER_MINUTE
    ):
        reasons.append("us_cash_open")
    if day in config.US_DATA_RELEASE_DATES and any(
        abs((closed - event_time_utc(day, event)).total_seconds())
        <= config.NEWS_BLACKOUT_MINUTES * config.SECONDS_PER_MINUTE
        for event in config.US_DATA_RELEASE_TIMES
    ):
        reasons.append("us_data_release")
    if day in config.FOMC_DATES and abs(
        (
            closed
            - event_time_utc(day, config.FOMC_ANNOUNCEMENT_TIME)
        ).total_seconds()
    ) <= config.NEWS_BLACKOUT_MINUTES * config.SECONDS_PER_MINUTE:
        reasons.append("fomc")
    return reasons


def _candidate_setups(
    candles: list[dict], params: dict, session: str
) -> list[dict]:
    """Sprawdza squeeze breakout, trend pullback i sweep reversion."""
    if len(candles) < config.M1_WARMUP_CANDLES:
        return []
    current = candles[-1]
    close, open_price = float(current["close"]), float(current["open"])
    high, low = float(current["high"]), float(current["low"])
    atr = params.get("atr14_usd")
    rsi_value = params.get("rsi5")
    histogram = params.get("macd_histogram")
    if atr is None or atr <= 0 or rsi_value is None or histogram is None:
        return []

    body_ratio = params["body_ratio"]
    previous_close = params.get("previous_close")
    candidates = []

    if (
        params.get("bb_width_percentile") is not None
        and params["bb_width_percentile"] <= config.SQUEEZE_MAX_PERCENTILE
        and params.get("donchian_high_20") is not None
        and params.get("donchian_low_20") is not None
        and float(params["candle_range"]) <= config.SQUEEZE_MAX_RANGE_ATR * atr
        and body_ratio >= config.SQUEEZE_MIN_BODY_RATIO
    ):
        if (
            close > params["donchian_high_20"]
            and close > open_price
            and histogram > 0
        ):
            candidates.append(("SQUEEZE_BREAKOUT", "LONG"))
        if (
            close < params["donchian_low_20"]
            and close < open_price
            and histogram < 0
        ):
            candidates.append(("SQUEEZE_BREAKOUT", "SHORT"))

    m5_trend = params.get("trend_5m")
    er = params.get("efficiency_ratio_30")
    ema9 = params["ema9"]
    ema21 = params["ema21"]
    ema55 = params["ema55"]
    pullback_ema9 = params.get("_ema9_series", [])
    pullback_ema21 = params.get("_ema21_series", [])
    recent = candles[-config.PULLBACK_LOOKBACK :]
    long_touch = any(
        float(row["low"]) <= max(fast, slow) + config.PULLBACK_DISTANCE_ATR * atr
        and float(row["low"]) >= min(fast, slow) - config.PULLBACK_DISTANCE_ATR * atr
        for row, fast, slow in zip(recent, pullback_ema9, pullback_ema21)
    )
    short_touch = any(
        float(row["high"]) >= min(fast, slow) - config.PULLBACK_DISTANCE_ATR * atr
        and float(row["high"]) <= max(fast, slow) + config.PULLBACK_DISTANCE_ATR * atr
        for row, fast, slow in zip(recent, pullback_ema9, pullback_ema21)
    )
    previous_ema9 = params.get("previous_ema9")
    previous_rsi = params.get("rsi5_previous")
    if (
        er is not None
        and er >= config.PULLBACK_MIN_ER
        and m5_trend == "UP"
        and ema9 > ema21 > ema55
        and long_touch
        and close > ema9
        and close > open_price
        and previous_close is not None
        and previous_ema9 is not None
        and previous_close <= previous_ema9
        and rsi_value > config.PULLBACK_RSI_MIDPOINT
        and previous_rsi is not None
        and rsi_value > previous_rsi
        and histogram > 0
    ):
        candidates.append(("TREND_PULLBACK", "LONG"))
    if (
        er is not None
        and er >= config.PULLBACK_MIN_ER
        and m5_trend == "DOWN"
        and ema9 < ema21 < ema55
        and short_touch
        and close < ema9
        and close < open_price
        and previous_close is not None
        and previous_ema9 is not None
        and previous_close >= previous_ema9
        and rsi_value < config.PULLBACK_RSI_MIDPOINT
        and previous_rsi is not None
        and rsi_value < previous_rsi
        and histogram < 0
    ):
        candidates.append(("TREND_PULLBACK", "SHORT"))

    previous_rows = candles[-config.DONCHIAN_PERIOD - 1 : -1]
    if len(previous_rows) == config.DONCHIAN_PERIOD:
        prior_low = min(float(row["low"]) for row in previous_rows)
        prior_high = max(float(row["high"]) for row in previous_rows)
        candle_range = float(params["candle_range"])
        lower_wick = min(open_price, close) - low
        upper_wick = high - max(open_price, close)
        lower_ratio = lower_wick / candle_range if candle_range else 0.0
        upper_ratio = upper_wick / candle_range if candle_range else 0.0
        if (
            low < prior_low
            and close > prior_low
            and close > open_price
            and rsi_value <= config.SWEEP_RSI_LONG_MAX
            and lower_ratio >= config.SWEEP_MIN_WICK_RATIO
            and histogram > params.get("_macd_histogram_previous", histogram)
        ):
            candidates.append(("SWEEP_REVERSION", "LONG"))
        if (
            high > prior_high
            and close < prior_high
            and close < open_price
            and rsi_value >= config.SWEEP_RSI_SHORT_MIN
            and upper_ratio >= config.SWEEP_MIN_WICK_RATIO
            and histogram < params.get("_macd_histogram_previous", histogram)
        ):
            candidates.append(("SWEEP_REVERSION", "SHORT"))
    return [
        {"setup": setup, "signal": direction, "session": session}
        for setup, direction in candidates
    ]


def _score_candidate(candidate: dict, params: dict, weekend: bool) -> tuple[int, list[str]]:
    """Ocenia jakość setupu na podstawie niezależnych potwierdzeń."""
    direction = candidate["signal"]
    score = config.CORE_SCORE
    reasons = [f"Potwierdzony setup {candidate['setup']} ({direction})."]
    aligned = (
        direction == "LONG" and params.get("trend_5m") == "UP"
    ) or (direction == "SHORT" and params.get("trend_5m") == "DOWN")
    if aligned:
        score += config.BONUS_SCORE
        reasons.append("Trend M5 jest zgodny z kierunkiem.")
    if (
        params.get("rel_volume") is not None
        and params["rel_volume"] >= config.BONUS_RELATIVE_VOLUME
    ):
        score += config.BONUS_SCORE
        reasons.append("Wolumen względny potwierdza ruch.")
    if params.get("body_ratio", 0.0) >= config.BONUS_BODY_RATIO:
        score += config.BONUS_SCORE
        reasons.append("Korpus świecy potwierdza impuls.")
    if (
        params.get("efficiency_ratio_30") is not None
        and params["efficiency_ratio_30"]
        >= config.BONUS_EFFICIENCY_RATIO
    ):
        score += config.BONUS_SCORE
        reasons.append("Ruch ceny ma wyraźny kierunek.")
    if weekend or candidate["session"] in {"ASIA", "LATE"}:
        score = max(0, score - config.BONUS_SCORE)
    return min(score, config.MAX_SCORE), reasons


def _compact_signal_parameters(result: dict) -> dict:
    """Buduje krótki JSON do bazy, zachowując pięć pól wymaganych na początku."""
    compact = {
        "price": result["price"],
        "signal": result["signal"],
        "timestamp": result["timestamp_epoch"],
        "instrument": config.INSTRUMENT_PARAMS_NAME,
        "reason": result["reason"],
    }
    if result["signal"] in {"LONG", "SHORT"}:
        extras = {
            "setup": result.get("setup"),
            "score": result.get("score"),
            "tp_price": result.get("tp_price"),
            "sl_price": result.get("sl_price"),
            "atr14_bps": result.get("params", {}).get("atr14_bps"),
            "rsi5": result.get("params", {}).get("rsi5"),
            "efficiency_ratio_30": result.get("params", {}).get(
                "efficiency_ratio_30"
            ),
            "rel_volume": result.get("params", {}).get("rel_volume"),
            "session_name": result.get("params", {}).get("session_name"),
        }
        for key in config.SIGNAL_PARAMS_EXTRA_KEYS:
            if key in extras:
                compact[key] = extras[key]
    return compact


def _fit_compact_payload(result: dict) -> dict:
    """Usuwa końcowe pola dodatkowe i skraca reason do limitu UTF-8."""
    compact = _compact_signal_parameters(result)
    extras = list(config.SIGNAL_PARAMS_EXTRA_KEYS)
    while (
        len(
            json.dumps(compact, ensure_ascii=False, allow_nan=False).encode("utf-8")
        )
        > config.SIGNAL_PARAMS_MAX_BYTES
        and extras
    ):
        compact.pop(extras.pop(), None)
    if (
        len(
            json.dumps(compact, ensure_ascii=False, allow_nan=False).encode("utf-8")
        )
        > config.SIGNAL_PARAMS_MAX_BYTES
    ):
        reason = compact["reason"]
        while reason and len(
            json.dumps(compact, ensure_ascii=False, allow_nan=False).encode("utf-8")
        ) > config.SIGNAL_PARAMS_MAX_BYTES:
            reason = reason[:-1]
            compact["reason"] = reason
        compact["reason"] = reason.rstrip(" .") + "."
    return compact


def generate_signal(
    candles: list[dict],
    now: datetime | None = None,
    last_signal_timestamp: int | None = None,
    minimum_score: int | None = None,
) -> dict:
    """Ocenia ostatnią zamkniętą świecę i zwraca diagnostykę oraz JSON do bazy."""
    reference = as_utc(now or datetime.now(timezone.utc))
    if minimum_score is not None and (
        isinstance(minimum_score, bool)
        or not isinstance(minimum_score, int)
        or not 0 <= minimum_score <= config.MAX_SCORE
    ):
        raise ValueError("Próg score musi być liczbą całkowitą od 0 do 100.")
    if last_signal_timestamp is not None and (
        isinstance(last_signal_timestamp, bool)
        or not isinstance(last_signal_timestamp, int)
        or last_signal_timestamp < 0
    ):
        raise ValueError("Czas poprzedniego sygnału musi być nieujemną epoką.")
    result = {
        "instrument": config.INSTRUMENT_NAME,
        "algorithm_name": STRATEGY_NAME,
        "strategy_context": STRATEGY_CONTEXT,
        "price": None,
        "timestamp": None,
        "timestamp_epoch": None,
        "candle_open_time": None,
        "candle_open_epoch": None,
        "candle_close_time": None,
        "signal": "NONE",
        "setup": None,
        "score": 0,
        "tp_price": None,
        "sl_price": None,
        "max_hold_minutes": config.MAX_HOLD_MINUTES,
        "params": {},
        "reasons": [],
        "blocked_by": [],
        "reason": "Brak zamkniętej świecy BTC/USD do oceny.",
    }
    ordered, invalid_count, latest_invalid_open = _normalize_candles(
        candles, reference
    )
    if invalid_count:
        logging.getLogger(__name__).warning(
            "Pominięto %s nieprawidłowych świec BTC/USD.", invalid_count
        )
    if latest_invalid_open is not None and (
        not ordered or latest_invalid_open >= ordered[-1]["timestamp_utc"]
    ):
        invalid_close = latest_invalid_open + config.CANDLE_DURATION
        result.update(
            timestamp=int(invalid_close.timestamp()),
            timestamp_epoch=int(invalid_close.timestamp()),
            candle_open_time=latest_invalid_open.astimezone(
                config.WARSAW
            ).isoformat(),
            candle_open_epoch=int(latest_invalid_open.timestamp()),
            candle_close_time=invalid_close.astimezone(
                config.WARSAW
            ).isoformat(),
            blocked_by=["invalid_candle"],
            reason="Najnowsza zamknięta świeca zawiera nieprawidłowe dane OHLC.",
        )
        result["signal_parameters"] = _fit_compact_payload(result)
        return result
    if not ordered:
        result["blocked_by"].append("no_data")
        if invalid_count:
            result["blocked_by"].append("invalid_candle")
            result["reason"] = "Dane OHLC świec BTC/USD są nieprawidłowe."
        result["signal_parameters"] = _fit_compact_payload(result)
        return result

    current = ordered[-1]
    opened = as_utc(current["timestamp_utc"])
    closed = opened + config.CANDLE_DURATION
    price = round(float(current["close"]), config.PRICE_DECIMALS)
    result.update(
        price=price,
        timestamp=int(closed.timestamp()),
        timestamp_epoch=int(closed.timestamp()),
        candle_open_time=opened.astimezone(config.WARSAW).isoformat(),
        candle_open_epoch=int(opened.timestamp()),
        candle_close_time=closed.astimezone(config.WARSAW).isoformat(),
    )
    if closed > reference:
        result["blocked_by"].append("forming_candle")
    age = (reference - closed).total_seconds() / config.SECONDS_PER_MINUTE
    if age > config.MAX_STALE_MINUTES:
        result["blocked_by"].append("stale_data")

    params, segment = calculate_indicators(ordered, reference)
    session = session_name(closed)
    weekend = is_weekend(closed)
    params.update(
        session_name=session,
        is_weekend=weekend,
        missing_candles_60m=params.get("missing_candles_60m"),
        data_age_minutes=max(0.0, age),
    )
    result["params"] = {
        key: value
        for key, value in params.items()
        if not key.startswith("_")
        and key not in {"recent_lows", "recent_highs", "bb_widths_recent"}
    }
    result["params"]["segment_candles"] = len(segment)
    result["params"]["invalid_candles_discarded"] = invalid_count
    if len(segment) < config.M1_WARMUP_CANDLES:
        result["blocked_by"].append("warmup_m1")
    if params.get("m5_candles", 0) < config.M5_WARMUP_CANDLES:
        result["blocked_by"].append("warmup_m5")
    if params.get("trend_5m") is None:
        result["blocked_by"].append("m5_trend_unavailable")
    blackouts = _blackout_reasons(closed)
    result["blocked_by"].extend(
        f"news_blackout:{reason}" for reason in blackouts
    )
    if "stale_data" in result["blocked_by"]:
        result["reason"] = "Ostatnia zamknięta świeca jest zbyt stara."
    elif any(reason.startswith("news_blackout:") for reason in result["blocked_by"]):
        result["reason"] = "Czas sygnału pokrywa się z zaplanowaną blokadą wydarzenia."
    elif (
        "warmup_m1" in result["blocked_by"]
        or "warmup_m5" in result["blocked_by"]
        or "m5_trend_unavailable" in result["blocked_by"]
    ):
        result["reason"] = "Niewystarczająca historia do rozgrzania wskaźników M1/M5."
    elif params.get("max_consecutive_missing", 0) >= 2:
        result["blocked_by"].append("data_gap")
        result["reason"] = "Wykryto istotną lukę danych; wskaźniki wymagają rozgrzania."
    elif result["blocked_by"]:
        result["reason"] = "Dane świecy nie spełniają warunków oceny sygnału."
    else:
        candidates = _candidate_setups(ordered, params, session)
        directions = {candidate["signal"] for candidate in candidates}
        if len(directions) > 1:
            result["blocked_by"].append("conflicting_setups")
            result["reason"] = "Setupy wskazują sprzeczne kierunki transakcji."
        elif not candidates:
            result["blocked_by"].append("no_setup")
            result["reason"] = "Nie wykryto kwalifikującego się setupu BTC/USD."
        else:
            candidates.sort(
                key=lambda candidate: _score_candidate(
                    candidate, params, weekend
                )[0],
                reverse=True,
            )
            selected = candidates[0]
            score, reasons = _score_candidate(selected, params, weekend)
            required_score = (
                config.WEEKEND_MIN_SCORE if weekend else config.MIN_SCORE
            )
            if session in {"ASIA", "LATE"}:
                required_score = max(
                    required_score, config.THIN_SESSION_MIN_SCORE
                )
            if minimum_score is not None:
                required_score = minimum_score
            atr = params["atr14_usd"]
            target_distance = atr * config.TP_ATR
            stop_distance = atr * config.SL_ATR
            target_bps = (
                target_distance / price * config.BASIS_POINTS_PER_UNIT
                if price
                else 0.0
            )
            net_reward_risk = (
                (
                    target_distance
                    - price
                    * config.ASSUMED_COST_BPS
                    / config.BASIS_POINTS_PER_UNIT
                )
                / (
                    stop_distance
                    + price
                    * config.ASSUMED_COST_BPS
                    / config.BASIS_POINTS_PER_UNIT
                )
            )
            if target_bps < config.MIN_TARGET_BPS or net_reward_risk < config.MIN_NET_REWARD_RISK:
                result["blocked_by"].append("target_too_small")
                result["reason"] = "Potencjalny zasięg nie pokrywa przyjętego kosztu."
            elif score < required_score:
                result["blocked_by"].append("score_below_threshold")
                result["reason"] = "Ocena setupu nie osiągnęła progu dla tej sesji."
            elif (
                last_signal_timestamp is not None
                and opened.timestamp() - int(last_signal_timestamp)
                < config.COOLDOWN_MINUTES * config.SECONDS_PER_MINUTE
            ):
                result["blocked_by"].append("cooldown")
                result["reason"] = "Wciąż obowiązuje przerwa po poprzednim sygnale."
            else:
                direction = selected["signal"]
                sign = 1 if direction == "LONG" else -1
                result.update(
                    signal=direction,
                    setup=selected["setup"],
                    score=score,
                    tp_price=round(
                        price + sign * target_distance, config.PRICE_DECIMALS
                    ),
                    sl_price=round(
                        price - sign * stop_distance, config.PRICE_DECIMALS
                    ),
                    reasons=reasons,
                    reason=(
                        f"Spełniono warunki {selected['setup']} ({direction}); "
                        "sygnał nie gwarantuje zysku."
                    ),
                    blocked_by=[],
                )
                result["params"]["assumed_cost_bps"] = config.ASSUMED_COST_BPS
                result["params"]["target_bps"] = target_bps
                result["params"]["net_reward_risk"] = net_reward_risk

    result["signal_parameters"] = _fit_compact_payload(result)
    return result
