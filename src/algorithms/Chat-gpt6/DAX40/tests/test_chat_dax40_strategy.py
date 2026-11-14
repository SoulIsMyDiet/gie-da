"""Testy wskaźników, luk, sygnałów i backtestu Chat-gpt6 DAX40."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

PACKAGE_PATH = Path(__file__).resolve().parents[1]
PACKAGE_SPEC = importlib.util.spec_from_file_location(
    "chat_gpt6_dax40_test",
    PACKAGE_PATH / "__init__.py",
    submodule_search_locations=[str(PACKAGE_PATH)],
)
assert PACKAGE_SPEC is not None and PACKAGE_SPEC.loader is not None
PACKAGE = importlib.util.module_from_spec(PACKAGE_SPEC)
sys.modules[PACKAGE_SPEC.name] = PACKAGE
PACKAGE_SPEC.loader.exec_module(PACKAGE)

from chat_gpt6_dax40_test import indicators as indicator_module
from chat_gpt6_dax40_test import DAX40StrategyV1 as strategy_module
from chat_gpt6_dax40_test.DAX40StrategyV1 import DAX40StrategyV1
from chat_gpt6_dax40_test.indicators import (
    _atr,
    _breakout_levels,
    _directional_movement,
    _ema,
    _rsi,
)
from chat_gpt6_dax40_test.storage import DAX40SignalStorage

UTC = timezone.utc
WARSAW = ZoneInfo("Europe/Warsaw")
ALWAYS_OPEN = lambda _symbol, _timestamp: True


def make_candles(count: int, start: datetime | None = None) -> list[dict[str, object]]:
    """Tworzy świece testowe z poprawnym timestampem UTC i zakresem OHLC."""
    first = start or datetime(2026, 1, 12, 8, 0, tzinfo=UTC)
    return [
        {
            "timestamp_utc": first + timedelta(minutes=index),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": None,
        }
        for index in range(count)
    ]


def indicator_fixture(
    count: int,
    direction: str,
) -> dict[str, list[float | None]]:
    """Buduje wskaźniki wskazujące poprawny trend dla wybranego kierunku."""
    if direction == "LONG":
        values = {
            "ema9": 102.0,
            "ema21": 101.0,
            "ema50": 100.0,
            "rsi14": 60.0,
            "atr14": 1.0,
            "plus_di14": 30.0,
            "minus_di14": 10.0,
            "adx14": 25.0,
            "breakout_high_10": 100.0,
            "breakout_low_10": 90.0,
        }
    else:
        values = {
            "ema9": 98.0,
            "ema21": 99.0,
            "ema50": 100.0,
            "rsi14": 40.0,
            "atr14": 1.0,
            "plus_di14": 10.0,
            "minus_di14": 30.0,
            "adx14": 25.0,
            "breakout_high_10": 110.0,
            "breakout_low_10": 100.0,
        }
    return {key: [value] * count for key, value in values.items()}


class IndicatorTests(unittest.TestCase):
    """Sprawdza ręczne przykłady wskaźników i poziomów wybicia."""

    def test_ema_wilder_and_true_range_known_values(self) -> None:
        ema = _ema([1.0, 2.0, 3.0], 2)
        self.assertAlmostEqual(ema[1], 5 / 3)
        self.assertAlmostEqual(ema[2], 23 / 9)
        self.assertEqual(_rsi([1.0, 2.0, 3.0, 4.0, 5.0], 4)[-1], 100.0)
        candles = [
            {"high": 12.0, "low": 10.0, "close": 11.0},
            {"high": 14.0, "low": 12.0, "close": 13.0},
            {"high": 15.0, "low": 12.0, "close": 14.0},
        ]
        self.assertAlmostEqual(_atr(candles, 2)[-1], 2.75)

    def test_adx_directional_movement_is_ready_after_full_warmup(self) -> None:
        candles = []
        for index in range(40):
            close = 100.0 + index
            candles.append(
                {
                    "open": close - 0.5,
                    "high": close + 0.5,
                    "low": close - 1.0,
                    "close": close,
                }
            )
        plus_di, minus_di, adx = _directional_movement(candles, 14)
        self.assertIsNone(adx[26])
        self.assertIsNotNone(adx[27])
        self.assertGreater(plus_di[-1], minus_di[-1])

    def test_adx_recovers_after_flat_start(self) -> None:
        candles = []
        for index in range(50):
            if index < 15:
                open_price = high = low = close = 100.0
            else:
                close = 100.0 + index - 14
                open_price = close - 0.25
                high = close + 0.5
                low = close - 0.5
            candles.append(
                {
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                }
            )

        plus_di, minus_di, adx = _directional_movement(candles, 14)

        self.assertIsNotNone(adx[27])
        self.assertTrue(all(value is not None for value in adx[27:]))
        self.assertGreater(plus_di[-1], minus_di[-1])

    def test_breakout_levels_exclude_current_candle(self) -> None:
        candles = make_candles(11)
        candles[-1]["high"] = 1000.0
        highs, lows = _breakout_levels(candles, 10)
        self.assertEqual(highs[-1], 101.0)
        self.assertEqual(lows[-1], 99.0)


class StrategyTests(unittest.TestCase):
    """Sprawdza sygnały, rozgrzewkę, luki i chronologię backtestu."""

    def _signal_candles(self, direction: str) -> list[dict[str, object]]:
        candles = make_candles(100)
        candles[-2]["close"] = 99.0 if direction == "LONG" else 101.0
        if direction == "LONG":
            candles[-1].update(
                open=99.5,
                high=101.0,
                low=99.0,
                close=100.8,
            )
        else:
            candles[-1].update(
                open=100.5,
                high=101.0,
                low=99.0,
                close=99.2,
            )
        return candles

    def test_long_and_short_require_fresh_directional_breakouts(self) -> None:
        strategy = DAX40StrategyV1(ALWAYS_OPEN)
        for direction in ("LONG", "SHORT"):
            candles = self._signal_candles(direction)
            series = indicator_fixture(len(candles), direction)
            series["adx14"][-1] = 18.0
            series["rsi14"][-1] = 52.0 if direction == "LONG" else 48.0
            if direction == "LONG":
                candles[-1].update(high=101.4, low=99.0)
            else:
                candles[-1].update(high=101.0, low=98.6)
            now = candles[-1]["timestamp_utc"] + timedelta(
                minutes=1, milliseconds=1
            )
            with patch.object(strategy_module, "calculate_indicator_series", return_value=series):
                result = strategy.evaluate(candles, now=now)
            self.assertEqual(result["signal"], direction, result)
            self.assertEqual(
                result["signal_parameters"]["reason"],
                f"{direction.lower()}_breakout_confirmed",
            )
            self.assertEqual(
                result["signal_parameters"]["breakout_high_10"]
                if direction == "LONG"
                else result["signal_parameters"]["breakout_low_10"],
                100.0 if direction == "LONG" else 100.0,
            )
            self.assertIn(
                "decision_thresholds",
                result["signal_parameters"],
            )
            self.assertIn(
                "previous_close",
                result["signal_parameters"],
            )

    def test_current_breakout_does_not_repeat_when_it_was_already_active(self) -> None:
        candles = self._signal_candles("LONG")
        candles[-2]["close"] = 101.0
        series = indicator_fixture(len(candles), "LONG")
        series["breakout_high_10"][-2] = 100.0
        strategy = DAX40StrategyV1(ALWAYS_OPEN)
        with patch.object(strategy_module, "calculate_indicator_series", return_value=series):
            result = strategy.evaluate(
                candles,
                now=candles[-1]["timestamp_utc"] + timedelta(
                    minutes=1, milliseconds=1
                ),
            )
        self.assertEqual(result["signal"], "NONE")
        self.assertIn(
            "breakout_already_active",
            result["signal_parameters"]["diagnostic_reasons"],
        )

    def test_fewer_than_100_candles_and_gap_recovery_return_none(self) -> None:
        strategy = DAX40StrategyV1(ALWAYS_OPEN)
        short_history = make_candles(99)
        result = strategy.evaluate(
            short_history,
            now=short_history[-1]["timestamp_utc"] + timedelta(minutes=1),
        )
        self.assertEqual(result["signal"], "NONE")
        self.assertEqual(
            result["signal_parameters"]["reason"],
            "insufficient_history",
        )

        candles = make_candles(104)
        candles = [
            candle
            for index, candle in enumerate(candles)
            if index not in (50, 51)
        ]
        result = strategy.evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        )
        self.assertEqual(result["signal"], "NONE")
        self.assertEqual(result["signal_parameters"]["reason"], "gap_recovery")
        self.assertEqual(
            result["signal_parameters"]["latest_unexpected_gap_minutes"],
            2,
        )
        self.assertTrue(
            result["signal_parameters"]["gap_length_scan_truncated"]
        )

    def test_single_missing_minute_keeps_the_same_indicator_segment(self) -> None:
        candles = make_candles(102)
        candles = [candle for index, candle in enumerate(candles) if index != 50]
        strategy = DAX40StrategyV1(ALWAYS_OPEN)
        series = indicator_fixture(len(candles), "LONG")
        with patch.object(strategy_module, "calculate_indicator_series", return_value=series):
            result = strategy.evaluate(
                candles,
                now=candles[-1]["timestamp_utc"] + timedelta(
                    minutes=1, milliseconds=1
                ),
            )
        self.assertEqual(
            result["signal_parameters"]["single_missing_minutes"],
            1,
        )
        self.assertFalse(result["signal_parameters"]["gap_recovery"])
        self.assertFalse(
            result["signal_parameters"]["gap_length_scan_truncated"]
        )

    def test_five_missing_minutes_report_a_truncated_lower_bound(self) -> None:
        candles = make_candles(110)
        candles = [
            candle
            for index, candle in enumerate(candles)
            if not 50 <= index <= 54
        ]
        result = DAX40StrategyV1(ALWAYS_OPEN).evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        )
        self.assertEqual(
            result["signal_parameters"]["latest_unexpected_gap_minutes"],
            2,
        )
        self.assertTrue(
            result["signal_parameters"]["gap_length_scan_truncated"]
        )

    def test_weekend_closure_is_not_counted_as_a_gap(self) -> None:
        def dax_session_open(_symbol: str, timestamp: datetime) -> bool:
            local = timestamp.astimezone(WARSAW)
            return (
                local.weekday() < 5
                and (local.hour * 60 + local.minute) >= 70
                and (local.hour * 60 + local.minute) < 22 * 60
            )

        candles = [
            {
                "timestamp_utc": datetime(2026, 1, 16, 20, 59, tzinfo=UTC),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
            },
            {
                "timestamp_utc": datetime(2026, 1, 19, 0, 10, tzinfo=UTC),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
            },
        ]
        strategy = DAX40StrategyV1(dax_session_open)
        result = strategy.evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(
                minutes=1, milliseconds=1
            ),
        )
        self.assertEqual(
            result["signal_parameters"]["latest_unexpected_gap_minutes"],
            0,
        )
        self.assertFalse(result["signal_parameters"]["gap_recovery"])

    def test_last_closed_candle_is_evaluated_at_session_end(self) -> None:
        berlin = ZoneInfo("Europe/Berlin")

        def dax_session_open(_symbol: str, timestamp: datetime) -> bool:
            local = timestamp.astimezone(berlin)
            minute_of_day = local.hour * 60 + local.minute
            return (
                local.weekday() < 5
                and 70 <= minute_of_day < 22 * 60
            )

        candles = make_candles(
            100,
            start=datetime(2026, 1, 12, 19, 20, tzinfo=UTC),
        )
        candles[-1].update(
            open=100.5,
            high=102.2,
            low=100.2,
            close=102.0,
        )
        strategy = DAX40StrategyV1(dax_session_open)
        indicators = indicator_fixture(len(candles), "LONG")

        with patch.object(
            strategy_module,
            "calculate_indicator_series",
            return_value=indicators,
        ):
            result = strategy.evaluate(
                candles,
                now=candles[-1]["timestamp_utc"] + timedelta(
                    minutes=1, milliseconds=1
                ),
            )

        self.assertEqual(result["signal"], "LONG")
        self.assertEqual(
            result["timestamp"],
            "2026-01-12T22:00:00+01:00",
        )

    def test_forming_candle_is_rejected(self) -> None:
        candles = make_candles(100)
        result = DAX40StrategyV1(ALWAYS_OPEN).evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(seconds=30),
        )
        self.assertEqual(result["signal"], "NONE")
        self.assertEqual(result["signal_parameters"]["reason"], "forming_candle")

    def test_invalid_timestamp_returns_none_without_raising(self) -> None:
        result = DAX40StrategyV1(ALWAYS_OPEN).evaluate(
            [
                {
                    "timestamp_utc": "not-a-timestamp",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                }
            ],
            now=datetime(2026, 1, 12, 9, 0, tzinfo=UTC),
        )

        self.assertEqual(result["signal"], "NONE")
        self.assertEqual(
            result["signal_parameters"]["reason"],
            "missing_data",
        )
        self.assertEqual(
            result["signal_parameters"]["data_error"],
            "invalid_candle_timestamp",
        )

    def test_zero_range_and_zero_atr_return_none_without_raising(self) -> None:
        candles = make_candles(100)
        for candle in candles:
            candle.update(open=100.0, high=100.0, low=100.0, close=100.0)

        result = DAX40StrategyV1(ALWAYS_OPEN).evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        )

        self.assertEqual(result["signal"], "NONE")
        self.assertEqual(
            result["signal_parameters"]["reason"],
            "indicator_not_ready",
        )
        self.assertEqual(result["signal_parameters"]["atr14"], 0.0)
        self.assertIsNone(result["signal_parameters"]["close_position"])

    def test_dst_offsets_and_invalid_optional_volume_are_serializable(self) -> None:
        strategy = DAX40StrategyV1(ALWAYS_OPEN)
        expected_times = (
            (
                datetime(2026, 1, 15, 9, 0, tzinfo=UTC),
                "2026-01-15T10:00:00+01:00",
                "2026-01-15T10:01:00+01:00",
            ),
            (
                datetime(2026, 7, 15, 9, 0, tzinfo=UTC),
                "2026-07-15T11:00:00+02:00",
                "2026-07-15T11:01:00+02:00",
            ),
        )
        for opened, expected_open, expected_close in expected_times:
            candle = {
                "timestamp_utc": opened,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": "not-a-number",
            }
            result = strategy.evaluate(
                [candle],
                now=opened + timedelta(minutes=1, milliseconds=1),
            )
            self.assertEqual(result["candle_open_time"], expected_open)
            self.assertEqual(result["timestamp"], expected_close)
            self.assertIsNone(result["signal_parameters"]["volume"])

    def test_historical_backtest_uses_increasing_prefixes_only(self) -> None:
        candles = make_candles(4)
        strategy = DAX40StrategyV1(ALWAYS_OPEN)
        received_lengths: list[int] = []

        def no_signal(prefix, now=None):
            received_lengths.append(len(prefix))
            return {
                "signal": "NONE",
                "signal_parameters": {"reason": "no_breakout"},
            }

        with patch.object(strategy, "evaluate", side_effect=no_signal):
            report = strategy.backtest(candles)
        self.assertEqual(received_lengths, [1, 2, 3, 4])
        self.assertEqual(report["signal_counts"]["NONE"], 4)
        self.assertEqual(report["reason_counts"], {"no_breakout": 4})


class StorageTests(unittest.TestCase):
    """Sprawdza, że flaga NONE zatrzymuje zapis przez istniejące repozytorium."""

    def test_none_flag_and_duplicate_result_are_delegated(self) -> None:
        class FakeRepository:
            """Zapisuje wywołania adaptera bez połączenia z bazą."""

            def __init__(self) -> None:
                self.calls = []

            def save_algorithm_signal(
                self,
                product: str,
                algorithm_name: str,
                signal: dict[str, object],
                candles_count: int,
            ) -> bool:
                self.calls.append(
                    (product, algorithm_name, signal, candles_count)
                )
                return False

        repository = FakeRepository()
        storage = DAX40SignalStorage(repository)
        signal = {
            "signal": "NONE",
            "candle_open_epoch": 1,
            "signal_parameters": {"reason": "no_breakout"},
        }
        with patch(
            "chat_gpt6_dax40_test.DAX40StrategyV1.SAVE_NONE_SIGNALS",
            False,
        ):
            self.assertFalse(storage.save(signal, 100))
        self.assertEqual(repository.calls, [])

        with patch(
            "chat_gpt6_dax40_test.DAX40StrategyV1.SAVE_NONE_SIGNALS",
            True,
        ):
            self.assertFalse(storage.save(signal, 100))
        self.assertEqual(len(repository.calls), 1)


if __name__ == "__main__":
    unittest.main()
