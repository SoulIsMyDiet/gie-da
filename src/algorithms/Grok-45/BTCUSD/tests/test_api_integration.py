"""Testy przepływu zamkniętych świec BTC/USD do strategii i bazy."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import Request

os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "test")
API_DIRECTORY = Path(__file__).resolve().parents[4] / "database" / "api"
sys.path.insert(0, str(API_DIRECTORY))

import app as api  # noqa: E402
from app import CandleRecord, MarketDataRepository  # noqa: E402
from strategy_context_registry import (  # noqa: E402
    load_strategy_context_registry,
)
from grok45_btcusd.config import (  # noqa: E402
    COOLDOWN_SECONDS,
    DATABASE_SYMBOL,
    STRATEGY_NAME,
)

UTC = timezone.utc


def create_request(state: SimpleNamespace) -> Request:
    """Tworzy żądanie API z atrapami repozytorium i strategii."""
    app_instance = SimpleNamespace(state=state)
    return Request(
        {
            "type": "http",
            "app": app_instance,
            "method": "POST",
            "path": "/api/candles",
            "headers": [],
        }
    )


def test_context_registry_discovers_btcusd_strategy_context() -> None:
    """Kontekst BTCUSD jest dostępny dla rejestru używanego przy weryfikacji AI."""
    registry = load_strategy_context_registry(API_DIRECTORY)

    assert registry[STRATEGY_NAME] == api.btcusd_package.STRATEGY_CONTEXT


def test_closed_btc_candle_runs_strategy_with_persistent_cooldown() -> None:
    """Nowa zamknięta świeca pobiera historię i poprzedni sygnał z repozytorium."""
    opened = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    closed = opened + timedelta(minutes=1)
    previous_signal_open = int(opened.timestamp()) - 180
    history = [
        {
            "timestamp_utc": opened,
            "open": 67_000.0,
            "high": 67_100.0,
            "low": 66_900.0,
            "close": 67_050.0,
            "volume": 100.0,
        }
    ]
    candidate_result = {
        "signal": "LONG",
        "candle_open_epoch": int(opened.timestamp()),
        "signal_parameters": {"signal": "LONG"},
    }
    result = {
        "signal": "NONE",
        "candle_open_epoch": int(opened.timestamp()),
        "signal_parameters": {"signal": "NONE"},
    }
    repository = Mock()
    repository._is_market_open.return_value = True
    repository.has_algorithm_evaluation.return_value = False
    repository.fetch_recent_candle_history.return_value = history
    repository.fetch_recent_algorithm_signal_timestamp.return_value = (
        previous_signal_open
    )
    strategy = Mock()
    strategy.evaluate.side_effect = [candidate_result, result]
    storage = Mock()
    state = SimpleNamespace(
        repository=repository,
        grok45_btcusd_strategy=strategy,
        grok45_btcusd_storage=storage,
        grok45_btcusd_last_processed_timestamp=None,
    )
    candle = CandleRecord(
        symbol=DATABASE_SYMBOL,
        datetime=opened,
        open="67000",
        high="67100",
        low="66900",
        close="67050",
        volume="100",
    )

    api._process_closed_strategy_candle(
        create_request(state),
        candle,
        closed,
        enqueue_notifications=True,
    )

    repository.fetch_recent_algorithm_signal_timestamp.assert_called_once_with(
        DATABASE_SYMBOL,
        STRATEGY_NAME,
        int(opened.timestamp()),
        COOLDOWN_SECONDS,
    )
    assert strategy.evaluate.call_args_list == [
        ((history,), {"now": closed}),
        (
            (history,),
            {
                "now": closed,
                "last_signal_timestamp": previous_signal_open,
            },
        ),
    ]
    storage.save.assert_called_once_with(
        result,
        len(history),
        enqueue_notification=False,
    )
    assert state.grok45_btcusd_last_processed_timestamp == opened


def test_repository_persists_none_signal_when_requested() -> None:
    """NONE trafia do tabeli signals bez spreadu i bez powiadomienia."""
    class FakeCursor:
        """Przechowuje zapytania testowanego repozytorium."""

        def __init__(self) -> None:
            """Tworzy pusty zapis zapytań."""
            self.calls = []
            self.rowcount = 0
            self.lastrowid = None

        def execute(self, query, parameters) -> None:
            """Symuluje utworzenie ewaluacji i sygnału."""
            self.calls.append((query, parameters))
            if "INSERT IGNORE INTO algorithm_evaluations" in query:
                self.rowcount = 1
                self.lastrowid = 41
            elif "INSERT IGNORE INTO signals" in query:
                self.rowcount = 1
                self.lastrowid = 42

        def close(self) -> None:
            """Udaje zamknięcie kursora."""
            pass

    class FakeConnection:
        """Udaje transakcję bazy danych."""

        def __init__(self) -> None:
            """Tworzy atrapę kursora i stan zatwierdzenia."""
            self.cursor_instance = FakeCursor()
            self.committed = False

        def cursor(self):
            """Zwraca atrapę kursora."""
            return self.cursor_instance

        def commit(self) -> None:
            """Oznacza transakcję jako zatwierdzoną."""
            self.committed = True

        def rollback(self) -> None:
            """Zgłasza błąd, jeśli test niespodziewanie wycofa transakcję."""
            raise AssertionError("Nieoczekiwany rollback.")

        def close(self) -> None:
            """Udaje zamknięcie połączenia."""
            pass

    opened = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    connection = FakeConnection()
    repository = MarketDataRepository.__new__(MarketDataRepository)
    repository.database_settings = {}
    payload = {
        "price": 67_000.0,
        "signal": "NONE",
        "timestamp": int((opened + timedelta(minutes=1)).timestamp()),
        "instrument": "BTCUSD",
        "reason": "Brak wybicia.",
    }
    result = {
        "signal": "NONE",
        "price": payload["price"],
        "timestamp": (opened + timedelta(minutes=1)).isoformat(),
        "candle_open_epoch": int(opened.timestamp()),
        "signal_parameters": payload,
    }

    with patch("app.mysql.connector.connect", return_value=connection):
        inserted = repository.save_algorithm_signal(
            DATABASE_SYMBOL,
            STRATEGY_NAME,
            result,
            60,
            record_evaluation=True,
            save_signal=True,
            save_none_signal=True,
            save_signal_potential=False,
            enqueue_notification=False,
        )

    assert inserted
    assert connection.committed
    assert len(connection.cursor_instance.calls) == 2
    assert "INSERT IGNORE INTO signals" in connection.cursor_instance.calls[1][0]
    saved_payload = json.loads(connection.cursor_instance.calls[1][1][6])
    assert saved_payload == payload


def test_repository_saves_btc_signal_with_strategy_spread() -> None:
    """Repozytorium zapisuje hipotetyczny spread przekazany przez strategię."""
    cursor = Mock()
    cursor.rowcount = 1
    cursor.lastrowid = 42
    connection = Mock()
    connection.cursor.return_value = cursor
    repository = MarketDataRepository.__new__(MarketDataRepository)
    repository.database_settings = {}
    opened = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    result = {
        "signal": "LONG",
        "price": 67_000.0,
        "timestamp": (opened + timedelta(minutes=1)).isoformat(),
        "candle_open_epoch": int(opened.timestamp()),
        "signal_parameters": {
            "price": 67_000.0,
            "signal": "LONG",
            "timestamp": int((opened + timedelta(minutes=1)).timestamp()),
            "instrument": "BTCUSD",
            "reason": "Wybicie wzrostowe.",
        },
    }

    with (
        patch("app.mysql.connector.connect", return_value=connection),
        patch.object(
            api.SignalPotentialCalculator,
            "spread_for_product",
            side_effect=AssertionError("Nie należy zakładać spreadu BTC."),
        ) as spread_lookup,
    ):
        inserted = repository.save_algorithm_signal(
            DATABASE_SYMBOL,
            STRATEGY_NAME,
            result,
            60,
            record_evaluation=True,
            save_signal=True,
            save_signal_potential=True,
            enqueue_notification=False,
            spread_price=Decimal("134.00"),
        )

    assert inserted
    spread_lookup.assert_not_called()
    assert cursor.execute.call_count == 3
    assert "INSERT INTO signal_potention" in cursor.execute.call_args_list[2].args[0]
    assert cursor.execute.call_args_list[2].args[1] == (42, Decimal("134.00"))


def test_repository_queries_only_recent_directional_signals() -> None:
    """Cooldown odczytuje ostatni sygnał kierunkowy w ograniczonym oknie."""
    class FakeCursor:
        """Przechowuje ostatnie zapytanie do sprawdzenia parametrów."""

        def __init__(self) -> None:
            """Tworzy pustą atrapę zapytania."""
            self.call = None

        def execute(self, query, parameters) -> None:
            """Zapisuje zapytanie i jego bezpiecznie parametryzowane wartości."""
            self.call = (query, parameters)

        def fetchone(self):
            """Zwraca przykładowy timestamp sygnału."""
            return (1_000,)

        def close(self) -> None:
            """Udaje zamknięcie kursora."""
            pass

    class FakeConnection:
        """Udaje połączenie z bazą dla odczytu cooldownu."""

        def __init__(self) -> None:
            """Tworzy atrapę kursora."""
            self.cursor_instance = FakeCursor()

        def cursor(self):
            """Zwraca atrapę kursora."""
            return self.cursor_instance

        def close(self) -> None:
            """Udaje zamknięcie połączenia."""
            pass

    connection = FakeConnection()
    repository = MarketDataRepository.__new__(MarketDataRepository)
    repository.database_settings = {}

    with patch("app.mysql.connector.connect", return_value=connection):
        timestamp = repository.fetch_recent_algorithm_signal_timestamp(
            DATABASE_SYMBOL,
            STRATEGY_NAME,
            1_180,
            COOLDOWN_SECONDS,
        )

    assert timestamp == 1_000
    query, parameters = connection.cursor_instance.call
    assert "`signal` IN ('long', 'short')" in query
    assert parameters == (
        DATABASE_SYMBOL,
        STRATEGY_NAME,
        1_180 - COOLDOWN_SECONDS,
        1_180,
    )
