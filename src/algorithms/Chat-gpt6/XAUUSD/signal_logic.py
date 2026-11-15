"""Samodzielny rdzeń sygnałów XAUUSD, bez zapisu i bez pobierania danych."""

from dataclasses import asdict
from datetime import datetime, timedelta
from math import isfinite
from typing import Any

from .config import CONFIG
from .indicators import aggregate_m5, indicator_series, session_vwap
from .schedule import UTC, WARSAW, market_open, missing_expected


def _normalize_candles(candles: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Sprawdza i sortuje świece M1; niepoprawne dane powodują ValueError."""
    if not isinstance(candles, list):
        raise ValueError("Dane wejściowe muszą być listą świec.")
    normalized = []
    seen = set()
    for row in candles:
        if not isinstance(row, dict):
            raise ValueError("Każda świeca musi być słownikiem.")
        stamp = row.get("timestamp_utc")
        if (not isinstance(stamp, datetime) or stamp.tzinfo is None or
                stamp.utcoffset() != timedelta(0) or stamp.second or stamp.microsecond):
            raise ValueError("Czas otwarcia musi być pełną minutą świadomego UTC.")
        stamp = stamp.astimezone(UTC)
        if stamp in seen:
            raise ValueError("Powtórzony czas otwarcia świecy.")
        seen.add(stamp)
        try:
            values = {key: float(row[key]) for key in ("open", "high", "low", "close")}
            volume = row["volume"]
            if volume is not None:
                volume = float(volume)
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise ValueError("Niepoprawne OHLC lub wolumen.") from error
        if (not all(isfinite(value) and value > 0 for value in values.values()) or
                values["high"] < max(values["open"], values["close"], values["low"]) or
                values["low"] > min(values["open"], values["close"]) or
                (volume is not None and (not isfinite(volume) or volume < 0))):
            raise ValueError("Niepoprawny zakres OHLC lub wolumen.")
        if stamp + timedelta(minutes=1) <= now and market_open(stamp):
            normalized.append({"timestamp_utc": stamp, **values, "volume": volume})
    return sorted(normalized, key=lambda candle: candle["timestamp_utc"])


def _segment(candles: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Wybiera ostatni segment i opisuje tolerowane oraz resetujące luki."""
    start = 0
    gaps = []
    resets = 0
    for index in range(1, len(candles)):
        previous = candles[index - 1]["timestamp_utc"]
        current = candles[index]["timestamp_utc"]
        count = missing_expected(previous, current)
        if count:
            reset = count >= CONFIG.unexpected_gap_minutes
            gaps.append({"after_utc": previous.isoformat(), "before_utc": current.isoformat(),
                         "missing_expected_minutes": count, "reset": reset})
            if reset:
                resets += 1
                start = index
    segment = candles[start:]
    return segment, {"reset_count": resets, "gaps": gaps,
                     "segment_start_utc": segment[0]["timestamp_utc"].isoformat()}


def _latest(series: dict[str, list[float | None]], offset: int = 1) -> dict[str, float | None]:
    """Zwraca ostatni lub poprzedni zestaw wartości szeregów wskaźników."""
    return {name: values[-offset] if len(values) >= offset else None
            for name, values in series.items()}


def _result_parameters(now: datetime) -> dict[str, Any]:
    """Przygotowuje ślad konfiguracji, ograniczeń i czasu obliczenia."""
    return {
        "config": asdict(CONFIG), "calibration": "Progi nie są skalibrowane na danych XAUUSD.",
        "calculated_at_utc": now.isoformat(),
        "costs": {"spread": None, "slippage": None, "fees": None,
                  "description": "Koszty i wykonanie nie są uwzględnione."},
        "entry_price_is_estimate": True, "reasons": [], "safety_checks": {},
        "gap_state": None, "warmup": None, "indicators": None, "candidates": {},
        "risk": None,
    }


def _evaluate_direction(direction: str, candle: dict[str, Any], previous: dict[str, Any],
                        m1: dict[str, float], prior_m1: dict[str, float],
                        m5: dict[str, float], vwap: float, high: float,
                        low: float) -> dict[str, Any]:
    """Ocenia setup i punktację jednego kierunku bez obchodzenia warunków bezpieczeństwa."""
    long = direction == "LONG"
    fast, middle, slow = (m1[f"ema_{period}"] for period in
                          (CONFIG.ema_fast, CONFIG.ema_middle, CONFIG.ema_slow))
    f5, m5_middle, s5 = (m5[f"ema_{period}"] for period in
                         (CONFIG.ema_fast, CONFIG.ema_middle, CONFIG.ema_slow))
    price = candle["close"]
    trend = fast > middle > slow if long else fast < middle < slow
    m5_aligned = f5 > m5_middle > s5 if long else f5 < m5_middle < s5
    m5_not_opposed = f5 >= s5 if long else f5 <= s5
    rsi_ok = (CONFIG.long_rsi_minimum <= m1["rsi"] <= CONFIG.long_rsi_maximum and
              m1["rsi"] > prior_m1["rsi"]) if long else (
              CONFIG.short_rsi_minimum <= m1["rsi"] <= CONFIG.short_rsi_maximum and
              m1["rsi"] < prior_m1["rsi"])
    pullback = trend and (previous["low"] <= prior_m1[f"ema_{CONFIG.ema_fast}"] +
                          CONFIG.pullback_distance_atr * prior_m1["atr"] and price > fast
                          if long else
                          previous["high"] >= prior_m1[f"ema_{CONFIG.ema_fast}"] -
                          CONFIG.pullback_distance_atr * prior_m1["atr"] and price < fast)
    breakout = ((price > high + CONFIG.breakout_buffer_atr * m1["atr"] and fast > slow)
                if long else
                (price < low - CONFIG.breakout_buffer_atr * m1["atr"] and fast < slow))
    setups = [name for name, match in (("TREND_PULLBACK", pullback),
                                      ("MOMENTUM_BREAKOUT", breakout)) if match]
    components = {
        "ema": CONFIG.weight_ema if trend else 0,
        "m5": CONFIG.weight_m5 if m5_aligned else 0,
        "rsi": CONFIG.weight_rsi if rsi_ok else 0,
        "atr": CONFIG.weight_atr if CONFIG.atr_minimum <= m1["atr"] <= CONFIG.atr_maximum else 0,
        "vwap": CONFIG.weight_vwap if (price > vwap if long else price < vwap) else 0,
        "setup": CONFIG.weight_setup if setups else 0,
    }
    checks = {"m5_not_opposed": m5_not_opposed, "rsi_momentum": rsi_ok,
              "setup_present": bool(setups), "minimum_score": sum(components.values()) >= CONFIG.minimum_score}
    return {"setups": setups, "checks": checks, "score_components": components,
            "setup_checks": {"trend_pullback": pullback, "momentum_breakout": breakout,
                             "m1_ema_aligned": trend, "m5_ema_aligned": m5_aligned,
                             "price_on_vwap_side": price > vwap if long else price < vwap},
            "score": sum(components.values()), "eligible": all(checks.values())}


def _generate_signal(candles: list[dict], now: datetime | None = None) -> dict:
    """Zwraca sygnał z zamkniętych świec; błędne dane i obliczenia dają bezpieczne NONE."""
    # Backend: żadne operacje wejścia/wyjścia ani zlecenia nie są wykonywane.
    result: dict[str, Any] = {"instrument": CONFIG.instrument, "price": None, "timestamp": None,
                              "candle_open_time": None, "candle_open_epoch": None, "signal": "NONE",
                              "signal_parameters": None}
    if now is None:
        now = datetime.now(UTC)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        result["signal_parameters"] = {"reasons": ["Niepoprawny czas obliczenia: wymagany świadomy datetime."]}
        return result
    now = now.astimezone(UTC)
    params = _result_parameters(now)
    result["signal_parameters"] = params
    try:
        rows = _normalize_candles(candles, now)
        if not rows:
            params["reasons"].append("Brak prawidłowych zamkniętych świec w godzinach handlu.")
            return result
        last = rows[-1]
        opened = last["timestamp_utc"]
        closed = opened + timedelta(minutes=1)
        result.update({"price": last["close"], "timestamp": closed.astimezone(WARSAW).isoformat(),
                       "candle_open_time": opened.astimezone(WARSAW).isoformat(),
                       "candle_open_epoch": int(opened.timestamp())})
        segment, gap_state = _segment(rows)
        trailing_end = now.replace(second=0, microsecond=0)
        trailing_missing = missing_expected(opened, trailing_end)
        gap_state.update({"trailing_missing_expected_minutes": trailing_missing,
                          "trailing_reset": trailing_missing >= CONFIG.unexpected_gap_minutes})
        params["gap_state"] = gap_state
        m5_candles = aggregate_m5(segment, now)
        params["warmup"] = {"m1_count": len(segment), "m5_count": len(m5_candles),
                            "m1_required": CONFIG.minimum_m1, "m5_required": CONFIG.minimum_m5,
                            "ready": len(segment) >= CONFIG.minimum_m1 and
                                     len(m5_candles) >= CONFIG.minimum_m5 and not gap_state["trailing_reset"]}
        m1_series = indicator_series(segment)
        m5_series = indicator_series(m5_candles)
        m1 = _latest(m1_series)
        previous_m1 = _latest(m1_series, 2)
        m5 = _latest(m5_series)
        vwap = session_vwap(segment)
        session_start_utc = datetime.fromisoformat(vwap["session_start_utc"])
        session_rows = [item for item in segment if item["timestamp_utc"] >= session_start_utc]
        vwap["missing_expected_minutes"] = (
            missing_expected(session_start_utc - timedelta(minutes=1),
                             session_rows[0]["timestamp_utc"]) +
            sum(missing_expected(a["timestamp_utc"], b["timestamp_utc"])
                for a, b in zip(session_rows, session_rows[1:])))
        vwap["complete"] = (vwap["missing_expected_minutes"] == 0 and
                            vwap["missing_volume_candles"] == 0)
        lookback = segment[-CONFIG.extrema_lookback - 1:-1]
        high = max((item["high"] for item in lookback), default=None)
        low = min((item["low"] for item in lookback), default=None)
        params["indicators"] = {
            "m1": m1, "m1_previous": previous_m1, "m5": m5,
            "m5_previous": _latest(m5_series, 2),
            "m5_last_open_utc": m5_candles[-1]["timestamp_utc"].isoformat() if m5_candles else None,
            "m5_last_candle": {key: m5_candles[-1][key] for key in ("open", "high", "low", "close", "volume")}
            if m5_candles else None,
            "vwap": vwap, "previous_high": high, "previous_low": low,
            "extrema_candles": [{"timestamp_utc": item["timestamp_utc"].isoformat(),
                                 "high": item["high"], "low": item["low"]} for item in lookback],
            "previous_candle": {key: segment[-2][key] for key in ("open", "high", "low", "close", "volume")}
            if len(segment) >= 2 else None,
            "current_candle": {key: last[key] for key in ("open", "high", "low", "close", "volume")},
        }
        age = (now - closed).total_seconds()
        m5_age = ((now - (m5_candles[-1]["timestamp_utc"] + timedelta(minutes=5))).total_seconds()
                  if m5_candles else None)
        params["ages_seconds"] = {"m1": age, "m5": m5_age}
        required_m1 = list(m1.values()) + [previous_m1.get("rsi"), previous_m1.get("atr"),
                                           previous_m1.get(f"ema_{CONFIG.ema_fast}")]
        checks = {
            "market_open_now": market_open(now),
            "warmup_complete": params["warmup"]["ready"],
            "fresh_m1": age <= CONFIG.maximum_signal_age_seconds,
            "fresh_m5": m5_age is not None and m5_age <= CONFIG.maximum_m5_age_seconds,
            "no_missing_latest_minutes": trailing_missing == 0,
            "indicators_available": all(value is not None and isfinite(value) for value in
                                        required_m1 + list(m5.values())),
            "vwap_available": vwap["value"] is not None and isfinite(vwap["value"]),
            "extrema_available": len(lookback) == CONFIG.extrema_lookback,
            "atr_in_range": m1["atr"] is not None and CONFIG.atr_minimum <= m1["atr"] <= CONFIG.atr_maximum,
            "bb_width_in_range": m1["bb_width"] is not None and
                                 CONFIG.bb_width_minimum <= m1["bb_width"] <= CONFIG.bb_width_maximum,
        }
        params["safety_checks"] = checks
        params["reasons"].extend(f"Warunek bezpieczeństwa niespełniony: {name}."
                                 for name, passed in checks.items() if not passed)
        if not all(checks.values()):
            return result
        for direction in ("LONG", "SHORT"):
            params["candidates"][direction] = _evaluate_direction(
                direction, last, segment[-2], m1, previous_m1, m5, vwap["value"], high, low)
        eligible = [direction for direction, candidate in params["candidates"].items() if candidate["eligible"]]
        if len(eligible) != 1:
            params["reasons"].append("Konflikt kierunków." if eligible else "Brak kwalifikującego się setupu.")
            return result
        direction = eligible[0]
        candidate = params["candidates"][direction]
        sign = 1 if direction == "LONG" else -1
        result["signal"] = direction
        params["selected_setup"] = candidate["setups"][0]
        params["strength"] = "strong" if candidate["score"] >= CONFIG.strong_score else "normal"
        params["risk"] = {"entry_estimate": last["close"],
                          "stop_loss": last["close"] - sign * CONFIG.stop_loss_atr * m1["atr"],
                          "take_profit": last["close"] + sign * CONFIG.take_profit_atr * m1["atr"],
                          "max_hold_minutes": CONFIG.max_hold_minutes}
        if params["risk"]["stop_loss"] <= 0 or params["risk"]["take_profit"] <= 0:
            result["signal"] = "NONE"
            params["reasons"].append("Niepoprawny, niedodatni poziom ryzyka.")
        return result
    except (ValueError, TypeError, OverflowError, ArithmeticError) as error:
        # Niepoprawne wejście lub przepełnienie nie może wyprodukować sygnału.
        result.update({"price": None, "timestamp": None, "candle_open_time": None,
                       "candle_open_epoch": None, "signal": "NONE"})
        params["reasons"].append(f"Niepoprawne dane lub obliczenia: {error}")
        return result


def _clean_numbers(value: Any) -> tuple[Any, bool]:
    """Zamienia nieskończone liczby i NaN na null oraz sygnalizuje problem."""
    if isinstance(value, float) and not isfinite(value):
        return None, True
    if isinstance(value, dict):
        cleaned = {key: _clean_numbers(item) for key, item in value.items()}
        return {key: item[0] for key, item in cleaned.items()}, any(item[1] for item in cleaned.values())
    if isinstance(value, list):
        cleaned_list = [_clean_numbers(item) for item in value]
        return [item[0] for item in cleaned_list], any(item[1] for item in cleaned_list)
    return value, False


def generate_signal(candles: list[dict], now: datetime | None = None) -> dict:
    """Oblicza sygnał bez operacji sieciowych ani zapisu.

    candles to lista OHLCV z czasem otwarcia timestamp_utc; now to świadomy czas
    obliczenia lub None dla bieżącego czasu UTC. Zwraca słownik zgodny z JSON;
    błędne dane i nieprawidłowe liczby wymuszają NONE zamiast wyjątku.
    """
    result, invalid_number = _clean_numbers(_generate_signal(candles, now))
    if invalid_number:
        result["signal"] = "NONE"
        result["signal_parameters"]["reasons"].append("Obliczenie zawierało nieskończoność lub NaN.")
    return result
