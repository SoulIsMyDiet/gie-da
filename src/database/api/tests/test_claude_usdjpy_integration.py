"""Testy integracji Claude USD/JPY z zapisem świec w API."""

import json
import os
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

UTC = timezone.utc


def _create_request(
    state: SimpleNamespace, path: str = "/api/candles/ibkr"
) -> Request:
    """Tworzy żądanie API z testowym stanem aplikacji."""
    return Request(
        {
            "type": "http",
            "app": SimpleNamespace(state=state),
            "method": "POST",
            "path": path,
            "headers": [],
        }
    )


def _create_usdjpy_candle(opened: datetime) -> app.CandleRecord:
    """Tworzy poprawną świecę USD/JPY otwartą w podanym czasie UTC."""
    return app.CandleRecord(
        symbol="USD/JPY",
        datetime=opened,
        open="150.0",
        high="150.1",
        low="149.9",
        close="150.05",
        volume="10",
    )


def _create_single_candle_state(opened: datetime) -> SimpleNamespace:
    """Przygotowuje atrapy repozytorium i strategii ChatGPT dla pojedynczej świecy."""
    repository = Mock()
    repository._is_market_open.return_value = True
    repository.save_ibkr_candle.return_value = (True, 0)
    repository.has_algorithm_evaluation.return_value = False
    repository.fetch_recent_candle_history.return_value = [
        {
            "timestamp_utc": opened,
            "open": 150.0,
            "high": 150.1,
            "low": 149.9,
            "close": 150.05,
            "volume": 10.0,
        }
    ]
    strategy = Mock()
    strategy.evaluate.return_value = {
        "signal": "NONE",
        "candle_open_epoch": int(opened.timestamp()),
        "signal_parameters": {"reason": "warmup"},
    }
    return SimpleNamespace(
        repository=repository,
        candle_strategy_batch_worker=Mock(),
        strategy_ready=True,
        eurusd_strategy=Mock(),
        grok45_v2_candles=[],
        usdjpy_strategy=strategy,
        usdjpy_storage=Mock(),
    )


@pytest.mark.parametrize(
    "return_code,signal,expected",
    [
        (0, "NONE", ("NONE", True)),
        (0, "LONG", ("LONG", True)),
        (4, "SHORT", ("SHORT", False)),
        (3, "NONE", None),
    ],
)
def test_process_result_reports_direction_and_audio_permission(
    return_code, signal, expected
):
    """Odczytuje kierunek oraz stan dźwięku bez mylenia duplikatu z zapisem."""
    opened = datetime.now(UTC).replace(second=0, microsecond=0)
    completed = SimpleNamespace(
        returncode=return_code,
        stdout=json.dumps({"signal": signal}),
        stderr="",
    )
    with patch.object(app.subprocess, "run", return_value=completed) as run:
        result = app._run_claude_usdjpy_strategy(opened)

    assert result == expected
    command = run.call_args.args[0]
    assert "USDJPYStrategyV1.py" in command[1]
    assert "Claude-sonnet55" in command[1]
    assert command[-2:] == [
        "--api-candle-open-epoch",
        str(int(opened.timestamp())),
    ]


def test_process_failure_is_reported():
    """Niepoprawny kod procesu zgłasza błąd zamiast udawać zapis sygnału."""
    opened = datetime.now(UTC).replace(second=0, microsecond=0)
    completed = SimpleNamespace(returncode=1, stdout="", stderr="błąd strategii")
    with patch.object(app.subprocess, "run", return_value=completed):
        with pytest.raises(app.ClaudeUSDJPYStrategyError):
            app._run_claude_usdjpy_strategy(opened)


def test_batch_process_reports_failed_candle_without_losing_other_results():
    """Oznacza błąd jednej świecy i zachowuje pozostałe wyniki paczki."""
    opened = datetime.now(UTC).replace(second=0, microsecond=0)
    failed_result = {
        "candle_open_epoch": int(opened.timestamp()),
        "error": True,
    }
    completed = SimpleNamespace(
        returncode=5,
        stdout=json.dumps({"results": [failed_result]}),
        stderr="",
    )
    with patch.object(app.subprocess, "run", return_value=completed):
        results = app._run_claude_usdjpy_batch([opened])

    assert results == [failed_result]


def test_batch_process_returns_one_result_per_candle():
    """Proces paczki uruchamia się raz i zwraca wynik dla każdej świecy."""
    opened = datetime.now(UTC).replace(second=0, microsecond=0)
    expected = {
        "candle_open_epoch": int(opened.timestamp()),
        "signal": "NONE",
        "saved": False,
    }
    completed = SimpleNamespace(
        returncode=0,
        stdout=json.dumps({"results": [expected]}),
        stderr="",
    )
    with patch.object(app.subprocess, "run", return_value=completed) as run:
        results = app._run_claude_usdjpy_batch([opened])

    assert results == [expected]
    command = run.call_args.args[0]
    assert command[-2:] == [
        "--api-candle-open-epochs",
        str(int(opened.timestamp())),
    ]


@pytest.mark.parametrize(
    "claude_signal,sound_enabled,expected_signal_count",
    [
        ("NONE", True, 0),
        ("LONG", True, 1),
        ("SHORT", False, 0),
    ],
)
def test_single_closed_ibkr_candle_runs_both_strategies(
    claude_signal, sound_enabled, expected_signal_count
):
    """Zamknięta świeca IBKR uruchamia obie strategie i zwraca status zapisu."""
    opened = (
        datetime.now(UTC).replace(second=0, microsecond=0)
        - timedelta(minutes=2)
    )
    state = _create_single_candle_state(opened)
    request = _create_request(state)

    with patch("app.is_monitored_product", return_value=True), patch(
        "app._run_claude_usdjpy_strategy",
        return_value=(claude_signal, sound_enabled),
    ) as claude:
        response = app._process_live_ibkr_candle(
            _create_usdjpy_candle(opened), request
        )

    assert response.status_code == 200
    assert response.body == f"1\n{expected_signal_count}\n".encode()
    claude.assert_called_once_with(opened)
    state.usdjpy_strategy.evaluate.assert_called_once()
    state.usdjpy_storage.save.assert_called_once()


def test_claude_failure_does_not_skip_chatgpt_evaluation(caplog):
    """Błąd Claude jest widoczny, ale nie blokuje osobnej ewaluacji ChatGPT."""
    opened = (
        datetime.now(UTC).replace(second=0, microsecond=0)
        - timedelta(minutes=2)
    )
    state = _create_single_candle_state(opened)
    request = _create_request(state)

    with patch("app.is_monitored_product", return_value=True), patch(
        "app._run_claude_usdjpy_strategy",
        side_effect=app.ClaudeUSDJPYStrategyError("błąd testowy"),
    ):
        response = app._process_live_ibkr_candle(
            _create_usdjpy_candle(opened), request
        )

    assert response.status_code == 200
    assert response.body == b"1\n0\n"
    assert response.headers["X-Claude-USDJPY-Strategy-Error"] == "1"
    state.usdjpy_strategy.evaluate.assert_called_once()


def test_legacy_batch_does_not_evaluate_usdjpy_strategy():
    """Paczka legacy zapisuje świece bez ponownego uruchamiania strategii."""
    opened = (
        datetime.now(UTC).replace(second=0, microsecond=0)
        - timedelta(minutes=2)
    )
    state = _create_single_candle_state(opened)
    state.repository.save_candles.return_value = (0, 1, "ostatnia", 0)
    state.repository.fetch_candle_history.return_value = []
    request = _create_request(state, "/api/candles/batch")
    batch = app.CandleBatch(candles=[_create_usdjpy_candle(opened)])

    with patch("app.is_monitored_product", return_value=True), patch(
        "app._run_claude_usdjpy_batch",
        return_value=[
            {
                "candle_open_epoch": int(opened.timestamp()),
                "signal": "NONE",
                "saved": False,
            }
        ],
    ) as claude:
        response = app.save_candle_batch(batch, request)

    assert response.status_code == 200
    assert response.body == b"1\nostatnia"
    assert "X-Strategy-Processing" not in response.headers
    state.candle_strategy_batch_worker.enqueue.assert_not_called()
    state.repository.fetch_candle_history.assert_not_called()
    claude.assert_not_called()
    state.usdjpy_strategy.evaluate.assert_not_called()


def test_batch_worker_persists_payload_before_returning():
    """Worker zapisuje JSON paczki do MySQL przed potwierdzeniem zlecenia."""
    opened = datetime.now(UTC).replace(second=0, microsecond=0)
    batch = app.CandleBatch(candles=[_create_usdjpy_candle(opened)])
    repository = SimpleNamespace(database_settings={"database": "test"})
    application = SimpleNamespace(state=SimpleNamespace())
    worker = app.CandleStrategyBatchWorker(repository, application)
    connection = Mock()
    cursor = Mock()
    connection.cursor.return_value = cursor

    with patch("app.mysql.connector.connect", return_value=connection) as connect:
        worker.enqueue(batch)

    connect.assert_called_once_with(database="test")
    query, parameters = cursor.execute.call_args.args
    assert "INSERT INTO candle_strategy_batch_jobs" in query
    assert app.CandleBatch.model_validate_json(parameters[0]) == batch
    connection.commit.assert_called_once()
    cursor.close.assert_called_once()
    connection.close.assert_called_once()


def test_batch_worker_marks_claimed_job_completed():
    """Worker deserializuje trwale zapisane zadanie i oznacza je jako wykonane."""
    opened = datetime.now(UTC).replace(second=0, microsecond=0)
    batch = app.CandleBatch(candles=[_create_usdjpy_candle(opened)])
    application = SimpleNamespace(state=SimpleNamespace())
    worker = app.CandleStrategyBatchWorker(
        SimpleNamespace(database_settings={}),
        application,
    )
    worker._claim_next_job = Mock(return_value=(12, batch.model_dump_json()))
    worker._mark_job_completed = Mock()

    with patch("app._process_candle_batch_strategies") as process:
        assert worker.process_next_job() is True

    worker._mark_job_completed.assert_called_once_with(12)
    processed_request, processed_batch = process.call_args.args
    assert processed_request.app is application
    assert processed_batch == batch


def test_batch_worker_removes_payload_after_success():
    """Udana ocena zwalnia miejsce, zachowując niewielki wpis końcowy."""
    worker = app.CandleStrategyBatchWorker(
        SimpleNamespace(database_settings={"database": "test"}),
        SimpleNamespace(state=SimpleNamespace()),
    )
    connection = Mock()
    cursor = Mock()
    connection.cursor.return_value = cursor

    with patch("app.mysql.connector.connect", return_value=connection):
        worker._mark_job_completed(14)

    query, parameters = cursor.execute.call_args.args
    assert "batch_payload = IF" in query
    assert parameters == ("completed", None, "completed", 14)
    connection.commit.assert_called_once()


def test_batch_worker_keeps_failed_job_error():
    """Błąd ewaluacji pozostaje zapisany przy zadaniu zamiast ginąć w tle."""
    opened = datetime.now(UTC).replace(second=0, microsecond=0)
    batch = app.CandleBatch(candles=[_create_usdjpy_candle(opened)])
    worker = app.CandleStrategyBatchWorker(
        SimpleNamespace(database_settings={}),
        SimpleNamespace(state=SimpleNamespace()),
    )
    worker._claim_next_job = Mock(return_value=(13, batch.model_dump_json()))
    worker._mark_job_failed = Mock()

    with patch(
        "app._process_candle_batch_strategies",
        side_effect=RuntimeError("błąd ewaluacji"),
    ):
        assert worker.process_next_job() is True

    worker._mark_job_failed.assert_called_once_with(13, "błąd ewaluacji")
