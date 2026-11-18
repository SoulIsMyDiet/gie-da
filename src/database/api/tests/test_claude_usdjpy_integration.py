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


def _create_request(state: SimpleNamespace, path: str = "/api/candles") -> Request:
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
    repository.save_candle.return_value = (0, 1, "", 0)
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
    "claude_result,expected_response",
    [
        (("NONE", True), b"0\n0"),
        (("LONG", True), b"0\n1"),
        (("SHORT", False), b"0\n0"),
    ],
)
def test_single_closed_candle_runs_both_strategies_and_counts_only_audio(
    claude_result, expected_response
):
    """Claude ocenia świecę obok ChatGPT, a NONE nie zwiększa licznika dźwięku."""
    opened = (
        datetime.now(UTC).replace(second=0, microsecond=0)
        - timedelta(minutes=2)
    )
    state = _create_single_candle_state(opened)
    request = _create_request(state)

    with patch("app.is_monitored_product", return_value=True), patch(
        "app._run_claude_usdjpy_strategy", return_value=claude_result
    ) as claude:
        response = app.save_candle(_create_usdjpy_candle(opened), request)

    assert response.status_code == 200
    assert response.body == expected_response
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
        response = app.save_candle(_create_usdjpy_candle(opened), request)

    assert response.status_code == 200
    assert response.body == b"0\n0"
    assert response.headers["X-Claude-USDJPY-Strategy-Error"] == "1"
    state.usdjpy_strategy.evaluate.assert_called_once()


def test_batch_runs_claude_for_closed_usdjpy_candles():
    """Paczka ocenia USD/JPY przez Claude i zachowuje dotychczasową odpowiedź."""
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
    claude.assert_called_once_with([opened])
    state.usdjpy_strategy.evaluate.assert_called_once()
