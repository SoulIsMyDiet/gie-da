"""Testy izolowane od rzeczywistej bazy i innych strategii."""

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from mysql.connector import Error as MySQLError

DIRECTORY = Path(__file__).resolve().parents[1]
OPENED = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def modules(monkeypatch):
    """Ładuje moduły z atrapami konfiguracji i generatora bez kolizji importów."""
    config = SimpleNamespace(
        DB_SYMBOL="XAU/USD", STRATEGY_NAME="Chat-gpt6/XAUUSDStrategyV1",
        HISTORY_LIMIT=2500, SAVE_NONE_SIGNALS=False, BEEP_ENABLED=True,
        SIGNAL_POTENTIAL_SPREAD=0.3,
        CONFIG=SimpleNamespace(
            ema_fast=9, ema_middle=21, atr_period=14, rsi_period=14,
        ),
    )
    generator = Mock()
    package = ModuleType("xau_test")
    package.__path__ = [str(DIRECTORY)]
    monkeypatch.setitem(sys.modules, "xau_test", package)
    monkeypatch.setitem(sys.modules, "xau_test.config", config)
    monkeypatch.setitem(sys.modules, "xau_test.signal_logic", SimpleNamespace(generate_signal=generator))
    spec = importlib.util.spec_from_file_location("xau_test.storage", DIRECTORY / "storage.py")
    storage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(storage)
    monkeypatch.setitem(sys.modules, "xau_test.storage", storage)
    spec = importlib.util.spec_from_file_location("xau_test.trigger", DIRECTORY / "trigger.py")
    trigger = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trigger)
    return storage, trigger, config, generator


def result(signal="LONG"):
    """Przygotowuje kompletny wynik dla przykładowego otwarcia UTC."""
    return {
        "instrument": "XAUUSD", "price": 2500.5,
        "timestamp": "2026-01-05T11:01:00+01:00",
        "candle_open_time": "2026-01-05T11:00:00+01:00",
        "candle_open_epoch": int(OPENED.timestamp()), "signal": signal,
        "signal_parameters": {
            "reasons": ["test", "szczegółowy opis pomocniczy"],
            "indicators": {
                "m1": {
                    "ema_9": 2500.1,
                    "ema_21": 2499.8,
                    "atr": 1.4,
                    "rsi": 52.3,
                    "bb_lower": 2498.0,
                    "bb_upper": 2502.0,
                    "bb_middle": 2500.0,
                }
            },
        },
    }


def connection_for(module):
    """Tworzy atrapę połączenia i magazyn używający wyłącznie tej atrapy."""
    connection, cursor = Mock(), Mock()
    connection.cursor.return_value = cursor
    storage = module.XAUUSDStorage({})
    storage._connect = Mock(return_value=connection)
    return storage, connection, cursor


def test_storage_writes_compact_json_and_configured_potential_spread(modules):
    """Zapisuje krótki JSON oraz spread przyjęty dla XAU/USD."""
    module, _, _, _ = modules
    storage, connection, cursor = connection_for(module)
    assert storage.save_signal(result(), 31)
    sql, values = cursor.execute.call_args_list[0].args
    assert "INSERT INTO signals" in sql
    potential_sql, potential_values = cursor.execute.call_args_list[1].args
    assert "INSERT INTO signal_potention" in potential_sql
    assert potential_values[1] == Decimal("0.3")
    assert values[:6] == (
        datetime(2026, 1, 5, 11, 1), "XAU/USD", Decimal("2500.5"),
        "long", "Chat-gpt6/XAUUSDStrategyV1", 31,
    )
    payload = json.loads(values[6])
    assert payload == {
        "price": 2500.5,
        "signal": "LONG",
        "timestamp": int(OPENED.timestamp()),
        "instrument": "XAU/USD",
        "parameters": {
            "ema9": 2500.1,
            "atr14": 1.4,
            "ema21": 2499.8,
            "rsi14": 52.3,
            "reason": "test",
            "bb_lower": 2498.0,
            "bb_upper": 2502.0,
            "bb_middle": 2500.0,
        },
    }
    assert values[7] == int(OPENED.timestamp())
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()
    connection.close.assert_called_once()


def test_get_processed_signal_accepts_compact_json(modules):
    """Odczyt kompaktowego JSON odtwarza epokę wymaganą przez trigger."""
    module, _, _, _ = modules
    storage, _, cursor = connection_for(module)
    compact_payload = {
        "price": 2500.5,
        "signal": "NONE",
        "timestamp": int(OPENED.timestamp()),
        "instrument": "XAU/USD",
        "parameters": {"reason": "Brak setupu."},
    }
    cursor.fetchone.return_value = (json.dumps(compact_payload),)

    stored = storage.get_processed_signal(OPENED)

    assert stored["candle_open_epoch"] == int(OPENED.timestamp())
    assert stored["parameters"] == compact_payload["parameters"]


@pytest.mark.parametrize("errno,duplicate", [(1062, True), (1205, False)])
def test_storage_rolls_back_duplicate_and_errors(modules, errno, duplicate):
    """Duplikat wycofuje transakcję bez dźwięku; inne błędy są propagowane."""
    storage, connection, cursor = connection_for(modules[0])
    cursor.execute.side_effect = MySQLError(msg="błąd", errno=errno)
    if duplicate:
        assert storage.save_signal(result(), 1) is False
    else:
        with pytest.raises(MySQLError):
            storage.save_signal(result(), 1)
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
    connection.close.assert_called_once()


def test_storage_rolls_back_failed_commit(modules):
    """Nieudany commit nie jest przedstawiany jako zapisany wynik."""
    storage, connection, _ = connection_for(modules[0])
    connection.commit.side_effect = MySQLError(msg="commit", errno=1205)
    with pytest.raises(MySQLError):
        storage.save_signal(result(), 1)
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()


def test_context_bounds_utc_and_converts_offsets(modules):
    """Odczyt ogranicza historię do UTC i rozróżnia czas letni od zimowego."""
    storage, connection, cursor = connection_for(modules[0])
    cursor.fetchall.return_value = [
        {"datetime": datetime(2026, 1, 5, 11), "utc_offset_minutes": 60,
         "open": Decimal("1"), "high": Decimal("2"), "low": Decimal("1"),
         "close": Decimal("2"), "volume": None}
    ]
    candles = storage.load_context(OPENED)
    sql, params = cursor.execute.call_args.args
    assert "DATE_SUB" in sql and "<= %s" in sql
    assert params == ("XAU/USD", OPENED.replace(tzinfo=None), 2500)
    assert candles[0]["timestamp_utc"] == OPENED
    assert candles[0]["volume"] is None
    connection.close.assert_called_once()


def test_stored_none_prevents_recalculation(modules):
    """Wcześniejszy NONE jest odczytywany przed pobraniem i obliczeniem."""
    _, trigger, _, generator = modules
    storage = Mock()
    storage.get_processed_signal.return_value = result("NONE")
    value, saved = trigger.analyze_candle(storage, OPENED, OPENED + timedelta(minutes=1))
    assert value["signal"] == "NONE" and not saved
    storage.load_context.assert_not_called()
    generator.assert_not_called()
    storage.save_signal.assert_not_called()


def test_analysis_no_lookahead_and_epoch_validation(modules):
    """Analiza używa granicy otwarcia i odrzuca obce lub przyszłe świece."""
    _, trigger, _, generator = modules
    storage = Mock()
    storage.get_processed_signal.return_value = None
    storage.load_context.return_value = [{"timestamp_utc": OPENED}]
    storage.save_signal.return_value = True
    generator.return_value = result()
    assert trigger.analyze_candle(storage, OPENED, OPENED + timedelta(minutes=1))[1]
    storage.load_context.assert_called_once_with(OPENED)
    generator.assert_called_once_with(storage.load_context.return_value, now=OPENED + timedelta(minutes=1))
    generator.return_value = {**result(), "candle_open_epoch": int(OPENED.timestamp()) + 60}
    with pytest.raises(ValueError):
        trigger.analyze_candle(storage, OPENED, OPENED + timedelta(minutes=1))
    storage.load_context.return_value = [
        {"timestamp_utc": OPENED + timedelta(minutes=1)}, {"timestamp_utc": OPENED}
    ]
    with pytest.raises(ValueError):
        trigger.analyze_candle(storage, OPENED, OPENED + timedelta(minutes=1))


def test_forming_and_missing_candle_do_not_save(modules):
    """Formowana lub brakująca świeca nigdy nie jest zapisywana jako sygnał."""
    _, trigger, _, generator = modules
    storage = Mock()
    with pytest.raises(ValueError):
        trigger.analyze_candle(storage, OPENED, OPENED + timedelta(seconds=59))
    storage.get_processed_signal.return_value = None
    storage.load_context.return_value = []
    with pytest.raises(ValueError):
        trigger.analyze_candle(storage, OPENED, OPENED + timedelta(minutes=1))
    generator.assert_not_called()
    storage.save_signal.assert_not_called()


def test_none_disabled_and_invalid_time_do_not_connect(modules):
    """Wyłączony NONE i błędna epoka nie trafiają do bazy."""
    module, _, config, _ = modules
    storage, _, _ = connection_for(module)
    config.SAVE_NONE_SIGNALS = False
    assert not storage.save_signal(result("NONE"), 1)
    with pytest.raises(ValueError):
        storage.save_signal({**result(), "candle_open_epoch": 1}, 1)
    storage._connect.assert_not_called()


@pytest.mark.parametrize("saved,enabled,code", [(True, True, 0), (True, False, 4), (False, True, 3)])
def test_cli_exit_codes(modules, monkeypatch, saved, enabled, code, capsys):
    """Kod procesu rozróżnia zapis z dźwiękiem, bez dźwięku i duplikat."""
    _, trigger, config, _ = modules
    config.BEEP_ENABLED = enabled
    monkeypatch.setattr(trigger, "analyze_candle", Mock(return_value=(result(), saved)))
    assert trigger.main(["--api-candle-open-epoch", str(int(OPENED.timestamp()))]) == code
    assert json.loads(capsys.readouterr().out)["instrument"] == "XAUUSD"


def test_real_generator_trigger_and_storage_together(monkeypatch):
    """Weryfikuje pomijanie NONE i zachowanie krótkiego wyniku strategii."""
    package = ModuleType("xau_integration_test")
    package.__path__ = [str(DIRECTORY)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    import importlib
    trigger = importlib.import_module("xau_integration_test.trigger")
    storage_module = importlib.import_module("xau_integration_test.storage")
    storage, connection, cursor = connection_for(storage_module)
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = [
        {"datetime": datetime(2026, 1, 5, 11), "utc_offset_minutes": 60,
         "open": Decimal("2500"), "high": Decimal("2502"), "low": Decimal("2499"),
         "close": Decimal("2501"), "volume": Decimal("10")}
    ]
    output, saved = trigger.analyze_candle(storage, OPENED, OPENED + timedelta(minutes=1))
    assert not saved and output["signal"] == "NONE"
    assert output["candle_open_epoch"] == int(OPENED.timestamp())
    assert not any(
        "INSERT INTO signals" in call.args[0]
        for call in cursor.execute.call_args_list
    )
    connection.commit.assert_not_called()


def test_real_cli_rejects_forming_candle_without_database():
    """Proces startuje niezależnie i zgłasza formowaną świecę bez dostępu do bazy."""
    opened = datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
    completed = subprocess.run(
        [sys.executable, str(DIRECTORY / "XAUUSDStrategyV1.py"),
         "--api-candle-open-epoch", str(int(opened.timestamp()))],
        capture_output=True, text=True, timeout=30, check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert completed.returncode == 1
    assert "nie jest jeszcze zamknięta" in completed.stderr
