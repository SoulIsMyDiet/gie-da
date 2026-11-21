"""Testy wskaźników, warunków sygnału, luk i czasu strategii USDMXN."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PACKAGE_PATH = Path(__file__).resolve().parents[1]
API_PATH = PACKAGE_PATH.parents[2] / "database" / "api"
sys.path.insert(0, str(API_PATH))
PACKAGE_SPEC = importlib.util.spec_from_file_location(
    "chat_gpt6_usdmxn_test",
    PACKAGE_PATH / "__init__.py",
    submodule_search_locations=[str(PACKAGE_PATH)],
)
assert PACKAGE_SPEC is not None and PACKAGE_SPEC.loader is not None
PACKAGE = importlib.util.module_from_spec(PACKAGE_SPEC)
sys.modules[PACKAGE_SPEC.name] = PACKAGE
PACKAGE_SPEC.loader.exec_module(PACKAGE)

from chat_gpt6_usdmxn_test import USDMXNStrategyV1 as strategy_module  # noqa: E402
from chat_gpt6_usdmxn_test.USDMXNStrategyV1 import USDMXNStrategyV1  # noqa: E402
from chat_gpt6_usdmxn_test.indicators import (  # noqa: E402
    _adx,
    _atr,
    _ema,
    _rsi,
    aggregate_m5,
)

UTC = timezone.utc
WARSAW = ZoneInfo("Europe/Warsaw")
ALWAYS_OPEN = lambda _symbol, _timestamp: True


def build_trending_candles(direction: str, count: int = 600) -> list[dict]:
    """Tworzy rzeczywiste szeregi OHLC z korektą i przecięciem RSI na końcu."""
    start = datetime(2026, 3, 16, 12, 0, tzinfo=UTC)
    trend_sign = 1 if direction == "LONG" else -1
    closes = [17.0 + trend_sign * 0.0003 * index for index in range(count)]
    for _ in range(10):
        closes.append(closes[-1] - trend_sign * 0.0003)
    closes.append(closes[-1] + trend_sign * 0.002)

    candles = []
    for index, close in enumerate(closes):
        previous_close = closes[index - 1] if index else close
        candles.append(
            {
                "timestamp_utc": start + timedelta(minutes=index),
                "open": previous_close,
                "high": max(previous_close, close) + 0.0002,
                "low": min(previous_close, close) - 0.0002,
                "close": close,
                "volume": None,
            }
        )
    candles[-1]["low"] = min(candles[-1]["open"], candles[-1]["close"]) - 0.001
    candles[-1]["high"] = max(candles[-1]["open"], candles[-1]["close"]) + 0.0002
    return candles


class IndicatorTests(unittest.TestCase):
    """Sprawdza podstawowe wartości i odrzucanie niekompletnych świec M5."""

    def test_ema_rsi_atr_and_adx_warmup(self) -> None:
        """Weryfikuje inicjalizację EMA oraz warm-up wskaźników Wildera."""
        ema = _ema([1.0, 2.0, 3.0], 2)
        self.assertAlmostEqual(ema[1], 5 / 3)
        self.assertAlmostEqual(ema[2], 23 / 9)
        self.assertEqual(_rsi([1.0, 2.0, 3.0, 4.0, 5.0], 4)[-1], 100.0)
        candles = [
            {"open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0}
            for _ in range(40)
        ]
        self.assertIsNone(_atr(candles[:13], 14)[-1])
        self.assertIsNone(_adx(candles[:27], 14)[-1])
        self.assertIsNotNone(_adx(candles, 14)[-1])

    def test_m5_aggregation_omits_incomplete_bucket(self) -> None:
        """Nie tworzy świecy M5, jeśli brakuje choć jednej należącej do niej M1."""
        candles = build_trending_candles("LONG", count=10)[:10]
        incomplete = candles[:4] + candles[5:]
        aggregate = aggregate_m5(incomplete)
        self.assertEqual(len(aggregate), 1)
        self.assertEqual(aggregate[0]["timestamp_utc"], candles[5]["timestamp_utc"])


class StrategyTests(unittest.TestCase):
    """Testuje kierunki, diagnostykę, warm-up i obsługę przerw w danych."""

    def test_controlled_candles_generate_long_and_short(self) -> None:
        """Używa pełnych obliczeń wskaźników dla obu zestawów warunków."""
        for direction in ("LONG", "SHORT"):
            candles = build_trending_candles(direction)
            result = USDMXNStrategyV1(ALWAYS_OPEN).evaluate(
                candles,
                now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
            )
            parameters = result["signal_parameters"]
            side = parameters["conditions"][direction.lower()]
            self.assertEqual(result["signal"], direction, parameters)
            self.assertTrue(all(side.values()), side)
            self.assertEqual(parameters["instrument"], "USDMXN")
            self.assertEqual(parameters["timestamp"], int(candles[-1]["timestamp_utc"].timestamp()))
            m5_close = datetime.fromisoformat(
                parameters["m5_candle"]["timestamp_close_utc"]
            )
            self.assertLessEqual(
                m5_close,
                candles[-1]["timestamp_utc"] + timedelta(minutes=1),
            )
            self.assertEqual(parameters["trade_parameters"]["max_holding_minutes"], 20)
            self.assertAlmostEqual(
                parameters["trade_parameters"]["take_profit_distance"],
                1.5 * parameters["trade_parameters"]["stop_loss_distance"],
            )
            json.dumps(parameters, allow_nan=False)

    def test_flat_market_returns_none_with_blocking_conditions(self) -> None:
        """NONE zawiera konkretne blokady i wszystkie dostępne wskaźniki."""
        candles = build_trending_candles("LONG")
        for candle in candles:
            candle.update(open=17.0, high=17.001, low=16.999, close=17.0)
        result = USDMXNStrategyV1(ALWAYS_OPEN).evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        )
        parameters = result["signal_parameters"]
        self.assertEqual(result["signal"], "NONE")
        self.assertIn("ADX M5 poniżej progu", parameters["reason"])
        self.assertIn("ema20_m5", parameters["indicators"])
        self.assertIsNone(parameters["trade_parameters"]["stop_loss"])

    def test_warmup_does_not_generate_trade_signal(self) -> None:
        """Mniej niż 100 świec M1 i 100 świec M5 blokuje wejście."""
        candles = build_trending_candles("LONG", count=99)
        result = USDMXNStrategyV1(ALWAYS_OPEN).evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        )
        self.assertEqual(result["signal"], "NONE")
        self.assertFalse(result["signal_parameters"]["data"]["warmup_ready"])
        self.assertIn("Warm-up", result["signal_parameters"]["reason"])

    def test_single_missing_minute_does_not_reset_indicators(self) -> None:
        """Pojedyncza luka nie rozpoczyna warm-up od nowa i nie tworzy świecy M5."""
        candles = build_trending_candles("LONG")
        del candles[300]
        result = USDMXNStrategyV1(ALWAYS_OPEN).evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        )
        gap_state = result["signal_parameters"]["data"]["gap_state"]
        self.assertFalse(gap_state["warmup_reset"])
        self.assertEqual(gap_state["single_missing_minutes"], 1)
        self.assertFalse(
            gap_state["latest_unexpected_gap_minutes_is_lower_bound"]
        )
        self.assertEqual(result["signal"], "LONG")
        self.assertLess(result["signal_parameters"]["data"]["completed_m5_candles"], 123)

    def test_long_unexpected_gap_resets_warmup(self) -> None:
        """Ponad jedna brakująca minuta handlowa odcina wcześniejszy segment."""
        candles = build_trending_candles("LONG")
        del candles[300:303]
        result = USDMXNStrategyV1(ALWAYS_OPEN).evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        )
        parameters = result["signal_parameters"]
        self.assertTrue(parameters["data"]["gap_state"]["warmup_reset"])
        self.assertTrue(
            parameters["data"]["gap_state"][
                "latest_unexpected_gap_minutes_is_lower_bound"
            ]
        )
        self.assertEqual(parameters["signal"], "NONE")
        self.assertLess(parameters["data"]["m1_candles_in_segment"], 500)

    def test_long_gap_duration_is_reported_as_a_lower_bound(self) -> None:
        """Diagnostyka jawnie oznacza długość luki obciętą dla wydajności."""
        candles = build_trending_candles("LONG")
        del candles[300:305]
        result = USDMXNStrategyV1(ALWAYS_OPEN).evaluate(
            candles,
            now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        )
        gap_state = result["signal_parameters"]["data"]["gap_state"]
        self.assertEqual(gap_state["latest_unexpected_gap_minutes"], 2)
        self.assertTrue(
            gap_state["latest_unexpected_gap_minutes_is_lower_bound"]
        )

    def test_regular_forex_weekend_closure_is_not_counted_as_data_gap(self) -> None:
        """Istniejący kalendarz Forex pomija planowe zamknięcie weekendowe."""
        from market_products import is_market_open

        strategy = USDMXNStrategyV1(is_market_open)
        friday = datetime(2026, 3, 13, 20, 59, tzinfo=UTC)
        sunday = datetime(2026, 3, 15, 21, 0, tzinfo=UTC)
        self.assertEqual(strategy._missing_market_minutes(friday, sunday), 0)

    def test_warsaw_timestamp_uses_seasonal_offset(self) -> None:
        """Obie polskie zmiany offsetu dają jednoznaczną epokę UTC."""
        for local, expected_offset in (
            (datetime(2026, 1, 5, 11, 0, tzinfo=WARSAW), 60),
            (datetime(2026, 7, 5, 12, 0, tzinfo=WARSAW), 120),
        ):
            utc_value = local.astimezone(UTC)
            self.assertEqual(
                int(utc_value.timestamp()),
                int(local.timestamp()),
            )
            self.assertEqual(int(local.utcoffset().total_seconds() // 60), expected_offset)

    def test_duplicate_timestamps_and_forming_candles_are_rejected(self) -> None:
        """Błędna historia nie jest zamieniana na pozorny wynik NONE."""
        candles = build_trending_candles("LONG")
        with self.assertRaisesRegex(ValueError, "powtórzony timestamp"):
            USDMXNStrategyV1(ALWAYS_OPEN).evaluate(
                [*candles, candles[-1]],
                now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
            )
        with self.assertRaisesRegex(ValueError, "niezakończonej"):
            USDMXNStrategyV1(ALWAYS_OPEN).evaluate(
                candles,
                now=candles[-1]["timestamp_utc"] + timedelta(seconds=30),
            )

    def test_none_storage_flag_is_single_configuration_switch(self) -> None:
        """Flaga pomija rekord NONE, ale adapter trwale zapisuje ewaluację."""
        from unittest.mock import Mock, patch

        from chat_gpt6_usdmxn_test.storage import USDMXNSignalStorage

        repository = Mock()
        repository.save_algorithm_signal.return_value = False
        storage = USDMXNSignalStorage(repository)
        result = {
            "signal": "NONE",
            "candle_open_epoch": 1,
            "signal_parameters": {
                "price": 17.0,
                "signal": "NONE",
                "timestamp": 1,
                "instrument": "USDMXN",
                "reason": "Brak sygnału; warunki blokują wejście.",
                "indicators": {"ema9": 17.0},
            },
        }
        with patch.object(strategy_module, "SAVE_NONE_SIGNALS", False):
            self.assertFalse(storage.save(result, 100))
        repository.save_algorithm_signal.assert_called_once_with(
            "USD/MXN",
            "Chat-gpt6/USDMXNStrategyV1",
            {
                **result,
                "signal_parameters": {
                    "price": 17.0,
                    "signal": "NONE",
                    "timestamp": 1,
                    "instrument": "USDMXN",
                    "reason": "Warunki LONG ani SHORT nie zostały spełnione.",
                },
            },
            100,
            record_evaluation=True,
            save_signal=False,
            enqueue_notification=False,
        )


if __name__ == "__main__":
    unittest.main()
