#!/usr/bin/env python3
"""Strategia mikrotransakcji USD/MXN oparta na zamkniętych świecach M1 i M5."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Callable, Sequence
from zoneinfo import ZoneInfo

from .config import (
    ADX_M5_MINIMUM,
    ADX_M5_PERIOD,
    CANDLE_DURATION,
    DATABASE_SYMBOL,
    EMA_FAST_M1_PERIOD,
    EMA_FAST_M5_PERIOD,
    EMA_SLOW_M1_PERIOD,
    EMA_SLOW_M5_PERIOD,
    HISTORY_LIMIT,
    INSTRUMENT,
    MAX_HOLDING_MINUTES,
    MAX_MISSING_MARKET_MINUTES_WITHOUT_RESET,
    MINIMUM_M1_CANDLES,
    MINIMUM_M5_CANDLES,
    RISK_REWARD_RATIO,
    RSI_MIDPOINT,
    RSI_M1_PERIOD,
    SAVE_NONE_SIGNALS,
    STOP_LOSS_ATR_MULTIPLE,
    STRATEGY_NAME,
    ATR_M1_PERIOD,
)
from .indicators import aggregate_m5, calculate_indicator_series, finite_number

logger = logging.getLogger(__name__)
WARSAW_TIMEZONE = ZoneInfo("Europe/Warsaw")
Candle = dict[str, Any]
MarketOpenCheck = Callable[[str, datetime], bool]


class USDMXNStrategyV1:
    """Ocenia zamknięte świece USD/MXN i buduje pełny JSON wyniku."""

    def __init__(self, market_is_open: MarketOpenCheck) -> None:
        """Przyjmuje istniejący kalendarz API do rozpoznawania przerw sesji."""
        self.market_is_open = market_is_open

    def evaluate(
        self,
        candles: Sequence[Candle],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Ocenia najnowszą zamkniętą świecę; błędne dane zgłasza jako ValueError."""
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
        indicator_series = calculate_indicator_series(segment, m5_candles)
        m1_index = len(segment) - 1
        m5_index = len(m5_candles) - 1
        current_values = {
            "ema9_m1": indicator_series["ema9_m1"][m1_index],
            "ema21_m1": indicator_series["ema21_m1"][m1_index],
            "rsi14_m1": indicator_series["rsi14_m1"][m1_index],
            "rsi14_m1_previous": (
                indicator_series["rsi14_m1"][m1_index - 1]
                if m1_index > 0
                else None
            ),
            "atr14_m1": indicator_series["atr14_m1"][m1_index],
            "ema20_m5": (
                indicator_series["ema20_m5"][m5_index]
                if m5_index >= 0
                else None
            ),
            "ema50_m5": (
                indicator_series["ema50_m5"][m5_index]
                if m5_index >= 0
                else None
            ),
            "adx14_m5": (
                indicator_series["adx14_m5"][m5_index]
                if m5_index >= 0
                else None
            ),
        }
        indicators = {
            name: finite_number(value) for name, value in current_values.items()
        }
        conditions = self._conditions(segment, indicators)
        warmup_ready = (
            len(segment) >= MINIMUM_M1_CANDLES
            and len(m5_candles) >= MINIMUM_M5_CANDLES
            and all(value is not None for value in indicators.values())
        )
        long_ready = warmup_ready and all(conditions["long"].values())
        short_ready = warmup_ready and all(conditions["short"].values())
        if long_ready and short_ready:
            direction = "NONE"
            reason = "Konflikt kierunków: jednocześnie spełniono LONG i SHORT."
        elif long_ready:
            direction = "LONG"
            reason = "Potwierdzony trend wzrostowy M5 i wybicie po korekcie M1."
        elif short_ready:
            direction = "SHORT"
            reason = "Potwierdzony trend spadkowy M5 i wybicie po korekcie M1."
        elif not warmup_ready:
            direction = "NONE"
            reason = (
                "Warm-up wskaźników: "
                f"M1 {len(segment)}/{MINIMUM_M1_CANDLES}, "
                f"M5 {len(m5_candles)}/{MINIMUM_M5_CANDLES}; "
                "wymagane wskaźniki nie są jeszcze gotowe."
            )
        else:
            direction = "NONE"
            reason = self._no_signal_reason(conditions)

        price = float(current["close"])
        atr = indicators["atr14_m1"]
        stop_distance = (
            STOP_LOSS_ATR_MULTIPLE * atr if atr is not None else None
        )
        target_distance = (
            RISK_REWARD_RATIO * stop_distance
            if stop_distance is not None
            else None
        )
        trade_parameters = self._trade_parameters(
            direction, price, stop_distance, target_distance
        )
        local_close = current_close.astimezone(WARSAW_TIMEZONE).isoformat()
        open_epoch = int(current_open.timestamp())
        payload = {
            "instrument": INSTRUMENT,
            "price": price,
            "timestamp": open_epoch,
            "signal": direction,
            "reason": reason,
            "indicators": indicators,
            "candle": self._candle_parameters(segment),
            "m5_candle": self._m5_candle_parameters(m5_candles),
            "trade_parameters": trade_parameters,
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
                "history_limit_m1": HISTORY_LIMIT,
                "minimum_m1_candles": MINIMUM_M1_CANDLES,
                "minimum_m5_candles": MINIMUM_M5_CANDLES,
                "adx14_m5_minimum": ADX_M5_MINIMUM,
                "rsi_midpoint": RSI_MIDPOINT,
                "stop_loss_atr_multiple": STOP_LOSS_ATR_MULTIPLE,
                "risk_reward_ratio": RISK_REWARD_RATIO,
                "max_holding_minutes": MAX_HOLDING_MINUTES,
                "maximum_missing_market_minutes_without_reset":
                    MAX_MISSING_MARKET_MINUTES_WITHOUT_RESET,
                "save_none_signals": SAVE_NONE_SIGNALS,
                "market_schedule": "forex_ny_24_5",
            },
            "data": {
                "m1_candles_in_segment": len(segment),
                "completed_m5_candles": len(m5_candles),
                "warmup_ready": warmup_ready,
                "gap_state": gap_state,
            },
        }
        logger.info("%s: %s (%s)", STRATEGY_NAME, direction, reason)
        return {
            "instrument": INSTRUMENT,
            "database_symbol": DATABASE_SYMBOL,
            "price": price,
            "timestamp": local_close,
            "candle_open_time": current_open.astimezone(WARSAW_TIMEZONE).isoformat(),
            "candle_open_epoch": open_epoch,
            "signal": direction,
            "signal_parameters": payload,
        }

    @staticmethod
    def _validate_and_order(candles: Sequence[Candle]) -> list[Candle]:
        """Waliduje OHLCV i UTC, sortuje dane oraz odrzuca duplikaty czasu."""
        if not candles:
            raise ValueError("Brak świec wejściowych.")
        normalized: list[Candle] = []
        for candle in candles:
            if not isinstance(candle, dict):
                raise ValueError("Każda świeca musi być obiektem OHLCV.")
            timestamp = candle.get("timestamp_utc")
            if (
                not isinstance(timestamp, datetime)
                or timestamp.tzinfo is None
                or timestamp.utcoffset() is None
            ):
                raise ValueError("Świeca musi zawierać świadomy timestamp UTC.")
            normalized.append(
                {**candle, "timestamp_utc": timestamp.astimezone(timezone.utc)}
            )
        ordered = sorted(normalized, key=lambda candle: candle["timestamp_utc"])
        previous_time: datetime | None = None
        for candle in ordered:
            timestamp = candle["timestamp_utc"]
            if timestamp.second != 0 or timestamp.microsecond != 0:
                raise ValueError("Świeca M1 musi zaczynać się równo w pełnej minucie.")
            if previous_time is not None and timestamp == previous_time:
                raise ValueError("Historia zawiera powtórzony timestamp świecy.")
            previous_time = timestamp
            try:
                prices = tuple(float(candle[key]) for key in ("open", "high", "low", "close"))
            except (KeyError, TypeError, ValueError, OverflowError) as error:
                raise ValueError("Świeca nie zawiera poprawnych wartości OHLC.") from error
            if (
                not all(isfinite(value) and value > 0 for value in prices)
                or prices[1] < max(prices[0], prices[3])
                or prices[2] > min(prices[0], prices[3])
                or prices[2] > prices[1]
            ):
                raise ValueError("Świeca zawiera nieprawidłowe wartości OHLC.")
            volume = candle.get("volume")
            if volume is not None:
                try:
                    normalized_volume = float(volume)
                except (TypeError, ValueError, OverflowError) as error:
                    raise ValueError("Świeca zawiera nieprawidłowy wolumen.") from error
                if not isfinite(normalized_volume) or normalized_volume < 0:
                    raise ValueError("Świeca zawiera nieprawidłowy wolumen.")
                candle["volume"] = normalized_volume
            candle.update(
                {
                    "open": prices[0],
                    "high": prices[1],
                    "low": prices[2],
                    "close": prices[3],
                }
            )
        return ordered

    def _current_segment(
        self, candles: Sequence[Candle]
    ) -> tuple[list[Candle], dict[str, Any]]:
        """Resetuje warm-up po luce nieoczekiwanej, tolerując jedną brakującą minutę."""
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
                latest_unexpected_gap_is_lower_bound = (
                    missing > MAX_MISSING_MARKET_MINUTES_WITHOUT_RESET
                )
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
        """Liczy brakujące minuty sesji, kończąc po przekroczeniu progu resetu."""
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
        """Wylicza osobno wszystkie warunki LONG i SHORT, również przy warm-up."""
        current = candles[-1]
        previous = candles[-2] if len(candles) > 1 else None
        ema9 = indicators["ema9_m1"]
        ema21 = indicators["ema21_m1"]
        ema20_m5 = indicators["ema20_m5"]
        ema50_m5 = indicators["ema50_m5"]
        rsi = indicators["rsi14_m1"]
        rsi_previous = indicators["rsi14_m1_previous"]
        adx = indicators["adx14_m5"]
        long = {
            "m5_trend_bullish": ema20_m5 is not None and ema50_m5 is not None and ema20_m5 > ema50_m5,
            "adx_filter": adx is not None and adx >= ADX_M5_MINIMUM,
            "m1_trend_bullish": ema9 is not None and ema21 is not None and ema9 > ema21,
            "pullback_touch_ema9": ema9 is not None and float(current["low"]) <= ema9,
            "close_above_ema9": ema9 is not None and float(current["close"]) > ema9,
            "rsi_cross_above_50": rsi_previous is not None and rsi is not None and rsi_previous <= RSI_MIDPOINT and rsi > RSI_MIDPOINT,
            "previous_high_breakout": previous is not None and float(current["close"]) > float(previous["high"]),
        }
        short = {
            "m5_trend_bearish": ema20_m5 is not None and ema50_m5 is not None and ema20_m5 < ema50_m5,
            "adx_filter": adx is not None and adx >= ADX_M5_MINIMUM,
            "m1_trend_bearish": ema9 is not None and ema21 is not None and ema9 < ema21,
            "pullback_touch_ema9": ema9 is not None and float(current["high"]) >= ema9,
            "close_below_ema9": ema9 is not None and float(current["close"]) < ema9,
            "rsi_cross_below_50": rsi_previous is not None and rsi is not None and rsi_previous >= RSI_MIDPOINT and rsi < RSI_MIDPOINT,
            "previous_low_breakout": previous is not None and float(current["close"]) < float(previous["low"]),
        }
        return {"long": long, "short": short}

    @staticmethod
    def _no_signal_reason(conditions: dict[str, dict[str, bool]]) -> str:
        """Wymienia warunki blokujące oba kierunki zamiast zwracać ogólny komunikat."""
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
    def _candle_parameters(candles: Sequence[Candle]) -> dict[str, float | None]:
        """Zbiera OHLCV bieżącej i poziomy poprzedniej świecy M1."""
        current = candles[-1]
        previous = candles[-2] if len(candles) > 1 else {}
        return {
            "open": float(current["open"]),
            "high": float(current["high"]),
            "low": float(current["low"]),
            "close": float(current["close"]),
            "volume": finite_number(current.get("volume")),
            "previous_high": finite_number(previous.get("high")),
            "previous_low": finite_number(previous.get("low")),
        }

    @staticmethod
    def _m5_candle_parameters(
        candles: Sequence[Candle],
    ) -> dict[str, float | int | str | None] | None:
        """Opisuje ostatnią kompletną świecę M5 wykorzystaną przez wskaźniki."""
        if not candles:
            return None
        candle = candles[-1]
        opened = candle["timestamp_utc"]
        return {
            "timestamp_open_utc": opened.isoformat(),
            "timestamp_close_utc": (opened + timedelta(minutes=5)).isoformat(),
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "volume": finite_number(candle.get("volume")),
        }

    @staticmethod
    def _trade_parameters(
        direction: str,
        entry_price: float,
        stop_distance: float | None,
        target_distance: float | None,
    ) -> dict[str, float | int | None]:
        """Oblicza orientacyjne SL i TP lub zwraca null dla braku sygnału."""
        stop_loss = None
        take_profit = None
        if direction == "LONG" and stop_distance is not None and target_distance is not None:
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + target_distance
        elif direction == "SHORT" and stop_distance is not None and target_distance is not None:
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - target_distance
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


def should_save_signal(result: dict[str, Any]) -> bool:
    """Stosuje jeden przełącznik konfiguracji dla zapisów NONE."""
    return result.get("signal") != "NONE" or SAVE_NONE_SIGNALS
