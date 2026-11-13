"""Testy jednostkowe wskaźników, agregacji M5 i strategii EURUSD/USD."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from EURUSDStrategyV1 import EURUSDStrategyV1  # noqa: E402
from indicators import (  # noqa: E402
    ExponentialMovingAverage,
    WilderAverageTrueRange,
    WilderRelativeStrengthIndex,
)
from market_data import (  # noqa: E402
    Candle,
    FiveMinuteCandleAggregator,
    count_missing_market_minutes,
)


class IndicatorTests(unittest.TestCase):
    """Sprawdza inicjalizację wskaźników i wygładzanie Wildera."""

    def test_ema_uses_first_price_as_seed_then_exponential_smoothing(self) -> None:
        ema = ExponentialMovingAverage(period=3)
        self.assertEqual(ema.update(1.0), 1.0)
        self.assertAlmostEqual(ema.update(2.0), 1.5)
        self.assertAlmostEqual(ema.update(3.0), 2.25)
        self.assertAlmostEqual(ema.update(4.0), 3.125)

    def test_ema_reset_uses_first_price_of_new_segment(self) -> None:
        ema = ExponentialMovingAverage(period=3)
        ema.update(1.0)
        ema.update(2.0)
        ema.reset()
        self.assertEqual(ema.update(10.0), 10.0)

    def test_rsi_uses_wilder_average(self) -> None:
        rsi = WilderRelativeStrengthIndex(period=3)
        for close in (1.0, 2.0, 3.0, 2.0):
            rsi.update(close)
        self.assertAlmostEqual(rsi.update(3.0), 77.7777777778)

    def test_atr_uses_wilder_average(self) -> None:
        atr = WilderAverageTrueRange(period=3)
        candles = [
            make_candle(0, 10.0, 12.0, 9.0, 11.0),
            make_candle(1, 11.0, 13.0, 10.0, 12.0),
            make_candle(2, 12.0, 14.0, 11.0, 13.0),
            make_candle(3, 13.0, 14.0, 12.0, 13.5),
        ]
        self.assertIsNone(atr.update(candles[0]))
        self.assertIsNone(atr.update(candles[1]))
        self.assertAlmostEqual(atr.update(candles[2]), 3.0)
        self.assertAlmostEqual(atr.update(candles[3]), 8.0 / 3.0)


class CandleAggregationTests(unittest.TestCase):
    """Sprawdza agregację i odrzucanie niekompletnych grup M1."""

    def test_m5_uses_exactly_five_consecutive_m1_candles(self) -> None:
        aggregator = FiveMinuteCandleAggregator()
        completed = None
        candles = [
            make_candle(index, 10 + index, 12 + index, 9 + index, 11 + index, 2)
            for index in range(5)
        ]
        for candle in candles:
            completed = aggregator.add(candle)

        self.assertIsNotNone(completed)
        self.assertEqual(completed.open, candles[0].open)
        self.assertEqual(completed.high, max(item.high for item in candles))
        self.assertEqual(completed.low, min(item.low for item in candles))
        self.assertEqual(completed.close, candles[-1].close)
        self.assertEqual(completed.volume, 10)

    def test_m5_does_not_fill_a_missing_m1_candle(self) -> None:
        aggregator = FiveMinuteCandleAggregator()
        for index in (0, 1, 3, 4):
            self.assertIsNone(aggregator.add(make_candle(index, 1, 2, 0.5, 1.5)))


class StrategyTests(unittest.TestCase):
    """Sprawdza rozgrzewkę, luki, zamknięte świece i kontrakt sygnału."""

    def test_does_not_analyze_an_open_m1_candle(self) -> None:
        strategy = EURUSDStrategyV1()
        candle = make_candle(0, 1.1, 1.2, 1.0, 1.15)
        result = strategy.process_candle(
            candle,
            now_utc=candle.timestamp + timedelta(seconds=30),
        )
        self.assertIsNone(result)
        self.assertEqual(strategy.candles_count, 0)

    def test_strategy_rejects_non_finite_candle_values(self) -> None:
        strategy = EURUSDStrategyV1()
        candle = make_candle(0, 1.1, 1.2, 1.0, float("nan"))
        with self.assertRaises(ValueError):
            strategy.process_candle(candle)

    def test_one_missing_candle_does_not_reset_indicators(self) -> None:
        strategy = EURUSDStrategyV1()
        first = make_candle(0, 1.1, 1.2, 1.0, 1.15)
        second = make_candle(1, 1.15, 1.2, 1.1, 1.16)
        strategy.process_candle(first)
        strategy.process_candle(second)
        after_gap = make_candle(3, 1.16, 1.2, 1.1, 1.17)
        strategy.process_candle(after_gap)
        expected_ema = 1.15 + (2 / 10) * (1.16 - 1.15)
        expected_ema += (2 / 10) * (1.17 - expected_ema)
        self.assertEqual(strategy.candles_count, 3)
        self.assertAlmostEqual(strategy._m1_ema9.value, expected_ema)
        self.assertEqual(strategy._rsi14.previous_close, 1.17)

    def test_two_missing_market_candles_reset_indicators(self) -> None:
        strategy = EURUSDStrategyV1()
        strategy.process_candle(make_candle(0, 1.1, 1.2, 1.0, 1.15))
        strategy.process_candle(make_candle(1, 1.15, 1.2, 1.1, 1.16))
        strategy.process_candle(make_candle(4, 1.16, 1.2, 1.1, 1.17))
        self.assertEqual(strategy.candles_count, 1)
        self.assertEqual(strategy._m1_ema9.value, 1.17)
        self.assertEqual(strategy._rsi14.previous_close, 1.17)
        self.assertIsNone(strategy._rsi14.value)
        self.assertEqual(strategy._rsi14._change_count, 0)

    def test_forex_weekend_gap_does_not_reset_indicators(self) -> None:
        new_york = ZoneInfo("America/New_York")
        friday_timestamp = datetime(
            2026, 1, 9, 16, 59, tzinfo=new_york
        ).astimezone(timezone.utc)
        sunday_timestamp = datetime(
            2026, 1, 11, 17, 0, tzinfo=new_york
        ).astimezone(timezone.utc)
        self.assertEqual(
            count_missing_market_minutes(friday_timestamp, sunday_timestamp),
            0,
        )

        strategy = EURUSDStrategyV1()
        first = Candle(friday_timestamp, 1.1, 1.2, 1.0, 1.15)
        second = Candle(
            friday_timestamp + timedelta(minutes=1),
            1.15, 1.2, 1.1, 1.16,
        )
        sunday = Candle(sunday_timestamp, 1.16, 1.2, 1.1, 1.17)
        strategy.process_candle(first)
        strategy.process_candle(second)
        strategy.process_candle(sunday)

        expected_ema = 1.15 + (2 / 10) * (1.16 - 1.15)
        expected_ema += (2 / 10) * (1.17 - expected_ema)
        self.assertEqual(strategy.candles_count, 3)
        self.assertAlmostEqual(strategy._m1_ema9.value, expected_ema)

    def test_open_market_gap_after_weekend_still_resets_indicators(self) -> None:
        new_york = ZoneInfo("America/New_York")
        friday_timestamp = datetime(
            2026, 1, 9, 16, 58, tzinfo=new_york
        ).astimezone(timezone.utc)
        sunday_timestamp = datetime(
            2026, 1, 11, 17, 3, tzinfo=new_york
        ).astimezone(timezone.utc)

        strategy = EURUSDStrategyV1()
        strategy.process_candle(
            Candle(friday_timestamp, 1.1, 1.2, 1.0, 1.15)
        )
        strategy.process_candle(
            Candle(sunday_timestamp, 1.16, 1.2, 1.1, 1.17)
        )

        self.assertEqual(strategy.candles_count, 1)
        self.assertEqual(strategy._m1_ema9.value, 1.17)
        self.assertEqual(strategy._rsi14._change_count, 0)

    def test_warmup_needs_fifty_complete_m5_candles(self) -> None:
        strategy = EURUSDStrategyV1()
        start = datetime(2026, 1, 5, tzinfo=timezone.utc)
        for index in range(249):
            price = 1.1 + index * 0.00001
            candle = Candle(
                timestamp=start + timedelta(minutes=index),
                open=price,
                high=price + 0.00002,
                low=price - 0.00002,
                close=price + 0.00001,
            )
            strategy.process_candle(
                candle,
                now_utc=candle.timestamp + timedelta(minutes=1),
            )
        self.assertEqual(strategy.candles_count, 249)
        self.assertEqual(strategy._m5_candle_count, 49)
        final_candle = Candle(
            timestamp=start + timedelta(minutes=249),
            open=1.10249,
            high=1.10251,
            low=1.10247,
            close=1.10250,
        )
        strategy.process_candle(
            final_candle,
            now_utc=final_candle.timestamp + timedelta(minutes=1),
        )
        self.assertEqual(strategy.candles_count, 250)
        self.assertEqual(strategy._m5_candle_count, 50)

    def test_signal_json_has_exact_contract_fields(self) -> None:
        signal = self._find_long_signal()
        payload = json.loads(signal.to_json())
        self.assertEqual(
            list(payload),
            [
                "signal",
                "instrument",
                "timestamp",
                "price",
                "ema9_1m",
                "ema21_1m",
                "ema9_5m",
                "ema21_5m",
                "rsi14_1m",
                "atr14_1m",
                "vwap",
            ],
        )
        self.assertEqual(payload["signal"], "LONG")
        self.assertEqual(payload["instrument"], "EURUSD")
        self.assertTrue(payload["timestamp"].endswith("Z"))
        self.assertIsNone(payload["vwap"])

    def test_strategy_can_generate_short_signal(self) -> None:
        strategy = EURUSDStrategyV1()
        start = datetime(2026, 1, 5, tzinfo=timezone.utc)
        close = 1.2
        candles = []
        for index in range(300):
            next_close = close - 0.00005
            candles.append(make_candle(index, close, close + 0.0001,
                                       next_close - 0.0001, next_close))
            close = next_close
        for index in range(300, 310):
            next_close = close + 0.00005
            candles.append(make_candle(index, close, next_close + 0.0001,
                                       close - 0.0001, next_close))
            close = next_close
        for index in range(310, 356):
            next_close = close - (0.0002 if index == 310 else 0.0001)
            candles.append(make_candle(index, close, close + 0.0001,
                                       next_close - 0.0001, next_close))
            close = next_close

        generated_signals = [
            signal
            for candle in candles
            if (
                signal := strategy.process_candle(
                    candle,
                    now_utc=candle.timestamp + timedelta(minutes=1),
                )
            ) is not None
        ]
        self.assertTrue(generated_signals)
        self.assertEqual(generated_signals[0].direction, "SHORT")

    def _find_long_signal(self):
        strategy = EURUSDStrategyV1()
        start = datetime(2026, 1, 5, tzinfo=timezone.utc)
        candles = []
        close = 1.1
        for index in range(300):
            next_close = close + 0.00005
            candles.append(make_candle(index, close, next_close + 0.0001,
                                       close - 0.0001, next_close))
            close = next_close
        for index in range(300, 310):
            next_close = close - 0.00005
            candles.append(make_candle(index, close, close + 0.0001,
                                       next_close - 0.0001, next_close))
            close = next_close
        for index in range(310, 356):
            next_close = close + (0.0002 if index == 310 else 0.0001)
            candles.append(make_candle(index, close, next_close + 0.0001,
                                       close - 0.0001, next_close))
            close = next_close

        generated_signals = []
        for candle in candles:
            result = strategy.process_candle(
                candle,
                now_utc=candle.timestamp + timedelta(minutes=1),
            )
            if result is not None:
                generated_signals.append(result)
        self.assertEqual(len(generated_signals), 1)
        return generated_signals[0]


def make_candle(
    minute: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float | None = None,
) -> Candle:
    """Tworzy świecę testową o stałym znaczniku czasu UTC."""
    return Candle(
        timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc)
        + timedelta(minutes=minute),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


if __name__ == "__main__":
    unittest.main()
