"""Testy transakcji MySQL, kodowania JSON i jednokrotnego wyzwalania."""

import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from mysql.connector import Error as MySQLError

DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIRECTORY))

import config  # noqa: E402
import storage as storage_module  # noqa: E402
import trigger  # noqa: E402

for _module_name in ("config", "indicators", "schedule", "signal_logic", "storage", "trigger"):
    sys.modules.pop(_module_name, None)

UTC = timezone.utc
OPENED = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)


def _result(signal: str = "NONE") -> dict:
    """Tworzy kompletny przykład wyniku strategii do zapisu."""
    return {
        "instrument": "XAUUSD",
        "price": 4012.356,
        "timestamp": int((OPENED + timedelta(minutes=1)).timestamp()),
        "candle_open_time": "2026-01-05T11:00:00+01:00",
        "signal": signal,
        "setup": None,
        "score": 0,
        "tp_price": None,
        "sl_price": None,
        "max_hold_minutes": 20,
        "reason": "Zablokowano: brak warunków do transakcji.",
        "blocked_by": ["no_setup"],
        "params": {"ema9": 4012.123456, "powod": "Świeca zamknięta."},
    }


def _mock_storage(monkeypatch):
    """Podstawia połączenie MySQL i zwraca obiekty testowe."""
    connection, cursor = Mock(), Mock()
    connection.cursor.return_value = cursor
    monkeypatch.setattr(
        storage_module.mysql.connector, "connect", Mock(return_value=connection)
    )
    return storage_module.XAUUSDStorage({}), connection, cursor


def test_payload_order_rounding_and_polish_utf8():
    """Weryfikuje dokładny kształt JSON, precyzję oraz polski tekst."""
    payload = storage_module.build_signal_parameters(_result())
    assert list(payload) == [
        "price", "signal", "timestamp", "instrument", "reason",
    ]
    assert payload["price"] == 4012.356
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    assert "Zablokowano" in encoded
    assert "Świeca zamknięta." not in encoded
    assert json.loads(encoded) == payload


def test_save_commits_naive_warsaw_close_and_reads_json_back(monkeypatch):
    """Zapisuje sygnał transakcyjnie i odczytuje JSON z polskimi znakami."""
    storage, connection, cursor = _mock_storage(monkeypatch)
    result = _result()
    assert storage.save_signal(result, 120)
    query, values = cursor.execute.call_args.args
    assert "INSERT INTO signals" in query
    assert values[0] == datetime(2026, 1, 5, 11, 1)
    assert values[1] == "XAU/USD"
    assert values[2] == Decimal("4012.35600")
    assert values[4] == config.STRATEGY_NAME
    assert values[5] == 120
    serialized = values[6]
    stored = json.loads(serialized)
    assert stored["reason"] == "Zablokowano: brak warunków do transakcji."
    assert list(stored) == [
        "price", "signal", "timestamp", "instrument", "reason"
    ]
    cursor.fetchone.return_value = (serialized,)
    assert storage.get_processed_signal(result["timestamp"]) == stored
    connection.commit.assert_called_once()
    assert connection.close.call_count == 2


def test_signal_history_keeps_cross_midnight_cooldown_without_counting_yesterday(monkeypatch):
    """Cooldown obejmuje poprzedni dzień, ale limit liczy tylko bieżący dzień."""
    storage, _, cursor = _mock_storage(monkeypatch)
    instant = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    today_signal = int(datetime(2026, 1, 5, 11, 30, tzinfo=UTC).timestamp())
    yesterday_signal = int(datetime(2026, 1, 4, 22, 55, tzinfo=UTC).timestamp())
    cursor.fetchall.return_value = [
        (today_signal, json.dumps({"setup": "RANGE_BREAKOUT", "signal": "SHORT"})),
        (yesterday_signal, json.dumps({"setup": "TREND_PULLBACK", "signal": "LONG"})),
    ]

    history = storage.signal_history(instant)

    assert history == {
        "last_signal_epoch": today_signal,
        "signals_today": 1,
        "range_breakout_directions_today": ["SHORT"],
    }
    query = cursor.execute.call_args.args[0]
    assert "MAX(source_timestamp)" in query


def test_signal_history_reads_range_breakout_from_compact_reason(monkeypatch):
    """Rozpoznaje sygnał wybicia bez pola setup w kompaktowym JSON."""
    storage, _, cursor = _mock_storage(monkeypatch)
    instant = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    breakout_timestamp = int(datetime(2026, 1, 5, 11, 30, tzinfo=UTC).timestamp())
    cursor.fetchall.return_value = [
        (
            breakout_timestamp,
            json.dumps(
                {
                    "signal": "SHORT",
                    "reason": (
                        "Spełnione warunki setupu RANGE_BREAKOUT (SHORT): "
                        "wybicie zakresu Azji."
                    ),
                }
            ),
        ),
    ]

    history = storage.signal_history(instant)

    assert history["range_breakout_directions_today"] == ["SHORT"]


def test_duplicate_is_rolled_back_and_not_reported_as_saved(monkeypatch):
    """Duplikat unikalnego klucza nie generuje drugiego zapisu ani dźwięku."""
    storage, connection, cursor = _mock_storage(monkeypatch)
    cursor.execute.side_effect = MySQLError(msg="duplikat", errno=1062)
    assert storage.save_signal(_result("LONG"), 100) is False
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


def test_database_failure_is_rolled_back_and_propagated(monkeypatch):
    """Błąd bazy jest widoczny dla wywołującego po rollback."""
    storage, connection, cursor = _mock_storage(monkeypatch)
    cursor.execute.side_effect = MySQLError(msg="awaria", errno=1205)
    with pytest.raises(MySQLError):
        storage.save_signal(_result("SHORT"), 100)
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()


def test_none_flag_is_the_single_save_gate(monkeypatch):
    """Wyłączony NONE kończy się przed otwarciem połączenia z bazą."""
    storage, _, _ = _mock_storage(monkeypatch)
    monkeypatch.setattr(config, "SAVE_NONE_SIGNALS", False)
    storage._connect = Mock(side_effect=AssertionError("nie powinno łączyć"))
    assert storage.save_signal(_result("NONE"), 10) is False


def test_analyze_rejects_forming_or_missing_candle(monkeypatch):
    """Formowana świeca oraz brak świecy w bazie nie są analizowane."""
    storage = Mock()
    with pytest.raises(ValueError):
        trigger.analyze_candle(storage, OPENED, OPENED + timedelta(seconds=59))
    storage.get_processed_signal.return_value = None
    storage.load_context.return_value = []
    with pytest.raises(ValueError):
        trigger.analyze_candle(storage, OPENED, OPENED + timedelta(minutes=1))
    storage.signal_history.assert_not_called()


def test_analyze_uses_one_signal_per_closed_candle(monkeypatch):
    """Wynik dla wskazanej świecy zapisuje się jeden raz i korzysta z historii."""
    storage = Mock()
    row = {"timestamp_utc": OPENED, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 1}
    storage.get_processed_signal.return_value = None
    storage.load_context.return_value = [row]
    storage.signal_history.return_value = {}
    storage.save_signal.return_value = True
    generated = _result("LONG")
    generated["price"] = 2.0
    generated["candle_open_time"] = OPENED.astimezone(storage_module.WARSAW).isoformat()
    generated["timestamp"] = int((OPENED + timedelta(minutes=1)).timestamp())
    monkeypatch.setattr(trigger, "generate_signal", Mock(return_value=generated))
    result, saved = trigger.analyze_candle(
        storage, OPENED, OPENED + timedelta(minutes=1)
    )
    assert saved and result["signal"] == "LONG"
    storage.get_processed_signal.assert_called_once_with(
        int((OPENED + timedelta(minutes=1)).timestamp())
    )
    storage.signal_history.assert_called_once()
    storage.save_signal.assert_called_once()


def test_loop_state_is_persisted_atomically(tmp_path):
    """Plik stanu pętli pozwala wznowić pracę po restarcie."""
    path = tmp_path / "loop" / "state.json"
    storage_module.write_loop_state(path, 123456)
    assert storage_module.read_loop_state(path) == 123456
    assert not path.with_suffix(".json.tmp").exists()


def test_loop_processes_new_closed_candle_once_across_restart(tmp_path, monkeypatch):
    """Pętla zapisuje świecę zamkniętą raz i po restarcie nie powtarza analizy."""
    opened = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    opened_epoch = int(opened.timestamp())
    state_path = tmp_path / "loop-state.json"
    storage = Mock()
    storage.latest_processed_close_epoch.return_value = None
    storage.latest_candle_open_epoch.return_value = opened_epoch
    storage.has_candle.return_value = True
    stop_event = Event()
    generated = _result("LONG")

    def analyze_once(*_args):
        stop_event.set()
        return generated, True

    analyze = Mock(side_effect=analyze_once)
    monkeypatch.setattr(trigger, "analyze_candle", analyze)
    monkeypatch.setattr(trigger, "play_signal_beep", Mock())
    trigger.run_loop(
        storage=storage,
        stop_event=stop_event,
        state_path=state_path,
        now_provider=lambda: opened + timedelta(minutes=1),
    )
    assert analyze.call_count == 1
    assert storage_module.read_loop_state(state_path) == opened_epoch

    storage.has_candle.return_value = False
    restarted_stop = Event()
    trigger.run_loop(
        storage=storage,
        stop_event=restarted_stop,
        state_path=state_path,
        now_provider=lambda: opened + timedelta(minutes=2),
        wait=lambda _seconds: restarted_stop.set(),
    )
    assert analyze.call_count == 1
    assert storage_module.read_loop_state(state_path) == opened_epoch


def test_loop_retries_database_error_while_loading_initial_cursor(tmp_path, monkeypatch):
    """Ponawia inicjalizację kursora po chwilowym błędzie bazy."""
    storage = Mock()
    storage.latest_candle_open_epoch.return_value = None
    stop_event = Event()
    attempts = []
    waits = []

    def initialize_cursor(*_args):
        attempts.append(None)
        if len(attempts) == 1:
            raise MySQLError(msg="chwilowa awaria", errno=1205)
        return None

    def wait_once(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            stop_event.set()

    monkeypatch.setattr(trigger, "_initial_cursor", initialize_cursor)
    trigger.run_loop(
        storage=storage,
        stop_event=stop_event,
        state_path=tmp_path / "loop-state.json",
        now_provider=lambda: datetime(2026, 1, 5, 12, 0, tzinfo=UTC),
        wait=wait_once,
    )

    assert len(attempts) == 2
    assert len(waits) == 2
