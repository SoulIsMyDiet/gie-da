"""Backend: granice symulacji transakcji i brak dostępu do przyszłości."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import TradeParameters, run_backtest, simulate_trade


def _row(stamp, close=6000.0, high=6001.0, low=5999.0):
    """Tworzy świecę o podanym czasie UTC i zakresie."""
    return {
        "timestamp_utc": stamp,
        "open": 6000.0,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100.0,
    }


def _signal():
    """Tworzy sygnał LONG z cenami SL i TP do symulacji."""
    return {
        "signal": "LONG",
        "setup": "TREND_PULLBACK",
        "price": 6000.0,
        "sl_points": 2.0,
        "tp_points": 2.0,
    }


def test_stop_precedes_target_when_both_hit():
    """Jednoczesne dotknięcie SL i TP rozlicza się jako stratę."""
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    rows = [_row(stamp), _row(stamp + timedelta(minutes=1), high=6003.0, low=5997.0)]
    trade = simulate_trade(rows, 0, _signal(), TradeParameters())
    assert trade["outcome"] == "stop_loss"
    assert trade["pnl_points"] == -2.6


def test_time_limit_includes_twentieth_candle():
    """Symulacja uwzględnia pełne 20 świec po wejściu, ale nie dwudziestą pierwszą."""
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    rows = [_row(stamp + timedelta(minutes=i)) for i in range(22)]
    rows[20]["high"] = 6003.0
    trade = simulate_trade(rows, 0, _signal(), TradeParameters())
    assert trade["outcome"] == "take_profit"
    assert trade["hold_minutes"] == 20
    rows[20]["high"] = 6001.0
    rows[21]["high"] = 6003.0
    trade = simulate_trade(rows, 0, _signal(), TradeParameters())
    assert trade["outcome"] == "limit_czasu"
    assert trade["hold_minutes"] == 20


def test_trade_exits_before_cash_session_close():
    """Świeca otwarta po 16:00 ET nie może zmienić wyniku pozycji."""
    stamp = datetime(2026, 11, 16, 20, 58, tzinfo=timezone.utc)
    rows = [
        _row(stamp),
        _row(stamp + timedelta(minutes=1), close=6000.5),
        _row(stamp + timedelta(minutes=2), high=6003.0),
    ]
    trade = simulate_trade(rows, 0, _signal(), TradeParameters())
    assert trade["exit"] == 6000.5
    assert trade["pnl_points"] == -0.1


def test_backtest_supplies_only_past_rows_and_historical_now():
    """Kolejne decyzje dostają tylko znaną historię i czas zamknięcia świecy."""
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    rows = [_row(stamp + timedelta(minutes=i)) for i in range(4)]
    observations = []

    def evaluate(candles, *args, now):
        """Zapamiętuje dane widoczne w chwili symulowanej decyzji."""
        observations.append((len(candles), now, candles[-1]["timestamp_utc"]))
        return {"signal": "NONE"}

    with patch("backtest.generate_signal", side_effect=evaluate):
        run_backtest(rows)
    assert [length for length, _, _ in observations] == [1, 2, 3, 4]
    assert all(now == last + timedelta(minutes=1) for _, now, last in observations)
