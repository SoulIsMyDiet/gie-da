#!/usr/bin/env python3
"""Strategia USD/JPY: trend M5 i potwierdzenie wybicia na zamkniętej M1."""

from __future__ import annotations

# Backend: krótki opis reguł strategii do wykorzystania przez przyszły walidator.
STRATEGY_CONTEXT = (
    "Instrument USD/JPY; sygnał na zamkniętej M1. LONG/SHORT wymagają zgodnego "
    "trendowego układu EMA na M1 i M5, ADX14 M5 >= 18, RSI14 przecinającego 50 "
    "w kierunku transakcji oraz wybicia maksimum/minimum poprzedniej M1. "
    "Przy braku potwierdzenia, świec albo aktualnych danych wynik to NONE."
)

import logging
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Callable, Sequence
from zoneinfo import ZoneInfo

from chat_gpt6_usdmxn.indicators import (
    aggregate_m5,
    calculate_indicator_series,
)

from .config import (
    ADX_M5_MINIMUM,
    ADX_M5_PERIOD,
    ATR_M1_PERIOD,
    CANDLE_DURATION,
    DATABASE_SYMBOL,
    EMA_FAST_M1_PERIOD,
    EMA_FAST_M5_PERIOD,
    EMA_SLOW_M1_PERIOD,
    EMA_SLOW_M5_PERIOD,
    HISTORY_LIMIT,
    INSTRUMENT,
    MAX_HOLDING_MINUTES,
    MAX_M5_AGE,
    MAX_MISSING_MARKET_MINUTES_WITHOUT_RESET,
    MINIMUM_M1_CANDLES,
    MINIMUM_M5_CANDLES,
    M5_DURATION,
    RISK_REWARD_RATIO,
    RSI_M1_PERIOD,
    RSI_MIDPOINT,
    SAVE_NONE_SIGNALS,
    STOP_LOSS_ATR_MULTIPLE,
    STRATEGY_NAME,
)

logger = logging.getLogger(__name__)
WARSAW_TIMEZONE = ZoneInfo("Europe/Warsaw")
Candle = dict[str, Any]
MarketOpenCheck = Callable[[str, datetime], bool]


class USDJPYStrategyV1:
    """Ocenia świece USD/JPY i zwraca kompletny, diagnostyczny wynik."""

    def __init__(self, market_is_open: MarketOpenCheck) -> None:
        """Korzysta z istniejącego kalendarza rynku do oceny przerw w danych."""
        self.market_is_open = market_is_open

    def evaluate(
        self,
        candles: Sequence[Candle],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Ocenia ostatnią zamkniętą świecę M1, nie używając danych przyszłych."""
        now_utc = now or datetime.now(timezone.utc)
        if now_utc.tzinfo is None or now_utc.utcoffset() is None:
            raise ValueError("Bieżący czas musi zawierać strefę czasową.")
        now_utc = now_utc.astimezone(timezone.utc)

        ordered = self._validate_and_order(candles)
        current = ordered[-1]
        current_open = current["timestamp_utc"]
        current_close = current_open + CANDLE_DURATION
        if current_close > now_utc:
            raise ValueError("Nie można oceniać niezakończonej świecy M1.")

        segment, gap_state = self._current_segment(ordered)
        m5_candles = aggregate_m5(segment)
        eligible_m5_indexes = [
            index
            for index, m5_candle in enumerate(m5_candles)
            if m5_candle["timestamp_utc"] + M5_DURATION <= current_close
        ]
        latest_m5_index = (
            eligible_m5_indexes[-1] if eligible_m5_indexes else None
        )
        latest_m5 = (
            m5_candles[latest_m5_index]
            if latest_m5_index is not None
            else None
        )
        latest_m5_age = (
            current_close - (latest_m5["timestamp_utc"] + M5_DURATION)
            if latest_m5 is not None
            else None
        )
        fresh_m5_available = (
            latest_m5 is not None
            and latest_m5_age is not None
            and timedelta(0) <= latest_m5_age <= MAX_M5_AGE
        )

        indicator_series = calculate_indicator_series(segment, m5_candles)
        m1_index = len(segment) - 1
        current_values: dict[str, float | None] = {
            "ema9_m1": self._finite_number(
                indicator_series["ema9_m1"][m1_index]
            ),
            "ema21_m1": self._finite_number(
                indicator_series["ema21_m1"][m1_index]
            ),
            "rsi14_m1": self._finite_number(
                indicator_series["rsi14_m1"][m1_index]
            ),
            "rsi14_m1_previous": (
                self._finite_number(
                    indicator_series["rsi14_m1"][m1_index - 1]
                )
                if m1_index > 0
                else None
            ),
            "atr14_m1": self._finite_number(
                indicator_series["atr14_m1"][m1_index]
            ),
            "ema20_m5": (
                self._finite_number(
                    indicator_series["ema20_m5"][latest_m5_index]
                )
                if latest_m5_index is not None and fresh_m5_available
                else None
            ),
            "ema50_m5": (
                self._finite_number(
                    indicator_series["ema50_m5"][latest_m5_index]
                )
                if latest_m5_index is not None and fresh_m5_available
                else None
            ),
            "adx14_m5": (
                self._finite_number(
                    indicator_series["adx14_m5"][latest_m5_index]
                )
                if latest_m5_index is not None and fresh_m5_available
                else None
            ),
        }
        conditions = self._conditions(segment, current_values)

        warmup_ready = (
            len(segment) >= MINIMUM_M1_CANDLES
            and len(m5_candles) >= MINIMUM_M5_CANDLES
            and all(value is not None for value in current_values.values())
        )
        long_ready = warmup_ready and all(conditions["long"].values())
        short_ready = warmup_ready and all(conditions["short"].values())

        if not fresh_m5_available:
            direction = "NONE"
            evaluation_status = "DATA_INCOMPLETE"
            reason = (
                "Brak zamkniętej świecy M5 z ostatnich 5 minut; "
                "sygnał pominięto."
            )
        elif not warmup_ready:
            direction = "NONE"
            evaluation_status = "WARMUP"
            reason = (
                "Wskaźniki nie są gotowe: "
                f"M1 {len(segment)}/{MINIMUM_M1_CANDLES}, "
                f"M5 {len(m5_candles)}/{MINIMUM_M5_CANDLES}."
            )
        elif long_ready and short_ready:
            direction = "NONE"
            evaluation_status = "EVALUATED"
            reason = "Konflikt warunków LONG i SHORT; sygnał pominięto."
        elif long_ready:
            direction = "LONG"
            evaluation_status = "EVALUATED"
            reason = (
                "Trend wzrostowy M5 potwierdzony przez ADX; "
                "M1 przebiła poprzednie maksimum po przecięciu RSI 50."
            )
        elif short_ready:
            direction = "SHORT"
            evaluation_status = "EVALUATED"
            reason = (
                "Trend spadkowy M5 potwierdzony przez ADX; "
                "M1 przebiła poprzednie minimum po przecięciu RSI 50."
            )
        else:
            direction = "NONE"
            evaluation_status = "EVALUATED"
            reason = self._no_signal_reason(conditions)

        price = float(current["close"])
        atr = current_values["atr14_m1"]
        stop_distance = (
            STOP_LOSS_ATR_MULTIPLE * atr if atr is not None else None
        )
        target_distance = (
            RISK_REWARD_RATIO * stop_distance
            if stop_distance is not None
            else None
        )
        open_epoch = int(current_open.timestamp())
        close_time = current_close.astimezone(WARSAW_TIMEZONE).isoformat()
        payload = {
            "instrument": INSTRUMENT,
            "price": price,
            "timestamp": open_epoch,
            "signal": direction,
            "reason": reason,
            "evaluation_status": evaluation_status,
            "indicators": current_values,
            "candle": self._candle_parameters(segment),
            "m5_candle": self._m5_candle_parameters(
                latest_m5 if fresh_m5_available else None
            ),
            "trade_parameters": self._trade_parameters(
                direction, price, stop_distance, target_distance
            ),
            "conditions": conditions,
            "thresholds": {
                "ema9_m1_period": EMA_FAST_M1_PERIOD,
                "ema21_m1_period": EMA_SLOW_M1_PERIOD,
                "rsi14_m1_period": RSI_M1_PERIOD,
                "atr14_m1_period": ATR_M1_PERIOD,
                "ema20_m5_period": EMA_FAST_M5_PERIOD,
                "ema50_m5_period": EMA_SLOW_M5_PERIOD,
                "adx14_m5_period": ADX_M5_PERIOD,
                "m5_bucket_minutes": 5,
                "maximum_m5_age_minutes": int(
                    MAX_M5_AGE.total_seconds() // 60
                ),
                "minimum_m1_candles": MINIMUM_M1_CANDLES,
                "minimum_m5_candles": MINIMUM_M5_CANDLES,
                "history_limit_m1": HISTORY_LIMIT,
                "adx14_m5_minimum": ADX_M5_MINIMUM,
                "rsi_midpoint": RSI_MIDPOINT,
                "stop_loss_atr_multiple": STOP_LOSS_ATR_MULTIPLE,
                "risk_reward_ratio": RISK_REWARD_RATIO,
                "max_holding_minutes": MAX_HOLDING_MINUTES,
                "maximum_missing_market_minutes_without_reset":
                    MAX_MISSING_MARKET_MINUTES_WITHOUT_RESET,
                "save_none_signals": SAVE_NONE_SIGNALS,
            },
            "data": {
                "m1_candles_in_segment": len(segment),
                "completed_m5_candles": len(m5_candles),
                "latest_m5_age_minutes": (
                    latest_m5_age.total_seconds() / 60
                    if latest_m5_age is not None
                    else None
                ),
                "warmup_ready": warmup_ready,
                "gap_state": gap_state,
            },
        }
        logger.info("%s: %s (%s)", STRATEGY_NAME, direction, reason)
        return {
            "instrument": INSTRUMENT,
            "database_symbol": DATABASE_SYMBOL,
            "price": price,
            "timestamp": open_epoch,
            "candle_close_time": close_time,
            "candle_open_epoch": open_epoch,
            "signal": direction,
            "reason": reason,
            "evaluation_status": evaluation_status,
            "signal_parameters": payload,
        }

    @staticmethod
    def _validate_and_order(candles: Sequence[Candle]) -> list[Candle]:
        """Waliduje OHLCV i UTC, sortuje dane oraz odrzuca zduplikowane czasy."""
        if not candles:
            raise ValueError("Brak świec wejściowych.")
        normalized: list[Candle] = []
        for source in candles:
            if not isinstance(source, dict):
                raise ValueError("Każda świeca musi być obiektem OHLCV.")
            timestamp = source.get("timestamp_utc")
            if not isinstance(timestamp, datetime):
                raise ValueError("Świeca nie zawiera czasu timestamp_utc.")
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("Czas świecy musi zawierać strefę czasową.")
            timestamp_utc = timestamp.astimezone(timezone.utc)
            if timestamp_utc.second != 0 or timestamp_utc.microsecond != 0:
                raise ValueError("Czas otwarcia świecy M1 musi być pełną minutą.")

            prices: dict[str, float] = {}
            for name in ("open", "high", "low", "close"):
                value = USDJPYStrategyV1._finite_number(source.get(name))
                if value is None or value <= 0:
                    raise ValueError(f"Świeca zawiera nieprawidłową cenę {name}.")
                prices[name] = value
            if (
                prices["high"] < prices["low"]
                or prices["high"] < max(prices["open"], prices["close"])
                or prices["low"] > min(prices["open"], prices["close"])
            ):
                raise ValueError("Świeca zawiera niespójne wartości OHLC.")

            candle = dict(source)
            candle["timestamp_utc"] = timestamp_utc
            candle.update(prices)
            if source.get("volume") is not None:
                volume = USDJPYStrategyV1._finite_number(source["volume"])
                if volume is None or volume < 0:
                    raise ValueError("Świeca zawiera nieprawidłowy wolumen.")
                candle["volume"] = volume
            normalized.append(candle)

        ordered = sorted(normalized, key=lambda candle: candle["timestamp_utc"])
        if any(
            ordered[index - 1]["timestamp_utc"]
            == ordered[index]["timestamp_utc"]
            for index in range(1, len(ordered))
        ):
            raise ValueError("Historia zawiera zduplikowane świece M1.")
        return ordered[-HISTORY_LIMIT:]

    def _current_segment(
        self, candles: Sequence[Candle]
    ) -> tuple[list[Candle], dict[str, Any]]:
        """Resetuje warm-up po długiej luce sesyjnej, tolerując jedną minutę."""
        segment_start = 0
        single_missing_minutes = 0
        latest_unexpected_gap = 0
        latest_unexpected_gap_is_lower_bound = False
        for index in range(1, len(candles)):
            previous = candles[index - 1]["timestamp_utc"]
            current = candles[index]["timestamp_utc"]
            missing = self._missing_market_minutes(previous, current)
            if 0 < missing <= MAX_MISSING_MARKET_MINUTES_WITHOUT_RESET:
                single_missing_minutes += missing
            elif missing > MAX_MISSING_MARKET_MINUTES_WITHOUT_RESET:
                segment_start = index
                single_missing_minutes = 0
                latest_unexpected_gap = missing
                latest_unexpected_gap_is_lower_bound = True

        segment = list(candles[segment_start:])
        return segment, {
            "single_missing_minutes": single_missing_minutes,
            "latest_unexpected_gap_minutes": latest_unexpected_gap,
            "latest_unexpected_gap_minutes_is_lower_bound":
                latest_unexpected_gap_is_lower_bound,
            "warmup_reset": segment_start > 0,
            "segment_start_utc": segment[0]["timestamp_utc"].isoformat(),
        }

    def _missing_market_minutes(
        self, previous_open: datetime, current_open: datetime
    ) -> int:
        """Liczy brakujące minuty wyłącznie w godzinach działania rynku."""
        missing = 0
        cursor = previous_open + CANDLE_DURATION
        while cursor < current_open:
            if self.market_is_open(DATABASE_SYMBOL, cursor):
                missing += 1
                if missing > MAX_MISSING_MARKET_MINUTES_WITHOUT_RESET:
                    return missing
            cursor += CANDLE_DURATION
        return missing

    @staticmethod
    def _conditions(
        candles: Sequence[Candle], indicators: dict[str, float | None]
    ) -> dict[str, dict[str, bool]]:
        """Wylicza każdy warunek LONG i SHORT, również podczas warm-up."""
        current = candles[-1]
        previous = candles[-2] if len(candles) > 1 else None
        ema9 = indicators["ema9_m1"]
        ema21 = indicators["ema21_m1"]
        ema20_m5 = indicators["ema20_m5"]
        ema50_m5 = indicators["ema50_m5"]
        rsi = indicators["rsi14_m1"]
        rsi_previous = indicators["rsi14_m1_previous"]
        adx = indicators["adx14_m5"]
        return {
            "long": {
                "m5_trend_bullish": (
                    ema20_m5 is not None
                    and ema50_m5 is not None
                    and ema20_m5 > ema50_m5
                ),
                "adx_filter": adx is not None and adx >= ADX_M5_MINIMUM,
                "m1_trend_bullish": (
                    ema9 is not None and ema21 is not None and ema9 > ema21
                ),
                "pullback_touch_ema9": (
                    ema9 is not None and float(current["low"]) <= ema9
                ),
                "close_above_ema9": (
                    ema9 is not None and float(current["close"]) > ema9
                ),
                "rsi_cross_above_50": (
                    rsi_previous is not None
                    and rsi is not None
                    and rsi_previous <= RSI_MIDPOINT
                    and rsi > RSI_MIDPOINT
                ),
                "previous_high_breakout": (
                    previous is not None
                    and float(current["close"]) > float(previous["high"])
                ),
            },
            "short": {
                "m5_trend_bearish": (
                    ema20_m5 is not None
                    and ema50_m5 is not None
                    and ema20_m5 < ema50_m5
                ),
                "adx_filter": adx is not None and adx >= ADX_M5_MINIMUM,
                "m1_trend_bearish": (
                    ema9 is not None and ema21 is not None and ema9 < ema21
                ),
                "pullback_touch_ema9": (
                    ema9 is not None and float(current["high"]) >= ema9
                ),
                "close_below_ema9": (
                    ema9 is not None and float(current["close"]) < ema9
                ),
                "rsi_cross_below_50": (
                    rsi_previous is not None
                    and rsi is not None
                    and rsi_previous >= RSI_MIDPOINT
                    and rsi < RSI_MIDPOINT
                ),
                "previous_low_breakout": (
                    previous is not None
                    and float(current["close"]) < float(previous["low"])
                ),
            },
        }

    @staticmethod
    def _no_signal_reason(
        conditions: dict[str, dict[str, bool]]
    ) -> str:
        """Podaje konkretne warunki, które blokują oba kierunki."""
        descriptions = {
            "m5_trend_bullish": "brak wzrostowego trendu M5",
            "m5_trend_bearish": "brak spadkowego trendu M5",
            "adx_filter": "ADX M5 poniżej progu",
            "m1_trend_bullish": "brak wzrostowego trendu M1",
            "m1_trend_bearish": "brak spadkowego trendu M1",
            "pullback_touch_ema9": "brak dotknięcia EMA9 podczas korekty",
            "close_above_ema9": "zamknięcie nie wróciło ponad EMA9",
            "close_below_ema9": "zamknięcie nie wróciło poniżej EMA9",
            "rsi_cross_above_50": "brak przecięcia RSI powyżej 50",
            "rsi_cross_below_50": "brak przecięcia RSI poniżej 50",
            "previous_high_breakout": "brak wybicia ponad maksimum poprzedniej M1",
            "previous_low_breakout": "brak wybicia poniżej minimum poprzedniej M1",
        }
        failed = [
            f"{direction.upper()}: {descriptions[name]}"
            for direction, checks in conditions.items()
            for name, passed in checks.items()
            if not passed
        ]
        return "Brak sygnału; warunki blokujące: " + "; ".join(failed) + "."

    @staticmethod
    def _candle_parameters(
        candles: Sequence[Candle],
    ) -> dict[str, float | None]:
        """Zbiera OHLCV analizowanej i poziomy poprzedniej świecy M1."""
        current = candles[-1]
        previous = candles[-2] if len(candles) > 1 else {}
        return {
            "open": float(current["open"]),
            "high": float(current["high"]),
            "low": float(current["low"]),
            "close": float(current["close"]),
            "volume": USDJPYStrategyV1._finite_number(current.get("volume")),
            "previous_high": USDJPYStrategyV1._finite_number(
                previous.get("high")
            ),
            "previous_low": USDJPYStrategyV1._finite_number(
                previous.get("low")
            ),
        }

    @staticmethod
    def _m5_candle_parameters(
        candle: Candle | None,
    ) -> dict[str, float | str | None] | None:
        """Opisuje ostatnią kompletną i aktualną świecę M5."""
        if candle is None:
            return None
        opened = candle["timestamp_utc"]
        return {
            "timestamp_open_utc": opened.isoformat(),
            "timestamp_close_utc": (opened + M5_DURATION).isoformat(),
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "volume": USDJPYStrategyV1._finite_number(
                candle.get("volume")
            ),
        }

    @staticmethod
    def _trade_parameters(
        direction: str,
        entry_price: float,
        stop_distance: float | None,
        target_distance: float | None,
    ) -> dict[str, float | int | bool | None]:
        """Oblicza orientacyjne SL i TP bez zaokrąglania ATR."""
        stop_loss = None
        take_profit = None
        if (
            direction == "LONG"
            and stop_distance is not None
            and target_distance is not None
        ):
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + float(target_distance)
        elif (
            direction == "SHORT"
            and stop_distance is not None
            and target_distance is not None
        ):
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - float(target_distance)
        return {
            "entry_price": entry_price if direction != "NONE" else None,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "stop_loss_distance": stop_distance,
            "take_profit_distance": target_distance,
            "risk_reward_ratio": RISK_REWARD_RATIO,
            "max_holding_minutes": MAX_HOLDING_MINUTES,
            "levels_are_indicative": True,
        }

    @staticmethod
    def _finite_number(value: object) -> float | None:
        """Zwraca wyłącznie skończoną liczbę albo None."""
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if isfinite(number) else None
