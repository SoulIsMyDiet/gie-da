"""Testy zgodności pythonowej wersji Grok-45 z zachowaniem generatora C++."""

from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from EURUSDStrategyV2 import (  # noqa: E402
    Candle,
    EURUSDStrategyV2,
    IndicatorCalculator,
)


def create_market_candles(seed: int, trend_per_candle: float) -> list[Candle]:
    """Tworzy takie same przykładowe świece jak testy generatora C++."""
    candles: list[Candle] = []
    random_state = seed
    close = 1.08
    for index in range(100):
        random_state = (random_state * 1664525 + 1013904223) & 0xFFFFFFFF
        random_fraction = (random_state >> 8) / 16777216.0
        price_change = trend_per_candle + (random_fraction - 0.5) * 0.00018
        next_close = close + price_change
        open_price = close if index == 0 else candles[-1].close
        candles.append(
            Candle(
                timestamp=index * 60,
                open=open_price,
                high=max(open_price, next_close) + 0.00005,
                low=min(open_price, next_close) - 0.00005,
                close=next_close,
                volume=0.0,
            )
        )
        close = next_close
    return candles


class IndicatorCalculatorTests(unittest.TestCase):
    """Sprawdza zgodność EMA, RSI, wstęg i ATR z implementacją C++."""

    def test_ema_uses_sma_seed_and_expected_multiplier(self) -> None:
        values = IndicatorCalculator.calculate_ema_series([1.0, 2.0, 3.0], 2)

        self.assertEqual(values[0], None)
        self.assertAlmostEqual(values[1], 1.5)
        self.assertAlmostEqual(values[2], 2.5)
        self.assertIsNone(IndicatorCalculator.calculate_ema([1.0], 2))

    def test_rsi_handles_rising_falling_and_flat_prices(self) -> None:
        self.assertEqual(IndicatorCalculator.calculate_rsi([1, 2, 3, 4], 2), 100)
        self.assertEqual(IndicatorCalculator.calculate_rsi([4, 3, 2, 1], 2), 0)
        self.assertEqual(IndicatorCalculator.calculate_rsi([5, 5, 5, 5], 2), 50)
        self.assertIsNone(IndicatorCalculator.calculate_rsi([1, 2], 2))

    def test_bollinger_bands_use_population_standard_deviation(self) -> None:
        bands = IndicatorCalculator.calculate_bollinger_bands([1, 2, 3], 3, 2)

        self.assertIsNotNone(bands)
        assert bands is not None
        deviation = math.sqrt(2.0 / 3.0)
        self.assertAlmostEqual(bands.middle, 2.0)
        self.assertAlmostEqual(bands.upper, 2.0 + 2.0 * deviation)
        self.assertAlmostEqual(bands.lower, 2.0 - 2.0 * deviation)
        self.assertIsNone(
            IndicatorCalculator.calculate_bollinger_bands([1, 2], 0, 2)
        )

    def test_atr_uses_wilder_average_and_rejects_invalid_ohlc(self) -> None:
        candles = [
            Candle(index * 60, 1.0, 1.0001, 0.9999, 1.0, 0.0)
            for index in range(16)
        ]

        self.assertAlmostEqual(
            IndicatorCalculator.calculate_atr(candles, 14) or 0.0,
            0.0002,
        )
        candles[-1].high = 0.5
        self.assertIsNone(IndicatorCalculator.calculate_atr(candles, 14))


class EURUSDStrategyV2Tests(unittest.TestCase):
    """Sprawdza sygnały oraz sytuacje brzegowe odpowiadające testom C++."""

    def setUp(self) -> None:
        """Tworzy nową instancję algorytmu dla każdego testu."""
        self.strategy = EURUSDStrategyV2()

    def test_bullish_fixture_generates_long_with_matching_json_contract(self) -> None:
        response = self.strategy.generate(create_market_candles(75, 0.000025))
        payload = json.loads(response)

        self.assertEqual(payload["instrument"], "EURUSD/USD")
        self.assertEqual(payload["signal"], "LONG")
        self.assertEqual(payload["timestamp"], 5940)
        self.assertEqual(
            set(payload["parameters"]),
            {
                "ema9",
                "ema21",
                "rsi14",
                "bb_upper",
                "bb_middle",
                "bb_lower",
                "atr14",
                "reason",
            },
        )

    def test_bearish_fixture_generates_short(self) -> None:
        response = self.strategy.generate(create_market_candles(194, -0.000025))

        self.assertEqual(json.loads(response)["signal"], "SHORT")

    def test_short_series_flat_prices_and_bad_timestamps_return_none(self) -> None:
        candles = create_market_candles(75, 0.000025)
        short_response = json.loads(
            self.strategy.generate(candles[: self.strategy.minimum_candles - 1])
        )
        self.assertEqual(short_response["signal"], "NONE")
        self.assertIn("co najmniej 50", short_response["parameters"]["reason"])

        flat_candles = [
            Candle(index * 60, 1.0, 1.0, 1.0, 1.0, 0.0)
            for index in range(self.strategy.minimum_candles)
        ]
        flat_response = json.loads(self.strategy.generate(flat_candles))
        self.assertEqual(flat_response["signal"], "NONE")
        self.assertIn("nie zmieniły się", flat_response["parameters"]["reason"])

        candles[-1].timestamp = candles[-2].timestamp
        invalid_response = json.loads(self.strategy.generate(candles))
        self.assertEqual(invalid_response["signal"], "NONE")
        self.assertIn("nie są ułożone", invalid_response["parameters"]["reason"])

    def test_gap_between_unique_timestamps_is_allowed(self) -> None:
        candles = create_market_candles(75, 0.000025)
        candles[-1].timestamp += 60

        response = json.loads(self.strategy.generate(candles))

        self.assertEqual(response["signal"], "LONG")

    def test_empty_input_has_same_response_shape(self) -> None:
        payload = json.loads(self.strategy.generate([]))

        self.assertEqual(payload["signal"], "NONE")
        self.assertIsNone(payload["price"])
        self.assertIsNone(payload["timestamp"])
        self.assertIsNone(payload["parameters"]["ema9"])
        self.assertEqual(payload["parameters"]["reason"], "Brak świec do analizy.")


if __name__ == "__main__":
    unittest.main()
