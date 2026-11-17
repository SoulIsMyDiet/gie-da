"""Sprawdza wyzwalanie XAUUSD i zgodność odpowiedzi POST z klientem."""

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import Request

os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # noqa: E402


def request_and_candle(opened, symbol="XAU/USD", saved=1):
    """Tworzy żądanie z atrapą repozytorium i świecą o podanym otwarciu."""
    repository = Mock()
    repository._is_market_open.return_value = True
    repository.save_candle.return_value = (2, saved, "", 3)
    state = SimpleNamespace(
        repository=repository, strategy_ready=True,
        eurusd_strategy=object(), grok45_v2_candles=[],
    )
    request = Request({"type": "http", "app": SimpleNamespace(state=state),
                       "method": "POST", "path": "/api/candles", "headers": []})
    candle = app.CandleRecord(
        symbol=symbol, datetime=opened, open="2500", high="2501",
        low="2499", close="2500", volume="10",
    )
    return request, candle, repository


@pytest.mark.parametrize("beep,expected", [(True, b"0\n4"), (False, b"0\n3")])
def test_closed_candle_runs_after_save_and_adds_only_new_beep(beep, expected):
    """Dźwięk dolicza się tylko po nowym zapisie, zachowując istniejący licznik."""
    opened = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
    request, candle, repository = request_and_candle(opened)
    repository.save_candle.return_value = (0, 1, "", 3)
    def run(value):
        repository.save_candle.assert_called_once()
        assert value == opened
        return beep
    with patch.object(app, "_run_xauusd_strategy", side_effect=run), \
            patch.object(app, "_run_claude_xauusd_strategy", return_value=False) as claude, \
            patch.object(app, "Thread") as thread:
        response = app.save_candle(candle, request)
    assert response.body == expected
    if beep:
        thread.assert_called_once()
        assert thread.call_args.kwargs["kwargs"] == {
            "number_of_beeps": 2, "duration_ms": 100,
        }
    else:
        thread.assert_not_called()
    claude.assert_called_once_with(opened)


def test_claude_signal_increments_response_count_after_successful_save():
    """Udany zapis Claude zwiększa licznik sygnałów przekazywany klientowi."""
    opened = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
    request, candle, repository = request_and_candle(opened)
    repository.save_candle.return_value = (0, 1, "", 3)
    with patch.object(app, "_run_xauusd_strategy", return_value=False), \
            patch.object(app, "_run_claude_xauusd_strategy", return_value=True), \
            patch.object(app, "Thread"):
        response = app.save_candle(candle, request)
    assert response.body == b"0\n4"


@pytest.mark.parametrize("saved,minutes,symbol", [(0, 2, "XAU/USD"), (1, 0, "XAU/USD"), (1, 2, "EURUSD/USD")])
def test_nonqualifying_candles_do_not_trigger(saved, minutes, symbol):
    """Brak zapisu, otwarta świeca i inny instrument nie wyzwalają XAU."""
    opened = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=minutes)
    request, candle, _ = request_and_candle(opened, symbol, saved)
    if symbol == "EURUSD/USD":
        request.app.state.eurusd_strategy = Mock()
        request.app.state.eurusd_strategy.process_candle.return_value = None
    with patch.object(app, "_run_xauusd_strategy") as run, \
            patch.object(app, "_run_claude_xauusd_strategy") as claude, \
            patch.object(app, "Thread"):
        app.save_candle(candle, request)
    run.assert_not_called()
    claude.assert_not_called()


@pytest.mark.parametrize("code,beep", [(0, True), (3, False), (4, False)])
def test_process_exit_codes(code, beep):
    """Kod procesu rozróżnia zapis z dźwiękiem, duplikat i zapis bez dźwięku."""
    opened = datetime.now(timezone.utc)
    with patch.object(app.subprocess, "run", return_value=SimpleNamespace(returncode=code, stderr="")) as run:
        assert app._run_xauusd_strategy(opened) is beep
    assert run.call_args.args[0][-2:] == ["--api-candle-open-epoch", str(int(opened.timestamp()))]
    assert "XAUUSDStrategyV1.py" in run.call_args.args[0][1]


@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired("python", 30),
    SimpleNamespace(returncode=1, stderr="błąd"),
])
def test_process_failure_is_logged_without_blocking_client(failure, caplog):
    """Po błędzie zatwierdzona świeca i liczniki pozostają widoczne klientowi."""
    opened = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
    request, candle, repository = request_and_candle(opened)
    process = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
    with patch.object(app.subprocess, "run", **process), \
            patch.object(app, "_run_claude_xauusd_strategy", return_value=False), \
            patch.object(app, "Thread"):
        response = app.save_candle(candle, request)
    assert response.status_code == 200 and response.body == b"2\n3"
    assert response.headers["X-XAUUSD-Strategy-Error"] == "1"
    assert "Błąd strategii XAUUSD" in caplog.text
    repository.save_candle.assert_called_once()


@pytest.mark.parametrize("exit_code,saved", [(0, True), (3, False), (4, False)])
def test_claude_strategy_process_exit_code(exit_code, saved):
    """Nowa strategia otrzymuje epokę otwarcia i raportuje tylko nowy zapis."""
    opened = datetime.now(timezone.utc)
    with patch.object(
        app.subprocess, "run",
        return_value=SimpleNamespace(returncode=exit_code, stderr=""),
    ) as run:
        assert app._run_claude_xauusd_strategy(opened) is saved
    command = run.call_args.args[0]
    assert "Claude-sonnet55" in command[1]
    assert command[-2:] == [
        "--api-candle-open-epoch",
        str(int(opened.timestamp())),
    ]


def test_initialize_never_backfills_xau_with_default_spread():
    """Atrapa inicjalizacji sprawdza filtr bez uruchamiania migracji w bazie."""
    repository = app.MarketDataRepository()
    connection, cursor = Mock(), Mock()
    connection.cursor.return_value = cursor
    repository.signal_potential_calculator = Mock()
    with patch.object(app.mysql.connector, "connect", return_value=connection), \
            patch.object(repository, "_migrate_signal_enum"), \
            patch.object(repository, "_migrate_signal_source_timestamp"), \
            patch.object(repository, "_migrate_signal_potention_schema"), \
            patch.object(repository, "_migrate_candle_timestamps"):
        repository.initialize()
    calls = [call.args for call in cursor.execute.call_args_list
             if "INSERT IGNORE INTO signal_potention" in call.args[0]]
    assert len(calls) == 1
    assert "AND NOT (product = %s AND algorithm_name = %s)" in calls[0][0]
    assert calls[0][1] == ("XAU/USD", "Chat-gpt6/XAUUSDStrategyV1")


def test_unexpected_strategy_failure_is_isolated(caplog):
    """Nieprzewidziany wyjątek analizy nie przerywa pobierania produktów."""
    opened = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
    request, candle, _ = request_and_candle(opened)
    with patch.object(app, "_run_xauusd_strategy", side_effect=ValueError("test")), \
            patch.object(app, "_run_claude_xauusd_strategy", return_value=False), \
            patch.object(app, "Thread"):
        response = app.save_candle(candle, request)
    assert response.status_code == 200 and response.body == b"2\n3"
    assert response.headers["X-XAUUSD-Strategy-Error"] == "1"
    assert "Błąd strategii XAUUSD" in caplog.text


def test_claude_strategy_failure_is_reported_without_reverting_candle(caplog):
    """Awaria Claude ma osobny nagłówek i nie cofa zapisu świecy."""
    opened = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
    request, candle, repository = request_and_candle(opened)
    with patch.object(app, "_run_xauusd_strategy", return_value=False), \
            patch.object(
                app,
                "_run_claude_xauusd_strategy",
                side_effect=app.XAUUSDStrategyError("błąd testowy"),
            ), \
            patch.object(app, "Thread"):
        response = app.save_candle(candle, request)
    assert response.status_code == 200 and response.body == b"2\n3"
    assert response.headers["X-Claude-XAUUSD-Strategy-Error"] == "1"
    assert "Błąd strategii Claude XAU/USD" in caplog.text
