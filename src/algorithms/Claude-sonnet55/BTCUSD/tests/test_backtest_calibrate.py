"""Testy symulacji transakcji i walidacji kalibracji progu."""

from datetime import datetime, timedelta, timezone

import pytest

from claude_btcusd_test.backtest import BacktestSettings, simulate_trade
from claude_btcusd_test.calibrate import calibrate_thresholds

UTC = timezone.utc


def test_simultaneous_stop_and_target_is_resolved_as_stop() -> None:
    """Jeśli jedna świeca narusza TP i SL, symulacja wybiera gorszy wynik."""
    opened = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
    candles = [
        {
            "timestamp_utc": opened,
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
        },
        {
            "timestamp_utc": opened + timedelta(minutes=1),
            "open": 100,
            "high": 103,
            "low": 97,
            "close": 100,
        },
    ]
    signal = {
        "signal": "LONG",
        "setup": "SQUEEZE_BREAKOUT",
        "price": 100,
        "tp_price": 102,
        "sl_price": 98,
    }

    trade = simulate_trade(candles, 0, signal, BacktestSettings(0, 0))

    assert trade["outcome"] == "stop_loss"
    assert trade["exit_price"] == 98


def test_backtest_uses_observed_entry_and_exit_spreads() -> None:
    """Koszt netto korzysta z kwotowań obu świec zamiast stałego założenia."""
    opened = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
    candles = [
        {
            "timestamp_utc": opened,
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "bid": 99.95,
            "ask": 100.05,
        },
        {
            "timestamp_utc": opened + timedelta(minutes=1),
            "open": 100,
            "high": 103,
            "low": 99,
            "close": 102,
            "bid": 101.9,
            "ask": 102.1,
        },
    ]
    signal = {
        "signal": "LONG",
        "setup": "SQUEEZE_BREAKOUT",
        "price": 100,
        "tp_price": 102,
        "sl_price": 98,
    }

    trade = simulate_trade(candles, 0, signal, BacktestSettings())

    assert trade["outcome"] == "take_profit"
    assert trade["applied_cost_bps"] == pytest.approx(15.0)
    assert trade["pnl_bps"] == pytest.approx(185.0)


def test_backtest_does_not_invent_spread_when_quotes_are_missing() -> None:
    """Bez ręcznego kosztu i kwotowań wynik netto pozostaje nierozstrzygnięty."""
    opened = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
    candles = [
        {
            "timestamp_utc": opened,
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
        },
        {
            "timestamp_utc": opened + timedelta(minutes=1),
            "open": 100,
            "high": 103,
            "low": 99,
            "close": 102,
        },
    ]
    signal = {
        "signal": "LONG",
        "setup": "SQUEEZE_BREAKOUT",
        "price": 100,
        "tp_price": 102,
        "sl_price": 98,
    }

    trade = simulate_trade(candles, 0, signal, BacktestSettings())

    assert trade["outcome"] == "take_profit"
    assert trade["applied_cost_bps"] is None
    assert trade["pnl_bps"] is None


def test_calibration_rejects_insufficient_history() -> None:
    """Kalibracja nie zgaduje progu, gdy historia nie wystarcza."""
    with pytest.raises(ValueError, match="Za mało danych"):
        calibrate_thresholds([])


def test_backtest_settings_reject_costs_beyond_valid_range() -> None:
    """Koszt ujemny nie może poprawiać sztucznie wyniku backtestu."""
    with pytest.raises(ValueError, match="nie mogą być ujemne"):
        BacktestSettings(cost_bps=-1)
