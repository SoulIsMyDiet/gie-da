"""Backend: ocena trzech setupów ES, ich filtrów i powodów blokady."""

from collections.abc import Collection
from datetime import datetime, time, timedelta, timezone
from math import isfinite

import config
from indicators import calculate_indicators
from schedule import as_utc, cash_close, cash_open, is_signal_window, is_trading_minute, previous_trading_opens, segment_id


def _in_window(clock, window) -> bool:
    """Sprawdza półotwarty przedział godzin ET."""
    return window[0] <= clock < window[1]


def _valid_candle(row: dict) -> bool:
    """Weryfikuje prawdziwą świecę M1 UTC i wartości OHLCV; błędna zwraca False."""
    try:
        raw = row["timestamp_utc"]
        stamp = as_utc(raw)
        o, h, l, c = (float(row[x]) for x in ("open", "high", "low", "close"))
        volume = row.get("volume")
        return (raw.utcoffset() == timedelta(0) and stamp.second == stamp.microsecond == 0
                and all(isfinite(x) and x > 0 for x in (o, h, l, c))
                and l <= min(o, c) <= max(o, c) <= h
                and (volume is None or (isfinite(float(volume)) and float(volume) >= 0)))
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return False


def _candle_open(row: dict) -> datetime:
    """Zwraca minutowy czas otwarcia UTC; błędny znacznik zgłasza jako ValueError."""
    try:
        raw = row["timestamp_utc"]
        stamp = as_utc(raw)
        if raw.utcoffset() != timedelta(0) or stamp.second or stamp.microsecond:
            raise ValueError("Znacznik świecy musi wskazywać pełną minutę UTC.")
        return stamp
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError("Historia zawiera nieprawidłowy znacznik czasu świecy M1 UTC.") from error


def _m5_trend(close: float | None, ema: float | None, slope: float | None) -> str | None:
    """Zwraca kierunek M5 lub NEUTRAL; przy brakującym wskaźniku zwraca None."""
    if close is None or ema is None or slope is None:
        return None
    if close > ema and slope > 0:
        return "UP"
    if close < ema and slope < 0:
        return "DOWN"
    return "NEUTRAL"


def _blackout(closed: datetime) -> bool:
    """Sprawdza blokady ET i UTC; blokada końcowa obejmuje także chwilę zamknięcia."""
    local = closed.astimezone(config.NEW_YORK)
    clock = local.time().replace(tzinfo=None)
    if any(_in_window(clock, window) for window in config.FIXED_BLACKOUTS):
        return True
    close_time = cash_close(local.date()).astimezone(timezone.utc)
    if close_time - timedelta(minutes=config.CLOSING_BLACKOUT_MINUTES) <= closed <= close_time:
        return True
    for event in config.EVENT_TIMES:
        event_time = datetime.combine(local.date(), event, config.NEW_YORK)
        if abs((closed - event_time.astimezone(timezone.utc)).total_seconds()) <= config.EVENT_BLACKOUT_MINUTES * 60:
            return True
    return any(as_utc(start) <= closed < as_utc(end) for start, end in config.NEWS_BLACKOUTS)


def _score_details(setup: str, direction: str, params: dict, clock: time) -> tuple[int, list[str]]:
    """Zwraca ocenę i opisy jej składników dla spełnionego setupu, kierunku, wskaźników i czasu ET."""
    core_descriptions = {
        "OPENING_RANGE_BREAKOUT": "pierwsze wybicie zakresu otwarcia, wolumen, korpus, VWAP i zgodne M5",
        "TREND_PULLBACK": "trend EMA i M5, dotknięcie EMA21, RSI oraz kierunkowa świeca względem VWAP",
        "VWAP_REVERSION": "niski ER i nachylenie EMA55, powrót w pasmo, RSI, odległość od VWAP i knot",
    }
    reasons = [f"Rdzeń {setup} {direction}: {core_descriptions[setup]} (+{config.CORE_SCORE} pkt)"]
    bonuses = []
    if params["relative_volume"] is not None and params["relative_volume"] >= config.BONUS_VOLUME:
        bonuses.append(f"wolumen względny ≥ {config.BONUS_VOLUME:g}")
    if any(_in_window(clock, window) for window in config.LIQUID_WINDOWS):
        bonuses.append("okno podwyższonej płynności")
    trend = _m5_trend(params["m5_close"], params["m5_ema20"], params["m5_ema20_slope"])
    aligned = (direction == "LONG" and trend == "UP") or (direction == "SHORT" and trend == "DOWN")
    if setup == "TREND_PULLBACK" and params["er"] is not None and params["er"] > config.BONUS_ER:
        bonuses.append(f"ER > {config.BONUS_ER:g}")
    elif setup != "TREND_PULLBACK" and aligned:
        bonuses.append("zamknięcie świecy M5 i nachylenie EMA20 zgodne z kierunkiem")
    if params["body_ratio"] >= config.BONUS_BODY:
        bonuses.append(f"udział korpusu ≥ {config.BONUS_BODY:g}")
    reasons.extend(f"Bonus: {bonus} (+{config.BONUS_SCORE} pkt)" for bonus in bonuses)
    return config.CORE_SCORE + len(bonuses) * config.BONUS_SCORE, reasons


def _score(setup: str, direction: str, params: dict, clock: time) -> int:
    """Zwraca ocenę spełnionego setupu dla kierunku, wskaźników i czasu ET."""
    return _score_details(setup, direction, params, clock)[0]


def _candidates(segment: list[dict], params: dict, clock, orb_directions_today) -> list[tuple[str, str, int]]:
    """Zwraca spełnione setupy, kierunki i oceny na podstawie wyłącznie bieżącego segmentu."""
    atr, vwap, volume, er = params["atr"], params["vwap"], params["relative_volume"], params["er"]
    if not atr or vwap is None or len(segment) < 2:
        return []
    row, previous = segment[-1], segment[-2]
    close, op = float(row["close"]), float(row["open"])
    previous_close = float(previous["close"])
    body = params["body_ratio"]
    m5_close, m5_ema, m5_slope = params["m5_close"], params["m5_ema20"], params["m5_ema20_slope"]
    m5_up = m5_close is not None and m5_ema is not None and m5_close > m5_ema
    m5_down = m5_close is not None and m5_ema is not None and m5_close < m5_ema
    found = []
    if (config.OPENING_END <= clock < config.ORB_END and len(segment) >= config.WARMUP_ORB
            and params["or_count"] >= config.ORB_MIN_CANDLES and params["or_width"] is not None
            and config.ORB_MIN_WIDTH <= params["or_width"] <= config.ORB_MAX_WIDTH
            and volume is not None and volume >= config.ORB_MIN_VOLUME and body >= config.ORB_MIN_BODY):
        top, bottom = params["or_high"], params["or_low"]
        used = {str(x).upper() for x in (orb_directions_today or [])}
        if ("LONG" not in used and previous_close <= top and top + config.ORB_BUFFER_ATR * atr < close
                and close - top <= config.ORB_MAX_EXTENSION_ATR * atr and close > op and close > vwap and m5_up):
            found.append(("OPENING_RANGE_BREAKOUT", "LONG"))
        if ("SHORT" not in used and previous_close >= bottom and bottom - config.ORB_BUFFER_ATR * atr > close
                and bottom - close <= config.ORB_MAX_EXTENSION_ATR * atr and close < op and close < vwap and m5_down):
            found.append(("OPENING_RANGE_BREAKOUT", "SHORT"))
    rsi_values = params["rsi_series"]
    if (len(segment) >= config.WARMUP_PULLBACK and any(_in_window(clock, x) for x in config.PULLBACK_WINDOWS)
            and er is not None and er >= config.PULLBACK_MIN_ER and body >= config.PULLBACK_MIN_BODY
            and params["rsi"] is not None and m5_slope is not None):
        # Porównujemy każde z pięciu minimów/maksimów z EMA21 z tej samej świecy.
        paired = zip(segment[-config.PULLBACK_TOUCH_LOOKBACK:], params["ema21_series"])
        pairs = list(paired)
        long_touch = any(abs(float(x["low"]) - avg) <= config.PULLBACK_DISTANCE_ATR * atr for x, avg in pairs)
        short_touch = any(abs(float(x["high"]) - avg) <= config.PULLBACK_DISTANCE_ATR * atr for x, avg in pairs)
        prior_rsi = [x for x in rsi_values[:-1][-config.PULLBACK_RSI_LOOKBACK:] if x is not None]
        if (m5_up and m5_slope > 0 and params["ema9"] > params["ema21"] > params["ema55"] and close > vwap
                and long_touch and any(x < config.PULLBACK_LONG_RSI_PRIOR for x in prior_rsi)
                and params["rsi"] > config.PULLBACK_LONG_RSI_CURRENT and close > op and close > previous_close):
            found.append(("TREND_PULLBACK", "LONG"))
        if (m5_down and m5_slope < 0 and params["ema9"] < params["ema21"] < params["ema55"] and close < vwap
                and short_touch and any(x > config.PULLBACK_SHORT_RSI_PRIOR for x in prior_rsi)
                and params["rsi"] < config.PULLBACK_SHORT_RSI_CURRENT and close < op and close < previous_close):
            found.append(("TREND_PULLBACK", "SHORT"))
    if (len(segment) >= config.WARMUP_REVERSION and _in_window(clock, config.REVERSION_WINDOW)
            and er is not None and er <= config.REVERSION_MAX_ER and params["ema55_slope"] is not None
            and abs(params["ema55_slope"]) < config.REVERSION_MAX_SLOPE_ATR * atr
            and params["bb_zscore_previous"] is not None and params["bb_zscore"] is not None):
        recent_rsi = [x for x in rsi_values[-config.REVERSION_RSI_LOOKBACK:] if x is not None]
        if (params["bb_zscore_previous"] <= -config.BB_DEVIATIONS and -config.BB_DEVIATIONS < params["bb_zscore"] < config.BB_DEVIATIONS
                and any(x <= config.REVERSION_RSI_LONG for x in recent_rsi) and vwap - close >= config.REVERSION_DISTANCE_ATR * atr
                and close > op and params["lower_wick_ratio"] >= config.REVERSION_MIN_WICK):
            found.append(("VWAP_REVERSION", "LONG"))
        if (params["bb_zscore_previous"] >= config.BB_DEVIATIONS and -config.BB_DEVIATIONS < params["bb_zscore"] < config.BB_DEVIATIONS
                and any(x >= config.REVERSION_RSI_SHORT for x in recent_rsi) and close - vwap >= config.REVERSION_DISTANCE_ATR * atr
                and close < op and params["upper_wick_ratio"] >= config.REVERSION_MIN_WICK):
            found.append(("VWAP_REVERSION", "SHORT"))
    return [(setup, direction, _score(setup, direction, params, clock)) for setup, direction in found]


def generate_signal(candles: list[dict], last_signal_time: datetime | None = None, signals_today: int = 0,
                    orb_directions_today: Collection[str] | None = None, now: datetime | None = None) -> dict:
    """Zwraca decyzję dla zamknięcia ostatniej świecy; now to świadomy czas UTC analizy.

    Niepoprawne lub powtórzone zamknięte świece i błędne znaczniki czasu zgłaszają ValueError.
    Pusty zbiór lub brak zamkniętych świec zwraca NONE; now pominięte oznacza bieżący czas UTC.
    Dla celowej analizy historycznej należy przekazać now jako czas zamknięcia świecy;
    starsze dane oceniane poza oknem dodatkowo otrzymują outside_session.
    """
    result = {"instrument": config.INSTRUMENT, "algorithm_name": config.STRATEGY_NAME,
              "price": None, "timestamp": None, "timestamp_epoch": None,
              "candle_open_time": None, "candle_open_epoch": None, "signal": "NONE",
              "setup": None, "score": 0, "tp_price": None, "sl_price": None,
              "tp_points": None, "sl_points": None, "max_hold_minutes": config.MAX_HOLD_MINUTES,
              "params": {}, "reasons": [], "blocked_by": []}
    reference = as_utc(now if now is not None else datetime.now(timezone.utc))
    # Każdy znacznik musi pozwolić ustalić, czy świeca jest już zamknięta.
    stamped_rows = [(row, _candle_open(row)) for row in candles]
    closed_rows = [(row, stamp) for row, stamp in stamped_rows
                   if reference - stamp >= timedelta(minutes=1)]
    if any(not _valid_candle(row) for row, _ in closed_rows):
        raise ValueError("Historia zawiera nieprawidłową świecę M1 UTC.")
    closed_rows.sort(key=lambda item: item[1])
    rows = [row for row, _ in closed_rows]
    if not rows:
        result["blocked_by"].extend(["no_data", "warmup"])
        return result
    stamps = [stamp for _, stamp in closed_rows]
    if len(set(stamps)) != len(stamps):
        raise ValueError("Historia zawiera zduplikowane otwarcia świec.")
    opened, current = stamps[-1], rows[-1]
    closed = opened + timedelta(minutes=1)
    age = (reference - closed).total_seconds() / 60
    local = closed.astimezone(config.NEW_YORK)
    day, clock = local.date(), local.time().replace(tzinfo=None)
    price = float(current["close"])
    result.update(price=price, timestamp=closed.astimezone(config.WARSAW).isoformat(),
                  timestamp_epoch=int(closed.timestamp()), candle_open_time=opened.astimezone(config.WARSAW).isoformat(),
                  candle_open_epoch=int(opened.timestamp()))
    blocked = result["blocked_by"]
    stale = age > config.MAX_STALE_MINUTES
    # Czas otwarcia decyduje o dostawie, zamknięcia o oknie sygnałowym.
    # Zegar analizy ma znaczenie tylko dla przeterminowanych danych, nie dla
    # prawidłowej świecy zamkniętej tuż przed końcem okna.
    if (not is_trading_minute(opened) or not is_signal_window(closed)
            or (stale and not is_signal_window(reference))):
        blocked.append("outside_session")
    if stale:
        blocked.append("stale_data")
    if _blackout(closed):
        blocked.append("news_blackout")
    if not is_trading_minute(opened):
        return result
    # Dane spoza harmonogramu nie należą do żadnego segmentu i nie mogą
    # wpływać na wskaźniki ani na sprawdzanie brakujących minut.
    scheduled = [(row, stamp) for row, stamp in zip(rows, stamps) if is_trading_minute(stamp)]
    rows = [row for row, _ in scheduled]
    stamps = [stamp for _, stamp in scheduled]
    if last_signal_time is not None and closed - as_utc(last_signal_time) < timedelta(minutes=config.COOLDOWN_MINUTES):
        blocked.append("cooldown")
    if signals_today >= config.MAX_SIGNALS_PER_DAY:
        blocked.append("daily_limit")
    expected = previous_trading_opens(opened, config.GAP_LOOKBACK)
    present = set(stamps)
    missing_count = consecutive = longest = 0
    for stamp in expected:
        if stamp >= stamps[0] and stamp not in present:
            missing_count += 1
            consecutive += 1
            longest = max(longest, consecutive)
        else:
            consecutive = 0
    # Poza oknem zachowujemy liczniki luk, ale nie dodajemy kolejnej blokady.
    if "outside_session" not in blocked and (
            missing_count > config.MAX_MISSING_IN_60 or longest > config.MAX_MISSING_CONSECUTIVE):
        blocked.append("data_gap")
    current_segment = segment_id(opened)
    segment = [row for row, stamp in zip(rows, stamps) if segment_id(stamp) == current_segment]
    session = [row for row, stamp in zip(rows, stamps) if stamp.astimezone(config.NEW_YORK).date() == day]
    params = calculate_indicators(segment, session, closed)
    # Zakres tworzy 15 otwarć 09:30–09:44. O 09:45 ostatni close należy
    # do własnego zakresu high/low, więc ścisłe wybicie jest wtedy niemożliwe.
    opening = [row for row in session if config.OPENING_START <= as_utc(row["timestamp_utc"]).astimezone(config.NEW_YORK).time().replace(tzinfo=None) < config.OPENING_END]
    top = max((float(x["high"]) for x in opening), default=None)
    bottom = min((float(x["low"]) for x in opening), default=None)
    minutes = (closed - cash_open(day).astimezone(timezone.utc)).total_seconds() / 60
    phase = "liquid" if any(_in_window(clock, window) for window in config.LIQUID_WINDOWS) else "normal"
    params.update(or_high=top, or_low=bottom, or_width=top - bottom if top is not None else None,
                  or_count=len(opening), minutes_since_cash_open=minutes, session_phase=phase,
                  us_session_start_pl=cash_open(day).astimezone(config.WARSAW).isoformat(),
                  missing_candles_60m=missing_count, max_consecutive_missing=longest,
                  data_age_minutes=age, segment_candles=len(segment),
                  high_impact_day=day in config.HIGH_IMPACT_DAYS)
    atr_value = params["atr"]
    m5_close, m5_ema, m5_slope = params["m5_close"], params["m5_ema20"], params["m5_ema20_slope"]
    trend = _m5_trend(m5_close, m5_ema, m5_slope)
    # Ujednolicone nazwy do dokumentu JSON, obok pól używanych wewnętrznie.
    params.update(atr14_points=atr_value,
                  atr14_bps=atr_value / price * 10000 if atr_value is not None else None,
                  rsi5=params["rsi"], ema20_5m=m5_ema, ema20_5m_slope=m5_slope,
                  trend_5m=trend, efficiency_ratio_30=params["er"],
                  vwap_distance_atr=(price - params["vwap"]) / atr_value
                  if atr_value and params["vwap"] is not None else None,
                  rel_volume=params["relative_volume"], or_width_points=params["or_width"])
    result["params"] = params
    atr, ratio, volume = params["atr"], params["atr_ratio"], params["relative_volume"]
    if atr is None or not config.ATR_MIN <= atr <= config.ATR_MAX:
        blocked.append("atr")
    if ratio is None or not config.ATR_RATIO_MIN <= ratio <= config.ATR_RATIO_MAX:
        blocked.append("atr_ratio")
    if volume is None or volume < config.MIN_RELATIVE_VOLUME:
        blocked.append("relative_volume")
    if atr and float(current["high"]) - float(current["low"]) > config.MAX_CANDLE_RANGE_ATR * atr:
        blocked.append("candle_range")
    if params["vwap"] is None:
        blocked.append("vwap")
    applicable = []
    if config.OPENING_END <= clock < config.ORB_END:
        applicable.append((config.WARMUP_ORB, params["or_count"] >= config.ORB_MIN_CANDLES))
    if any(_in_window(clock, window) for window in config.PULLBACK_WINDOWS):
        applicable.append((config.WARMUP_PULLBACK, True))
    if _in_window(clock, config.REVERSION_WINDOW):
        applicable.append((config.WARMUP_REVERSION, True))
    if applicable and not any(len(segment) >= minimum and ready for minimum, ready in applicable):
        blocked.append("warmup")
    elif not applicable and "outside_session" not in blocked:
        # Przed lub między oknami porównujemy historię z najkrótszą rozgrzewką.
        if len(segment) < min(config.WARMUP_ORB, config.WARMUP_PULLBACK, config.WARMUP_REVERSION):
            blocked.append("warmup")
        else:
            blocked.append("no_setup")
    candidates = _candidates(segment, params, clock, orb_directions_today)
    if len({item[1] for item in candidates}) > 1:
        blocked.append("conflicting_setups")
        return result
    if not candidates:
        if not applicable or "warmup" in blocked:
            return result
        if params["er"] is not None and config.REVERSION_MAX_ER < params["er"] < config.PULLBACK_MIN_ER:
            blocked.append("regime_unclear")
        else:
            blocked.append("no_setup")
        return result
    setup, direction, score = max(candidates, key=lambda item: item[2])
    result["reasons"].append(setup)
    result["reasons"].extend(_score_details(setup, direction, params, clock)[1])
    tp = min(config.TP_ATR * atr, abs(params["vwap"] - price)) if setup == "VWAP_REVERSION" else max(config.MIN_TP, config.TP_ATR * atr)
    sl = config.SL_ATR * atr
    sign = 1 if direction == "LONG" else -1
    result.update(setup=setup, score=score, tp_points=tp, sl_points=sl,
                  tp_price=price + sign * tp, sl_price=price - sign * sl)
    if score < config.MIN_SCORE:
        blocked.append("score")
    if tp < config.MIN_TP or tp < config.COST_MULTIPLE * config.ASSUMED_COST_POINTS:
        blocked.append("target_too_small")
    if not blocked:
        result["signal"] = direction
    return result
