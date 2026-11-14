#!/usr/bin/env python3
"""Strategia wybiciowa DAX40 dla zamkniętych świec M1."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from math import isclose, isfinite
from typing import Any, Callable, Sequence
from zoneinfo import ZoneInfo

from .config import (
    ADX_MINIMUM,
    ADX_PERIOD,
    ATR_PERIOD,
    BREAKOUT_BUFFER_ATR,
    BREAKOUT_LOOKBACK,
    CANDLE_DURATION,
    DATABASE_SYMBOL,
    EMA_FAST_PERIOD,
    EMA_MIDDLE_PERIOD,
    EMA_SLOW_PERIOD,
    INSTRUMENT,
    LONG_CLOSE_POSITION_MINIMUM,
    LONG_RSI_MAXIMUM,
    LONG_RSI_MINIMUM,
    MAX_MISSING_MINUTES_WITHOUT_RESET,
    MINIMUM_CANDLE_BODY_ATR,
    MINIMUM_SEGMENT_CANDLES,
    RSI_PERIOD,
    SAVE_NONE_SIGNALS,
    SHORT_CLOSE_POSITION_MAXIMUM,
    SHORT_RSI_MAXIMUM,
    SHORT_RSI_MINIMUM,
    STRATEGY_VERSION,
)
from .indicators import calculate_indicator_series, finite_number

logger = logging.getLogger(__name__)
WARSAW_TIMEZONE = ZoneInfo("Europe/Warsaw")
MarketOpenCheck = Callable[[str, datetime], bool]
Candle = dict[str, Any]


def _at_least(value: float, threshold: float) -> bool:
    """Uwzględnia błąd float wyłącznie przy równych progach włącznie."""
    return value >= threshold or isclose(
        value,
        threshold,
        rel_tol=1e-12,
        abs_tol=1e-12,
    )


def _at_most(value: float, threshold: float) -> bool:
    """Uwzględnia błąd float wyłącznie przy równych progach włącznie."""
    return value <= threshold or isclose(
        value,
        threshold,
        rel_tol=1e-12,
        abs_tol=1e-12,
    )


def _candle_open_time(candle: Candle) -> datetime:
    """Odczytuje początek świecy jako świadomy znacznik czasu UTC."""
    value = candle.get("timestamp_utc", candle.get("datetime"))
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Świeca musi zawierać świadomy czas otwarcia.")
    if value.utcoffset() is None:
        raise ValueError("Czas otwarcia świecy musi mieć prawidłowy offset.")
    return value.astimezone(timezone.utc)


def _valid_candle(candle: Candle) -> bool:
    """Sprawdza obecność i skończoność wymaganych wartości OHLC."""
    try:
        open_price = float(candle["open"])
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return (
        all(isfinite(value) for value in (open_price, high, low, close))
        and min(open_price, high, low, close) > 0
        and high >= max(open_price, close)
        and low <= min(open_price, close)
        and high >= low
    )


def _missing_expected_minutes(
    previous_open: datetime,
    current_open: datetime,
    market_is_open: MarketOpenCheck,
) -> tuple[int, bool]:
    """Liczy lukę do progu resetu i oznacza, czy dalsze liczenie pominięto."""
    cursor = previous_open + CANDLE_DURATION
    missing = 0
    while cursor < current_open:
        if market_is_open(DATABASE_SYMBOL, cursor):
            missing += 1
            if missing > MAX_MISSING_MINUTES_WITHOUT_RESET:
                return missing, True
        cursor += CANDLE_DURATION
    return missing, False


def _current_segment(
    candles: Sequence[Candle],
    market_is_open: MarketOpenCheck,
) -> tuple[list[Candle], dict[str, Any]]:
    """Odcina historię po ostatniej luce większej od ustawionego limitu."""
    segment_start = 0
    single_gap_count = 0
    latest_gap_size = 0
    latest_gap_scan_truncated = False
    for index in range(1, len(candles)):
        missing, scan_truncated = _missing_expected_minutes(
            _candle_open_time(candles[index - 1]),
            _candle_open_time(candles[index]),
            market_is_open,
        )
        if 0 < missing <= MAX_MISSING_MINUTES_WITHOUT_RESET:
            single_gap_count += missing
        elif missing > MAX_MISSING_MINUTES_WITHOUT_RESET:
            latest_gap_size = missing
            latest_gap_scan_truncated = scan_truncated
            segment_start = index
            single_gap_count = 0

    return list(candles[segment_start:]), {
        "single_missing_minutes": single_gap_count,
        "latest_unexpected_gap_minutes": latest_gap_size,
        "gap_length_scan_truncated": latest_gap_scan_truncated,
        "gap_recovery": latest_gap_size > MAX_MISSING_MINUTES_WITHOUT_RESET,
        "segment_candles": len(candles) - segment_start,
    }


def _none_result(
    candle: Candle | None,
    reason: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Buduje stały JSON-kształt dla braku sygnału."""
    if candle is None:
        open_time = None
        close_time = None
        price = None
    else:
        open_utc = _candle_open_time(candle)
        close_utc = open_utc + CANDLE_DURATION
        open_time = open_utc.astimezone(WARSAW_TIMEZONE).isoformat()
        close_time = close_utc.astimezone(WARSAW_TIMEZONE).isoformat()
        price = finite_number(candle.get("close"))
    parameters = {**parameters, "reason": reason}
    return {
        "instrument": INSTRUMENT,
        "database_symbol": DATABASE_SYMBOL,
        "price": price,
        "timestamp": close_time,
        "timestamp_epoch": (
            int((_candle_open_time(candle) + CANDLE_DURATION).timestamp())
            if candle is not None
            else None
        ),
        "candle_open_time": open_time,
        "candle_open_epoch": (
            int(_candle_open_time(candle).timestamp()) if candle is not None else None
        ),
        "signal": "NONE",
        "signal_parameters": parameters,
    }


class DAX40StrategyV1:
    """Generuje breakout-sygnały z zamkniętych świec M1 GER30."""

    def __init__(self, market_is_open: MarketOpenCheck) -> None:
        """Tworzy strategię korzystającą z harmonogramu przekazanego przez API."""
        self.market_is_open = market_is_open

    def evaluate(
        self,
        candles: Sequence[Candle],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Ocenia najnowszą świecę bez dostępu do danych z przyszłości."""
        if now is not None and (now.tzinfo is None or now.utcoffset() is None):
            raise ValueError("Bieżący czas musi zawierać strefę czasową.")
        now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if not candles:
            return _none_result(
                None,
                "insufficient_history",
                {
                    "strategy_version": STRATEGY_VERSION,
                    "segment_candles": 0,
                    "gap_recovery": False,
                },
            )

        try:
            ordered = sorted(candles, key=_candle_open_time)
        except (OverflowError, TypeError, ValueError) as error:
            logger.warning("Pominięto świece z nieprawidłowym timestampem: %s", error)
            return _none_result(
                None,
                "missing_data",
                {
                    "strategy_version": STRATEGY_VERSION,
                    "database_symbol": DATABASE_SYMBOL,
                    "segment_candles": 0,
                    "gap_recovery": False,
                    "data_error": "invalid_candle_timestamp",
                },
            )
        if any(not _valid_candle(candle) for candle in ordered):
            return _none_result(
                ordered[-1],
                "missing_data",
                {
                    "strategy_version": STRATEGY_VERSION,
                    "segment_candles": 0,
                    "gap_recovery": False,
                },
            )
        open_times = [_candle_open_time(candle) for candle in ordered]
        if len(set(open_times)) != len(open_times):
            return _none_result(
                ordered[-1],
                "missing_data",
                {
                    "strategy_version": STRATEGY_VERSION,
                    "segment_candles": 0,
                    "gap_recovery": False,
                    "data_error": "duplicate_candle_timestamp",
                },
            )

        current = ordered[-1]
        current_open = open_times[-1]
        current_close = current_open + CANDLE_DURATION
        if now_utc < current_close:
            return _none_result(
                current,
                "forming_candle",
                {
                    "strategy_version": STRATEGY_VERSION,
                    "segment_candles": 0,
                    "gap_recovery": False,
                },
            )
        if not self.market_is_open(DATABASE_SYMBOL, current_open):
            return _none_result(
                current,
                "outside_session",
                {
                    "strategy_version": STRATEGY_VERSION,
                    "segment_candles": 0,
                    "gap_recovery": False,
                },
            )

        segment, gap_parameters = _current_segment(ordered, self.market_is_open)
        parameters: dict[str, Any] = {
            "strategy_version": STRATEGY_VERSION,
            "database_symbol": DATABASE_SYMBOL,
            "timestamp_utc": current_close.isoformat(),
            "data_age_seconds": round((now_utc - current_close).total_seconds(), 3),
            **gap_parameters,
        }
        indicators = calculate_indicator_series(segment)
        index = len(segment) - 1
        current_indicators = {
            name: values[index] for name, values in indicators.items()
        }
        parameters.update(
            self._decision_parameters(
                segment,
                current_indicators,
                index,
                gap_parameters,
            )
        )
        if len(segment) < MINIMUM_SEGMENT_CANDLES:
            reason = (
                "gap_recovery"
                if gap_parameters["gap_recovery"]
                else "insufficient_history"
            )
            parameters["minimum_segment_candles"] = MINIMUM_SEGMENT_CANDLES
            return _none_result(current, reason, parameters)

        required_values = (
            current_indicators["ema9"],
            current_indicators["ema21"],
            current_indicators["ema50"],
            current_indicators["rsi14"],
            current_indicators["atr14"],
            current_indicators["plus_di14"],
            current_indicators["minus_di14"],
            current_indicators["adx14"],
            current_indicators["breakout_high_10"],
            current_indicators["breakout_low_10"],
        )
        if any(value is None for value in required_values):
            return _none_result(current, "indicator_not_ready", parameters)

        if float(current_indicators["atr14"]) <= 0:
            return _none_result(current, "indicator_not_ready", parameters)

        decisions = self._evaluate_conditions(
            segment,
            indicators,
            current_indicators,
            index,
        )
        parameters["conditions"] = decisions["conditions"]
        if decisions["signal"] == "NONE":
            parameters["diagnostic_reasons"] = decisions["reasons"]
            return _none_result(current, decisions["reason"], parameters)

        result = _none_result(
            current,
            decisions["reason"],
            parameters,
        )
        result["signal"] = decisions["signal"]
        result["signal_parameters"] = {
            **parameters,
            "reason": decisions["reason"],
        }
        return result

    @staticmethod
    def _decision_parameters(
        candles: Sequence[Candle],
        current: dict[str, float | None],
        index: int,
        gap_parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Zbiera wskaźniki i dane świecy wykorzystane przez reguły sygnału."""
        candle = candles[index]
        open_price = float(candle["open"])
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        candle_range = high - low
        atr = current["atr14"]
        candle_body = abs(close - open_price)
        close_position = (close - low) / candle_range if candle_range > 0 else None
        body_atr = candle_body / atr if atr is not None and atr > 0 else None
        breakout_distance: float | None = None
        if atr is not None and atr > 0:
            breakout_high = current["breakout_high_10"]
            breakout_low = current["breakout_low_10"]
            if breakout_high is not None and close > breakout_high:
                breakout_distance = (close - breakout_high) / atr
            elif breakout_low is not None and close < breakout_low:
                breakout_distance = (breakout_low - close) / atr

        return {
            "ema9": finite_number(current["ema9"]),
            "ema21": finite_number(current["ema21"]),
            "ema50": finite_number(current["ema50"]),
            "rsi14": finite_number(current["rsi14"]),
            "atr14": finite_number(atr),
            "adx14": finite_number(current["adx14"]),
            "plus_di14": finite_number(current["plus_di14"]),
            "minus_di14": finite_number(current["minus_di14"]),
            "breakout_high_10": finite_number(current["breakout_high_10"]),
            "breakout_low_10": finite_number(current["breakout_low_10"]),
            "candle_body": candle_body,
            "candle_range": candle_range,
            "candle_body_atr": finite_number(body_atr),
            "close_position": finite_number(close_position),
            "breakout_distance_atr": finite_number(breakout_distance),
            "volume": finite_number(candle.get("volume")),
            "single_missing_minutes": gap_parameters["single_missing_minutes"],
            "latest_unexpected_gap_minutes": gap_parameters[
                "latest_unexpected_gap_minutes"
            ],
            "gap_length_scan_truncated": gap_parameters[
                "gap_length_scan_truncated"
            ],
            "gap_recovery": gap_parameters["gap_recovery"],
            "segment_candles": len(candles),
            "breakout_lookback": BREAKOUT_LOOKBACK,
            "minimum_segment_candles": MINIMUM_SEGMENT_CANDLES,
            "indicator_periods": {
                "ema9": EMA_FAST_PERIOD,
                "ema21": EMA_MIDDLE_PERIOD,
                "ema50": EMA_SLOW_PERIOD,
                "rsi14": RSI_PERIOD,
                "atr14": ATR_PERIOD,
                "adx14": ADX_PERIOD,
            },
            "decision_thresholds": {
                "adx_minimum": ADX_MINIMUM,
                "breakout_buffer_atr": BREAKOUT_BUFFER_ATR,
                "minimum_candle_body_atr": MINIMUM_CANDLE_BODY_ATR,
                "long_close_position_minimum": LONG_CLOSE_POSITION_MINIMUM,
                "short_close_position_maximum": SHORT_CLOSE_POSITION_MAXIMUM,
                "long_rsi_minimum": LONG_RSI_MINIMUM,
                "long_rsi_maximum": LONG_RSI_MAXIMUM,
                "short_rsi_minimum": SHORT_RSI_MINIMUM,
                "short_rsi_maximum": SHORT_RSI_MAXIMUM,
            },
        }

    @staticmethod
    def _evaluate_conditions(
        candles: Sequence[Candle],
        indicators: dict[str, list[float | None]],
        current: dict[str, float | None],
        index: int,
    ) -> dict[str, Any]:
        """Sprawdza trend, wybicie, korpus, pozycję close i RSI."""
        candle = candles[index]
        close = float(candle["close"])
        open_price = float(candle["open"])
        high = float(candle["high"])
        low = float(candle["low"])
        atr = float(current["atr14"])
        body = abs(close - open_price)
        candle_range = high - low
        close_position = (close - low) / candle_range if candle_range > 0 else None
        buffer = BREAKOUT_BUFFER_ATR * atr
        high_level = float(current["breakout_high_10"])
        low_level = float(current["breakout_low_10"])

        long_trend = (
            float(current["ema9"]) > float(current["ema21"])
            > float(current["ema50"])
            and _at_least(float(current["adx14"]), ADX_MINIMUM)
        )
        short_trend = (
            float(current["ema9"]) < float(current["ema21"])
            < float(current["ema50"])
            and _at_least(float(current["adx14"]), ADX_MINIMUM)
        )
        long_breakout = close > high_level + buffer
        short_breakout = close < low_level - buffer
        candle_body_ok = _at_least(
            body,
            MINIMUM_CANDLE_BODY_ATR * atr,
        )
        long_close_position_ok = (
            close_position is not None
            and _at_least(close_position, LONG_CLOSE_POSITION_MINIMUM)
        )
        short_close_position_ok = (
            close_position is not None
            and _at_most(close_position, SHORT_CLOSE_POSITION_MAXIMUM)
        )
        rsi = float(current["rsi14"])
        long_momentum = (
            _at_least(rsi, LONG_RSI_MINIMUM)
            and _at_most(rsi, LONG_RSI_MAXIMUM)
        )
        short_momentum = (
            _at_least(rsi, SHORT_RSI_MINIMUM)
            and _at_most(rsi, SHORT_RSI_MAXIMUM)
        )

        long_conditions = {
            "trend": long_trend,
            "level_crossed": long_breakout,
            "breakout": long_breakout,
            "candle_body": candle_body_ok,
            "close_position": long_close_position_ok,
            "momentum": long_momentum,
        }
        short_conditions = {
            "trend": short_trend,
            "level_crossed": short_breakout,
            "breakout": short_breakout,
            "candle_body": candle_body_ok,
            "close_position": short_close_position_ok,
            "momentum": short_momentum,
        }
        long_signal = all(long_conditions.values())
        short_signal = all(short_conditions.values())
        if long_signal:
            return {
                "signal": "LONG",
                "reason": "long_breakout_confirmed",
                "conditions": {"long": long_conditions, "short": short_conditions},
                "reasons": [],
            }
        if short_signal:
            return {
                "signal": "SHORT",
                "reason": "short_breakout_confirmed",
                "conditions": {"long": long_conditions, "short": short_conditions},
                "reasons": [],
            }

        reasons: list[str] = []
        if not long_trend and not short_trend:
            reasons.append("trend_not_confirmed")
        if not long_breakout and not short_breakout:
            reasons.append("no_breakout")
        if not candle_body_ok or not (
            long_close_position_ok or short_close_position_ok
        ):
            reasons.append("volatility_filter")
        if not long_momentum and not short_momentum:
            reasons.append("momentum_not_confirmed")
        if not reasons:
            reasons.append("no_signal")
        return {
            "signal": "NONE",
            "reason": reasons[0],
            "conditions": {"long": long_conditions, "short": short_conditions},
            "reasons": reasons,
        }

    def backtest(self, candles: Sequence[Candle]) -> dict[str, Any]:
        """Przetwarza historię chronologicznie bez zapisu i bez dźwięku."""
        ordered = sorted(candles, key=_candle_open_time)
        signal_counts = {"LONG": 0, "SHORT": 0, "NONE": 0}
        reason_counts: dict[str, int] = {}
        results: list[dict[str, Any]] = []
        for index, candle in enumerate(ordered):
            candle_close = _candle_open_time(candle) + CANDLE_DURATION
            result = self.evaluate(
                ordered[: index + 1],
                now=candle_close + timedelta(milliseconds=1),
            )
            signal_counts[result["signal"]] += 1
            reason = result["signal_parameters"]["reason"]
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            results.append(result)
        return {
            "strategy_version": STRATEGY_VERSION,
            "instrument": INSTRUMENT,
            "candles_processed": len(ordered),
            "signal_counts": signal_counts,
            "reason_counts": reason_counts,
            "results": results,
        }

    @staticmethod
    def to_json(result: dict[str, Any]) -> str:
        """Serializuje wynik bez dopuszczania NaN i Infinity."""
        return json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def should_save_signal(result: dict[str, Any]) -> bool:
    """Stosuje jedyny przełącznik zapisu dla wyników NONE."""
    return result.get("signal") != "NONE" or SAVE_NONE_SIGNALS
