"""Testy harmonogramu, wskaźników, sygnałów, zapisu i raportu GER30."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import importlib.util

package_path = Path(__file__).resolve().parents[1]
package_spec = importlib.util.spec_from_file_location(
    "claude_sonnet55_dax40",
    package_path / "__init__.py",
    submodule_search_locations=[str(package_path)],
)
assert package_spec is not None and package_spec.loader is not None
package_module = importlib.util.module_from_spec(package_spec)
sys.modules[package_spec.name] = package_module
package_spec.loader.exec_module(package_module)

from claude_sonnet55_dax40 import storage
from claude_sonnet55_dax40.backtest import BacktestEngine
from claude_sonnet55_dax40.indicators import (
    _atr,
    _ema,
    _rsi,
    calculate_indicators,
    completed_five_minute_candles,
)
from claude_sonnet55_dax40.schedule import (
    expected_candle_opens,
    is_trading_minute,
    new_york_session_time,
    segment_id,
)
from claude_sonnet55_dax40.signal_logic import (
    _gap_status,
    _in_blackout,
    _setup_candidates,
    generate_signal,
)
from claude_sonnet55_dax40.storage import DAX40Storage

UTC = timezone.utc
WARSAW = ZoneInfo("Europe/Warsaw")
REQUIRED_PARAMS = {
    "atr14_points",
    "atr_ratio",
    "rsi5",
    "ema9",
    "ema21",
    "ema55",
    "ema20_5m",
    "ema20_5m_slope",
    "trend_5m",
    "efficiency_ratio_30",
    "bb_zscore",
    "vwap",
    "vwap_distance_atr",
    "rel_volume",
    "body_ratio",
    "or_high",
    "or_low",
    "or_width_points",
    "minutes_since_open",
    "session_phase",
    "missing_candles_60m",
    "data_age_minutes",
    "segment_candles",
}


def build_candles(start: datetime, count: int) -> list[dict[str, object]]:
    """Tworzy zamknięte świece M1 o deterministycznych cenach i wolumenie."""
    candles = []
    price = 18000.0
    for index in range(count):
        candle_time = start + timedelta(minutes=index)
        candles.append(
            {
                "timestamp_utc": candle_time,
                "open": price,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price + (0.25 if index % 2 == 0 else -0.15),
                "volume": 100.0,
            }
        )
        price = float(candles[-1]["close"])
    return candles


class ScheduleTests(unittest.TestCase):
    """Sprawdza grafik sesji, przerwy zamknięcia oraz przejścia DST."""

    def test_warsaw_winter_summer_and_weekend_schedule(self) -> None:
        winter_open = datetime(2026, 1, 12, 0, 10, tzinfo=UTC)
        summer_open = datetime(2026, 7, 13, 0, 10, tzinfo=UTC)
        self.assertFalse(is_trading_minute(winter_open - timedelta(minutes=1)))
        self.assertTrue(is_trading_minute(winter_open))
        self.assertTrue(is_trading_minute(summer_open))
        self.assertFalse(is_trading_minute(summer_open - timedelta(minutes=1)))
        self.assertFalse(is_trading_minute(datetime(2026, 7, 11, 8, 0, tzinfo=UTC)))
        self.assertFalse(is_trading_minute(datetime(2026, 1, 12, 21, 0, tzinfo=UTC)))

    def test_last_sundays_march_and_october_are_zoneinfo_aware(self) -> None:
        march_transition = datetime(2026, 3, 29, 1, 0, tzinfo=UTC)
        before_march = (march_transition - timedelta(minutes=1)).astimezone(WARSAW)
        after_march = march_transition.astimezone(WARSAW)
        self.assertEqual(before_march.strftime("%H:%M"), "01:59")
        self.assertEqual(after_march.strftime("%H:%M"), "03:00")
        self.assertFalse(is_trading_minute(march_transition - timedelta(minutes=1)))
        self.assertFalse(is_trading_minute(march_transition))
        march_opens = expected_candle_opens(
            datetime(2026, 3, 29, 0, 55, tzinfo=UTC),
            datetime(2026, 3, 29, 1, 15, tzinfo=UTC),
        )
        self.assertEqual(march_opens, [])

        october_start = datetime(2026, 10, 25, 0, 10, tzinfo=UTC)
        repeated_hour_end = datetime(2026, 10, 25, 2, 0, tzinfo=UTC)
        before_october = datetime(2026, 10, 25, 0, 59, tzinfo=UTC).astimezone(WARSAW)
        after_october = datetime(2026, 10, 25, 1, 0, tzinfo=UTC).astimezone(WARSAW)
        self.assertEqual(before_october.strftime("%H:%M %z"), "02:59 +0200")
        self.assertEqual(after_october.strftime("%H:%M %z"), "02:00 +0100")
        october_opens = expected_candle_opens(october_start, repeated_hour_end)
        self.assertEqual(october_opens, [])
        self.assertFalse(is_trading_minute(datetime(2026, 10, 25, 0, 10, tzinfo=UTC)))
        self.assertFalse(is_trading_minute(datetime(2026, 10, 25, 1, 10, tzinfo=UTC)))

    def test_scheduled_session_closures_are_not_missing_minutes(self) -> None:
        start = datetime(2026, 1, 12, 20, 59, tzinfo=UTC)
        end = datetime(2026, 1, 13, 0, 10, tzinfo=UTC)
        self.assertEqual(expected_candle_opens(start, end), [start])
        self.assertEqual(segment_id(start), ("2026-01-12", "session"))

    def test_new_york_blackouts_follow_us_dst_not_warsaw_dst(self) -> None:
        us_winter = datetime(2026, 1, 12, 14, 30, tzinfo=UTC)
        us_summer_before_eu = datetime(2026, 3, 9, 13, 30, tzinfo=UTC)
        self.assertEqual(new_york_session_time(us_winter).strftime("%H:%M"), "09:30")
        self.assertEqual(new_york_session_time(us_summer_before_eu).strftime("%H:%M"), "09:30")
        self.assertIn(
            "us_data_blackout",
            _in_blackout(us_summer_before_eu.astimezone(WARSAW), us_summer_before_eu),
        )
        close_during_auction = datetime(2026, 1, 12, 16, 32, tzinfo=UTC)
        self.assertIn(
            "warsaw_closing_blackout",
            _in_blackout(
                close_during_auction.astimezone(WARSAW),
                close_during_auction,
            ),
        )


class IndicatorAndGapTests(unittest.TestCase):
    """Sprawdza ręczne przykłady wskaźników, koszyki 5M i politykę luk."""

    def test_first_true_range_and_wilder_rsi_known_values(self) -> None:
        bars = [
            {"high": 12.0, "low": 10.0, "close": 11.0},
            {"high": 13.0, "low": 11.0, "close": 12.0},
        ]
        self.assertEqual(_atr(bars, period=2), 2.0)
        self.assertEqual(_rsi([1, 2, 3, 4, 5, 6], period=5), 100.0)

    def test_ema_matches_ewm_adjust_false_calculation(self) -> None:
        self.assertEqual(
            _ema([1.0, 2.0, 3.0, 4.0], period=3),
            [1.0, 1.5, 2.25, 3.125],
        )

    def test_vwap_uses_session_history_and_stays_undefined_without_volume(self) -> None:
        session_candles = [
            {
                "timestamp_utc": datetime(2026, 1, 12, 8, index, tzinfo=UTC),
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price,
                "volume": volume,
            }
            for index, price, volume in ((0, 100.0, 1.0), (1, 200.0, 3.0))
        ]
        indicators = calculate_indicators(
            [session_candles[-1]],
            now=datetime(2026, 1, 12, 8, 2, tzinfo=UTC),
            vwap_candles=session_candles,
        )
        self.assertEqual(indicators["vwap"], 175.0)

        no_volume = [
            {**candle, "volume": 0.0}
            for candle in session_candles
        ]
        indicators = calculate_indicators(
            [no_volume[-1]],
            now=datetime(2026, 1, 12, 8, 2, tzinfo=UTC),
            vwap_candles=no_volume,
        )
        self.assertIsNone(indicators["vwap"])

    def test_flat_prices_and_zero_volume_do_not_raise(self) -> None:
        candles = [
            {
                "timestamp_utc": datetime(2026, 1, 12, 8, index, tzinfo=UTC),
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": 0.0,
            }
            for index in range(31)
        ]
        indicators = calculate_indicators(
            candles,
            now=datetime(2026, 1, 12, 8, 31, tzinfo=UTC),
        )
        self.assertEqual(indicators["rsi5"], 50.0)
        self.assertEqual(indicators["efficiency_ratio_30"], 0.0)
        self.assertIsNone(indicators["bb_zscore"])
        self.assertIsNone(indicators["vwap"])

    def test_five_minute_aggregates_require_closed_bucket_and_four_distinct_members(self) -> None:
        base = datetime(2026, 1, 12, 8, 0, tzinfo=UTC)
        candles = build_candles(base, 10)
        candles = [item for index, item in enumerate(candles) if index != 2]
        complete = completed_five_minute_candles(candles, base + timedelta(minutes=10))
        self.assertEqual([item["minute_candles"] for item in complete], [4, 5])
        partial = completed_five_minute_candles(candles, base + timedelta(minutes=9))
        self.assertEqual(len(partial), 1)

    def test_gap_tolerance_and_segment_reset(self) -> None:
        start = datetime(2026, 1, 12, 8, 0, tzinfo=UTC)
        candles = build_candles(start, 70)
        one_hole = [item for index, item in enumerate(candles) if index != 67]
        missing, consecutive, segment = _gap_status(one_hole)
        self.assertEqual((missing, consecutive), (1, 1))
        self.assertEqual(len(segment), 2)

        two_consecutive = [
            item for index, item in enumerate(candles) if index not in (67, 68)
        ]
        missing, consecutive, segment = _gap_status(two_consecutive)
        self.assertEqual((missing, consecutive), (2, 2))
        self.assertEqual(len(segment), 1)

    def test_more_than_three_holes_in_expected_last_hour_are_counted(self) -> None:
        start = datetime(2026, 1, 12, 8, 0, tzinfo=UTC)
        candles = build_candles(start, 70)
        holes = {15, 25, 35, 45}
        missing, _, _ = _gap_status(
            [item for index, item in enumerate(candles) if index not in holes]
        )
        self.assertEqual(missing, 4)


class SetupTests(unittest.TestCase):
    """Weryfikuje warunki setupów dla obu kierunków."""

    def _orb_data(self, direction: str):
        """Przygotowuje zgodny zakres ORB oraz potwierdzającą świecę."""
        opens = build_candles(datetime(2026, 1, 12, 8, 0, tzinfo=UTC), 16)
        # Zakres 09:00–09:15 ma 15 otwarć i szerokość 20 punktów.
        for item in opens[:15]:
            item["high"], item["low"] = 18010.0, 17990.0
        if direction == "LONG":
            opens[-2]["close"] = 18000.0
            opens[-1].update(open=18008.0, high=18016.0, low=18007.0, close=18014.0)
            indicators = {
                "atr14_points": 5.0,
                "relative_volume": 1.3,
                "body_ratio": 6 / 9,
                "vwap": 18011.0,
                "ema20_5m": 18012.0,
                "close_5m": 18014.0,
            }
        else:
            opens[-2]["close"] = 18000.0
            opens[-1].update(open=17992.0, high=17993.0, low=17984.0, close=17986.0)
            indicators = {
                "atr14_points": 5.0,
                "relative_volume": 1.3,
                "body_ratio": 6 / 9,
                "vwap": 17989.0,
                "ema20_5m": 17988.0,
                "close_5m": 17986.0,
            }
        close_time = opens[-1]["timestamp_utc"] + timedelta(minutes=1)
        return opens, indicators, close_time, (18010.0, 17990.0, 15)

    def test_orb_core_and_mirror_directions(self) -> None:
        for direction in ("LONG", "SHORT"):
            candles, indicators, close_time, opening_range = self._orb_data(direction)
            result = _setup_candidates(candles, indicators, close_time, opening_range)
            self.assertIn(direction, [item["direction"] for item in result])

    def test_conflicting_candidate_directions_are_rejected(self) -> None:
        candles = build_candles(datetime(2026, 1, 12, 8, 0, tzinfo=UTC), 131)
        indicators = {
            "atr14_points": 5.0,
            "atr_ratio": 1.0,
            "candle_range": 2.0,
            "relative_volume": 0.8,
            "efficiency_ratio_30": 0.1,
        }
        with (
            patch(
                "claude_sonnet55_dax40.signal_logic.calculate_indicators",
                return_value=indicators,
            ),
            patch(
                "claude_sonnet55_dax40.signal_logic._setup_candidates",
                return_value=[
                    {"direction": "LONG", "setup": "TEST_LONG"},
                    {"direction": "SHORT", "setup": "TEST_SHORT"},
                ],
            ),
        ):
            result = generate_signal(
                candles,
                now=candles[-1]["timestamp_utc"] + timedelta(minutes=1, seconds=1),
            )
        self.assertEqual(result["signal"], "NONE")
        self.assertEqual(result["blocked_by"], ["conflicting_setups"])

    def test_unclear_efficiency_does_not_block_an_orb_signal(self) -> None:
        candles = build_candles(datetime(2026, 1, 12, 8, 0, tzinfo=UTC), 20)
        indicators = {
            "atr14_points": 5.0,
            "atr_ratio": 1.0,
            "candle_range": 2.0,
            "relative_volume": 1.5,
            "body_ratio": 0.8,
            "trend_5m": "UP",
            "rsi5": 52.0,
            "ema20_5m_slope": 1.0,
            "efficiency_ratio_30": 0.25,
        }
        with (
            patch(
                "claude_sonnet55_dax40.signal_logic.calculate_indicators",
                return_value=indicators,
            ),
            patch(
                "claude_sonnet55_dax40.signal_logic._setup_candidates",
                return_value=[
                    {"direction": "LONG", "setup": "OPENING_RANGE_BREAKOUT"}
                ],
            ),
        ):
            result = generate_signal(
                candles,
                now=candles[-1]["timestamp_utc"] + timedelta(minutes=1, seconds=1),
            )
        self.assertEqual(result["signal"], "LONG")

    def test_range_reversion_requires_candle_direction(self) -> None:
        candles = build_candles(datetime(2026, 1, 12, 10, 0, tzinfo=UTC), 60)
        candles[-1].update(
            open=100.0,
            high=105.0,
            low=90.0,
            close=99.0,
        )
        indicators = {
            "atr14_points": 5.0,
            "relative_volume": 0.8,
            "efficiency_ratio_30": 0.1,
            "vwap": 107.0,
            "ema55": 100.0,
            "ema55_10_bars_ago": 100.0,
            "bb_zscore_previous": -2.1,
            "bb_zscore": -1.0,
            "rsi5_last_two": [20.0, 20.0],
        }
        close_time = datetime(2026, 1, 12, 11, 0, tzinfo=UTC)
        self.assertEqual(
            _setup_candidates(candles, indicators, close_time, (None, None, 0)),
            [],
        )

        candles[-1].update(
            open=99.0,
            high=105.0,
            low=90.0,
            close=100.0,
        )
        indicators.update(
            vwap=108.0,
            bb_zscore_previous=-2.1,
            bb_zscore=-1.0,
            rsi5_last_two=[20.0, 20.0],
        )
        long_candidates = _setup_candidates(
            candles, indicators, close_time, (None, None, 0)
        )
        self.assertIn(
            ("LONG", "RANGE_REVERSION"),
            [(item["direction"], item["setup"]) for item in long_candidates],
        )

        candles[-1].update(
            open=100.0,
            high=110.0,
            low=95.0,
            close=99.0,
        )
        indicators.update(
            vwap=91.0,
            bb_zscore_previous=2.1,
            bb_zscore=1.0,
            rsi5_last_two=[80.0, 80.0],
        )
        short_candidates = _setup_candidates(
            candles, indicators, close_time, (None, None, 0)
        )
        self.assertIn(
            ("SHORT", "RANGE_REVERSION"),
            [(item["direction"], item["setup"]) for item in short_candidates],
        )

    def test_trend_pullback_core_conditions_mirror_both_directions(self) -> None:
        for direction in ("LONG", "SHORT"):
            candles = build_candles(
                datetime(2026, 1, 12, 8, 0, tzinfo=UTC),
                100,
            )
            previous_close = 100.5 if direction == "LONG" else 99.5
            if direction == "LONG":
                candles[-1].update(
                    open=100.0,
                    high=102.0,
                    low=100.5,
                    close=101.0,
                )
                for candle in candles[-5:]:
                    candle["low"] = 100.5
                indicators = {
                    "ema9": 102.0,
                    "ema21": 101.0,
                    "ema55": 100.0,
                    "ema20_5m": 102.0,
                    "ema20_5m_previous": 101.0,
                    "close_5m": 103.0,
                    "vwap": 100.0,
                    "efficiency_ratio_30": 0.4,
                    "rsi5_prior_three": [35.0, 41.0, 43.0],
                    "rsi5": 46.0,
                }
            else:
                candles[-1].update(
                    open=100.0,
                    high=99.5,
                    low=98.0,
                    close=99.0,
                )
                for candle in candles[-5:]:
                    candle["high"] = 99.5
                indicators = {
                    "ema9": 98.0,
                    "ema21": 99.0,
                    "ema55": 100.0,
                    "ema20_5m": 98.0,
                    "ema20_5m_previous": 99.0,
                    "close_5m": 97.0,
                    "vwap": 100.0,
                    "efficiency_ratio_30": 0.4,
                    "rsi5_prior_three": [65.0, 59.0, 57.0],
                    "rsi5": 54.0,
                }
            candles[-2]["close"] = previous_close
            indicators.update(
                atr14_points=5.0,
                relative_volume=0.8,
                body_ratio=0.6,
            )
            close_time = candles[-1]["timestamp_utc"] + timedelta(minutes=1)
            candidates = _setup_candidates(
                candles,
                indicators,
                close_time,
                (None, None, 0),
            )
            self.assertIn(
                (direction, "TREND_PULLBACK"),
                [(item["direction"], item["setup"]) for item in candidates],
            )

    def test_persisted_orb_direction_cannot_repeat_on_same_local_day(self) -> None:
        candles = build_candles(datetime(2026, 1, 12, 8, 0, tzinfo=UTC), 131)
        indicators = {
            "atr14_points": 5.0,
            "atr_ratio": 1.0,
            "candle_range": 2.0,
            "relative_volume": 0.8,
            "efficiency_ratio_30": 0.1,
        }
        with (
            patch(
                "claude_sonnet55_dax40.signal_logic.calculate_indicators",
                return_value=indicators,
            ),
            patch(
                "claude_sonnet55_dax40.signal_logic._setup_candidates",
                return_value=[
                    {"direction": "LONG", "setup": "OPENING_RANGE_BREAKOUT"}
                ],
            ),
        ):
            result = generate_signal(
                candles,
                now=candles[-1]["timestamp_utc"] + timedelta(minutes=1, seconds=1),
                orb_directions_today=["LONG"],
            )
        self.assertEqual(result["blocked_by"], ["orb_direction_already_used"])


class OutputAndBacktestTests(unittest.TestCase):
    """Weryfikuje zamknięcie/starość, strukturę JSON oraz metryki backtestu."""

    def test_forming_and_stale_candles_use_close_time_and_three_minute_limit(self) -> None:
        candle = build_candles(datetime(2026, 1, 12, 8, 4, tzinfo=UTC), 1)[0]
        forming = generate_signal([candle], now=candle["timestamp_utc"] + timedelta(seconds=30))
        self.assertEqual(forming["blocked_by"], ["forming_candle"])
        stale = generate_signal(
            [candle], now=candle["timestamp_utc"] + timedelta(minutes=4, seconds=1)
        )
        self.assertEqual(stale["blocked_by"], ["stale_data"])
        self.assertEqual(stale["data_age_minutes_from_close"], 3.0167)

    def test_none_json_includes_close_and_open_epochs_and_required_diagnostics(self) -> None:
        candle = build_candles(datetime(2026, 1, 12, 8, 4, tzinfo=UTC), 1)[0]
        result = generate_signal(
            [candle], now=candle["timestamp_utc"] + timedelta(minutes=1, seconds=1)
        )
        decoded = json.loads(json.dumps(result, allow_nan=False))
        self.assertEqual(decoded["instrument"], "DAX40")
        self.assertEqual(decoded["symbol"], "GER30")
        self.assertEqual(decoded["timestamp"], "2026-01-12T09:05:00+01:00")
        self.assertEqual(decoded["timestamp_epoch"], int(datetime(2026, 1, 12, 8, 5, tzinfo=UTC).timestamp()))
        self.assertEqual(decoded["candle_open_epoch"], int(candle["timestamp_utc"].timestamp()))
        self.assertEqual(decoded["candle_open_time"], "2026-01-12T09:04:00+01:00")
        self.assertEqual(decoded["candle_open_time_epoch"], int(candle["timestamp_utc"].timestamp()))
        self.assertTrue(REQUIRED_PARAMS.issubset(decoded["params"]))
        self.assertEqual(decoded["signal"], "NONE")
        self.assertEqual(decoded["blocked_by"], ["insufficient_atr_history"])

    def test_empty_history_has_complete_none_contract(self) -> None:
        result = generate_signal([], now=datetime(2026, 1, 12, 8, 0, tzinfo=UTC))
        self.assertEqual(result["signal"], "NONE")
        self.assertTrue(REQUIRED_PARAMS.issubset(result["params"]))
        self.assertIsNone(result["timestamp"])

    def test_warsaw_blackout_is_reported_before_general_signal_window(self) -> None:
        candle = build_candles(datetime(2026, 1, 12, 8, 3, tzinfo=UTC), 1)[0]
        result = generate_signal(
            [candle], now=candle["timestamp_utc"] + timedelta(minutes=1, seconds=1)
        )
        self.assertEqual(result["blocked_by"], ["warsaw_opening_blackout"])

    def test_weekend_returns_outside_session_instead_of_stale_or_gap(self) -> None:
        candle = build_candles(datetime(2026, 1, 16, 8, 0, tzinfo=UTC), 1)[0]
        result = generate_signal(
            [candle],
            now=datetime(2026, 1, 17, 12, 0, tzinfo=UTC),
        )
        self.assertEqual(result["blocked_by"], ["outside_session"])

    def test_empty_history_during_trading_hours_reports_warmup(self) -> None:
        result = generate_signal(
            [],
            now=datetime(2026, 1, 12, 8, 0, tzinfo=UTC),
        )
        self.assertEqual(result["blocked_by"], ["warmup"])

    def test_five_atr_candle_range_is_rejected(self) -> None:
        candles = build_candles(datetime(2026, 1, 12, 8, 0, tzinfo=UTC), 131)
        indicators = {
            "atr14_points": 5.0,
            "atr_ratio": 1.0,
            "candle_range": 25.0,
            "relative_volume": 1.5,
            "body_ratio": 0.8,
            "trend_5m": "UP",
            "rsi5": 52.0,
            "ema20_5m_slope": 1.0,
            "efficiency_ratio_30": 0.5,
        }
        with patch(
            "claude_sonnet55_dax40.signal_logic.calculate_indicators",
            return_value=indicators,
        ):
            result = generate_signal(
                candles,
                now=candles[-1]["timestamp_utc"] + timedelta(minutes=1, seconds=1),
            )
        self.assertEqual(result["blocked_by"], ["candle_range_spike"])

    def test_actionable_signal_uses_warsaw_close_and_open_timestamps(self) -> None:
        candles = build_candles(datetime(2026, 1, 12, 8, 0, tzinfo=UTC), 131)
        indicators = {
            "atr14_points": 5.0,
            "atr_ratio": 1.0,
            "candle_range": 2.0,
            "relative_volume": 1.5,
            "body_ratio": 0.8,
            "trend_5m": "UP",
            "rsi5": 52.0,
            "ema20_5m_slope": 1.0,
            "efficiency_ratio_30": 0.5,
        }
        with (
            patch(
                "claude_sonnet55_dax40.signal_logic.calculate_indicators",
                return_value=indicators,
            ),
            patch(
                "claude_sonnet55_dax40.signal_logic._setup_candidates",
                return_value=[
                    {"direction": "LONG", "setup": "OPENING_RANGE_BREAKOUT"}
                ],
            ),
        ):
            result = generate_signal(
                candles,
                now=candles[-1]["timestamp_utc"] + timedelta(minutes=1, seconds=1),
            )
        self.assertEqual(result["signal"], "LONG")
        self.assertEqual(result["timestamp"], "2026-01-12T11:11:00+01:00")
        self.assertEqual(result["candle_open_time"], "2026-01-12T11:10:00+01:00")
        self.assertEqual(result["timestamp_epoch"], int(datetime(2026, 1, 12, 10, 11, tzinfo=UTC).timestamp()))

    def test_backtest_report_includes_all_required_metrics(self) -> None:
        trades = [
            {"pnl_points": 4.0, "setup": "ORB", "warsaw_hour": 9},
            {"pnl_points": -2.0, "setup": "ORB", "warsaw_hour": 9},
            {"pnl_points": 3.0, "setup": "PULLBACK", "warsaw_hour": 10},
        ]
        result = BacktestEngine._report(trades, {datetime(2026, 1, 12).date(): 3})
        for key in (
            "total_signals",
            "win_rate_percent",
            "average_win_points",
            "average_loss_points",
            "expectancy_points",
            "profit_factor",
            "maximum_drawdown_points",
            "by_setup",
            "by_warsaw_hour",
        ):
            self.assertIn(key, result)
        self.assertEqual(result["by_setup"]["ORB"]["signals"], 2)
        self.assertEqual(result["by_warsaw_hour"]["9"]["signals"], 2)

    def test_backtest_time_stop_exits_after_twentieth_future_candle(self) -> None:
        start = datetime(2026, 1, 12, 8, 0, tzinfo=UTC)
        candles = [
            {
                "timestamp_utc": start + timedelta(minutes=index),
                "open": 18000.0,
                "high": 18001.0,
                "low": 17999.0,
                "close": 18000.0,
            }
            for index in range(21)
        ]
        trade = BacktestEngine._simulate_trade(
            candles,
            0,
            {
                "signal": "LONG",
                "price": 18000.0,
                "sl_points": 15.0,
                "tp_points": 20.0,
            },
        )
        self.assertIsNotNone(trade)
        self.assertEqual(trade["hold_minutes"], 20.0)
        self.assertEqual(trade["outcome"], "time_stop_or_segment_end")


class StorageTests(unittest.TestCase):
    """Weryfikuje idempotencję, format czasu i atomowy zapis JSON."""

    def test_signal_uses_close_datetime_and_open_epoch_for_idempotency(self) -> None:
        class FakeCursor:
            rowcount = 1
            lastrowid = 42

            def execute(self, query: str, values: tuple[object, ...]) -> None:
                if not hasattr(self, "queries"):
                    self.queries = []
                self.queries.append((query, values))

            def close(self) -> None:
                pass

        class FakeConnection:
            def __init__(self) -> None:
                self.commits = 0
                self.rollbacks = 0
                self.cursor_instance = FakeCursor()

            def cursor(self) -> FakeCursor:
                return self.cursor_instance

            def commit(self) -> None:
                self.commits += 1

            def rollback(self) -> None:
                self.rollbacks += 1

            def close(self) -> None:
                pass

        connection = FakeConnection()
        opened = datetime(2026, 1, 12, 8, 4, tzinfo=UTC)
        closed_local = datetime(2026, 1, 12, 9, 5, tzinfo=WARSAW)
        signal = {
            "signal": "LONG",
            "price": 18000.0,
            "candle_open_time": opened.astimezone(WARSAW).isoformat(),
            "candle_open_epoch": int(opened.timestamp()),
            "timestamp": closed_local.isoformat(),
            "timestamp_epoch": int(closed_local.timestamp()),
            "params": {"atr14_points": 2.5},
        }
        with patch.object(storage.mysql.connector, "connect", return_value=connection):
            inserted = DAX40Storage({}).save_signal(signal, 40)
        signal_query, values = connection.cursor_instance.queries[0]
        potential_query, potential_values = connection.cursor_instance.queries[1]
        self.assertTrue(inserted)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertIn("INSERT IGNORE INTO signals", signal_query)
        self.assertEqual(values[0], datetime(2026, 1, 12, 9, 5))
        self.assertEqual(values[3], "long")
        self.assertEqual(values[7], int(opened.timestamp()))
        self.assertIn("INSERT INTO signal_potention", potential_query)
        self.assertIn("source_candle.ask - source_candle.bid", potential_query)
        self.assertIn("signals.source_timestamp", potential_query)
        self.assertEqual(potential_values, (42,))
        payload = json.loads(values[6])
        self.assertEqual(payload["params"]["atr14_points"], 2.5)
        self.assertEqual(payload["utc_offset_minutes"], 60)

    def test_duplicate_returns_false_and_none_flag_is_single_runtime_gate(self) -> None:
        class FakeCursor:
            rowcount = 0

            def execute(self, query: str, values: tuple[object, ...]) -> None:
                pass

            def close(self) -> None:
                pass

        class FakeConnection:
            def cursor(self) -> FakeCursor:
                return FakeCursor()

            def commit(self) -> None:
                pass

            def rollback(self) -> None:
                pass

            def close(self) -> None:
                pass

        opened = datetime(2026, 1, 12, 8, 4, tzinfo=UTC)
        close = opened + timedelta(minutes=1)
        payload = {
            "signal": "LONG",
            "price": 18000,
            "candle_open_time": opened.isoformat(),
            "timestamp": close.isoformat(),
        }
        with patch.object(storage.mysql.connector, "connect", return_value=FakeConnection()):
            self.assertFalse(DAX40Storage({}).save_signal(payload, 1))
        with patch.object(storage, "SAVE_NONE_SIGNALS", False):
            self.assertFalse(
                DAX40Storage({}).save_signal({**payload, "signal": "NONE"}, 1)
            )

    def test_failed_insert_rolls_back(self) -> None:
        class FailedCursor:
            def execute(self, query: str, values: tuple[object, ...]) -> None:
                raise RuntimeError("insert failed")

            def close(self) -> None:
                pass

        class FailedConnection:
            rolled_back = False

            def cursor(self) -> FailedCursor:
                return FailedCursor()

            def commit(self) -> None:
                raise AssertionError("commit must not happen")

            def rollback(self) -> None:
                self.rolled_back = True

            def close(self) -> None:
                pass

        connection = FailedConnection()
        opened = datetime(2026, 1, 12, 8, 4, tzinfo=UTC)
        with patch.object(storage.mysql.connector, "connect", return_value=connection):
            with self.assertRaises(RuntimeError):
                DAX40Storage({}).save_signal(
                    {
                        "signal": "LONG",
                        "price": 18000,
                        "candle_open_time": opened.isoformat(),
                        "timestamp": (opened + timedelta(minutes=1)).isoformat(),
                    },
                    1,
                )
        self.assertTrue(connection.rolled_back)


if __name__ == "__main__":
    unittest.main()
