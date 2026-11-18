"""Sprawdza wyzwalanie XAUUSD i zgodność odpowiedzi POST z klientem."""

import json
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


@pytest.mark.parametrize(
    "chat_result,claude_result,expected,chat_beep",
    [
        (("NONE", False), ("NONE", False), b"0\n0", False),
        (("LONG", True), ("NONE", False), b"0\n1", True),
        (("NONE", False), ("SHORT", True), b"0\n1", False),
        (("LONG", False), ("NONE", False), b"0\n0", False),
    ],
)
def test_closed_candle_counts_only_actionable_signals(
    chat_result, claude_result, expected, chat_beep
):
    """NONE nie jest zapisywany; API liczy wyłącznie nowe sygnały LONG/SHORT."""
    opened = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
    request, candle, repository = request_and_candle(opened)
    repository.save_candle.return_value = (0, 1, "", 0)

    def run(value):
        repository.save_candle.assert_called_once()
        assert value == opened
        return chat_result

    with patch.object(app, "_run_xauusd_strategy", side_effect=run), \
            patch.object(
                app, "_run_claude_xauusd_strategy", return_value=claude_result
            ) as claude, \
            patch.object(app, "Thread") as thread:
        response = app.save_candle(candle, request)
    assert response.body == expected
    if chat_beep:
        thread.assert_called_once()
        assert thread.call_args.kwargs["kwargs"] == {
            "number_of_beeps": 2, "duration_ms": 100,
        }
    else:
        thread.assert_not_called()
    claude.assert_called_once_with(opened)


def test_batch_evaluates_both_xauusd_strategies_without_sound():
    """Paczka ocenia zamknięte świece XAU/USD bez zmiany formatu odpowiedzi."""
    latest_open = (
        datetime.now(timezone.utc).replace(second=0, microsecond=0)
        - timedelta(minutes=2)
    )
    candles = [
        app.CandleRecord(
            symbol="XAU/USD",
            datetime=latest_open - timedelta(minutes=1),
            open="2500",
            high="2501",
            low="2499",
            close="2500",
            volume="10",
        ),
        app.CandleRecord(
            symbol="XAU/USD",
            datetime=latest_open,
            open="2500",
            high="2501",
            low="2499",
            close="2500",
            volume="10",
        ),
    ]
    repository = Mock()
    repository._is_market_open.return_value = True
    repository.save_candles.return_value = (0, 2, "czas", 0)
    repository.fetch_candle_history.return_value = []
    state = SimpleNamespace(
        repository=repository,
        strategy_ready=True,
        eurusd_strategy=Mock(),
        grok45_v2_candles=[],
    )
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(state=state),
            "method": "POST",
            "path": "/api/candles/batch",
            "headers": [],
        }
    )
    with patch.object(app, "is_monitored_product", return_value=True), \
            patch.object(
                app, "_run_xauusd_strategy", return_value=("NONE", True)
            ) as chat, \
                patch.object(
                    app, "_run_claude_xauusd_strategy", return_value=("NONE", True)
                ) as claude, \
            patch.object(app, "Thread") as thread:
        response = app.save_candle_batch(app.CandleBatch(candles=candles), request)

    assert response.status_code == 200
    assert response.body == b"2\nczas"
    expected_times = [candle.datetime for candle in candles]
    assert [call.args[0] for call in chat.call_args_list] == expected_times
    assert [call.args[0] for call in claude.call_args_list] == expected_times
    thread.assert_not_called()


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


@pytest.mark.parametrize(
    "code,signal,expected",
    [
        (0, "LONG", ("LONG", True)),
        (0, "NONE", ("NONE", True)),
        (3, "NONE", None),
        (4, "SHORT", ("SHORT", False)),
    ],
)
def test_process_exit_codes(code, signal, expected):
    """Kod procesu oraz JSON odróżniają zapis kierunkowy od duplikatu."""
    opened = datetime.now(timezone.utc)
    result = {"signal": signal}
    with patch.object(
        app.subprocess,
        "run",
        return_value=SimpleNamespace(
            returncode=code, stderr="", stdout=json.dumps(result)
        ),
    ) as run:
        assert app._run_xauusd_strategy(opened) == expected
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
            patch.object(app, "_run_claude_xauusd_strategy", return_value=None), \
            patch.object(app, "Thread"):
        response = app.save_candle(candle, request)
    assert response.status_code == 200 and response.body == b"2\n3"
    assert response.headers["X-XAUUSD-Strategy-Error"] == "1"
    assert "Błąd strategii XAUUSD" in caplog.text
    repository.save_candle.assert_called_once()


@pytest.mark.parametrize(
    "exit_code,signal,expected",
    [
        (0, "NONE", ("NONE", True)),
        (0, "SHORT", ("SHORT", True)),
        (3, "NONE", None),
        (4, "SHORT", ("SHORT", False)),
    ],
)
def test_claude_strategy_process_exit_code(exit_code, signal, expected):
    """Strategia Claude zwraca kierunek zapisanego sygnału lub brak nowego wpisu."""
    opened = datetime.now(timezone.utc)
    with patch.object(
        app.subprocess, "run",
        return_value=SimpleNamespace(
            returncode=exit_code,
            stderr="",
            stdout=json.dumps({"signal": signal}),
        ),
    ) as run:
        assert app._run_claude_xauusd_strategy(opened) == expected
    command = run.call_args.args[0]
    assert "Claude-sonnet55" in command[1]
    assert command[-2:] == [
        "--api-candle-open-epoch",
        str(int(opened.timestamp())),
    ]


def test_initialize_backfills_xau_with_configured_spread():
    """Inicjalizacja przypisuje XAU/USD jego własny spread."""
    repository = app.MarketDataRepository()
    connection, cursor = Mock(), Mock()
    connection.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    with patch.object(app.mysql.connector, "connect", return_value=connection), \
            patch.object(repository, "_migrate_signal_enum"), \
            patch.object(repository, "_migrate_signal_source_timestamp"), \
            patch.object(repository, "_migrate_signal_potention_schema"), \
            patch.object(repository, "_migrate_candle_timestamps"):
        repository.initialize()
    calls = [call.args for call in cursor.execute.call_args_list
             if "INSERT IGNORE INTO signal_potention" in call.args[0]]
    assert len(calls) == 1
    assert "CASE s.product" in calls[0][0]
    spreads = app.SignalPotentialCalculator.SPREADS_BY_PRODUCT
    expected_xau_pair = ("XAU/USD", spreads["XAU/USD"])
    case_pairs = tuple(
        (calls[0][1][index], calls[0][1][index + 1])
        for index in range(0, 2 * len(spreads), 2)
    )
    assert expected_xau_pair in case_pairs


def test_unexpected_strategy_failure_is_isolated(caplog):
    """Nieprzewidziany wyjątek analizy nie przerywa pobierania produktów."""
    opened = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
    request, candle, _ = request_and_candle(opened)
    with patch.object(app, "_run_xauusd_strategy", side_effect=ValueError("test")), \
            patch.object(app, "_run_claude_xauusd_strategy", return_value=None), \
            patch.object(app, "Thread"):
        response = app.save_candle(candle, request)
    assert response.status_code == 200 and response.body == b"2\n3"
    assert response.headers["X-XAUUSD-Strategy-Error"] == "1"
    assert "Błąd strategii XAUUSD" in caplog.text


def test_claude_strategy_failure_is_reported_without_reverting_candle(caplog):
    """Awaria Claude ma osobny nagłówek i nie cofa zapisu świecy."""
    opened = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
    request, candle, repository = request_and_candle(opened)
    with patch.object(app, "_run_xauusd_strategy", return_value=None), \
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
