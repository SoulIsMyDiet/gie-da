"""Testy wyzwalania Claude-sonnet55 BTC/USD przez lokalne API."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import Request

os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "test")
API_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIRECTORY))

import app as api  # noqa: E402
from app import CandleRecord  # noqa: E402
from claude_sonnet55_btcusd.config import (  # noqa: E402
    DB_SYMBOL,
    STRATEGY_NAME,
)
from strategy_context_registry import (  # noqa: E402
    load_strategy_context_registry,
)

UTC = timezone.utc


def create_request(state: SimpleNamespace) -> Request:
    """Tworzy minimalne żądanie z repository i strategiami w stanie aplikacji."""
    app_instance = SimpleNamespace(state=state)
    return Request(
        {
            "type": "http",
            "app": app_instance,
            "method": "POST",
            "path": "/api/candles/ibkr",
            "headers": [],
        }
    )


def test_context_registry_finds_claude_btcusd() -> None:
    """Kontekst strategii jest widoczny dla istniejącego weryfikatora."""
    registry = load_strategy_context_registry(API_DIRECTORY)

    assert STRATEGY_NAME in registry
    assert "BID/ASK" in registry[STRATEGY_NAME]
    assert "BTC/USD" in registry[STRATEGY_NAME]


def test_closed_candle_is_processed_and_saved_once() -> None:
    """Nowa zamknięta świeca przechodzi przez wspólne repozytorium API."""
    opened = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
    result = {
        "signal": "LONG",
        "price": 67_000.0,
        "timestamp": int((opened + timedelta(minutes=1)).timestamp()),
        "timestamp_epoch": int((opened + timedelta(minutes=1)).timestamp()),
        "candle_open_epoch": int(opened.timestamp()),
        "candle_close_time": (
            opened + timedelta(minutes=1)
        ).astimezone(api.WARSAW_TIME_ZONE).isoformat(),
        "signal_parameters": {
            "price": 67_000.0,
            "signal": "LONG",
            "timestamp": int((opened + timedelta(minutes=1)).timestamp()),
            "instrument": "BTC/USD",
            "reason": "Warunki setupu zostały spełnione.",
        },
    }
    repository = Mock()
    repository.has_algorithm_evaluation.return_value = False
    repository.fetch_recent_candle_history.return_value = [
        {
            "timestamp_utc": opened,
            "open": 66_900.0,
            "high": 67_100.0,
            "low": 66_850.0,
            "close": 67_000.0,
            "volume": None,
        }
    ]
    repository.fetch_recent_algorithm_signal_timestamp.return_value = None
    strategy = Mock()
    strategy.evaluate.return_value = result
    storage = Mock()
    state = SimpleNamespace(
        repository=repository,
        claude_btcusd_strategy=strategy,
        claude_btcusd_storage=storage,
        claude_btcusd_last_processed_timestamp=None,
    )
    candle = CandleRecord(
        symbol=DB_SYMBOL,
        datetime=opened,
        open="66900",
        high="67100",
        low="66850",
        close="67000",
        volume=None,
    )

    api._process_claude_btcusd_candle(
        create_request(state),
        candle,
        opened + timedelta(minutes=1),
        enqueue_notifications=True,
    )

    repository.fetch_recent_candle_history.assert_called_once_with(
        DB_SYMBOL,
        api.CLAUDE_BTCUSD_HISTORY_LIMIT,
        opened,
    )
    repository.fetch_recent_algorithm_signal_timestamp.assert_called_once_with(
        DB_SYMBOL,
        STRATEGY_NAME,
        int(opened.timestamp()),
        api.CLAUDE_BTCUSD_COOLDOWN_MINUTES * 60,
    )
    storage.save.assert_called_once_with(
        result,
        1,
        enqueue_notification=True,
    )
    assert state.claude_btcusd_last_processed_timestamp == opened


def test_existing_evaluation_prevents_strategy_rerun() -> None:
    """Trwały marker zapobiega ponownemu wywołaniu algorytmu."""
    opened = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
    repository = Mock()
    repository.has_algorithm_evaluation.return_value = True
    state = SimpleNamespace(
        repository=repository,
        claude_btcusd_strategy=Mock(),
        claude_btcusd_storage=Mock(),
    )
    candle = CandleRecord(
        symbol=DB_SYMBOL,
        datetime=opened,
        open="66900",
        high="67100",
        low="66850",
        close="67000",
        volume=None,
    )

    api._process_claude_btcusd_candle(
        create_request(state),
        candle,
        opened + timedelta(minutes=1),
        enqueue_notifications=False,
    )

    repository.fetch_recent_candle_history.assert_not_called()
    state.claude_btcusd_strategy.evaluate.assert_not_called()
    assert state.claude_btcusd_last_processed_timestamp == opened
