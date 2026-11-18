"""Testy transakcji MySQL, kodowania JSON i jednokrotnego wyzwalania USD/JPY."""

import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest
from mysql.connector import Error as MySQLError

DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIRECTORY))

import config  # noqa: E402
import calibrate  # noqa: E402
import storage as storage_module  # noqa: E402
import trigger  # noqa: E402

UTC = timezone.utc
OPENED = datetime(2026, 1, 5, 21, 0, tzinfo=UTC)


def _result(signal: str = "NONE") -> dict:
    """Tworzy kompletny przykład wyniku przeznaczony do zapisu."""
    return {
        "instrument": "USDJPY",
        "price": 155.4327,
        "timestamp": int((OPENED + timedelta(minutes=1)).timestamp()),
        "candle_open_time": OPENED.astimezone(
            storage_module.WARSAW
        ).isoformat(),
        "signal": signal,
        "setup": None,
        "score": 0,
        "tp_price": None,
        "sl_price": None,
        "max_hold_minutes": 20,
        "reason": "Zablokowano: trwa przerwa.",
        "blocked_by": ["outside_session"],
        "reasons": ["Brak transakcji poza sesją."],
        "params": {"ema9": 155.123456, "powod": "Świeca zamknięta."},
    }


def _mock_storage(monkeypatch, connections=1):
    """Podstawia połączenia MySQL i zwraca ich obiekty testowe."""
    connection_rows = []
    for _ in range(connections):
        connection, cursor = Mock(), Mock()
        connection.cursor.return_value = cursor
        connection_rows.append((connection, cursor))
    monkeypatch.setattr(
        storage_module.mysql.connector,
        "connect",
        Mock(side_effect=[row[0] for row in connection_rows]),
    )
    return (
        storage_module.USDJPYStorage({}),
        connection_rows,
    )


def test_payload_key_order_rounding_polish_and_valid_json():
    """Sprawdza dokładny kształt JSON, zaokrąglenie i polski tekst."""
    payload = storage_module.build_signal_parameters(_result())
    assert list(payload) == [
        "price",
        "signal",
        "timestamp",
        "instrument",
        "reason",
    ]
    assert payload["price"] == 155.433
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    assert "Zablokowano" in encoded
    assert "Świeca zamknięta." not in encoded
    assert json.loads(encoded) == payload


def test_save_commits_warsaw_open_and_reads_json_with_polish_text(monkeypatch):
    """Zapisuje sygnał transakcyjnie i odczytuje jego tekst z bazy."""
    storage, connections = _mock_storage(monkeypatch, connections=2)
    assert storage.save_signal(_result(), 120)
    connection, cursor = connections[0]
    query, values = cursor.execute.call_args.args
    assert "INSERT INTO signals" in query
    assert values[0] == datetime(2026, 1, 5, 22, 0)
    assert values[1] == "USD/JPY"
    assert values[2] == Decimal("155.433")
    assert values[4] == config.STRATEGY_NAME
    assert values[5] == 120
    serialized = values[6]
    stored = json.loads(serialized)
    assert list(stored) == [
        "price", "signal", "timestamp", "instrument", "reason"
    ]
    assert stored["reason"] == "Zablokowano: trwa przerwa."
    connection.commit.assert_called_once()

    read_connection, read_cursor = connections[1]
    read_cursor.fetchone.return_value = (serialized,)
    assert storage.get_processed_signal(_result()["timestamp"]) == stored
    read_connection.close.assert_called_once()


def test_signal_history_reads_range_breakout_from_compact_reason(monkeypatch):
    """Rozpoznaje kierunek wybicia bez dodatkowego klucza setup."""
    storage, connections = _mock_storage(monkeypatch)
    instant = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    breakout_timestamp = int(datetime(2026, 1, 5, 11, 30, tzinfo=UTC).timestamp())
    _, cursor = connections[0]
    cursor.fetchall.return_value = [
        (
            breakout_timestamp,
            json.dumps(
                {
                    "signal": "SHORT",
                    "reason": (
                        "Spełnione warunki setupu RANGE_BREAKOUT (SHORT): "
                        "wybicie zakresu tokijskiego."
                    ),
                }
            ),
        ),
    ]

    history = storage.signal_history(instant)

    assert history == {
        "last_signal_epoch": breakout_timestamp,
        "signals_today": 1,
        "range_breakout_directions_today": ["SHORT"],
    }


def test_database_connection_uses_utf8mb4(monkeypatch):
    """Przekazuje charset UTF-8 do sterownika MySQL."""
    connector = Mock()
    monkeypatch.setattr(storage_module.mysql.connector, "connect", connector)
    storage_module.USDJPYStorage(
        {
            "host": "127.0.0.1",
            "port": 3306,
            "database": "gie_da",
            "user": "gie_da_app",
            "password": "test-only",
            "charset": "utf8mb4",
        }
    )._connect()
    assert connector.call_args.kwargs["charset"] == "utf8mb4"


def test_environment_reader_handles_container_path_without_env_file(
    tmp_path, monkeypatch
):
    """Brak pliku .env w kontenerze nie blokuje ustawień ze zmiennych środowiska."""
    algorithm_directory = tmp_path / "app" / "claude_usdjpy_algorithm"
    algorithm_directory.mkdir(parents=True)
    monkeypatch.setattr(
        storage_module,
        "__file__",
        str(algorithm_directory / "storage.py"),
    )

    assert storage_module._read_project_environment() == {}


def test_environment_reader_uses_nearest_project_env(tmp_path, monkeypatch):
    """Odczytuje ustawienia MYSQL wyłącznie z pliku projektu."""
    project_directory = tmp_path / "project"
    algorithm_directory = (
        project_directory / "src" / "algorithms" / "Claude-sonnet55" / "USDJPY"
    )
    algorithm_directory.mkdir(parents=True)
    (project_directory / ".env").write_text(
        "MYSQL_HOST=db.local\nOTHER_SETTING=ignored\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        storage_module,
        "__file__",
        str(algorithm_directory / "storage.py"),
    )

    assert storage_module._read_project_environment() == {
        "MYSQL_HOST": "db.local"
    }


def test_load_context_uses_datetime_index_and_filters_future_local_rows(monkeypatch):
    """Sortuje świece po UTC bez funkcji SQL na indeksowanej kolumnie czasu."""
    storage, connections = _mock_storage(monkeypatch)
    _, cursor = connections[0]
    cursor.fetchall.return_value = [
        {
            "datetime": datetime(2026, 1, 5, 13, 30),
            "utc_offset_minutes": 60,
            "open": 155.4,
            "high": 155.5,
            "low": 155.3,
            "close": 155.45,
            "volume": 10,
        },
        {
            "datetime": datetime(2026, 1, 5, 13, 0),
            "utc_offset_minutes": 120,
            "open": 155.3,
            "high": 155.4,
            "low": 155.2,
            "close": 155.35,
            "volume": 10,
        },
        {
            "datetime": datetime(2026, 1, 5, 13, 0),
            "utc_offset_minutes": 60,
            "open": 155.4,
            "high": 155.5,
            "low": 155.3,
            "close": 155.45,
            "volume": 10,
        },
    ]
    cutoff = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)

    candles = storage.load_context(cutoff, limit=2)

    assert [candle["timestamp_utc"] for candle in candles] == [
        datetime(2026, 1, 5, 11, 0, tzinfo=UTC),
        datetime(2026, 1, 5, 12, 0, tzinfo=UTC),
    ]
    query, values = cursor.execute.call_args.args
    assert "DATE_SUB" not in query
    assert "`datetime` <= %s" in query
    assert values[1] == datetime(2026, 1, 5, 14, 0)
    assert values[-1] == 2 + config.CANDLE_QUERY_OVERFETCH


def test_duplicate_is_rolled_back_without_confirming_a_save(monkeypatch):
    """Nie potwierdza duplikatu ani nie zostawia otwartej transakcji."""
    storage, connections = _mock_storage(monkeypatch)
    connection, cursor = connections[0]
    cursor.execute.side_effect = MySQLError(msg="duplikat", errno=1062)
    assert storage.save_signal(_result("LONG"), 100) is False
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
    connection.close.assert_called_once()


def test_database_error_is_rolled_back_and_propagated(monkeypatch):
    """Propaguje błąd bazy po rollback zamiast udawać poprawny zapis."""
    storage, connections = _mock_storage(monkeypatch)
    connection, cursor = connections[0]
    cursor.execute.side_effect = MySQLError(msg="awaria", errno=1205)
    with pytest.raises(MySQLError):
        storage.save_signal(_result("SHORT"), 100)
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()


def test_save_none_flag_is_the_single_gate(monkeypatch):
    """Wyłączony NONE kończy się przed otwarciem połączenia."""
    storage, _ = _mock_storage(monkeypatch)
    monkeypatch.setattr(config, "SAVE_NONE_SIGNALS", False)
    storage._connect = Mock(side_effect=AssertionError("nie powinno łączyć"))
    assert storage.save_signal(_result("NONE"), 10) is False


def test_analyze_rejects_forming_or_missing_candle():
    """Nie analizuje świecy formowanej ani nieobecnej w historii."""
    storage = Mock()
    with pytest.raises(ValueError):
        trigger.analyze_candle(
            storage, OPENED, OPENED + timedelta(seconds=59)
        )
    storage.get_processed_signal.return_value = None
    storage.load_context.return_value = []
    with pytest.raises(ValueError):
        trigger.analyze_candle(storage, OPENED, OPENED + timedelta(minutes=1))
    storage.signal_history.assert_not_called()


def test_analyze_writes_closed_candle_once(monkeypatch):
    """Przekazuje wyłącznie zamkniętą świecę i stan wcześniejszych sygnałów."""
    storage = Mock()
    storage.get_processed_signal.return_value = None
    storage.load_context.return_value = [
        {
            "timestamp_utc": OPENED,
            "open": 155.4,
            "high": 155.5,
            "low": 155.3,
            "close": 155.45,
            "volume": 10.0,
        }
    ]
    storage.signal_history.return_value = {}
    storage.save_signal.return_value = True
    generated = _result("LONG")
    generated["price"] = 155.45
    generated["timestamp"] = int((OPENED + timedelta(minutes=1)).timestamp())
    generated["candle_open_time"] = OPENED.astimezone(
        storage_module.WARSAW
    ).isoformat()
    monkeypatch.setattr(trigger, "generate_signal", Mock(return_value=generated))

    result, saved = trigger.analyze_candle(
        storage, OPENED, OPENED + timedelta(minutes=1)
    )

    assert saved
    assert result["signal"] == "LONG"
    storage.get_processed_signal.assert_called_once_with(
        int((OPENED + timedelta(minutes=1)).timestamp())
    )
    storage.save_signal.assert_called_once()


def test_state_file_is_written_atomically(tmp_path):
    """Zapisuje i odczytuje stan UTF-8 przez plik tymczasowy."""
    path = tmp_path / "loop" / "state.json"
    storage_module.write_loop_state(path, 123456)
    assert storage_module.read_loop_state(path) == 123456
    assert not path.with_suffix(".json.tmp").exists()


def test_loop_resumes_after_latest_processed_candle_once(tmp_path, monkeypatch):
    """Po restarcie nie analizuje ponownie świecy już przetworzonej."""
    opened_epoch = int(OPENED.timestamp())
    storage = Mock()
    storage.latest_processed_close_epoch.return_value = opened_epoch + 60
    storage.has_candle.return_value = False
    state_path = tmp_path / "loop-state.json"
    stop_event = Event()
    analyze = Mock()
    monkeypatch.setattr(trigger, "analyze_candle", analyze)

    trigger.run_loop(
        storage=storage,
        stop_event=stop_event,
        state_path=state_path,
        now_provider=lambda: OPENED + timedelta(minutes=2),
        wait=lambda _seconds: stop_event.set(),
    )

    assert analyze.call_count == 0
    assert storage_module.read_loop_state(state_path) == opened_epoch


def test_loop_logs_and_skips_a_missing_minute(tmp_path, monkeypatch):
    """Pominięta luka przesuwa trwały kursor i kończy pętlę testową."""
    opened_epoch = int(OPENED.timestamp())
    storage = Mock()
    storage.latest_processed_close_epoch.return_value = None
    storage.latest_candle_open_epoch.return_value = opened_epoch
    storage.has_candle.return_value = False
    stop_event = Event()
    current_time = OPENED + timedelta(minutes=1, seconds=21)
    trigger.run_loop(
        storage=storage,
        stop_event=stop_event,
        state_path=tmp_path / "loop-state.json",
        now_provider=lambda: current_time,
        wait=lambda _seconds: stop_event.set(),
    )
    assert stop_event.is_set()
    assert storage_module.read_loop_state(
        tmp_path / "loop-state.json"
    ) == opened_epoch


def test_loop_does_not_play_sound_for_saved_none(tmp_path, monkeypatch):
    """Zapisany NONE przesuwa kursor pętli bez dźwięku."""
    opened_epoch = int(OPENED.timestamp())
    storage = Mock()
    storage.latest_processed_close_epoch.return_value = None
    storage.latest_candle_open_epoch.return_value = opened_epoch
    storage.has_candle.side_effect = [True, False]
    stop_event = Event()
    monkeypatch.setattr(
        trigger,
        "analyze_candle",
        Mock(return_value=(_result("NONE"), True)),
    )
    beep = Mock()
    monkeypatch.setattr(trigger, "play_signal_beep", beep)

    trigger.run_loop(
        storage=storage,
        stop_event=stop_event,
        state_path=tmp_path / "loop-state.json",
        now_provider=lambda: OPENED + timedelta(minutes=2),
        wait=lambda _seconds: stop_event.set(),
    )

    beep.assert_not_called()
    assert storage.has_candle.call_count == 2


def test_calibration_suggests_only_and_does_not_mutate_thresholds():
    """Kalibracja nie zmienia ręcznie zatwierdzanych parametrów."""
    report = {
        "TOKYO": {"atr14_pips": {"50": 4.0}},
        "LONDON": {"atr14_pips": {"50": 5.0}},
        "OVERLAP": {"atr14_pips": {"50": 6.0}},
        "NY": {"atr14_pips": {"50": 5.5}},
        "TOKYO_RANGE_PIPS": {"50": 31.0},
    }
    suggestions = calibrate._suggestions(report)
    assert suggestions
    assert config.ATR_MIN_PIPS == 1.2


def test_empty_database_run_once_returns_without_raising(caplog):
    """Pusta baza kończy jednorazowe uruchomienie czytelnym kodem, bez wyjątku."""
    storage = Mock()
    storage.latest_candle_open_epoch.return_value = None

    result = trigger.run_once(storage=storage)

    assert result == 2
    assert "nie zawiera świec" in caplog.text


@pytest.mark.parametrize("beep_enabled,expected_code", [(True, 0), (False, 4)])
def test_api_mode_saves_none_without_playing_sound(
    monkeypatch, capsys, beep_enabled, expected_code
):
    """Tryb API zapisuje NONE bez lokalnego dźwięku i zachowuje kod statusu."""
    opened_epoch = int(OPENED.timestamp())
    generated = _result("NONE")
    storage = Mock()
    monkeypatch.setattr(trigger, "USDJPYStorage", lambda: storage)
    monkeypatch.setattr(
        trigger,
        "analyze_candle",
        Mock(return_value=(generated, True)),
    )
    beep = Mock()
    monkeypatch.setattr(trigger, "play_signal_beep", beep)
    monkeypatch.setattr(config, "BEEP_ENABLED", beep_enabled)

    result = trigger.main(
        ["--api-candle-open-epoch", str(opened_epoch)]
    )

    assert result == expected_code
    beep.assert_not_called()
    assert json.loads(capsys.readouterr().out)["signal"] == "NONE"


def test_api_batch_keeps_results_and_reports_partial_failure(
    monkeypatch, capsys
):
    """Tryb API dla paczki zachowuje wynikowe wpisy i zgłasza częściową awarię."""
    batch_results = [
        {
            "candle_open_epoch": int(OPENED.timestamp()),
            "signal": "NONE",
            "saved": True,
        },
        {
            "candle_open_epoch": int(
                (OPENED + timedelta(minutes=1)).timestamp()
            ),
            "error": True,
        },
    ]
    monkeypatch.setattr(trigger, "USDJPYStorage", Mock())
    monkeypatch.setattr(
        trigger, "_analyze_api_batch", Mock(return_value=batch_results)
    )
    beep = Mock()
    monkeypatch.setattr(trigger, "play_signal_beep", beep)

    result = trigger.main(
        [
            "--api-candle-open-epochs",
            str(int(OPENED.timestamp())),
            str(int((OPENED + timedelta(minutes=1)).timestamp())),
        ]
    )

    assert result == 5
    beep.assert_not_called()
    assert json.loads(capsys.readouterr().out) == {"results": batch_results}
