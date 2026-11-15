"""Testy atrap bazy, wyzwalania i dźwięku bez zapisu produkcyjnego."""

import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from mysql.connector import Error


@pytest.fixture
def modules(monkeypatch):
    """Izoluje moduły strategii; zwraca ich przestrzeń i przywraca importy po teście."""
    names = ("config", "indicators", "signal_logic", "sound", "storage", "trigger")
    previous = {name: sys.modules.get(name) for name in names}
    for name in names:
        sys.modules.pop(name, None)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    try:
        yield SimpleNamespace(**{name: importlib.import_module(name) for name in names})
    finally:
        for name in names:
            sys.modules.pop(name, None)
            if previous[name] is not None:
                sys.modules[name] = previous[name]


@pytest.fixture
def sample():
    """Zwraca poprawny wynik dla zamkniętej minuty UTC; nie zgłasza wyjątków."""
    opened = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    return {
        "instrument": "S&P500", "price": 6000.5,
        "candle_open_time": opened.astimezone(timezone(timedelta(hours=1))).isoformat(),
        "candle_open_epoch": int(opened.timestamp()),
        "timestamp": (opened + timedelta(minutes=1)).astimezone(timezone(timedelta(hours=1))).isoformat(),
        "signal": "LONG", "max_hold_minutes": 20,
        "signal_parameters": {"reason": "test", "reasons": [], "ema_9": 5999.5, "score": 80},
    }


def connection_for(storage, monkeypatch):
    """Podstawia atrapę połączenia storage i zwraca (połączenie, kursor); bez wyjątków."""
    connection, cursor = Mock(), Mock()
    connection.cursor.return_value = cursor
    monkeypatch.setattr(storage, "_connect", lambda: connection)
    return connection, cursor


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_save_atomic_potential_and_full_json(modules, monkeypatch, sample, direction):
    """LONG/SHORT zapisuje płaski JSON i koszt 0,6 w jednej transakcji."""
    storage = modules.storage.SP500Storage({})
    connection, cursor = connection_for(storage, monkeypatch)
    cursor.lastrowid = 42
    sample["signal"] = direction
    assert storage.save_signal(sample, 200) is True
    first, second = cursor.execute.call_args_list
    values = first.args[1]
    assert values[0] == datetime(2026, 1, 15, 13, 1)
    assert values[1] == "ES"
    assert values[3] == direction.lower()
    assert values[4:6] == ("Grok-45/S&P500", 200)
    payload = json.loads(values[6])
    assert payload == {**sample["signal_parameters"], **{key: value for key, value in sample.items() if key != "signal_parameters"}}
    assert payload["ema_9"] == 5999.5 and payload["score"] == 80
    assert payload["timestamp"] == sample["timestamp"]
    assert "signal_parameters" not in payload
    assert values[7] == sample["candle_open_epoch"]
    assert second.args[1] == (42, Decimal("0.6"))
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()
    connection.close.assert_called_once()


def test_none_flag_and_duplicate(modules, monkeypatch, sample):
    """NONE podlega tylko fladze, zaś duplikat wycofuje transakcję."""
    storage = modules.storage.SP500Storage({})
    connection, cursor = connection_for(storage, monkeypatch)
    sample["signal"] = "NONE"
    monkeypatch.setattr(modules.config, "SAVE_NONE_SIGNALS", False)
    assert not storage.save_signal(sample, 1)
    connection.cursor.assert_not_called()
    monkeypatch.setattr(modules.config, "SAVE_NONE_SIGNALS", True)
    assert storage.save_signal(sample, 1)
    assert cursor.execute.call_count == 1
    connection.commit.assert_called_once()
    connection.reset_mock()
    cursor.execute.side_effect = Error(msg="duplikat", errno=1062)
    assert not storage.save_signal(sample, 1)
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


@pytest.mark.parametrize("stage,errno", [(0, 1205), (1, 1062)])
def test_save_failure_rolls_back(modules, monkeypatch, sample, stage, errno):
    """Błędy inne niż duplikat sygnału, także potencjału, nie są ukrywane."""
    storage = modules.storage.SP500Storage({})
    connection, cursor = connection_for(storage, monkeypatch)
    error = Error(msg="błąd", errno=errno)
    cursor.execute.side_effect = [error] if stage == 0 else [None, error]
    with pytest.raises(Error):
        storage.save_signal(sample, 1)
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


def test_context_sql_cutoff_and_dst(modules, monkeypatch):
    """SQL odcina przyszłe świece i sortuje po UTC również przy zmianie DST."""
    storage = modules.storage.SP500Storage({})
    connection, cursor = connection_for(storage, monkeypatch)
    cursor.fetchall.return_value = [
        {"datetime": datetime(2026, 10, 25, 2, 0), "utc_offset_minutes": 60,
         "open": 2, "high": 3, "low": 1, "close": 2, "volume": None},
        {"datetime": datetime(2026, 10, 25, 2, 59), "utc_offset_minutes": 120,
         "open": 2, "high": 3, "low": 1, "close": 2, "volume": 5},
    ]
    cutoff = datetime(2026, 10, 25, 1, tzinfo=timezone.utc)
    candles = storage.load_context(cutoff)
    sql, params = cursor.execute.call_args.args
    assert "DATE_SUB" in sql and "DESC" in sql
    assert params == ("ES", cutoff.replace(tzinfo=None), 200)
    assert list(candles.timestamp_utc) == [cutoff - timedelta(minutes=1), cutoff]
    connection.close.assert_called_once()


def test_read_processed_signal_and_bad_json(modules, monkeypatch, sample):
    """Odczyt używa klucza świecy, a uszkodzony JSON nie jest ukrywany."""
    storage = modules.storage.SP500Storage({})
    connection, cursor = connection_for(storage, monkeypatch)
    opened = datetime.fromtimestamp(sample["candle_open_epoch"], timezone.utc)
    flattened = {**sample["signal_parameters"], **{key: value for key, value in sample.items() if key != "signal_parameters"}}
    cursor.fetchone.return_value = (json.dumps(flattened),)
    assert storage.get_processed_signal(opened) == sample
    assert cursor.execute.call_args.args[1] == ("ES", "Grok-45/S&P500", sample["candle_open_epoch"])
    cursor.fetchone.return_value = ("[1]",)
    with pytest.raises(ValueError):
        storage.get_processed_signal(opened)
    assert connection.close.call_count == 2


def test_saved_flat_signal_can_be_neutralized_on_duplicate(modules, monkeypatch, sample):
    """Odczyt płaskiego JSON przywraca strukturę potrzebną dla nieaktualnego duplikatu."""
    storage = modules.storage.SP500Storage({})
    _, cursor = connection_for(storage, monkeypatch)
    cursor.lastrowid = 42
    assert storage.save_signal(sample, 1)
    saved_json = cursor.execute.call_args_list[0].args[1][6]
    cursor.fetchone.return_value = (saved_json,)
    opened = datetime.fromtimestamp(sample["candle_open_epoch"], timezone.utc)
    restored, saved = modules.trigger.analyze_candle(storage, opened, opened + timedelta(minutes=1))
    assert not saved and restored == sample
    assert restored["max_hold_minutes"] == 20
    assert restored["signal_parameters"]["ema_9"] == 5999.5
    result, saved = modules.trigger.analyze_candle(storage, opened, opened + timedelta(minutes=10))
    assert not saved and result["signal"] == "NONE"
    assert result["max_hold_minutes"] == 20
    assert result["signal_parameters"]["ema_9"] == 5999.5
    assert "stale_data" in result["signal_parameters"]["reasons"]


def test_connection_error_is_not_silenced(modules, monkeypatch, sample):
    """Niedostępność bazy nie jest mylona z brakiem wcześniej zapisanego wyniku."""
    storage = modules.storage.SP500Storage({})
    monkeypatch.setattr(storage, "_connect", Mock(side_effect=Error(msg="baza niedostępna")))
    opened = datetime.fromtimestamp(sample["candle_open_epoch"], timezone.utc)
    with pytest.raises(Error):
        storage.get_processed_signal(opened)


def test_trigger_missing_forming_duplicate_and_stale(modules, sample):
    """Brak, formowanie i duplikat nie powodują zapisu ani ponownego liczenia."""
    trigger = modules.trigger
    opened = datetime.fromtimestamp(sample["candle_open_epoch"], timezone.utc)
    storage = Mock()
    result, saved = trigger.analyze_candle(storage, opened, opened + timedelta(seconds=59))
    assert not saved and result["signal_parameters"]["reasons"] == ["forming_candle"]
    storage.get_processed_signal.assert_not_called()
    storage.get_processed_signal.return_value = None
    storage.load_context.return_value = pd.DataFrame()
    result, saved = trigger.analyze_candle(storage, opened, opened + timedelta(minutes=1))
    assert not saved and result["signal_parameters"]["reasons"] == ["missing_candle"]
    storage.get_processed_signal.return_value = sample
    storage.load_context.reset_mock()
    result, saved = trigger.analyze_candle(storage, opened, opened + timedelta(minutes=10))
    assert not saved and result["signal"] == "NONE"
    assert "stale_data" in result["signal_parameters"]["reasons"]
    assert sample["signal"] == "LONG"
    storage.load_context.assert_not_called()
    storage.save_signal.assert_not_called()


def test_trigger_valid_and_stale_are_saved(modules, monkeypatch, sample):
    """Rzeczywista świeca jest zapisywana, a opóźniony LONG neutralizowany."""
    trigger = modules.trigger
    opened = datetime.fromtimestamp(sample["candle_open_epoch"], timezone.utc)
    storage = Mock()
    storage.get_processed_signal.return_value = None
    storage.load_context.return_value = pd.DataFrame([{"timestamp_utc": opened}])
    storage.save_signal.return_value = True
    monkeypatch.setattr(trigger, "generate_signal", lambda candles, now: sample.copy())
    result, saved = trigger.analyze_candle(storage, opened, opened + timedelta(minutes=2))
    assert saved and result["signal"] == "LONG"
    result, saved = trigger.analyze_candle(storage, opened, opened + timedelta(minutes=8))
    assert saved and result["signal"] == "NONE"
    assert storage.save_signal.call_args.args[0]["signal"] == "NONE"
    with pytest.raises(ValueError):
        trigger.analyze_candle(storage, opened + timedelta(seconds=1), opened + timedelta(minutes=2))


def test_trigger_rejects_wrong_last_candle(modules, sample):
    """Historia bez wskazanej świecy nie może zastąpić jej starszą świecą."""
    opened = datetime.fromtimestamp(sample["candle_open_epoch"], timezone.utc)
    storage = Mock()
    storage.get_processed_signal.return_value = None
    storage.load_context.return_value = pd.DataFrame(
        [{"timestamp_utc": opened - timedelta(minutes=1)}]
    )
    result, saved = modules.trigger.analyze_candle(storage, opened, opened + timedelta(minutes=1))
    assert not saved and result["signal_parameters"]["reasons"] == ["missing_candle"]
    storage.save_signal.assert_not_called()


def test_real_signal_outside_session_is_saved(modules):
    """Rzeczywista logika generuje i zapisuje NONE także poza sesją giełdową."""
    opened = datetime(2026, 1, 18, 12, 0, tzinfo=timezone.utc)
    storage = Mock()
    storage.get_processed_signal.return_value = None
    storage.load_context.return_value = pd.DataFrame([{
        "timestamp_utc": opened, "open": 6000, "high": 6001,
        "low": 5999, "close": 6000, "volume": 100,
    }])
    storage.save_signal.return_value = True
    result, saved = modules.trigger.analyze_candle(storage, opened, opened + timedelta(minutes=1))
    assert saved and result["signal"] == "NONE"
    assert result["candle_open_epoch"] == int(opened.timestamp())
    storage.save_signal.assert_called_once_with(result, 1)


@pytest.mark.parametrize("api,enabled,saved,expected", [
    (True, True, True, 0), (False, True, True, 0),
    (True, False, True, 4), (False, False, True, 4),
    (True, True, False, 3), (False, True, False, 3),
])
def test_main_sound_and_exit_codes(modules, monkeypatch, capsys, sample, api, enabled, saved, expected):
    """Kod procesu i lokalny dźwięk odpowiadają wynikowi zatwierdzenia."""
    trigger = modules.trigger
    monkeypatch.setattr(modules.config, "BEEP_ENABLED", enabled)
    monkeypatch.setattr(trigger, "analyze_candle", Mock(return_value=(sample, saved)))
    beep = Mock()
    monkeypatch.setattr(trigger, "play_signal_confirmation", beep)
    option = "--api-candle-open-epoch" if api else "--candle-open-epoch"
    assert trigger.main([option, str(sample["candle_open_epoch"])]) == expected
    assert json.loads(capsys.readouterr().out) == sample
    assert beep.call_count == int(saved and enabled and not api)


def test_main_failure_and_sound_failure(modules, monkeypatch, capsys):
    """Błąd argumentu i bazy daje kod 1; błąd dźwięku nie przerywa procesu."""
    trigger = modules.trigger
    assert trigger.main([]) == 1
    monkeypatch.setattr(trigger, "analyze_candle", Mock(side_effect=RuntimeError("baza")))
    assert trigger.main(["--api-candle-open-epoch", "0"]) == 1
    assert capsys.readouterr().out == ""
    sound = modules.sound
    monkeypatch.setattr(sound.sys, "platform", "linux")
    monkeypatch.setattr(sound.time, "sleep", Mock(side_effect=OSError("dźwięk")))
    sound.play_signal_confirmation()


def test_windows_sound_beeps_twice(modules, monkeypatch):
    """Windows emituje dwa dźwięki o parametrach z konfiguracji."""
    sound = modules.sound
    winsound = SimpleNamespace(Beep=Mock())
    monkeypatch.setitem(sys.modules, "winsound", winsound)
    monkeypatch.setattr(sound.sys, "platform", "win32")
    sleep = Mock()
    monkeypatch.setattr(sound.time, "sleep", sleep)
    sound.play_signal_confirmation()
    assert winsound.Beep.call_count == 2
    winsound.Beep.assert_called_with(1000, 120)
    sleep.assert_called_once_with(0.1)
