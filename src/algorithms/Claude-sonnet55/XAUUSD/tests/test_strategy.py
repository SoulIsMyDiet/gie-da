"""Testy wskaźników, czasu i diagnostyki strategii XAU/USD."""

import json
import math
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIRECTORY))

import config  # noqa: E402
import indicators  # noqa: E402
import schedule  # noqa: E402
import signal_logic  # noqa: E402
import backtest  # noqa: E402
import calibrate  # noqa: E402

for _module_name in (
    "config", "indicators", "schedule", "signal_logic", "storage", "backtest"
):
    sys.modules.pop(_module_name, None)

UTC = timezone.utc


def _candles(count: int, start: datetime) -> list[dict]:
    """Tworzy deterministyczne świece M1 do testów bez dostępu do bazy."""
    candles = []
    for index in range(count):
        price = 2400 + 0.02 * index + 0.1 * math.sin(index / 3)
        candles.append({
            "timestamp_utc": start + timedelta(minutes=index),
            "open": price - 0.02,
            "high": price + 0.08,
            "low": price - 0.08,
            "close": price,
            "volume": 100.0,
        })
    return candles


def test_configuration_matches_storage_contract():
    """Sprawdza symbole, unikalną nazwę algorytmu i progi akceptowane przez użytkownika."""
    assert config.DB_SYMBOL == "XAU/USD"
    assert config.INSTRUMENT_NAME == "XAUUSD"
    assert config.INSTRUMENT_PARAMS_NAME == "XAU/USD"
    assert config.STRATEGY_NAME == "Claude-sonnet55/XAUUSDStrategyV1"
    assert config.PRICE_DECIMALS == 2
    assert config.SIGNAL_PRICE_DECIMALS == 5
    assert config.ASSUMED_COST_USD == 0.40
    assert config.SAVE_NONE_SIGNALS is True
    assert config.BEEP_ENABLED is True


def test_wilder_indicators_and_segment_first_true_range():
    """Porównuje ATR/RSI z prostymi wartościami i weryfikuje pierwsze TR."""
    rows = pd.DataFrame([
        {"open": 10, "high": 12, "low": 9, "close": 11},
        {"open": 11, "high": 14, "low": 10, "close": 13},
        {"open": 13, "high": 14, "low": 11, "close": 12},
    ])
    assert indicators.true_range(rows).tolist() == pytest.approx([3, 4, 3])
    assert indicators.wilder_atr(rows, 2).iloc[-1] == pytest.approx(3.25)
    assert indicators.wilder_rsi(pd.Series([1, 2, 1, 2, 3]), 3).iloc[-1] == pytest.approx(
        100 - 100 / (1 + (7 / 9) / (2 / 9))
    )


def test_wilder_atr_starts_with_sma_seed():
    """Inicjalizuje ATR średnią TR, a potem kontynuuje wygładzanie Wildera."""
    period = 14
    ranges = list(range(1, period + 2))
    rows = pd.DataFrame({
        "high": ranges,
        "low": [0] * len(ranges),
        "close": ranges,
    })

    atr = indicators.wilder_atr(rows, period)

    assert atr.iloc[period - 2] != atr.iloc[period - 2]
    assert atr.iloc[period - 1] == pytest.approx(7.5)
    assert atr.iloc[period] == pytest.approx((7.5 * 13 + 15) / 14)


def test_m5_uses_four_of_five_closed_minutes_without_filling():
    """Pozwala na cztery rzeczywiste minuty i odrzuca niedomknięty kubełek."""
    start = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    rows = _candles(5, start)
    frame = pd.DataFrame(rows[:4])
    result = indicators.aggregate_m5(frame, start + timedelta(minutes=5))
    assert len(result) == 1
    assert result.iloc[0]["open"] == rows[0]["open"]
    assert result.iloc[0]["close"] == rows[3]["close"]
    assert indicators.aggregate_m5(
        pd.DataFrame(rows[:3]), start + timedelta(minutes=5)
    ).empty
    assert indicators.aggregate_m5(frame, start + timedelta(minutes=4, seconds=59)).empty


def test_session_and_dst_are_converted_by_region():
    """Sprawdza tygodnie rozjazdu czasu letniego w Europie i USA."""
    march_london = schedule.london_open(datetime(2026, 3, 9).date())
    march_ny = schedule.ny_open(datetime(2026, 3, 9).date())
    assert march_london.astimezone(UTC).hour == 8
    assert march_ny.astimezone(UTC).hour == 12
    november_london = schedule.london_open(datetime(2026, 10, 26).date())
    november_ny = schedule.ny_open(datetime(2026, 10, 26).date())
    assert november_london.astimezone(UTC).hour == 8
    assert november_ny.astimezone(UTC).hour == 12
    assert schedule.session_name(datetime(2026, 3, 9, 12, 30, tzinfo=UTC)) == "OVERLAP"


def test_schedule_skips_rollover_and_weekend_in_expected_minutes():
    """Planowane przerwy nie pojawiają się w kalendarzu oczekiwanych świec."""
    before_rollover = datetime(2026, 1, 5, 22, 0, tzinfo=UTC)
    after_rollover = datetime(2026, 1, 5, 23, 0, tzinfo=UTC)
    assert schedule.expected_candle_opens(before_rollover, after_rollover) == []
    friday = datetime(2026, 1, 9, 22, 0, tzinfo=UTC)
    sunday = datetime(2026, 1, 11, 23, 0, tzinfo=UTC)
    assert schedule.expected_candle_opens(friday, sunday) == []


def test_rollover_blackout_ends_at_market_close_and_asia_has_one_reason():
    """Nie rozciąga blokad rollover na noc ani po ponownym otwarciu rynku."""
    london_asia = datetime(2026, 1, 6, 3, 0, tzinfo=schedule.LONDON)
    assert signal_logic._time_filters(london_asia.astimezone(UTC)) == [
        "outside_session"
    ]

    rollover = datetime(2026, 1, 6, 16, 55, tzinfo=schedule.NEW_YORK)
    after_close = datetime(2026, 1, 6, 17, 30, tzinfo=schedule.NEW_YORK)
    reopened = datetime(2026, 1, 6, 18, 10, tzinfo=schedule.NEW_YORK)
    assert "rollover" in schedule.blackout_codes(rollover)
    assert "rollover" not in schedule.blackout_codes(after_close)
    assert "rollover" not in schedule.blackout_codes(reopened)
    assert "pre_break_buffer" not in signal_logic._time_filters(
        after_close.astimezone(UTC)
    )


def test_is_trading_minute_accepts_aware_and_warsaw_naive_times():
    """Weryfikuje profil XAU/USD dla czasu świadomego i lokalnego naiwnego."""
    assert schedule.is_trading_minute(datetime(2026, 1, 5, 23, 0, tzinfo=UTC))
    assert not schedule.is_trading_minute(datetime(2026, 1, 5, 22, 30, tzinfo=UTC))
    assert schedule.is_trading_minute(datetime(2026, 1, 6, 0, 0))


def test_result_is_json_safe_closed_and_contains_diagnostics():
    """Wynik NONE zawiera zamknięcie świecy, komplet diagnostyki i prawidłowy JSON."""
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    rows = _candles(100, start)
    now = rows[-1]["timestamp_utc"] + timedelta(minutes=1)
    result = signal_logic.generate_signal(rows, now=now)
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    decoded = json.loads(encoded)
    assert decoded["instrument"] == "XAUUSD"
    assert decoded["signal"] == "NONE"
    assert decoded["price"] == round(rows[-1]["close"], config.SIGNAL_PRICE_DECIMALS)
    assert decoded["timestamp"] == int(now.timestamp())
    assert decoded["candle_open_time"].endswith("+01:00")
    assert "atr14_usd" in decoded["params"]
    assert "missing_candles_60m" in decoded["params"]
    assert decoded["reason"].endswith(".")
    assert decoded["blocked_by"]


def test_single_missing_candle_is_diagnostic_but_not_data_gap():
    """Pojedyncza brakująca minuta pozostaje diagnostyką i nie jest blokadą."""
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    candles = _candles(70, start)
    del candles[40]
    missing, longest = signal_logic._missing_state(
        candles, candles[-1]["timestamp_utc"]
    )
    assert missing == 1
    assert longest == 1
    assert longest <= config.MAX_CONSECUTIVE_MISSING


def test_blackouts_cover_event_windows_and_manual_dates(monkeypatch):
    """Sprawdza okna danych USA oraz ręcznie oznaczone dni bez handlu."""
    event_time = datetime(2026, 1, 5, 13, 30, tzinfo=UTC)
    assert "us_0830" in schedule.blackout_codes(event_time)
    monkeypatch.setattr(
        schedule,
        "NO_TRADE_DATES",
        {event_time.astimezone(schedule.WARSAW).date()},
    )
    assert schedule.is_holiday(event_time)


def test_early_close_applies_the_pre_break_buffer(monkeypatch):
    """Bufor 30 minut odnosi się do skróconego zamknięcia, nie tylko 17:00 ET."""
    event_time = datetime(2026, 1, 6, 20, 35, tzinfo=UTC)
    early_closes = {event_time.astimezone(schedule.NEW_YORK).date(): time(16, 0)}
    monkeypatch.setattr(schedule, "EARLY_CLOSE_DATES", early_closes)
    monkeypatch.setattr(signal_logic, "EARLY_CLOSE_DATES", early_closes)

    assert "pre_break_buffer" in signal_logic._time_filters(event_time)


def test_holiday_and_weekend_latest_candles_return_blocked_results(monkeypatch):
    """Zachowuje wskazaną świecę poza sesją, aby zwrócić NONE zamiast starego wyniku."""
    holiday_day = date(2026, 1, 5)
    monkeypatch.setattr(schedule, "NO_TRADE_DATES", {holiday_day})
    holiday_open = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    holiday_rows = _candles(2, holiday_open)
    holiday_now = holiday_rows[-1]["timestamp_utc"] + timedelta(minutes=1)

    holiday_result = signal_logic.generate_signal(holiday_rows, now=holiday_now)

    assert holiday_result["timestamp"] == int(holiday_now.timestamp())
    assert holiday_result["signal"] == "NONE"
    assert "holiday" in holiday_result["blocked_by"]

    weekend_open = datetime(2026, 1, 4, 3, 0, tzinfo=UTC)
    weekend_row = _candles(1, weekend_open)
    weekend_now = weekend_open + timedelta(minutes=1)
    weekend_result = signal_logic.generate_signal(weekend_row, now=weekend_now)

    assert weekend_result["timestamp"] == int(weekend_now.timestamp())
    assert weekend_result["signal"] == "NONE"
    assert "outside_session" in weekend_result["blocked_by"]


def test_early_close_target_candle_is_not_replaced_by_an_older_candle(monkeypatch):
    """Zwraca wynik dla zamkniętej świecy po skróconym zamknięciu."""
    early_closes = {date(2026, 1, 5): time(16, 0)}
    monkeypatch.setattr(schedule, "EARLY_CLOSE_DATES", early_closes)
    monkeypatch.setattr(signal_logic, "EARLY_CLOSE_DATES", early_closes)
    opened = datetime(2026, 1, 5, 21, 1, tzinfo=UTC)
    now = opened + timedelta(minutes=1)

    result = signal_logic.generate_signal(_candles(1, opened), now=now)

    assert result["timestamp"] == int(now.timestamp())
    assert result["signal"] == "NONE"
    assert "early_close" in result["blocked_by"]


def test_short_segment_reports_warmup_without_indicator_blockers(monkeypatch):
    """Oznacza zbyt krótki segment jako warmup, a nie brak ATR lub wolumenu."""
    opened = datetime(2026, 1, 5, 8, 16, tzinfo=UTC)
    candles = _candles(5, opened)
    now = candles[-1]["timestamp_utc"] + timedelta(minutes=1)
    monkeypatch.setattr(signal_logic, "_missing_state", lambda *_args: (0, 0))

    result = signal_logic.generate_signal(candles, now=now)

    assert "warmup" in result["blocked_by"]
    assert "atr_out_of_range" not in result["blocked_by"]
    assert "low_relative_volume" not in result["blocked_by"]


def test_backtest_exits_with_stop_loss_first_when_both_levels_hit():
    """Kontrakt backtestu wybiera SL przed TP przy dotknięciu obu poziomów."""
    opened = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    candles = _candles(3, opened)
    result = {
        "signal": "LONG",
        "setup": "RANGE_BREAKOUT",
        "price": 2400.0,
        "tp_price": 2401.0,
        "sl_price": 2399.0,
        "candle_open_time": opened.isoformat(),
    }
    candles[1]["high"] = 2402
    candles[1]["low"] = 2398
    trade = backtest._trade_result(candles, 0, result)
    assert trade["exit_reason"] == "stop_loss"
    assert trade["exit_price"] == 2399.0
    assert trade["pnl"] < 0


def test_backtest_time_stop_is_twenty_minutes_after_candle_close():
    """Maksymalny czas pozycji liczy się od zamknięcia świecy sygnałowej."""
    opened = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    candles = _candles(22, opened)
    signal = {
        "signal": "LONG",
        "setup": "TREND_PULLBACK",
        "price": candles[0]["close"],
        "tp_price": 9999.0,
        "sl_price": 1.0,
        "candle_open_time": opened.isoformat(),
    }

    trade = backtest._trade_result(candles, 0, signal)

    assert trade["entry_time"] == (opened + timedelta(minutes=1)).isoformat()
    assert trade["exit_time"] == (opened + timedelta(minutes=21)).isoformat()
    assert trade["exit_reason"] == "time_stop"


def test_backtest_ignores_take_profit_after_time_limit():
    """Nie zalicza poziomu TP na świecy, która zamyka się po limicie 20 minut."""
    opened = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    candles = _candles(22, opened)
    candles.pop(20)
    candles[20]["high"] = 2402.0
    signal = {
        "signal": "LONG",
        "setup": "TREND_PULLBACK",
        "price": 2400.0,
        "tp_price": 2401.0,
        "sl_price": 2390.0,
        "candle_open_time": opened.isoformat(),
    }

    trade = backtest._trade_result(candles, 0, signal)

    assert trade["exit_reason"] == "time_stop"
    assert trade["exit_time"] == (opened + timedelta(minutes=21)).isoformat()


def test_backtest_closes_at_early_market_close(monkeypatch):
    """Kończy pozycję na końcu skróconej sesji, nawet bez zmiany segmentu."""
    monkeypatch.setattr(
        schedule,
        "EARLY_CLOSE_DATES",
        {date(2026, 1, 5): time(16, 0)},
    )
    opened = datetime(2026, 1, 5, 20, 58, tzinfo=UTC)
    candles = _candles(3, opened)
    signal = {
        "signal": "LONG",
        "setup": "TREND_PULLBACK",
        "price": candles[0]["close"],
        "tp_price": 9999.0,
        "sl_price": 1.0,
        "candle_open_time": opened.isoformat(),
    }

    trade = backtest._trade_result(candles, 0, signal)

    assert trade["exit_reason"] == "segment_end"
    assert trade["exit_time"] == (opened + timedelta(minutes=2)).isoformat()


def test_calibration_excludes_forming_candles_and_incomplete_asia_range():
    """Kalibruje wyłącznie zamknięte świece i pomija zakres Azji z małym pokryciem."""
    london_open = datetime(2026, 1, 5, 13, 0, tzinfo=UTC)
    london_rows = _candles(15, london_open)
    london_rows[-1]["high"] += 3.0
    calibration_now = london_open + timedelta(minutes=14, seconds=30)

    report = calibrate.calibration_report(london_rows, now=calibration_now)

    assert report["LONDON"]["atr14_usd"]["95"] < 0.2

    asia_open = datetime(2026, 1, 5, 1, 0, tzinfo=UTC)
    partial_asia = _candles(20, asia_open)
    asia_report = calibrate.calibration_report(
        partial_asia,
        now=asia_open + timedelta(minutes=21),
    )
    assert asia_report["ASIA_RANGE_USD"] == {}


def test_cooldown_and_daily_limit_use_closed_signal_history():
    """Cooldown blokuje sygnał przed 10 minutami, a limit dzienny wynosi 10."""
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    rows = _candles(100, start)
    now = rows[-1]["timestamp_utc"] + timedelta(minutes=1)
    closed_epoch = int(now.timestamp())

    cooling = signal_logic.generate_signal(
        rows,
        now=now,
        signal_history={"last_signal_epoch": closed_epoch - 599},
    )
    at_limit = signal_logic.generate_signal(
        rows,
        now=now,
        signal_history={"signals_today": config.MAX_SIGNALS_PER_DAY},
    )
    expired = signal_logic.generate_signal(
        rows,
        now=now,
        signal_history={"last_signal_epoch": closed_epoch - 600},
    )

    assert "cooldown" in cooling["blocked_by"]
    assert "daily_limit" in at_limit["blocked_by"]
    assert "cooldown" not in expired["blocked_by"]


def test_opposite_setups_conflict_even_if_one_score_is_below_threshold(monkeypatch):
    """Przeciwne setupy dają NONE, nawet gdy tylko jeden osiąga próg punktowy."""
    opened = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    candles = [{
        "timestamp_utc": opened,
        "open": 2400.0,
        "high": 2401.0,
        "low": 2399.0,
        "close": 2400.5,
        "volume": 100.0,
    }]
    candidates = [
        {"direction": "LONG", "score": 80, "setup": "RANGE_BREAKOUT"},
        {"direction": "SHORT", "score": 60, "setup": "MEAN_REVERSION"},
    ]

    def indicators_for_rows(rows):
        rows["atr14_usd"] = 1.0
        rows["atr_ratio"] = 1.0
        rows["rel_volume"] = 1.0
        return rows

    monkeypatch.setattr(signal_logic, "_normalize_candles", lambda rows, _now: rows)
    monkeypatch.setattr(signal_logic, "build_m1_indicators", indicators_for_rows)
    monkeypatch.setattr(signal_logic, "aggregate_m5", lambda *_args: pd.DataFrame())
    monkeypatch.setattr(signal_logic, "anchored_vwap", lambda *_args: (None, opened))
    monkeypatch.setattr(signal_logic, "_asian_range", lambda *_args: (None, None, None, 0, 0.0))
    monkeypatch.setattr(signal_logic, "_missing_state", lambda *_args: (0, 0))
    monkeypatch.setattr(
        signal_logic, "_build_params",
        lambda *_args: {"data_age_minutes": 0.0, "candle_range_usd": 2.0},
    )
    monkeypatch.setattr(signal_logic, "_time_filters", lambda _instant: [])
    monkeypatch.setattr(signal_logic, "_detect_setups", lambda *_args: candidates)
    monkeypatch.setattr(signal_logic, "WARMUP_BREAKOUT", 1)
    monkeypatch.setattr(signal_logic, "WARMUP_PULLBACK", 1)

    result = signal_logic.generate_signal(candles, now=opened + timedelta(minutes=1))

    assert result["signal"] == "NONE"
    assert result["blocked_by"] == ["conflicting_setups"]
