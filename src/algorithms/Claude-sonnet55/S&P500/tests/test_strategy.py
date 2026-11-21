"""Backend: testy harmonogramu, sygnałów, bazy i symulacji transakcji ES."""

import json
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from backtest import TradeParameters, run_backtest, simulate_trade
from indicators import completed_five_minute_candles
from schedule import is_signal_window, is_trading_minute
from signal_logic import generate_signal
from storage import SP500Storage
from trigger import analyze_candle, main

UTC = timezone.utc


def candle(stamp, price=6000.0, volume=100):
    """Tworzy poprawną świecę M1 dla wskazanego czasu i ceny."""
    return {"timestamp_utc": stamp, "open": price, "high": price + 1,
            "low": price - 1, "close": price, "volume": volume}


def history(count=120):
    """Buduje ciąg minut zakończony w poniedziałek podczas sesji USA."""
    end = datetime(2026, 11, 16, 15, 20, tzinfo=UTC)
    return [candle(end - timedelta(minutes=count - 1 - index)) for index in range(count)]


def test_market_schedule_and_dst():
    """Kontroluje przerwy tygodnia i godziny sygnałów po zmianach DST USA."""
    assert not is_trading_minute(datetime(2026, 11, 16, 22, 30, tzinfo=UTC))
    assert is_trading_minute(datetime(2026, 11, 16, 23, 0, tzinfo=UTC))
    assert not is_trading_minute(datetime(2026, 11, 20, 22, 0, tzinfo=UTC))
    assert not is_trading_minute(datetime(2026, 11, 21, 23, 0, tzinfo=UTC))
    assert is_trading_minute(datetime(2026, 11, 22, 22, 0, tzinfo=UTC))
    assert is_signal_window(datetime(2026, 3, 9, 13, 40, tzinfo=UTC))
    assert is_signal_window(datetime(2026, 11, 2, 14, 40, tzinfo=UTC))
    assert not is_signal_window(datetime(2026, 11, 2, 20, 55, tzinfo=UTC))


def test_five_minute_completion_and_minimum():
    """M5 nie wykorzystuje niedomkniętego koszyka i wymaga czterech minut."""
    start = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    rows = [candle(start + timedelta(minutes=i)) for i in range(5)]
    assert completed_five_minute_candles(rows, start + timedelta(minutes=4)) == []
    assert len(completed_five_minute_candles(rows[:3], start + timedelta(minutes=5))) == 0
    assert len(completed_five_minute_candles(rows[:4], start + timedelta(minutes=5))) == 1


def test_gap_detection_tolerates_one_and_blocks_two():
    """Jedna brakująca minuta jest tolerowana, dwie kolejne blokują wynik."""
    rows = history()
    now = rows[-1]["timestamp_utc"] + timedelta(minutes=1)
    single = generate_signal(rows[:-10] + rows[-9:], now=now)
    double = generate_signal(rows[:-10] + rows[-8:], now=now)
    assert single["params"]["missing_candles_60m"] == 1
    assert "data_gap" not in single["blocked_by"]
    assert "data_gap" in double["blocked_by"]


def test_null_volume_blocks_signal_and_is_json_safe():
    """Nieznany wolumen nie jest zastępowany dodatnią wartością."""
    rows = history()
    rows[-1]["volume"] = None
    result = generate_signal(rows, now=rows[-1]["timestamp_utc"] + timedelta(minutes=1))
    assert "relative_volume" in result["blocked_by"]
    json.dumps(result, allow_nan=False)


def test_missing_historical_volume_makes_vwap_undefined():
    """Nieznany wolumen w sesji unieważnia VWAP, nawet gdy świeca bieżąca go ma."""
    rows = history()
    rows[-5]["volume"] = None
    result = generate_signal(rows, now=rows[-1]["timestamp_utc"] + timedelta(minutes=1))
    assert result["params"]["vwap"] is None
    assert result["params"]["relative_volume"] is None
    assert "vwap" in result["blocked_by"]


def test_future_target_and_outside_window_do_not_query_database():
    """API nie analizuje świec formujących się lub znajdujących się poza oknem."""
    store = Mock()
    store.get_processed_signal.return_value = None
    start = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    assert not analyze_candle(store, start, start + timedelta(seconds=59))[1]
    assert not analyze_candle(store, start.replace(hour=21), start.replace(hour=22))[1]
    store.load_context.assert_not_called()


def test_api_targets_exact_candle_and_preserves_cutoff():
    """Żądana minuta zostaje przekazana do odczytu i zapisu bez sięgania w przyszłość."""
    rows = history()
    opened = rows[-1]["timestamp_utc"]
    store = Mock()
    store.get_processed_signal.return_value = None
    store.load_context.return_value = (rows, {"last_signal_time": None, "signals_today": 0, "orb_directions_today": []})
    store.save_signal.return_value = True
    result, saved = analyze_candle(store, opened, opened + timedelta(minutes=1))
    assert saved
    store.load_context.assert_called_once_with(opened)
    expected_closed = opened + timedelta(minutes=1)
    assert result["candle_open_time"] == opened.astimezone(config.WARSAW).isoformat()
    assert result["timestamp"] == expected_closed.astimezone(config.WARSAW).isoformat()
    assert result["candle_open_epoch"] == int(opened.timestamp())
    assert result["timestamp_epoch"] == int(expected_closed.timestamp())
    assert "stale_data" not in result["blocked_by"]
    assert store.save_signal.call_args.args[1] == len(rows)


def test_missing_requested_candle_is_not_replaced():
    """Starsza ostatnia świeca nie zastępuje konkretnej brakującej świecy API."""
    rows = history()
    opened = rows[-1]["timestamp_utc"] + timedelta(minutes=1)
    store = Mock()
    store.get_processed_signal.return_value = None
    store.load_context.return_value = (rows, {})
    assert not analyze_candle(store, opened, opened + timedelta(minutes=1))[1]
    store.save_signal.assert_not_called()


@pytest.mark.parametrize(
    ("saved", "beep_enabled", "expected_code"),
    [(True, True, 0), (True, False, 4), (False, True, 3), (False, False, 3)],
)
def test_api_exit_code_and_no_local_sound(saved, beep_enabled, expected_code, capsys):
    """API zwraca kod zależny od zapisu i flagi oraz nigdy nie odtwarza dźwięku."""
    result = {"signal": "NONE"}
    with patch.object(sys, "argv", ["strategy", "--api-candle-open-epoch", "1700000040"]), \
            patch("trigger.SP500Storage"), \
            patch("trigger.analyze_candle", return_value=(result, saved)), \
            patch.object(config, "BEEP_ENABLED", beep_enabled), \
            patch("trigger.play_signal_confirmation") as sound:
        assert main() == expected_code
    sound.assert_not_called()
    assert json.loads(capsys.readouterr().out) == result


@pytest.mark.parametrize("error", [RuntimeError("Błąd bazy"), KeyboardInterrupt()])
def test_api_error_is_not_a_success(error, capsys):
    """Błąd i przerwanie analizy zwracają kod błędu bez pozornego wyniku."""
    with patch.object(sys, "argv", ["strategy", "--api-candle-open-epoch", "1700000040"]), \
            patch("trigger.SP500Storage"), \
            patch("trigger.analyze_candle", side_effect=error), \
            patch("trigger.play_signal_confirmation") as sound:
        assert main() == 1
    sound.assert_not_called()
    assert capsys.readouterr().out == ""


def test_dst_offsets_order_and_state_cutoff():
    """Odczyt odtwarza oba wystąpienia powtórzonej godziny Warszawy w kolejności UTC."""
    cursor = Mock()
    wall = datetime(2026, 10, 25, 2, 30)
    rows = [{"datetime": wall, "utc_offset_minutes": offset, "open": 1, "high": 2,
             "low": 1, "close": 1, "volume": None} for offset in (60, 120)]
    cursor.fetchall.side_effect = [rows, []]
    cursor.fetchone.return_value = None
    connection = Mock()
    connection.cursor.return_value = cursor
    with patch.object(SP500Storage, "_connect", return_value=connection):
        data, _ = SP500Storage({}).load_context(datetime(2026, 10, 25, 2, 0, tzinfo=UTC))
    assert data[0]["timestamp_utc"] == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    assert data[1]["timestamp_utc"] == datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    assert cursor.execute.call_args_list[1].args[1][-1] == int(datetime(2026, 10, 25, 2, 0, tzinfo=UTC).timestamp())


def test_save_transaction_and_duplicate():
    """Zapis używa czasu zamknięcia PL; duplikat wycofuje transakcję bez wyciszenia innych błędów."""
    import mysql.connector
    rows = history()
    result = generate_signal(rows, now=rows[-1]["timestamp_utc"] + timedelta(minutes=1))
    cursor, connection = Mock(), Mock()
    connection.cursor.return_value = cursor
    with patch.object(SP500Storage, "_connect", return_value=connection):
        store = SP500Storage({})
        assert store.save_signal(result, len(rows))
        arguments = cursor.execute.call_args.args[1]
        assert arguments[1] == "ES"
        assert arguments[-1] == int(rows[-1]["timestamp_utc"].timestamp())
        assert arguments[0] == datetime.fromisoformat(result["timestamp"]).astimezone(config.WARSAW).replace(tzinfo=None)
        assert json.loads(arguments[-2])["blocked_by"] == result["blocked_by"]
        connection.commit.assert_called_once()
        cursor.execute.side_effect = mysql.connector.IntegrityError(errno=1062)
        assert not store.save_signal(result, len(rows))
        cursor.execute.side_effect = mysql.connector.IntegrityError(errno=1452)
        with pytest.raises(mysql.connector.IntegrityError):
            store.save_signal(result, len(rows))


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_save_direction_creates_potential_with_cost_and_aware_timestamp(direction):
    """LONG i SHORT zapisują koszt ES i zgodny z kalkulatorem czas w jednej transakcji."""
    rows = history()
    result = generate_signal(rows, now=rows[-1]["timestamp_utc"] + timedelta(minutes=1))
    result["signal"] = direction
    result["timestamp"] = datetime.fromisoformat(result["timestamp"]).astimezone(config.WARSAW).isoformat()
    cursor, connection = Mock(), Mock()
    cursor.lastrowid = 123
    connection.cursor.return_value = cursor
    calls = Mock()
    calls.attach_mock(cursor.execute, "execute")
    calls.attach_mock(connection.commit, "commit")
    with patch.object(SP500Storage, "_connect", return_value=connection):
        assert SP500Storage({}).save_signal(result, len(rows))
    assert cursor.execute.call_count == 2
    first, second = cursor.execute.call_args_list
    assert "INSERT INTO signals" in first.args[0]
    assert "INSERT INTO signal_potention" in second.args[0]
    assert second.args[1] == (123, Decimal("0.6"))
    assert [call[0] for call in calls.mock_calls] == ["execute", "execute", "commit"]
    payload = first.args[1][-2]
    timestamp = json.loads(payload)["timestamp"]
    assert isinstance(timestamp, str)
    assert datetime.fromisoformat(timestamp).utcoffset() is not None
    calculator_path = Path(__file__).resolve().parents[4] / "database" / "api" / "signal_potential_calculator.py"
    spec = importlib.util.spec_from_file_location("es_test_potential_calculator", calculator_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parsed = module.SignalPotentialCalculator._signal_timestamp(payload)
    assert parsed == rows[-1]["timestamp_utc"].replace(tzinfo=None) + timedelta(minutes=1)


def test_none_does_not_create_potential():
    """NONE zapisuje tylko sygnał, bez tworzenia wiersza potencjału."""
    result = generate_signal(history(), now=history()[-1]["timestamp_utc"] + timedelta(minutes=1))
    assert result["signal"] == "NONE"
    cursor, connection = Mock(), Mock()
    connection.cursor.return_value = cursor
    with patch.object(SP500Storage, "_connect", return_value=connection):
        assert SP500Storage({}).save_signal(result, 120)
    assert cursor.execute.call_count == 1
    connection.commit.assert_called_once()


def test_none_disabled_does_not_open_connection():
    """Wyłączenie tymczasowego zapisu NONE nie wymaga pozostałych zmian."""
    result = generate_signal(history(), now=history()[-1]["timestamp_utc"] + timedelta(minutes=1))
    assert result["signal"] == "NONE"
    with patch.object(config, "SAVE_NONE_SIGNALS", False), \
            patch.object(SP500Storage, "_connect") as connect:
        assert not SP500Storage({}).save_signal(result, 120)
    connect.assert_not_called()


@pytest.mark.parametrize("error_number", [1062, 1452])
def test_potential_failure_rolls_back_signal_and_propagates(error_number):
    """Każdy błąd drugiego INSERT, nawet duplikat, cofa sygnał i jest zgłaszany."""
    import mysql.connector
    result = generate_signal(history(), now=history()[-1]["timestamp_utc"] + timedelta(minutes=1))
    result["signal"] = "LONG"
    cursor, connection = Mock(), Mock()
    cursor.lastrowid = 123
    cursor.execute.side_effect = [None, mysql.connector.IntegrityError(errno=error_number)]
    connection.cursor.return_value = cursor
    with patch.object(SP500Storage, "_connect", return_value=connection):
        with pytest.raises(mysql.connector.IntegrityError):
            SP500Storage({}).save_signal(result, 120)
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


def test_naive_timestamp_is_rejected_before_connection():
    """Nieświadomy strefy czas zamknięcia nie jest zapisywany do JSON."""
    result = generate_signal(history(), now=history()[-1]["timestamp_utc"] + timedelta(minutes=1))
    result["signal"] = "LONG"
    result["timestamp"] = "2026-11-16T15:21:00"
    with patch.object(SP500Storage, "_connect") as connect:
        with pytest.raises(ValueError):
            SP500Storage({}).save_signal(result, 120)
    connect.assert_not_called()


@pytest.mark.parametrize(("direction", "expected"), [("LONG", Decimal("1.40000")), ("SHORT", Decimal("0.40000"))])
def test_potential_calculator_uses_saved_es_cost(direction, expected):
    """Istniejący kalkulator stosuje zapisany koszt ES do następnej świecy."""
    result = generate_signal(history(), now=history()[-1]["timestamp_utc"] + timedelta(minutes=1))
    result["signal"] = direction
    insert_cursor, connection = Mock(), Mock()
    insert_cursor.lastrowid = 123
    connection.cursor.return_value = insert_cursor
    with patch.object(SP500Storage, "_connect", return_value=connection):
        assert SP500Storage({}).save_signal(result, 120)
    insert_calls = insert_cursor.execute.call_args_list
    payload = insert_calls[0].args[1][-2]
    signal_id, spread = insert_calls[1].args[1]
    calculator_path = Path(__file__).resolve().parents[4] / "database" / "api" / "signal_potential_calculator.py"
    spec = importlib.util.spec_from_file_location("es_test_potential_calculator", calculator_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calculation_cursor = Mock()
    calculation_cursor.fetchall.side_effect = [
        [(signal_id, "ES", Decimal("6000"), direction.lower(), spread, payload)],
        [(datetime(2025, 1, 1), Decimal("6002"), Decimal("5999"))],
    ]
    module.SignalPotentialCalculator().update_pending(calculation_cursor, {"ES"})
    assert calculation_cursor.execute.call_args_list[-1].args[1][0] == expected


def test_backtest_stop_precedes_target_and_caps_holding():
    """Jednoczesny SL i TP realizuje SL, a koszt i mnożnik są parametryzowane."""
    rows = history(3)
    rows[1].update(low=5998, high=6002)
    signal = {"signal": "LONG", "setup": "TEST", "price": 6000, "sl_points": 1, "tp_points": 1}
    trade = simulate_trade(rows, 0, signal, TradeParameters(cost_points=0.6, point_value=50))
    assert trade["outcome"] == "stop_loss"
    assert trade["pnl_points"] == -1.6
    assert trade["pnl_value"] == -80
    assert trade["hold_minutes"] <= 20
    with pytest.raises(ValueError):
        TradeParameters(max_hold_minutes=21)


def test_stop_gap_uses_adverse_opening():
    """Gorsze otwarcie poniżej SL nie zostaje zastąpione optymistyczną ceną stop."""
    rows = history(2)
    rows[1].update(open=5997, low=5996, high=5999, close=5998)
    signal = {"signal": "LONG", "setup": "TEST", "price": 6000, "sl_points": 1, "tp_points": 1}
    trade = simulate_trade(rows, 0, signal, TradeParameters())
    assert trade["exit"] == 5997
    assert trade["pnl_points"] == -3.6


def test_backtest_uses_no_future_history_and_no_clock():
    """Każde wywołanie strategii otrzymuje tylko świece do danej minuty."""
    rows = history(4)
    calls = []
    def fake(history_rows, *args, **kwargs):
        calls.append([row["timestamp_utc"] for row in history_rows])
        assert kwargs["now"] == history_rows[-1]["timestamp_utc"] + timedelta(minutes=1)
        return {"signal": "NONE"}
    with patch("backtest.generate_signal", side_effect=fake):
        run_backtest(rows)
    assert [len(x) for x in calls] == [1, 2, 3, 4]
    assert all(x[-1] == rows[i]["timestamp_utc"] for i, x in enumerate(calls))
