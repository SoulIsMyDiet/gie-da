"""Backend: trwały stan i ponawianie odczytu zamkniętej minuty."""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from mysql.connector import Error as MySQLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import trigger
from storage import SP500Storage


@pytest.fixture
def state_path():
    """Tworzy nazwę pliku stanu w projekcie i usuwa go po teście."""
    path = Path.cwd() / ("test-stanu-" + uuid4().hex + ".json")
    yield path
    path.unlink(missing_ok=True)


def test_checkpoint_round_trip_and_corruption(state_path):
    """Stan odtwarza minutę UTC i nie ukrywa błędnego pliku."""
    path = state_path
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    assert trigger.load_checkpoint(path) is None
    trigger.save_checkpoint(path, stamp)
    assert trigger.load_checkpoint(path) == stamp
    path.write_text('{"last_handled_open":"błędny czas"}', encoding="utf-8")
    with pytest.raises(ValueError):
        trigger.load_checkpoint(path)


def test_single_run_uses_latest_closed_candle():
    """Jednorazowa analiza korzysta z ostatniej świecy bazy, a nie bieżącej minuty."""
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    now = stamp + timedelta(minutes=5)
    storage = Mock()
    storage.latest_closed_open.return_value = stamp
    with patch("trigger.analyze_candle", return_value=({"signal": "NONE"}, True)) as analyze:
        assert trigger.run_latest(storage, now)[1]
    analyze.assert_called_once_with(storage, stamp, now, allow_outside=True)


def test_previously_saved_candle_is_not_analyzed():
    """Ponowny POST po restarcie nie przelicza już zapisanego wyniku."""
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    storage = Mock()
    storage.get_processed_signal.return_value = {"signal": "LONG", "score": 80}
    with patch("trigger.generate_signal") as generate:
        result, saved = trigger.analyze_candle(storage, stamp, stamp + timedelta(minutes=1))
    assert not saved
    assert result == {"signal": "LONG", "score": 80}
    storage.load_context.assert_not_called()
    generate.assert_not_called()


@pytest.mark.parametrize(("opened", "now", "reason", "previous_candles"), [
    (datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc),
     datetime(2026, 11, 16, 15, 0, 30, tzinfo=timezone.utc), "forming_candle", []),
    (datetime(2026, 11, 15, 15, 0, tzinfo=timezone.utc),
     datetime(2026, 11, 15, 15, 1, tzinfo=timezone.utc), "outside_session", []),
    (datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc),
     datetime(2026, 11, 16, 15, 1, tzinfo=timezone.utc), "missing_candle", []),
    (datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc),
     datetime(2026, 11, 16, 15, 1, tzinfo=timezone.utc), "missing_candle",
     [{"timestamp_utc": datetime(2026, 11, 16, 14, 59, tzinfo=timezone.utc)}]),
])
def test_blocked_candle_returns_full_json_without_indicators_or_save(opened, now, reason, previous_candles):
    """Blokady zachowują pełną strukturę, czas żądanej świecy i nie zapisują wyniku."""
    storage = Mock()
    storage.get_processed_signal.return_value = None
    storage.load_context.return_value = (previous_candles, {})
    with patch("trigger.generate_signal", wraps=trigger.generate_signal) as generate, \
            patch("signal_logic.calculate_indicators") as indicators:
        result, saved = trigger.analyze_candle(storage, opened, now)
    closed = opened + timedelta(minutes=1)
    assert result == {
        "instrument": trigger.config.INSTRUMENT,
        "algorithm_name": trigger.config.STRATEGY_NAME,
        "price": None,
        "timestamp": closed.astimezone(trigger.config.WARSAW).isoformat(),
        "timestamp_epoch": int(closed.timestamp()),
        "candle_open_time": opened.astimezone(trigger.config.WARSAW).isoformat(),
        "candle_open_epoch": int(opened.timestamp()),
        "signal": "NONE",
        "setup": None,
        "score": 0,
        "tp_price": None,
        "sl_price": None,
        "tp_points": None,
        "sl_points": None,
        "max_hold_minutes": trigger.config.MAX_HOLD_MINUTES,
        "params": {},
        "reasons": [],
        "blocked_by": [reason],
    }
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    assert not saved
    generate.assert_called_once_with([], now=now)
    indicators.assert_not_called()
    storage.save_signal.assert_not_called()
    if reason == "missing_candle":
        storage.get_processed_signal.assert_called_once_with(opened)
        storage.load_context.assert_called_once_with(opened)
    else:
        storage.get_processed_signal.assert_not_called()
        storage.load_context.assert_not_called()


@pytest.mark.parametrize(("opened", "now", "reason"), [
    (datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc),
     datetime(2026, 11, 16, 15, 0, 30, tzinfo=timezone.utc), "forming_candle"),
    (datetime(2026, 11, 15, 15, 0, tzinfo=timezone.utc),
     datetime(2026, 11, 15, 15, 1, tzinfo=timezone.utc), "outside_session"),
    (datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc),
     datetime(2026, 11, 16, 15, 1, tzinfo=timezone.utc), "missing_candle"),
])
def test_api_blocked_candle_emits_full_json(opened, now, reason, capsys):
    """Rzeczywista ścieżka API emituje pełny JSON i kod braku zapisu dla blokad."""
    storage = Mock()
    storage.get_processed_signal.return_value = None
    storage.load_context.return_value = ([], {})
    clock = Mock(wraps=datetime)
    clock.now.return_value = now
    with patch.object(sys, "argv", ["trigger.py", "--api-candle-open-epoch", str(int(opened.timestamp()))]), \
            patch("trigger.datetime", clock), patch("trigger.SP500Storage", return_value=storage):
        assert trigger.main() == 3
    result = json.loads(capsys.readouterr().out)
    assert result["blocked_by"] == [reason]
    assert result["candle_open_epoch"] == int(opened.timestamp())
    assert result["timestamp_epoch"] == int((opened + timedelta(minutes=1)).timestamp())
    assert result["price"] is None
    assert set(result) == set(trigger.generate_signal([], now=now))
    storage.save_signal.assert_not_called()


def test_stored_result_is_loaded_with_exact_epoch():
    """Wcześniejszy wynik jest wyszukiwany po jednoznacznym czasie otwarcia UTC."""
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    cursor, connection = Mock(), Mock()
    connection.cursor.return_value = cursor
    cursor.fetchone.return_value = ('{"signal":"NONE","params":{"rsi5":50}}',)
    with patch.object(SP500Storage, "_connect", return_value=connection):
        result = SP500Storage({}).get_processed_signal(stamp)
    assert result == {"signal": "NONE", "params": {"rsi5": 50}}
    assert cursor.execute.call_args.args[1][-1] == int(stamp.timestamp())
    cursor.close.assert_called_once()
    connection.close.assert_called_once()


def test_loop_waits_until_candle_exists_and_checkpoints_once(state_path):
    """Świeca opóźniona o kilka sekund jest analizowana tylko raz."""
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    now = stamp + timedelta(minutes=1, seconds=5)
    clock = Mock(wraps=datetime)
    clock.now.return_value = now
    storage = Mock()
    storage.latest_processed_open.return_value = stamp - timedelta(minutes=1)
    path = state_path
    with patch("trigger.datetime", clock), patch("trigger.is_trading_minute", return_value=True), \
            patch("trigger.is_signal_window", return_value=True), \
            patch("trigger.analyze_candle", side_effect=[
                ({"signal": "NONE", "blocked_by": ["missing_candle"]}, False),
                ({"signal": "NONE", "blocked_by": []}, True),
            ]) as analyze, \
            patch("trigger.play_signal_confirmation"), \
            patch("trigger.time.sleep", side_effect=[None, KeyboardInterrupt]):
        with pytest.raises(KeyboardInterrupt):
            trigger.run_loop(storage, path)
    assert analyze.call_count == 2
    assert json.loads(path.read_text(encoding="utf-8"))["last_handled_open"] == stamp.isoformat()


def test_loop_checks_expired_candle_before_skipping(state_path):
    """Po limicie pętla sprawdza brak świecy, zanim zapisze pominiętą minutę."""
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    clock = Mock(wraps=datetime)
    clock.now.return_value = stamp + timedelta(minutes=1, seconds=21)
    storage = Mock()
    storage.latest_processed_open.return_value = stamp - timedelta(minutes=1)
    path = state_path
    with patch("trigger.datetime", clock), patch("trigger.is_trading_minute", return_value=True), \
            patch("trigger.is_signal_window", return_value=True), \
            patch("trigger.analyze_candle", return_value=(
                {"signal": "NONE", "blocked_by": ["missing_candle"]}, False)) as analyze, \
            patch("trigger.time.sleep", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            trigger.run_loop(storage, path)
    analyze.assert_called_once()
    assert trigger.load_checkpoint(path) == stamp


def test_loop_handles_existing_candle_after_deadline(state_path):
    """Przeterminowany odczyt nadal ocenia dostępną świecę zamiast ją pomijać."""
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    now = stamp + timedelta(minutes=5)
    trigger.save_checkpoint(state_path, stamp - timedelta(minutes=1))
    storage = Mock()
    storage.get_processed_signal.return_value = None
    storage.load_context.return_value = ([{
        "timestamp_utc": stamp, "open": 100.0, "high": 101.0,
        "low": 99.0, "close": 100.0, "volume": 10.0,
    }], {})
    storage.save_signal.return_value = False
    clock = Mock(wraps=datetime)
    clock.now.return_value = now
    with patch("trigger.datetime", clock), patch("trigger._emit", side_effect=KeyboardInterrupt) as emit:
        with pytest.raises(KeyboardInterrupt):
            trigger.run_loop(storage, state_path)
    result = emit.call_args.args[0]
    assert "stale_data" in result["blocked_by"]
    storage.load_context.assert_called_once_with(stamp)
    assert trigger.load_checkpoint(state_path) == stamp


def test_loop_retries_mysql_error_without_checkpoint_and_then_reads_candle(state_path):
    """Błąd bazy nie kończy minuty; ponowienie po limicie nadal odczytuje świecę."""
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    before = stamp + timedelta(minutes=1, seconds=5)
    after = stamp + timedelta(minutes=1, seconds=30)
    trigger.save_checkpoint(state_path, stamp - timedelta(minutes=1))
    storage = Mock()
    clock = Mock(wraps=datetime)
    clock.now.side_effect = [before, before, after, after]

    def wait(_seconds):
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        assert payload["last_handled_open"] == (stamp - timedelta(minutes=1)).isoformat()

    with patch("trigger.datetime", clock), \
            patch("trigger.analyze_candle", side_effect=[
                MySQLError("chwilowa niedostępność"),
                ({"signal": "NONE", "blocked_by": []}, False),
            ]) as analyze, \
            patch("trigger.time.sleep", side_effect=[wait, KeyboardInterrupt]) as sleep, \
            patch("trigger._emit"):
        with pytest.raises(KeyboardInterrupt):
            trigger.run_loop(storage, state_path)
    assert analyze.call_count == 2
    assert analyze.call_args.args[2] == after
    assert sleep.call_args_list[0].args == (trigger.config.POLL_INTERVAL_SECONDS,)
    assert trigger.load_checkpoint(state_path) == stamp


def test_mysql_recovery_after_restart_reads_stale_candle(state_path):
    """Restart po błędzie bazy nie porzuca świecy dostępnej po upływie limitu."""
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    before = stamp + timedelta(minutes=1, seconds=5)
    after = stamp + timedelta(minutes=5)
    trigger.save_checkpoint(state_path, stamp - timedelta(minutes=1))
    storage = Mock()
    storage.get_processed_signal.return_value = None
    storage.load_context.side_effect = [
        MySQLError("chwilowa niedostępność"),
        ([{"timestamp_utc": stamp, "open": 100.0, "high": 101.0,
           "low": 99.0, "close": 100.0, "volume": 10.0}], {}),
    ]
    storage.save_signal.return_value = False
    clock = Mock(wraps=datetime)
    clock.now.return_value = before
    with patch("trigger.datetime", clock), patch("trigger.time.sleep", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            trigger.run_loop(storage, state_path)
    assert trigger.load_checkpoint(state_path) == stamp - timedelta(minutes=1)
    clock.now.return_value = after
    with patch("trigger.datetime", clock), patch("trigger._emit", side_effect=KeyboardInterrupt) as emit:
        with pytest.raises(KeyboardInterrupt):
            trigger.run_loop(storage, state_path)
    assert "stale_data" in emit.call_args.args[0]["blocked_by"]
    assert storage.load_context.call_count == 2
    assert trigger.load_checkpoint(state_path) == stamp


def test_unsaved_none_checkpoint_prevents_reanalysis_after_restart(state_path):
    """Niezapisany NONE też utrwala obsłużenie minuty przed następnym startem."""
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    clock = Mock(wraps=datetime)
    clock.now.return_value = stamp + timedelta(minutes=1, seconds=5)
    storage = Mock()
    with patch("trigger.datetime", clock), \
            patch.object(trigger.config, "SAVE_NONE_SIGNALS", False), \
            patch("trigger.analyze_candle", return_value=(
                {"signal": "NONE", "blocked_by": []}, False)) as analyze, \
            patch("trigger._emit"), \
            patch("trigger.time.sleep", side_effect=KeyboardInterrupt):
        for _ in range(2):
            with pytest.raises(KeyboardInterrupt):
                trigger.run_loop(storage, state_path)
    assert analyze.call_count == 1
    assert trigger.load_checkpoint(state_path) == stamp


def test_loop_outside_session_avoids_database_and_checkpoint(state_path):
    """Weekend nie powoduje analizy, zapytań ani zapisu pliku stanu."""
    clock = Mock(wraps=datetime)
    clock.now.return_value = datetime(2026, 11, 15, 15, 0, tzinfo=timezone.utc)
    storage = Mock()
    with patch("trigger.datetime", clock), \
            patch("trigger.analyze_candle") as analyze, \
            patch("trigger.time.sleep", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            trigger.run_loop(storage, state_path)
    analyze.assert_not_called()
    assert not storage.mock_calls
    assert not state_path.exists()


def test_single_run_outside_session_analyzes_old_candle_without_saving():
    """Poza oknem zachowuje dane starej świecy, ale nie zapisuje wyniku NONE."""
    stamp = datetime(2026, 11, 13, 20, 49, tzinfo=timezone.utc)
    now = datetime(2026, 11, 15, 15, 0, tzinfo=timezone.utc)
    storage = Mock()
    storage.latest_closed_open.return_value = stamp
    storage.get_processed_signal.return_value = None
    storage.load_context.return_value = ([{
        "timestamp_utc": stamp, "open": 100.0, "high": 101.0,
        "low": 99.0, "close": 100.0, "volume": 10.0,
    }], {})
    result, saved = trigger.run_latest(storage, now)
    assert result["candle_open_epoch"] == int(stamp.timestamp())
    assert result["signal"] == "NONE"
    assert "outside_session" in result["blocked_by"]
    assert not saved
    storage.get_processed_signal.assert_called_once_with(stamp)
    storage.load_context.assert_called_once_with(stamp)
    storage.save_signal.assert_not_called()


def test_single_run_rechecks_stale_saved_long_without_resaving():
    """Stary zapisany LONG jest nieaktywny, bez przeliczania zmienionej historii."""
    opened = datetime(2026, 11, 13, 15, 0, tzinfo=timezone.utc)
    now = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    storage = Mock()
    storage.latest_closed_open.return_value = opened
    storage.get_processed_signal.return_value = {"signal": "LONG", "blocked_by": []}
    storage.load_context.return_value = ([{
        "timestamp_utc": opened, "open": 100.0, "high": 101.0,
        "low": 99.0, "close": 100.0, "volume": 10.0,
    }], {})
    result, saved = trigger.run_latest(storage, now)
    assert result["signal"] == "NONE"
    assert "stale_data" in result["blocked_by"]
    assert result["candle_open_epoch"] == int(opened.timestamp())
    assert not saved
    storage.get_processed_signal.assert_called_once_with(opened)
    storage.load_context.assert_not_called()
    storage.save_signal.assert_not_called()


def test_api_keeps_saved_stale_result_without_recalculating():
    """API zwraca nieaktywną kopię dawnego wyniku bez przeliczania historii."""
    opened = datetime(2026, 11, 13, 15, 0, tzinfo=timezone.utc)
    now = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    previous = {
        "signal": "LONG", "blocked_by": ["daily_limit"], "price": 100.0,
        "params": {"data_age_minutes": 0.0, "atr": 2.0},
        "setup": "TREND_PULLBACK", "score": 80, "reasons": ["historyczny sygnał"],
        "tp_price": 105.0, "sl_price": 98.0, "tp_points": 5.0, "sl_points": 2.0,
    }
    storage = Mock()
    storage.get_processed_signal.return_value = previous
    with patch("trigger.generate_signal", wraps=trigger.generate_signal) as generate:
        result, saved = trigger.analyze_candle(storage, opened, now)
    assert result["signal"] == "NONE"
    assert result["blocked_by"] == ["daily_limit", "stale_data"]
    assert result["params"]["data_age_minutes"] == (now - opened - timedelta(minutes=1)).total_seconds() / 60
    assert result["params"]["atr"] == 2.0
    assert result["price"] == previous["price"]
    assert result["candle_open_epoch"] == int(opened.timestamp())
    assert result["timestamp_epoch"] == int((opened + timedelta(minutes=1)).timestamp())
    assert result["setup"] is None and result["score"] == 0 and result["reasons"] == []
    assert all(result[field] is None for field in ("tp_price", "sl_price", "tp_points", "sl_points"))
    assert previous["signal"] == "LONG" and previous["params"]["data_age_minutes"] == 0.0
    assert previous["blocked_by"] == ["daily_limit"]
    assert not saved
    storage.load_context.assert_not_called()
    storage.save_signal.assert_not_called()
    generate.assert_called_once_with([], now=now)


def test_single_run_outside_session_removes_gap_block_but_keeps_counts():
    """Po oknie liczby luk pozostają w diagnostyce, bez dodatkowej blokady data_gap."""
    day = datetime(2026, 11, 16, tzinfo=trigger.config.NEW_YORK)
    closed = day.replace(hour=15, minute=50).astimezone(timezone.utc)
    now = day.replace(hour=15, minute=51).astimezone(timezone.utc)
    rows = []
    for offset in range(10, 0, -1):
        if offset in (4, 5):
            continue
        rows.append({
            "timestamp_utc": closed - timedelta(minutes=offset),
            "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.0, "volume": 10.0,
        })
    storage = Mock()
    storage.latest_closed_open.return_value = rows[-1]["timestamp_utc"]
    storage.load_context.return_value = (rows, {})
    result, saved = trigger.run_latest(storage, now)
    assert result["signal"] == "NONE"
    assert "outside_session" in result["blocked_by"]
    assert "data_gap" not in result["blocked_by"]
    assert result["params"]["missing_candles_60m"] == 2
    assert result["params"]["max_consecutive_missing"] == 2
    assert not saved
    storage.save_signal.assert_not_called()


@pytest.mark.parametrize(("early", "now_clock", "outside"), [
    (False, (15, 50, 0), False),
    (False, (15, 50, 5), False),
    (False, (15, 50, 59, 999999), False),
    (False, (15, 51, 0), True),
    (True, (12, 50, 0), False),
    (True, (12, 50, 5), False),
    (True, (12, 50, 59, 999999), False),
    (True, (12, 51, 0), True),
])
def test_single_run_end_of_signal_window(early, now_clock, outside):
    """Ostatnia minuta okna pozostaje dostępna w zwykły i skrócony dzień."""
    day = datetime(2026, 11, 16, tzinfo=trigger.config.NEW_YORK)
    closed = day.replace(hour=12 if early else 15, minute=50)
    opened = (closed - timedelta(minutes=1)).astimezone(timezone.utc)
    now = day.replace(hour=now_clock[0], minute=now_clock[1], second=now_clock[2],
                      microsecond=now_clock[3] if len(now_clock) > 3 else 0).astimezone(timezone.utc)
    storage = Mock()
    storage.latest_closed_open.return_value = opened
    storage.get_processed_signal.return_value = None
    storage.load_context.return_value = ([{
        "timestamp_utc": opened, "open": 100.0, "high": 101.0,
        "low": 99.0, "close": 100.0, "volume": 10.0,
    }], {})
    storage.save_signal.return_value = True
    with patch.object(trigger.config, "EARLY_CLOSE_DATES", [day.date()] if early else []):
        result, saved = trigger.run_latest(storage, now)
    assert ("outside_session" in result["blocked_by"]) is outside
    assert saved is not outside
    assert storage.save_signal.call_count == (0 if outside else 1)


def test_stale_candle_remains_inside_during_final_minute():
    """Starsza świeca jest przeterminowana, lecz ostatnia minuta nadal należy do okna."""
    day = datetime(2026, 11, 16, tzinfo=trigger.config.NEW_YORK)
    opened = day.replace(hour=15, minute=45).astimezone(timezone.utc)
    now = day.replace(hour=15, minute=50, second=5).astimezone(timezone.utc)
    storage = Mock()
    storage.latest_closed_open.return_value = opened
    storage.get_processed_signal.return_value = None
    storage.load_context.return_value = ([{
        "timestamp_utc": opened, "open": 100.0, "high": 101.0,
        "low": 99.0, "close": 100.0, "volume": 10.0,
    }], {})
    storage.save_signal.return_value = True
    result, saved = trigger.run_latest(storage, now)
    assert "stale_data" in result["blocked_by"]
    assert "outside_session" not in result["blocked_by"]
    assert saved


def test_no_data_in_last_minute_is_not_outside_session():
    """Brak świec w ostatniej minucie nie oznacza jeszcze wyjścia poza okno."""
    now = datetime(2026, 11, 16, 15, 50, 59, tzinfo=trigger.config.NEW_YORK)
    storage = Mock()
    storage.latest_closed_open.return_value = None
    result, saved = trigger.run_latest(storage, now)
    assert "no_data" in result["blocked_by"]
    assert "outside_session" not in result["blocked_by"]
    assert not saved
    storage.save_signal.assert_not_called()


@pytest.mark.parametrize(("saved", "beep", "code"), [
    (True, True, 0), (True, False, 4), (False, True, 3), (False, False, 3),
])
def test_api_startup_json_exit_code_and_no_sound(saved, beep, code, capsys):
    """Proces API zwraca JSON i kod zapisu, bez lokalnego dźwięku."""
    with patch.object(sys, "argv", ["trigger.py", "--api-candle-open-epoch", "1700000040"]), \
            patch("trigger.SP500Storage"), \
            patch("trigger.analyze_candle", return_value=({"signal": "NONE"}, saved)), \
            patch.object(trigger.config, "BEEP_ENABLED", beep), \
            patch("trigger.play_signal_confirmation") as sound:
        assert trigger.main() == code
    assert json.loads(capsys.readouterr().out) == {"signal": "NONE"}
    sound.assert_not_called()


def test_loop_ctrl_c_exits_cleanly():
    """Ctrl+C podczas pętli kończy ręczny proces kodem sukcesu."""
    with patch.object(sys, "argv", ["trigger.py", "--loop"]), \
            patch("trigger.SP500Storage"), \
            patch("trigger.run_loop", side_effect=KeyboardInterrupt):
        assert trigger.main() == 0


def test_api_mysql_error_exits_with_error_and_no_json(capsys):
    """Błąd bazy w trybie API nie udaje wyniku ani zapisu."""
    with patch.object(sys, "argv", ["trigger.py", "--api-candle-open-epoch", "1700000040"]), \
            patch("trigger.SP500Storage"), \
            patch("trigger.analyze_candle", side_effect=MySQLError("błąd bazy")):
        assert trigger.main() == 1
    assert capsys.readouterr().out == ""


def test_cli_help_starts_without_database():
    """Uruchomienie skryptu z pomocą nie łączy się z bazą."""
    script = Path(trigger.__file__).resolve()
    completed = subprocess.run([sys.executable, str(script), "--help"],
                               capture_output=True, text=True, check=False)
    assert completed.returncode == 0
    assert "--loop" in completed.stdout
