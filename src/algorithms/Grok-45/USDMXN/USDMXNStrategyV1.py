#!/usr/bin/env python3
"""Mikrostrategia USD/MXN na zamkniętych świecach M1.

Wskaźniki: EMA(5/13), RSI(7), Bollinger Bands(20, 2), ATR(14) i Momentum(5).
Filtr ATR: bieżący ATR jest dodatni i nie mniejszy niż 20. percentyl z ostatnich
100 dostępnych wartości ATR. Zakładany maksymalny czas pozycji to 20 minut.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Callable, Sequence
from zoneinfo import ZoneInfo

from .config import (
    ATR_MINIMUM_PERCENTILE,
    ATR_PERIOD,
    ATR_REFERENCE_LOOKBACK,
    BB_PERIOD,
    BB_STANDARD_DEVIATIONS,
    CANDLE_DURATION,
    DATABASE_SYMBOL,
    DELIVERY_PAUSE_AFTER_MINUTES,
    EMA_FAST_PERIOD,
    EMA_SLOW_PERIOD,
    GAP_DIAGNOSTIC_MINUTES_CAP,
    HISTORY_LIMIT,
    INSTRUMENT,
    LONG_RSI_MAXIMUM,
    LONG_RSI_MINIMUM,
    MAX_REPORTED_GAP_INTERVALS,
    MAX_HOLDING_MINUTES,
    MINIMUM_M1_CANDLES,
    MOMENTUM_LOOKBACK,
    MOMENTUM_MINIMUM_PERCENT,
    RSI_PERIOD,
    SAVE_NONE_SIGNALS,
    SHORT_RSI_MAXIMUM,
    SHORT_RSI_MINIMUM,
    STRATEGY_NAME,
)
from .indicators import calculate_indicator_series, percentile

logger = logging.getLogger(__name__)
WARSAW_TIMEZONE = ZoneInfo("Europe/Warsaw")
Candle = dict[str, Any]
MarketOpenCheck = Callable[[str, datetime], bool]


class USDMXNStrategyV1:
    """Ocenia świece USD/MXN bez zależności od innej strategii na tym instrumencie."""

    def __init__(self, market_is_open: MarketOpenCheck | None = None) -> None:
        """Przyjmuje opcjonalny kalendarz rynku do opisu planowych zamknięć."""
        self.market_is_open = market_is_open

    def evaluate(
        self,
        candles: Sequence[Candle],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Ocenia najnowszą zamkniętą świecę i buduje wynik dla istniejącego API.

        Args:
            candles: Świece M1 z czasem UTC lub lokalnym czasem Europe/Warsaw.
            now: Bieżący czas ze strefą; domyślnie aktualny czas UTC.

        Returns:
            Wynik API wraz z kompletnym obiektem JSON do signal_parameters.

        Raises:
            ValueError: Gdy dane OHLCV lub czas są nieprawidłowe albo świeca nie jest zamknięta.
        """
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None or current_time.utcoffset() is None:
            raise ValueError("Bieżący czas musi zawierać strefę czasową.")
        current_time = current_time.astimezone(timezone.utc)

        ordered = self._validate_and_order(candles)[-HISTORY_LIMIT:]
        current = ordered[-1]
        candle_open = current["timestamp_utc"]
        candle_close = candle_open + CANDLE_DURATION
        if candle_close > current_time:
            raise ValueError("Nie można oceniać niezakończonej świecy M1.")

        series = calculate_indicator_series(ordered)
        last_index = len(ordered) - 1
        values = {
            name: self._finite_number(indicator_values[last_index])
            for name, indicator_values in series.items()
        }
        rsi_values = series["rsi"]
        values["rsi_prev"] = self._finite_number(
            rsi_values[last_index - 1] if last_index > 0 else None
        )

        atr_start = max(0, len(ordered) - ATR_REFERENCE_LOOKBACK)
        atr_reference = [
            float(value)
            for value in series["atr"][atr_start:]
            if value is not None and isfinite(float(value))
        ]
        values["atr_minimum_threshold"] = self._finite_number(
            percentile(atr_reference, ATR_MINIMUM_PERCENTILE)
        )
        previous = ordered[-2] if len(ordered) > 1 else None
        conditions = self._conditions(current, values)
        required_values = (
            "ema_fast",
            "ema_slow",
            "rsi",
            "rsi_prev",
            "bb_middle",
            "bb_upper",
            "bb_lower",
            "bb_width",
            "atr",
            "momentum_5",
            "atr_minimum_threshold",
        )
        warmup_ready = len(ordered) >= MINIMUM_M1_CANDLES and all(
            values[name] is not None for name in required_values
        )
        long_ready = warmup_ready and self._all_required_conditions(
            conditions["long"], "long"
        )
        short_ready = warmup_ready and self._all_required_conditions(
            conditions["short"], "short"
        )

        if long_ready and short_ready:
            signal = "NONE"
            reason = "Konflikt kierunków: jednocześnie spełniono LONG i SHORT."
        elif long_ready:
            signal = "LONG"
            reason = "Warunki krótkoterminowego momentum wzrostowego są spełnione."
        elif short_ready:
            signal = "SHORT"
            reason = "Warunki krótkoterminowego momentum spadkowego są spełnione."
        elif not warmup_ready:
            signal = "NONE"
            reason = (
                f"Warm-up wskaźników: dostępnych {len(ordered)}/"
                f"{MINIMUM_M1_CANDLES} wymaganych świec M1 lub brakuje "
                "wartości wskaźników."
            )
        else:
            signal = "NONE"
            reason = "Warunki LONG ani SHORT nie zostały spełnione."

        price = float(current["close"])
        open_epoch = int(candle_open.timestamp())
        payload = self._build_payload(
            signal=signal,
            reason=reason,
            price=price,
            open_epoch=open_epoch,
            current=current,
            previous=previous,
            values=values,
            conditions=conditions,
            candles=ordered,
            warmup_ready=warmup_ready,
            atr_reference_sample_count=len(atr_reference),
        )
        logger.info("%s: %s (%s)", STRATEGY_NAME, signal, reason)
        return {
            "instrument": INSTRUMENT,
            "database_symbol": DATABASE_SYMBOL,
            "price": price,
            "timestamp": open_epoch,
            "reason": reason,
            "candle_close_time": candle_close.astimezone(
                WARSAW_TIMEZONE
            ).isoformat(),
            "candle_open_time": candle_open.astimezone(WARSAW_TIMEZONE).isoformat(),
            "candle_open_epoch": open_epoch,
            "signal": signal,
            "signal_parameters": payload,
        }

    @classmethod
    def _validate_and_order(cls, candles: Sequence[Candle]) -> list[Candle]:
        """Waliduje i sortuje świece, nie uzupełniając brakujących minut."""
        if not candles:
            raise ValueError("Brak świec wejściowych.")
        normalized: list[Candle] = []
        for source in candles:
            if not isinstance(source, dict):
                raise ValueError("Każda świeca musi być obiektem OHLCV.")
            candle = dict(source)
            instrument = candle.get("instrument")
            if instrument is not None and str(instrument).replace("/", "").upper() != INSTRUMENT:
                raise ValueError(f"Strategia przyjmuje wyłącznie {INSTRUMENT}.")
            candle["timestamp_utc"] = cls._normalize_timestamp(candle)
            normalized.append(candle)

        ordered = sorted(normalized, key=lambda candle: candle["timestamp_utc"])
        previous_timestamp: datetime | None = None
        for candle in ordered:
            timestamp = candle["timestamp_utc"]
            if timestamp.second != 0 or timestamp.microsecond != 0:
                raise ValueError("Świeca M1 musi zaczynać się równo w pełnej minucie.")
            if timestamp == previous_timestamp:
                raise ValueError("Historia zawiera powtórzony timestamp świecy.")
            previous_timestamp = timestamp

            try:
                prices = tuple(
                    float(candle[key]) for key in ("open", "high", "low", "close")
                )
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
                    volume = float(volume)
                except (TypeError, ValueError, OverflowError) as error:
                    raise ValueError("Świeca zawiera nieprawidłowy wolumen.") from error
                if not isfinite(volume) or volume < 0:
                    raise ValueError("Świeca zawiera nieprawidłowy wolumen.")
                candle["volume"] = volume
            candle.update(
                {
                    "open": prices[0],
                    "high": prices[1],
                    "low": prices[2],
                    "close": prices[3],
                }
            )
        return ordered

    @staticmethod
    def _normalize_timestamp(candle: Candle) -> datetime:
        """Zwraca czas świecy w UTC, bez zgadywania niejednoznacznego czasu lokalnego."""
        timestamp_key = next(
            (
                key
                for key in ("timestamp_utc", "datetime", "open_time")
                if candle.get(key) is not None
            ),
            None,
        )
        timestamp = candle.get(timestamp_key) if timestamp_key is not None else None
        if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
            if not isfinite(float(timestamp)):
                raise ValueError("Timestamp musi być liczbą skończoną.")
            return datetime.fromtimestamp(float(timestamp), timezone.utc)
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("Nie można odczytać czasu świecy.") from error
        if not isinstance(timestamp, datetime):
            raise ValueError(
                "Świeca musi zawierać timestamp_utc, datetime lub open_time."
            )
        if timestamp.tzinfo is not None and timestamp.utcoffset() is not None:
            return timestamp.astimezone(timezone.utc)

        offset_minutes = candle.get("utc_offset_minutes")
        if offset_minutes is not None:
            try:
                offset = float(offset_minutes)
            except (TypeError, ValueError, OverflowError) as error:
                raise ValueError("Nieprawidłowy utc_offset_minutes.") from error
            if not isfinite(offset) or not offset.is_integer() or not -840 <= offset <= 840:
                raise ValueError("Nieprawidłowy utc_offset_minutes.")
            local = timestamp.replace(tzinfo=timezone(timedelta(minutes=int(offset))))
            return local.astimezone(timezone.utc)

        if timestamp_key == "timestamp_utc":
            raise ValueError("timestamp_utc musi zawierać informację o strefie.")
        first_occurrence = timestamp.replace(tzinfo=WARSAW_TIMEZONE, fold=0)
        second_occurrence = timestamp.replace(tzinfo=WARSAW_TIMEZONE, fold=1)
        if first_occurrence.utcoffset() != second_occurrence.utcoffset():
            raise ValueError(
                "Lokalny czas jest niejednoznaczny lub nie istnieje; "
                "wymagany jest utc_offset_minutes."
            )
        return first_occurrence.astimezone(timezone.utc)

    @staticmethod
    def _finite_number(value: object) -> float | None:
        """Zamienia liczbę skończoną na float, a brakujące wartości na None."""
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if isfinite(number) else None

    def _missing_market_minutes(
        self, previous_open: datetime, current_open: datetime
    ) -> int:
        """Zlicza minuty bez świecy, kiedy rynek był otwarty, z ograniczonym skanem."""
        if self.market_is_open is None:
            return max(
                0,
                int((current_open - previous_open).total_seconds() // 60) - 1,
            )

        missing = 0
        cursor = previous_open + CANDLE_DURATION
        while cursor < current_open:
            if self.market_is_open(DATABASE_SYMBOL, cursor):
                missing += 1
                if missing > GAP_DIAGNOSTIC_MINUTES_CAP:
                    return missing
            cursor += CANDLE_DURATION
        return missing

    def _gap_diagnostics(self, candles: Sequence[Candle]) -> dict[str, Any]:
        """Raportuje odstępy; żadna luka nie tworzy świecy ani nie resetuje wskaźników."""
        intervals = []
        delivery_pause_count = 0
        scheduled_closure_count = 0
        for previous, current in zip(candles, candles[1:]):
            previous_open = previous["timestamp_utc"]
            current_open = current["timestamp_utc"]
            elapsed = int((current_open - previous_open).total_seconds() // 60)
            if elapsed <= 1:
                continue
            missing_market_minutes = self._missing_market_minutes(
                previous_open, current_open
            )
            delivery_pause = (
                elapsed > DELIVERY_PAUSE_AFTER_MINUTES
                and missing_market_minutes > 0
            )
            scheduled_closure = missing_market_minutes == 0
            delivery_pause_count += int(delivery_pause)
            scheduled_closure_count += int(scheduled_closure)
            intervals.append(
                {
                    "from_utc": previous_open.isoformat(),
                    "to_utc": current_open.isoformat(),
                    "elapsed_minutes": elapsed,
                    "missing_market_minutes_lower_bound": missing_market_minutes,
                    "missing_market_minutes_is_lower_bound": (
                        missing_market_minutes > GAP_DIAGNOSTIC_MINUTES_CAP
                    ),
                    "delivery_pause": delivery_pause,
                    "scheduled_closure_only": scheduled_closure,
                }
            )
        return {
            "non_contiguous_intervals": len(intervals),
            "delivery_pause_count": delivery_pause_count,
            "scheduled_closure_count": scheduled_closure_count,
            "delivery_pause_after_minutes": DELIVERY_PAUSE_AFTER_MINUTES,
            "recent_interval_limit": MAX_REPORTED_GAP_INTERVALS,
            "warmup_reset": False,
            "recent_intervals": intervals[-MAX_REPORTED_GAP_INTERVALS:],
        }

    @staticmethod
    def _conditions(
        current: Candle,
        indicators: dict[str, float | None],
    ) -> dict[str, dict[str, bool]]:
        """Oblicza warunki LONG i SHORT oraz diagnostykę alternatywy Bollingera."""
        close = float(current["close"])
        ema_fast = indicators["ema_fast"]
        ema_slow = indicators["ema_slow"]
        rsi = indicators["rsi"]
        rsi_previous = indicators["rsi_prev"]
        bb_middle = indicators["bb_middle"]
        bb_upper = indicators["bb_upper"]
        bb_lower = indicators["bb_lower"]
        momentum = indicators["momentum_5"]
        atr = indicators["atr"]
        atr_threshold = indicators["atr_minimum_threshold"]

        long_middle = bb_middle is not None and close > bb_middle
        long_alternative = (
            bb_lower is not None
            and rsi is not None
            and close > bb_lower
            and rsi < LONG_RSI_MAXIMUM
        )
        short_middle = bb_middle is not None and close < bb_middle
        short_alternative = (
            bb_upper is not None
            and rsi is not None
            and close < bb_upper
            and rsi > SHORT_RSI_MINIMUM
        )
        atr_filter = (
            atr is not None
            and atr_threshold is not None
            and atr > 0
            and atr >= atr_threshold
        )
        return {
            "long": {
                "ema_fast_above_slow": (
                    ema_fast is not None and ema_slow is not None and ema_fast > ema_slow
                ),
                "close_above_ema_fast": ema_fast is not None and close > ema_fast,
                "rsi_above_minimum": rsi is not None and rsi > LONG_RSI_MINIMUM,
                "rsi_increasing": (
                    rsi is not None
                    and rsi_previous is not None
                    and rsi > rsi_previous
                ),
                "momentum_above_minimum": (
                    momentum is not None and momentum > MOMENTUM_MINIMUM_PERCENT
                ),
                "close_above_bb_middle": long_middle,
                "close_above_bb_lower_and_rsi_below_maximum": long_alternative,
                "bollinger_filter": long_middle or long_alternative,
                "atr_not_extremely_low": atr_filter,
            },
            "short": {
                "ema_fast_below_slow": (
                    ema_fast is not None and ema_slow is not None and ema_fast < ema_slow
                ),
                "close_below_ema_fast": ema_fast is not None and close < ema_fast,
                "rsi_below_maximum": rsi is not None and rsi < SHORT_RSI_MAXIMUM,
                "rsi_decreasing": (
                    rsi is not None
                    and rsi_previous is not None
                    and rsi < rsi_previous
                ),
                "momentum_below_minimum": (
                    momentum is not None and momentum < -MOMENTUM_MINIMUM_PERCENT
                ),
                "close_below_bb_middle": short_middle,
                "close_below_bb_upper_and_rsi_above_minimum": short_alternative,
                "bollinger_filter": short_middle or short_alternative,
                "atr_not_extremely_low": atr_filter,
            },
        }

    @staticmethod
    def _all_required_conditions(
        conditions: dict[str, bool], direction: str
    ) -> bool:
        """Łączy obowiązkowe filtry, uwzględniając operator LUB dla Bollingera."""
        if direction == "long":
            required = (
                "ema_fast_above_slow",
                "close_above_ema_fast",
                "rsi_above_minimum",
                "rsi_increasing",
                "momentum_above_minimum",
                "bollinger_filter",
                "atr_not_extremely_low",
            )
        elif direction == "short":
            required = (
                "ema_fast_below_slow",
                "close_below_ema_fast",
                "rsi_below_maximum",
                "rsi_decreasing",
                "momentum_below_minimum",
                "bollinger_filter",
                "atr_not_extremely_low",
            )
        else:
            raise ValueError(f"Nieznany kierunek sygnału: {direction}.")
        return all(conditions[name] for name in required)

    def _build_payload(
        self,
        *,
        signal: str,
        reason: str,
        price: float,
        open_epoch: int,
        current: Candle,
        previous: Candle | None,
        values: dict[str, float | None],
        conditions: dict[str, dict[str, bool]],
        candles: Sequence[Candle],
        warmup_ready: bool,
        atr_reference_sample_count: int,
    ) -> dict[str, Any]:
        """Buduje pełny JSON sygnału z wartościami, progami i diagnostyką danych."""
        close_previous = (
            self._finite_number(previous.get("close"))
            if previous is not None
            else None
        )
        volume = self._finite_number(current.get("volume"))
        return {
            "instrument": INSTRUMENT,
            "price": price,
            "timestamp": open_epoch,
            "signal": signal,
            "reason": reason,
            "ema_fast": values["ema_fast"],
            "ema_slow": values["ema_slow"],
            "rsi": values["rsi"],
            "rsi_prev": values["rsi_prev"],
            "bb_middle": values["bb_middle"],
            "bb_upper": values["bb_upper"],
            "bb_lower": values["bb_lower"],
            "bb_width": values["bb_width"],
            "atr": values["atr"],
            "atr_minimum_threshold": values["atr_minimum_threshold"],
            "momentum_5": values["momentum_5"],
            "close_prev": close_previous,
            "high": float(current["high"]),
            "low": float(current["low"]),
            "volume": volume,
            "candle": {
                "open": float(current["open"]),
                "high": float(current["high"]),
                "low": float(current["low"]),
                "close": price,
                "close_prev": close_previous,
                "volume": volume,
            },
            "thresholds": {
                "ema_fast_period": EMA_FAST_PERIOD,
                "ema_slow_period": EMA_SLOW_PERIOD,
                "rsi_period": RSI_PERIOD,
                "long_rsi_minimum_exclusive": LONG_RSI_MINIMUM,
                "long_rsi_alternative_maximum_exclusive": LONG_RSI_MAXIMUM,
                "short_rsi_maximum_exclusive": SHORT_RSI_MAXIMUM,
                "short_rsi_alternative_minimum_exclusive": SHORT_RSI_MINIMUM,
                "bollinger_period": BB_PERIOD,
                "bollinger_standard_deviations": BB_STANDARD_DEVIATIONS,
                "atr_period": ATR_PERIOD,
                "atr_reference_lookback_candles": ATR_REFERENCE_LOOKBACK,
                "atr_minimum_percentile": ATR_MINIMUM_PERCENTILE,
                "momentum_lookback_candles": MOMENTUM_LOOKBACK,
                "momentum_minimum_percent": MOMENTUM_MINIMUM_PERCENT,
                "minimum_m1_candles": MINIMUM_M1_CANDLES,
                "history_limit_m1": HISTORY_LIMIT,
                "max_holding_minutes": MAX_HOLDING_MINUTES,
                "save_none_signals": SAVE_NONE_SIGNALS,
                "percentile_method": "interpolacja liniowa",
            },
            "trade_parameters": {"max_holding_minutes": MAX_HOLDING_MINUTES},
            "conditions": conditions,
            "data": {
                "m1_candles_available": len(candles),
                "atr_reference_sample_count": atr_reference_sample_count,
                "warmup_ready": warmup_ready,
                "gap_diagnostics": self._gap_diagnostics(candles),
            },
        }


def should_save_signal(result: dict[str, Any]) -> bool:
    """Stosuje centralną flagę zapisu wyników NONE."""
    return result.get("signal") != "NONE" or SAVE_NONE_SIGNALS


def process_new_candle(
    candles: Sequence[Candle],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Udostępnia prosty punkt wywołania niezależny od integracji bazy danych."""
    return USDMXNStrategyV1().evaluate(candles, now)
