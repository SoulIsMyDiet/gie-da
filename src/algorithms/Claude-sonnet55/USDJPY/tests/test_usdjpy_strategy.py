"""Testy wskaźników, harmonogramu i diagnostyki strategii USD/JPY."""

from datetime import date, datetime, time, timedelta, timezone
import json
import math
import sys
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIRECTORY))

import config  # noqa: E402
import backtest  # noqa: E402
import calibrate  # noqa: E402
import indicators  # noqa: E402
import schedule  # noqa: E402
import signal_logic  # noqa: E402

UTC = timezone.utc


def _candles(count: int, start: datetime) -> list[dict]:
    """Tworzy deterministyczne świece OHLCV do testów bez dostępu do bazy."""
    candles = []
    for index in range(count):
        price = 155.0 + 0.003 * index + 0.004 * math.sin(index / 3)
        candles.append(
            {
                "timestamp_utc": start + timedelta(minutes=index),
                "open": price - 0.002,
                "high": price + 0.006,
                "low": price - 0.006,
                "close": price,
                "volume": 100.0,
            }
        )
    return candles


def test_configuration_matches_confirmed_usdjpy_settings():
    """Sprawdza symbole, pip, zaokrąglenie i potwierdzone parametry."""
    assert config.DB_SYMBOL == "USD/JPY"
    assert config.INSTRUMENT_NAME == "USDJPY"
    assert config.INSTRUMENT_PARAMS_NAME == "USD/JPY"
    assert config.PRICE_DECIMALS == 3
    assert config.PIP_SIZE == 0.01
    assert config.ASSUMED_COST_PIPS == 0.8
    assert config.SAVE_NONE_SIGNALS is True
    assert config.BEEP_ENABLED is True


def test_indicators_use_wilder_smoothing_and_first_true_range():
    """Sprawdza przykładowe TR, ATR i RSI Wildera."""
    frame = pd.DataFrame(
        [
            {"open": 10, "high": 12, "low": 9, "close": 11},
            {"open": 11, "high": 14, "low": 10, "close": 13},
            {"open": 13, "high": 14, "low": 11, "close": 12},
        ]
    )
    assert indicators.true_range(frame).tolist() == pytest.approx([3, 4, 3])
    assert indicators.wilder_atr(frame, 2).iloc[-1] == pytest.approx(3.25)
    rsi = indicators.wilder_rsi(pd.Series([1, 2, 1, 2, 3]), 3)
    assert rsi.iloc[-1] == pytest.approx(100 - 100 / (1 + (7 / 9) / (2 / 9)))


def test_m5_uses_four_real_closed_minutes_and_drops_open_bucket():
    """Agreguje cztery z pięciu świec rzeczywistych, bez bieżącego kubełka."""
    opened = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    candles = _candles(5, opened)
    aggregated = indicators.aggregate_m5(
        pd.DataFrame(candles[:4]), opened + timedelta(minutes=5)
    )
    assert len(aggregated) == 1
    assert aggregated.iloc[0]["close"] == candles[3]["close"]
    assert indicators.aggregate_m5(
        pd.DataFrame(candles[:3]), opened + timedelta(minutes=5)
    ).empty
    assert indicators.aggregate_m5(
        pd.DataFrame(candles[:4]), opened + timedelta(minutes=4, seconds=59)
    ).empty


def test_dst_offsets_are_taken_from_tokyo_london_and_new_york_zones():
    """Weryfikuje niezależne przejścia DST USA, UE i UK oraz brak DST w Tokio."""
    us_only_week = date(2026, 3, 9)
    eu_uk_summer_week = date(2026, 3, 30)
    tokyo_time = schedule.tokyo_open(us_only_week)
    london_us_only = schedule.london_open(us_only_week)
    london_summer = schedule.london_open(eu_uk_summer_week)
    ny_open = schedule.ny_open(us_only_week)
    assert tokyo_time.astimezone(UTC).hour == 0
    assert london_us_only.astimezone(UTC).hour == 8
    assert london_summer.astimezone(UTC).hour == 7
    assert ny_open.astimezone(UTC).hour == 12

    autumn_uk_open = schedule.london_open(date(2026, 10, 26))
    autumn_ny_open = schedule.ny_open(date(2026, 10, 26))
    us_winter_open = schedule.ny_open(date(2026, 11, 2))
    assert autumn_uk_open.astimezone(UTC).hour == 8
    assert autumn_ny_open.astimezone(UTC).hour == 12
    assert us_winter_open.astimezone(UTC).hour == 13


def test_trading_schedule_matches_forex_week_and_has_no_daily_rollover_gap():
    """Pomija weekend, ale nie tworzy fikcyjnej dziennej przerwy rollover."""
    friday_before_close = datetime(2026, 1, 9, 21, 59, tzinfo=UTC)
    friday_after_close = datetime(2026, 1, 9, 22, 0, tzinfo=UTC)
    sunday_before_open = datetime(2026, 1, 11, 21, 59, tzinfo=UTC)
    sunday_after_open = datetime(2026, 1, 11, 22, 0, tzinfo=UTC)
    assert schedule.is_trading_minute(friday_before_close)
    assert not schedule.is_trading_minute(friday_after_close)
    assert not schedule.is_trading_minute(sunday_before_open)
    assert schedule.is_trading_minute(sunday_after_open)

    rollover_start = datetime(2026, 1, 5, 16, 55, tzinfo=schedule.NEW_YORK)
    rollover_end = datetime(2026, 1, 5, 17, 5, tzinfo=schedule.NEW_YORK)
    expected = schedule.expected_candle_opens(rollover_start, rollover_end)
    assert len(expected) == 10
    assert schedule.segment_id(rollover_start) == schedule.segment_id(rollover_end)


def test_signal_window_uses_japan_and_us_timezones_with_dst():
    """Potwierdza początek po 09:15 JST oraz blackout do następnego otwarcia."""
    tokyo_start = datetime(2026, 3, 30, 9, 15, tzinfo=schedule.TOKYO)
    before_tokyo = tokyo_start - timedelta(minutes=1)
    ny_rollover = datetime(2026, 3, 30, 16, 56, tzinfo=schedule.NEW_YORK)
    assert schedule.signal_window_open(tokyo_start)
    assert not schedule.signal_window_open(before_tokyo)
    assert "rollover" in schedule.blackout_codes(ny_rollover)


def test_session_helpers_report_region_specific_open_times():
    """Sprawdza otwarcia sesji i klasyfikację przecięcia Londyn–Nowy Jork."""
    day = date(2026, 3, 30)
    assert schedule.tokyo_open(day).hour == 9
    assert schedule.london_open(day).hour == 8
    assert schedule.ny_open(day).hour == 8
    overlap = datetime(2026, 3, 30, 13, 30, tzinfo=UTC)
    assert schedule.session_name(overlap) == "OVERLAP"


def test_single_missing_minute_is_counted_but_not_blocked():
    """Jedna luka pozostaje w diagnostyce, ale nie przekracza dozwolonego progu."""
    start = datetime(2026, 1, 5, 20, 20, tzinfo=UTC)
    candles = _candles(60, start)
    del candles[8]
    missing, longest = signal_logic._missing_state(candles, candles[-1]["timestamp_utc"])
    assert missing == 1
    assert longest == 1
    assert not (
        longest > config.MAX_CONSECUTIVE_MISSING
        or missing > config.MAX_MISSING_IN_60
    )


def test_forming_candle_is_removed_and_closed_timestamp_is_exact():
    """Nie używa formowanej świecy, a wynik zapisuje czas zamknięcia w epoch."""
    opened = datetime(2026, 1, 5, 22, 0, tzinfo=UTC)
    candles = _candles(2, opened)
    result = signal_logic.generate_signal(
        candles,
        now=opened + timedelta(minutes=2),
    )
    assert result["timestamp"] == int((opened + timedelta(minutes=2)).timestamp())
    assert result["candle_open_time"].endswith("+01:00")
    assert result["instrument"] == "USDJPY"
    assert result["params"]["atr14_pips"] is None
    assert result["signal"] == "NONE"


def test_stale_data_and_outside_session_return_none_with_polish_reason():
    """Oznacza stary zestaw danych i blokadę poza oknem jako NONE."""
    opened = datetime(2026, 1, 5, 22, 0, tzinfo=UTC)
    candles = _candles(80, opened)
    result = signal_logic.generate_signal(
        candles,
        now=opened + timedelta(minutes=84),
    )
    assert result["signal"] == "NONE"
    assert "stale_data" in result["blocked_by"]
    assert isinstance(result["reason"], str)
    assert result["reason"].endswith(".")


def test_output_contains_requested_diagnostics_and_serializes_without_nan():
    """Sprawdza pola wyniku, parametry oraz brak niepoprawnych NaN w JSON."""
    opened = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    candles = _candles(100, opened)
    now = candles[-1]["timestamp_utc"] + timedelta(minutes=1)
    result = signal_logic.generate_signal(candles, now=now)
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    decoded = json.loads(encoded)
    assert decoded["instrument"] == "USDJPY"
    assert decoded["timestamp"] == int(now.timestamp())
    assert decoded["candle_open_time"].endswith("+01:00")
    for key in (
        "atr14_pips",
        "atr_ratio",
        "ema20_5m",
        "efficiency_ratio_30",
        "range_spike_ratio",
        "missing_candles_60m",
        "data_age_minutes",
        "segment_candles",
        "minutes_since_tokyo_open",
    ):
        assert key in decoded["params"]
    assert decoded["max_hold_minutes"] == 20


def test_qualified_setup_returns_long_levels_and_polish_reason(monkeypatch):
    """Przekłada poprawny setup na cenę wejścia, TP/SL i opis po polsku."""
    opened = datetime(2026, 1, 5, 13, 26, tzinfo=UTC)
    candles = _candles(100, opened)
    for candle in candles:
        candle["high"] = max(candle["open"], candle["close"]) + 0.01
        candle["low"] = min(candle["open"], candle["close"]) - 0.01
    monkeypatch.setattr(
        signal_logic,
        "_detect_setups",
        Mock(
            return_value=[
                {
                    "setup": "TREND_PULLBACK",
                    "direction": "LONG",
                    "score": 80,
                    "reasons": ["odbicie i potwierdzenie"],
                }
            ]
        ),
    )

    result = signal_logic.generate_signal(
        candles, now=opened + timedelta(minutes=100)
    )

    assert result["signal"] == "LONG"
    assert result["setup"] == "TREND_PULLBACK"
    assert result["score"] == 80
    assert result["tp_price"] > result["price"]
    assert result["sl_price"] < result["price"]
    assert result["reason"].startswith("Spełnione warunki setupu")


def test_manual_holiday_and_news_blackouts_are_used(monkeypatch):
    """Stosuje ręczne daty bez handlu i przedziały ważnych publikacji."""
    event_time = datetime(2026, 1, 5, 13, 30, tzinfo=UTC)
    day = event_time.astimezone(schedule.WARSAW).date()
    monkeypatch.setattr(schedule, "NO_TRADE_DATES", {day})
    assert schedule.is_holiday(event_time)
    monkeypatch.setattr(
        schedule,
        "NEWS_BLACKOUTS",
        [
            {
                "start": "2026-01-05T13:29:00+00:00",
                "end": "2026-01-05T13:31:00+00:00",
                "timezone": "UTC",
                "code": "news",
            }
        ],
    )
    assert "news" in schedule.blackout_codes(event_time)


def test_result_reason_for_no_setup_is_exact_polish_sentence():
    """Używa wymaganego domyślnego zdania dla braku setupu."""
    assert signal_logic._reason(["no_setup"], "NONE", None) == (
        "Warunki LONG ani SHORT nie zostały spełnione."
    )


def test_backtest_stops_before_take_profit_when_both_levels_hit():
    """Wybiera SL jako pierwszy, gdy TP i SL wypadają w tym samym OHLC."""
    opened = datetime(2026, 1, 5, 22, 0, tzinfo=UTC)
    candles = _candles(3, opened)
    candles[1]["high"] = 155.2
    candles[1]["low"] = 154.8
    signal = {
        "signal": "LONG",
        "setup": "RANGE_BREAKOUT",
        "price": 155.0,
        "tp_price": 155.1,
        "sl_price": 154.9,
        "candle_open_time": opened.isoformat(),
    }
    trade = backtest._trade_result(candles, 0, signal)
    assert trade["exit_reason"] == "stop_loss"
    assert trade["exit_price"] == 154.9
    assert trade["pnl_pips"] < 0


def test_backtest_closes_at_the_forex_segment_boundary():
    """Zamyka pozycję na piątkowym zamknięciu zamiast przenosić ją przez weekend."""
    opened = datetime(2026, 1, 9, 21, 40, tzinfo=UTC)
    candles = _candles(21, opened)
    signal = {
        "signal": "LONG",
        "setup": "TREND_PULLBACK",
        "price": 155.0,
        "tp_price": 200.0,
        "sl_price": 100.0,
        "candle_open_time": opened.isoformat(),
    }

    trade = backtest._trade_result(candles, 0, signal)

    assert trade["exit_reason"] == "segment_end"
    assert datetime.fromisoformat(trade["exit_time"]) == datetime(
        2026, 1, 9, 22, 0, tzinfo=UTC
    )


def test_backtest_time_stop_is_at_most_twenty_minutes():
    """Nie utrzymuje pozycji dłużej niż skonfigurowany maksymalny czas."""
    opened = datetime(2026, 1, 5, 20, 0, tzinfo=UTC)
    candles = _candles(30, opened)
    signal = {
        "signal": "LONG",
        "setup": "TREND_PULLBACK",
        "price": 155.0,
        "tp_price": 200.0,
        "sl_price": 100.0,
        "candle_open_time": opened.isoformat(),
    }

    trade = backtest._trade_result(candles, 0, signal)

    assert trade["exit_reason"] == "time_stop"
    assert datetime.fromisoformat(trade["exit_time"]) == datetime(
        2026, 1, 5, 20, 21, tzinfo=UTC
    )


def test_calibration_excludes_open_candles_and_does_not_change_config():
    """Pomija niedomkniętą świecę i zwraca wymagane przedziały percentyli."""
    opened = datetime(2026, 1, 5, 22, 0, tzinfo=UTC)
    candles = _candles(100, opened)
    report = calibrate.calibration_report(
        candles, now=opened + timedelta(minutes=99, seconds=30)
    )
    assert "TOKYO_RANGE_PIPS" in report
    assert "TOKYO" in report and "OVERLAP" in report
    assert isinstance(report["suggestions"], list)
    assert config.RANGE_MIN_PIPS == 15.0


def test_calibration_reports_tokyo_range_under_tokyo_session():
    """Pokazuje rozkład zakresu sesji Tokio także w statystykach TOKYO."""
    opened = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    candles = _candles(200, opened)
    report = calibrate.calibration_report(
        candles, now=opened + timedelta(minutes=200)
    )

    assert report["TOKYO"]["tokyo_range_pips"]
    assert report["TOKYO_RANGE_PIPS"] == report["TOKYO"]["tokyo_range_pips"]
