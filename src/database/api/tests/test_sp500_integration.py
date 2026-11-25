"""Sprawdza wyzwalanie strategii ES po zapisie zamkniętej świecy."""

from __future__ import annotations

import os
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import (  # noqa: E402
    CandleRecord,
    SP500StrategyError,
    GrokSP500Worker,
    _run_sp500_strategy,
    _process_live_ibkr_candle as save_candle,
)
from fastapi import Request  # noqa: E402


@pytest.fixture(autouse=True)
def allow_strategy_test_symbols(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pozwala testować strategię ES mimo tymczasowego wyłączenia pobierania."""
    monkeypatch.setattr("app.is_monitored_product", lambda _symbol: True)


def _request() -> tuple[Request, Mock]:
    """Tworzy żądanie z atrapą repozytorium i działającym stanem API."""
    repository = Mock()
    repository._is_market_open.return_value = True
    repository.save_ibkr_candle.return_value = (True, 0)
    state = SimpleNamespace(
        repository=repository,
        strategy_ready=True,
        eurusd_strategy=object(),
        grok45_v2_candles=[],
    )
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(state=state),
            "method": "POST",
            "path": "/api/candles/ibkr",
            "headers": [],
        }
    )
    return request, repository


def _candle(open_time: datetime, symbol: str = "ES") -> CandleRecord:
    """Przygotowuje świecę z określonym początkiem i symbolem."""
    return CandleRecord(
        symbol=symbol,
        datetime=open_time,
        open="6000",
        high="6002",
        low="5999",
        close="6001",
        volume="100",
    )


def test_closed_es_candle_triggers_only_claude_after_save() -> None:
    """POST uruchamia Claude, lecz nie pobiera powiadomień Groka."""
    open_time = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
    request, repository = _request()
    with patch("app._run_sp500_strategy", return_value=True) as strategy:
        response = save_candle(_candle(open_time), request)
    repository.save_ibkr_candle.assert_called_once()
    strategy.assert_called_once_with(open_time)
    assert response.body == b"1\n1\n"


def test_forming_candle_and_other_symbol_do_not_trigger_es() -> None:
    """Formowana świeca i inny instrument nie uruchamiają strategii ES."""
    open_time = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    request, _ = _request()
    with patch("app._run_sp500_strategy") as strategy:
        save_candle(_candle(open_time), request)
        save_candle(_candle(open_time - timedelta(minutes=2), "XAU/USD"), request)
    strategy.assert_not_called()


def test_unsaved_candle_does_not_trigger_es() -> None:
    """Analiza nie jest uruchamiana, gdy API nie zapisało świecy."""
    open_time = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
    request, repository = _request()
    repository.save_ibkr_candle.return_value = (False, 0)
    with patch("app._run_sp500_strategy") as strategy:
        response = save_candle(_candle(open_time), request)
    strategy.assert_not_called()
    assert response.body == b"0\n0\n"


@pytest.mark.parametrize(("exit_code", "should_beep"), [(0, True), (3, False), (4, False)])
def test_process_result_controls_sound(exit_code: int, should_beep: bool) -> None:
    """Duplikat i wyłączony dźwięk nie zwiększają liczby powiadomień."""
    open_time = datetime.now(timezone.utc)
    completed = SimpleNamespace(returncode=exit_code, stderr="")
    with patch("app.subprocess.run", return_value=completed) as process:
        assert _run_sp500_strategy(open_time) is should_beep
    assert process.call_args.args[0][-2:] == [
        "--api-candle-open-epoch",
        str(int(open_time.timestamp())),
    ]


def test_process_failure_is_not_reported_as_success() -> None:
    """Błąd procesu oraz przekroczenie czasu są przekazywane do API."""
    open_time = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
    request, repository = _request()
    with patch("app.subprocess.run", side_effect=subprocess.TimeoutExpired("python", 30)):
        response = save_candle(_candle(open_time), request)
    assert response.status_code == 503
    assert response.body == b"SP500_PARTIAL\n1\n0"
    repository.save_ibkr_candle.assert_called_once()


def test_unexpected_process_exit_is_error() -> None:
    """Nieznany kod wyjścia nie jest traktowany jak duplikat."""
    with patch(
        "app.subprocess.run",
        return_value=SimpleNamespace(returncode=2, stderr="błąd"),
    ):
        with pytest.raises(SP500StrategyError):
            _run_sp500_strategy(datetime.now(timezone.utc))


def test_partial_error_does_not_consume_grok_notifications() -> None:
    """Błąd Claude nie pobiera powiadomień niezależnej pętli Groka."""
    open_time = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
    request, repository = _request()
    with patch("app._run_sp500_strategy", side_effect=SP500StrategyError("błąd")):
        response = save_candle(_candle(open_time), request)
    assert response.status_code == 503
    assert response.body == b"SP500_PARTIAL\n1\n0"
    repository.grok_sp500_notification_ids.assert_not_called()
    repository.save_ibkr_candle.assert_called_once()


def test_worker_restarts_after_process_exit() -> None:
    """Nadzorca uruchamia proces pętli, bez pobierania powiadomień klienta."""
    worker = GrokSP500Worker()
    process = Mock()
    process.wait.side_effect = worker._stop.set
    with patch("app.subprocess.Popen", return_value=process) as launch:
        worker._run()
    assert launch.call_args.args[0][-1] == "--loop"
    assert launch.call_args.kwargs["stdout"] == subprocess.DEVNULL


def test_notification_repository_filters_and_paginates() -> None:
    """Zapytanie wybiera wyłącznie Groka ES i odrębne znaczniki dźwięku."""
    import app as module
    repository = module.MarketDataRepository()
    connection, cursor = Mock(), Mock()
    connection.cursor.return_value = cursor
    cursor.fetchall.return_value = [(11,), (14,), (18,)]
    with patch("app.mysql.connector.connect", return_value=connection):
        assert repository.grok_sp500_notification_ids(10, 2) == ([11, 14], True)
    sql, params = cursor.execute.call_args.args
    assert params == ("ES", "Grok-45/S&P500", 10, 3)
    assert "audio_notification" in sql and "ORDER BY id ASC" in sql
    connection.close.assert_called_once()


def test_notification_cursor_uses_only_grok_and_es() -> None:
    """Stan startowy pochodzi wyłącznie z ID konkretnej strategii i instrumentu."""
    import app as module
    repository = module.MarketDataRepository()
    connection, cursor = Mock(), Mock()
    connection.cursor.return_value = cursor
    cursor.fetchone.return_value = (17,)
    with patch("app.mysql.connector.connect", return_value=connection):
        assert repository.grok_sp500_notification_cursor() == 17
    assert cursor.execute.call_args.args[1] == ("ES", "Grok-45/S&P500")
    connection.close.assert_called_once()


def test_notification_endpoints_with_http_validation() -> None:
    """Kursor i strony są niezależne, a błędne argumenty nie odpytują bazy."""
    import app as module
    repository = Mock()
    repository.grok_sp500_notification_cursor.return_value = 10
    repository.grok_sp500_notification_ids.return_value = ([11, 14], True)

    def get(path: str, query: bytes = b"") -> tuple[int, dict]:
        """Wykonuje żądanie ASGI bez sieci i zwraca kod oraz odpowiedź JSON."""
        messages = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            messages.append(message)
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
            "query_string": query, "headers": [], "client": ("127.0.0.1", 1),
            "server": ("test", 80), "root_path": "",
        }
        asyncio.run(module.app(scope, receive, send))
        return messages[0]["status"], json.loads(messages[-1]["body"])

    with patch.object(module.app.state, "repository", repository):
        path = "/api/signals/grok-sp500/notifications"
        assert get(path + "/cursor") == (200, {"cursor": 10})
        assert get(path, b"after_id=10&limit=2") == (
            200, {"ids": [11, 14], "next_cursor": 14, "has_more": True}
        )
        assert get(path, b"after_id=-1")[0] == 422
        assert get(path, b"after_id=0&limit=101")[0] == 422
        repository.grok_sp500_notification_ids.assert_called_once_with(10, 2)
        repository.grok_sp500_notification_ids.return_value = ([], False)
        assert get(path, b"after_id=14") == (
            200, {"ids": [], "next_cursor": 14, "has_more": False}
        )
        repository.grok_sp500_notification_ids.side_effect = module.mysql.connector.Error("baza")
        assert get(path, b"after_id=14")[0] == 503


def test_lifespan_starts_and_stops_worker() -> None:
    """Start i zakończenie API obejmują aktywne workery strategii i AI."""
    import app as module
    worker = Mock()
    ai_verification_worker = Mock()

    async def exercise():
        """Przechodzi pełny cykl życia API z atrapą repozytorium."""
        async with module.lifespan(module.app):
            worker.start.assert_called_once()
            worker.stop.assert_not_called()
            ai_verification_worker.start.assert_called_once()
            ai_verification_worker.stop.assert_not_called()
        worker.stop.assert_called_once()
        ai_verification_worker.stop.assert_called_once()

    with patch.object(module, "configure_error_file_logging"), \
            patch.object(module, "GrokSP500Worker", return_value=worker), \
            patch.object(
                module,
                "AIVerificationWorker",
                return_value=ai_verification_worker,
            ), \
            patch.object(module.repository, "initialize"), \
            patch.object(module, "get_monitored_symbols", return_value=()), \
            patch.object(module, "is_monitored_product", return_value=True), \
            patch.object(module.repository, "fetch_candle_history", return_value=[]), \
            patch.object(module.eurusd_strategy, "warm_up"), \
            patch.object(module, "_grok45_candles_from_history", return_value=[]):
        asyncio.run(exercise())


def test_lifespan_skips_worker_when_sp500_is_not_monitored() -> None:
    """Nie uruchamia pętli Grok dla wyłączonego instrumentu ES."""
    import app as module

    async def exercise():
        """Sprawdza stan API podczas cyklu życia bez aktywnego ES."""
        async with module.lifespan(module.app):
            assert module.app.state.grok_sp500_worker is None

    with patch.object(module, "configure_error_file_logging"), \
            patch.object(module, "GrokSP500Worker") as worker_factory, \
            patch.object(
                module,
                "CandleStrategyBatchWorker",
                return_value=Mock(),
            ), \
            patch.object(module.repository, "initialize"), \
            patch.object(module, "get_monitored_symbols", return_value=()), \
            patch.object(module, "is_monitored_product", return_value=False), \
            patch.object(module.repository, "fetch_candle_history", return_value=[]), \
            patch.object(module.eurusd_strategy, "warm_up"), \
            patch.object(module, "_grok45_candles_from_history", return_value=[]):
        asyncio.run(exercise())

    worker_factory.assert_not_called()
