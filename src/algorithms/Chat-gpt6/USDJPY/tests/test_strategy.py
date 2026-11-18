"""Testy strategii USDJPY na kontrolowanych świecach OHLCV."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

ALGORITHM_ROOT = Path(__file__).resolve().parents[2]


def _load_algorithm_package(name: str, directory: Path) -> None:
    """Ładuje pakiet strategii z katalogu, którego nazwa zawiera myślnik."""
    spec = importlib.util.spec_from_file_location(
        name,
        directory / "__init__.py",
        submodule_search_locations=[str(directory)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Nie można załadować pakietu {name}.")
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)


if "chat_gpt6_usdmxn" not in sys.modules:
    _load_algorithm_package(
        "chat_gpt6_usdmxn", ALGORITHM_ROOT / "USDMXN"
    )
_load_algorithm_package("chat_gpt6_usdjpy", ALGORITHM_ROOT / "USDJPY")

from chat_gpt6_usdjpy.USDJPYStrategyV1 import USDJPYStrategyV1  # noqa: E402
from chat_gpt6_usdjpy.config import (  # noqa: E402
    DATABASE_SYMBOL,
    INSTRUMENT,
)
from chat_gpt6_usdjpy.storage import USDJPYSignalStorage  # noqa: E402


def _candles(
    direction: str | None = None,
    count: int = 1200,
    start: datetime | None = None,
) -> list[dict[str, object]]:
    """Tworzy monotoniczną historię i opcjonalnie końcowe wybicie kierunkowe."""
    first_open = start or datetime(2026, 1, 5, tzinfo=timezone.utc)
    direction_sign = 1 if direction != "SHORT" else -1
    prices = [
        150.0 + direction_sign * index * 0.003
        for index in range(count)
    ]
    if direction in {"LONG", "SHORT"}:
        pullback_count = 14
        pullback_start = count - pullback_count
        for offset in range(pullback_count):
            prices[pullback_start + offset] = (
                prices[pullback_start] - direction_sign * offset * 0.006
            )
        prices[-1] = prices[-2] + direction_sign * 0.18

    candles = []
    for index, close in enumerate(prices):
        open_price = prices[index - 1] if index else close
        high = max(open_price, close) + 0.002
        low = min(open_price, close) - 0.002
        if index == count - 1:
            if direction == "LONG":
                low = min(low, close - 0.03)
            elif direction == "SHORT":
                high = max(high, close + 0.03)
        candles.append(
            {
                "timestamp_utc": first_open + timedelta(minutes=index),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": 10.0,
            }
        )
    return candles


class USDJPYStrategyTests(unittest.TestCase):
    """Sprawdza sygnały, warm-up, kompletność danych i czas UTC."""

    def setUp(self) -> None:
        self.strategy = USDJPYStrategyV1(
            market_is_open=lambda _symbol, _timestamp: True
        )

    def test_controlled_candles_generate_long_and_correct_levels(self) -> None:
        candles = _candles("LONG")
        result = self.strategy.evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        )
        payload = result["signal_parameters"]
        conditions = payload["conditions"]

        self.assertEqual(result["signal"], "LONG")
        self.assertTrue(all(conditions["long"].values()))
        self.assertFalse(all(conditions["short"].values()))
        trade = payload["trade_parameters"]
        self.assertLess(trade["stop_loss"], trade["entry_price"])
        self.assertGreater(trade["take_profit"], trade["entry_price"])
        self.assertEqual(
            trade["stop_loss_distance"],
            payload["indicators"]["atr14_m1"] * 1.2,
        )

    def test_controlled_candles_generate_short_and_correct_levels(self) -> None:
        candles = _candles("SHORT")
        result = self.strategy.evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        )
        payload = result["signal_parameters"]
        conditions = payload["conditions"]

        self.assertEqual(result["signal"], "SHORT")
        self.assertTrue(all(conditions["short"].values()))
        self.assertFalse(all(conditions["long"].values()))
        trade = payload["trade_parameters"]
        self.assertGreater(trade["stop_loss"], trade["entry_price"])
        self.assertLess(trade["take_profit"], trade["entry_price"])

    def test_ordinary_no_signal_has_condition_diagnostics(self) -> None:
        candles = _candles(count=600)
        result = self.strategy.evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        )

        self.assertEqual(result["signal"], "NONE")
        self.assertEqual(result["evaluation_status"], "EVALUATED")
        self.assertIn("warunki blokujące", result["reason"])
        self.assertIn("long", result["signal_parameters"]["conditions"])
        self.assertIn("short", result["signal_parameters"]["conditions"])

    def test_warmup_and_incomplete_m5_are_distinguished(self) -> None:
        short_history = _candles(count=100)
        warmup = self.strategy.evaluate(
            short_history,
            now=short_history[-1]["timestamp_utc"] + timedelta(minutes=1),
        )
        self.assertEqual(warmup["evaluation_status"], "WARMUP")

        candles = _candles("LONG")
        incomplete = candles[:-6] + [candles[-1]]
        result = self.strategy.evaluate(
            incomplete,
            now=incomplete[-1]["timestamp_utc"] + timedelta(minutes=1),
        )
        payload = result["signal_parameters"]
        self.assertEqual(result["evaluation_status"], "DATA_INCOMPLETE")
        self.assertIsNone(payload["m5_candle"])
        self.assertIsNone(payload["indicators"]["adx14_m5"])

    def test_single_missing_minute_does_not_reset_warmup(self) -> None:
        candles = _candles("LONG")
        del candles[150]
        result = self.strategy.evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        )
        gap_state = result["signal_parameters"]["data"]["gap_state"]

        self.assertEqual(result["signal"], "LONG")
        self.assertEqual(gap_state["single_missing_minutes"], 1)
        self.assertFalse(gap_state["warmup_reset"])

    def test_unexpected_long_gap_resets_indicator_warmup(self) -> None:
        candles = _candles("LONG")
        del candles[800:804]
        result = self.strategy.evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        )

        self.assertTrue(result["signal_parameters"]["data"]["gap_state"]["warmup_reset"])
        self.assertEqual(result["evaluation_status"], "WARMUP")
        self.assertEqual(result["signal"], "NONE")

    def test_regular_market_closure_does_not_reset_warmup(self) -> None:
        first_open = datetime(2026, 1, 9, 20, 0, tzinfo=timezone.utc)
        candles = _candles(count=1200, start=first_open)
        for candle in candles[400:]:
            candle["timestamp_utc"] += timedelta(days=2)
        gap_start = candles[399]["timestamp_utc"] + timedelta(minutes=1)
        gap_end = candles[400]["timestamp_utc"]

        def market_open(_symbol: str, timestamp: datetime) -> bool:
            return not gap_start <= timestamp < gap_end

        strategy = USDJPYStrategyV1(market_is_open=market_open)
        result = strategy.evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        )

        self.assertFalse(
            result["signal_parameters"]["data"]["gap_state"]["warmup_reset"]
        )
        self.assertGreaterEqual(
            result["signal_parameters"]["data"]["m1_candles_in_segment"],
            100,
        )

    def test_warsaw_winter_and_summer_timestamps_are_unix_utc(self) -> None:
        warsaw = ZoneInfo("Europe/Warsaw")
        for local_open in (
            datetime(2026, 1, 15, 12, 0, tzinfo=warsaw),
            datetime(2026, 7, 15, 12, 0, tzinfo=warsaw),
        ):
            candles = _candles(
                count=600,
                start=local_open.astimezone(timezone.utc),
            )
            result = self.strategy.evaluate(
                candles,
                now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
            )
            self.assertEqual(
                result["timestamp"],
                int(candles[-1]["timestamp_utc"].timestamp()),
            )
            self.assertEqual(
                result["signal_parameters"]["instrument"],
                INSTRUMENT,
            )

    def test_open_candle_is_rejected(self) -> None:
        candles = _candles(count=600)
        with self.assertRaisesRegex(ValueError, "niezakończonej"):
            self.strategy.evaluate(
                candles,
                now=candles[-1]["timestamp_utc"]
                + timedelta(seconds=30),
            )

    def test_json_contains_parameters_and_no_nonfinite_values(self) -> None:
        candles = _candles("LONG")
        result = self.strategy.evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        )
        payload = result["signal_parameters"]

        self.assertEqual(payload["instrument"], "USDJPY")
        self.assertEqual(payload["timestamp"], result["timestamp"])
        self.assertEqual(payload["price"], candles[-1]["close"])
        self.assertEqual(result["database_symbol"], DATABASE_SYMBOL)
        self.assertIn("thresholds", payload)
        self.assertIn("conditions", payload)
        self.assertIn("trade_parameters", payload)

    def test_indicators_are_reused_from_existing_usdmxn_implementation(self) -> None:
        with patch(
            "chat_gpt6_usdjpy.USDJPYStrategyV1.calculate_indicator_series",
            wraps=sys.modules[
                "chat_gpt6_usdmxn.indicators"
            ].calculate_indicator_series,
        ) as calculate:
            candles = _candles("LONG")
            self.strategy.evaluate(
                candles,
                now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
            )
        calculate.assert_called_once()

    def test_storage_persists_compact_json_and_close_timestamp_for_repository(self) -> None:
        candles = _candles("LONG")
        result = self.strategy.evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        )
        repository = Mock()
        repository.save_algorithm_signal.return_value = True
        storage = USDJPYSignalStorage(repository)

        self.assertTrue(storage.save(result, len(candles)))

        saved_result = repository.save_algorithm_signal.call_args.args[2]
        self.assertEqual(
            set(saved_result["signal_parameters"]),
            {"price", "signal", "timestamp", "instrument", "reason"},
        )
        for field in ("price", "signal", "timestamp", "instrument", "reason"):
            self.assertEqual(
                saved_result["signal_parameters"][field],
                (
                    "Warunki LONG zostały spełnione."
                    if field == "reason"
                    else result["signal_parameters"][field]
                ),
            )
        self.assertNotEqual(
            saved_result["signal_parameters"]["reason"],
            result["signal_parameters"]["reason"],
        )
        self.assertEqual(
            saved_result["timestamp"],
            result["candle_close_time"],
        )
        self.assertEqual(
            result["signal_parameters"]["timestamp"],
            result["timestamp"],
        )
        self.assertTrue(
            repository.save_algorithm_signal.call_args.kwargs[
                "enqueue_notification"
            ]
        )

    def test_none_storage_is_controlled_by_one_flag(self) -> None:
        candles = _candles(count=600)
        result = self.strategy.evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        )
        repository = Mock()
        storage = USDJPYSignalStorage(repository)

        with patch("chat_gpt6_usdjpy.storage.SAVE_NONE_SIGNALS", False):
            storage.save(result, len(candles))

        self.assertFalse(
            repository.save_algorithm_signal.call_args.kwargs["save_signal"]
        )
        self.assertTrue(
            repository.save_algorithm_signal.call_args.kwargs[
                "record_evaluation"
            ]
        )
        self.assertFalse(
            repository.save_algorithm_signal.call_args.kwargs[
                "enqueue_notification"
            ]
        )

    def test_storage_propagates_database_failure_without_success_fallback(self) -> None:
        candles = _candles("LONG")
        result = self.strategy.evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        )
        repository = Mock()
        repository.save_algorithm_signal.side_effect = RuntimeError("db error")

        with self.assertRaisesRegex(RuntimeError, "db error"):
            USDJPYSignalStorage(repository).save(result, len(candles))


if __name__ == "__main__":
    unittest.main()
