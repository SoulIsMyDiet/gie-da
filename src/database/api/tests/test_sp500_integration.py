"""Sprawdza wyzwalanie strategii ES po zapisie zamkniętej świecy."""

from __future__ import annotations

import os
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
    _run_grok_sp500_strategy,
    _run_sp500_strategy,
    save_candle,
)
from fastapi import Request  # noqa: E402


def _request() -> tuple[Request, Mock]:
    """Tworzy żądanie z atrapą repozytorium i działającym stanem API."""
    repository = Mock()
    repository._is_market_open.return_value = True
    repository.save_candle.return_value = (0, 1, "", 0)
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
            "path": "/api/candles",
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


def test_closed_es_candle_triggers_analysis_after_save() -> None:
    """Nowy zapis sygnału ES jest przekazywany do dźwięku aplikacji Windows."""
    open_time = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
    request, repository = _request()
    with patch("app._run_sp500_strategy", return_value=True) as strategy, \
            patch("app._run_grok_sp500_strategy", return_value=True) as grok:
        response = save_candle(_candle(open_time), request)
    repository.save_candle.assert_called_once()
    strategy.assert_called_once_with(open_time)
    grok.assert_called_once_with(open_time)
    assert response.body == b"0\n2"


def test_forming_candle_and_other_symbol_do_not_trigger_es() -> None:
    """Formowana świeca i inny instrument nie uruchamiają strategii ES."""
    open_time = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    request, _ = _request()
    with patch("app._run_sp500_strategy") as strategy, \
            patch("app._run_grok_sp500_strategy") as grok:
        save_candle(_candle(open_time), request)
        save_candle(_candle(open_time - timedelta(minutes=2), "XAU/USD"), request)
    strategy.assert_not_called()
    grok.assert_not_called()


def test_unsaved_candle_does_not_trigger_es() -> None:
    """Analiza nie jest uruchamiana, gdy API nie zapisało świecy."""
    open_time = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
    request, repository = _request()
    repository.save_candle.return_value = (0, 0, "", 0)
    with patch("app._run_sp500_strategy") as strategy, \
            patch("app._run_grok_sp500_strategy") as grok:
        response = save_candle(_candle(open_time), request)
    strategy.assert_not_called()
    grok.assert_not_called()
    assert response.body == b"0\n0"


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
    assert response.body == b"SP500_PARTIAL\n0\n0"
    repository.save_candle.assert_called_once()


def test_unexpected_process_exit_is_error() -> None:
    """Nieznany kod wyjścia nie jest traktowany jak duplikat."""
    with patch(
        "app.subprocess.run",
        return_value=SimpleNamespace(returncode=2, stderr="błąd"),
    ):
        with pytest.raises(SP500StrategyError):
            _run_sp500_strategy(datetime.now(timezone.utc))


@pytest.mark.parametrize(("exit_code", "should_beep"), [(0, True), (3, False), (4, False)])
def test_grok_process_uses_isolated_strategy(exit_code: int, should_beep: bool) -> None:
    """Grok używa osobnego katalogu i tych samych kodów potwierdzenia zapisu."""
    completed = SimpleNamespace(returncode=exit_code, stderr="")
    with patch("app.subprocess.run", return_value=completed) as process:
        assert _run_grok_sp500_strategy(datetime.now(timezone.utc)) is should_beep
    script = Path(process.call_args.args[0][1])
    assert script.name == "S&P500StrategyV1.py"
    assert script.parent.name in ("grok_sp500_algorithm", "S&P500")
    assert "Grok-45" in str(script) or script.parent.name == "grok_sp500_algorithm"


def test_both_strategies_are_attempted_if_one_fails() -> None:
    """Błąd Claude nie uniemożliwia próby zapisu sygnału Groka."""
    open_time = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
    request, repository = _request()
    with patch("app._run_sp500_strategy", side_effect=SP500StrategyError("błąd")), \
            patch("app._run_grok_sp500_strategy", return_value=True) as grok:
        response = save_candle(_candle(open_time), request)
    assert response.status_code == 503
    assert response.body == b"SP500_PARTIAL\n0\n1"
    grok.assert_called_once_with(open_time)
    repository.save_candle.assert_called_once()


def test_partial_error_keeps_successful_claude_confirmation() -> None:
    """Niepowodzenie Groka nie ukrywa potwierdzenia zapisu strategii Claude."""
    open_time = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
    request, repository = _request()
    repository.save_candle.return_value = (2, 1, "", 0)
    with patch("app._run_sp500_strategy", return_value=True), \
            patch("app._run_grok_sp500_strategy", side_effect=SP500StrategyError("błąd")):
        response = save_candle(_candle(open_time), request)
    assert response.status_code == 503
    assert response.body == b"SP500_PARTIAL\n2\n1"
