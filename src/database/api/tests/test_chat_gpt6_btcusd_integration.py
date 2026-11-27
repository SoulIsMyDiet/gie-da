"""Testy integracji Chat-gpt6 BTC/USD z lokalnym API."""

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
from strategy_context_registry import (  # noqa: E402
    load_strategy_context_registry,
)
from chat_gpt6_btcusd.config import (  # noqa: E402
    DATABASE_SYMBOL,
    STRATEGY_NAME,
)

UTC = timezone.utc


def create_request(state: SimpleNamespace) -> Request:
    """Tworzy żądanie z repozytorium i strategią testową."""
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


def test_context_registry_discovers_chat_gpt6_btcusd() -> None:
    """Kontekst algorytmu jest dostępny dla wspólnego weryfikatora."""
    registry = load_strategy_context_registry(API_DIRECTORY)

    assert STRATEGY_NAME in registry
    assert "spreadu ani BID/ASK" in registry[STRATEGY_NAME]


def test_closed_candle_is_processed_once_through_shared_repository() -> None:
    """Zamknięta świeca trafia do strategii, a wynik zapisuje wspólne API."""
    opened = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    result = {
        "signal": "NONE",
        "price": 67_000.0,
        "candle_open_epoch": int(opened.timestamp()),
        "candle_close_time": (opened + timedelta(minutes=1)).isoformat(),
        "signal_parameters": {
            "price": 67_000.0,
            "signal": "NONE",
            "timestamp": int((opened + timedelta(minutes=1)).timestamp()),
            "instrument": "BTCUSD",
            "reason": "Brak warunków.",
        },
    }
    repository = Mock()
    repository.has_algorithm_evaluation.return_value = False
    repository.fetch_recent_candle_history.return_value = [
        {
            "timestamp_utc": opened,
            "open": 67_000.0,
            "high": 67_100.0,
            "low": 66_900.0,
            "close": 67_000.0,
            "volume": None,
        }
    ]
    strategy = Mock()
    strategy.evaluate.return_value = result
    storage = Mock()
    state = SimpleNamespace(
        repository=repository,
        chat_gpt6_btcusd_strategy=strategy,
        chat_gpt6_btcusd_storage=storage,
        chat_gpt6_btcusd_last_processed_timestamp=None,
    )
    candle = CandleRecord(
        symbol=DATABASE_SYMBOL,
        datetime=opened,
        open="67000",
        high="67100",
        low="66900",
        close="67000",
        volume=None,
    )

    api._process_chat_gpt6_btcusd_candle(
        create_request(state),
        candle,
        opened + timedelta(minutes=1),
        enqueue_notifications=True,
    )

    repository.fetch_recent_candle_history.assert_called_once_with(
        DATABASE_SYMBOL,
        api.CHAT_GPT6_BTCUSD_HISTORY_LIMIT,
        opened,
    )
    repository.fetch_recent_algorithm_signal_timestamp.assert_not_called()
    strategy.evaluate.assert_called_once_with(
        repository.fetch_recent_candle_history.return_value,
        now=opened + timedelta(minutes=1),
    )
    storage.save.assert_called_once_with(
        result,
        1,
        enqueue_notification=False,
    )
    assert state.chat_gpt6_btcusd_last_processed_timestamp == opened


def test_recent_directional_signal_is_rechecked_with_cooldown_timestamp() -> None:
    """Przekazuje ostatni sygnał kierunkowy do ponownej oceny cooldownu."""
    opened = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    previous_signal_open = int((opened - timedelta(minutes=2)).timestamp())
    directional_result = {
        "signal": "LONG",
        "price": 67_000.0,
        "candle_open_epoch": int(opened.timestamp()),
        "candle_close_time": (opened + timedelta(minutes=1)).isoformat(),
        "signal_parameters": {},
    }
    suppressed_result = {
        **directional_result,
        "signal": "NONE",
    }
    repository = Mock()
    repository.has_algorithm_evaluation.return_value = False
    repository.fetch_recent_candle_history.return_value = [
        {
            "timestamp_utc": opened,
            "open": 67_000.0,
            "high": 67_100.0,
            "low": 66_900.0,
            "close": 67_000.0,
            "volume": None,
        }
    ]
    repository.fetch_recent_algorithm_signal_timestamp.return_value = (
        previous_signal_open
    )
    strategy = Mock()
    strategy.evaluate.side_effect = [directional_result, suppressed_result]
    storage = Mock()
    state = SimpleNamespace(
        repository=repository,
        chat_gpt6_btcusd_strategy=strategy,
        chat_gpt6_btcusd_storage=storage,
        chat_gpt6_btcusd_last_processed_timestamp=None,
    )
    candle = CandleRecord(
        symbol=DATABASE_SYMBOL,
        datetime=opened,
        open="67000",
        high="67100",
        low="66900",
        close="67000",
        volume=None,
    )

    api._process_chat_gpt6_btcusd_candle(
        create_request(state),
        candle,
        opened + timedelta(minutes=1),
        enqueue_notifications=True,
    )

    repository.fetch_recent_algorithm_signal_timestamp.assert_called_once_with(
        DATABASE_SYMBOL,
        STRATEGY_NAME,
        int(opened.timestamp()),
        api.CHAT_GPT6_BTCUSD_COOLDOWN_MINUTES * 60,
    )
    assert strategy.evaluate.call_count == 2
    assert strategy.evaluate.call_args_list[1].kwargs[
        "last_signal_timestamp"
    ] == previous_signal_open
    storage.save.assert_called_once_with(
        suppressed_result,
        1,
        enqueue_notification=False,
    )


def test_existing_evaluation_prevents_strategy_rerun() -> None:
    """Ponowna dostawa tej samej świecy respektuje trwały marker ewaluacji."""
    opened = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    repository = Mock()
    repository.has_algorithm_evaluation.return_value = True
    state = SimpleNamespace(
        repository=repository,
        chat_gpt6_btcusd_strategy=Mock(),
        chat_gpt6_btcusd_storage=Mock(),
    )
    candle = CandleRecord(
        symbol=DATABASE_SYMBOL,
        datetime=opened,
        open="67000",
        high="67100",
        low="66900",
        close="67000",
        volume=None,
    )

    api._process_chat_gpt6_btcusd_candle(
        create_request(state),
        candle,
        opened + timedelta(minutes=1),
        enqueue_notifications=False,
    )

    repository.fetch_recent_candle_history.assert_not_called()
    assert state.chat_gpt6_btcusd_last_processed_timestamp == opened
