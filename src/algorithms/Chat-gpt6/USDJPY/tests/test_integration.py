"""Testy wyzwalania strategii przez API i istniejącego outboxa dźwięku."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_PORT", "3306")
os.environ.setdefault("MYSQL_DATABASE", "test")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "database" / "api"))

from app import (  # noqa: E402
    CandleRecord,
    MarketDataRepository,
    acknowledge_usdjpy_notification,
    save_candle,
    usdjpy_notifications,
)
from fastapi import Request  # noqa: E402

UTC = timezone.utc


def _request(state: SimpleNamespace, path: str = "/api/candles") -> Request:
    """Tworzy żądanie ASGI korzystające ze wskazanego stanu atrap."""
    app = SimpleNamespace(state=state)
    return Request(
        {
            "type": "http",
            "app": app,
            "method": "POST",
            "path": path,
            "headers": [],
        }
    )


def _candle(opened: datetime) -> CandleRecord:
    """Tworzy świecę USD/JPY z czasem otwarcia w UTC."""
    return CandleRecord(
        symbol="USD/JPY",
        datetime=opened,
        open="154.200",
        high="154.250",
        low="154.180",
        close="154.235",
        volume="12",
    )


class USDJPYIntegrationTests(unittest.TestCase):
    """Sprawdza uruchomienie raz na świecę oraz powiadomienia po zapisie."""

    def test_api_evaluates_a_closed_candle_only_once(self) -> None:
        opened = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(
            minutes=3
        )
        repository = Mock()
        repository._is_market_open.return_value = True
        repository.save_candle.return_value = (0, 1, "", 0)
        repository.has_algorithm_evaluation.return_value = False
        repository.fetch_recent_candle_history.return_value = [
            {
                "timestamp_utc": opened,
                "open": 154.2,
                "high": 154.25,
                "low": 154.18,
                "close": 154.235,
                "volume": 12.0,
            }
        ]
        strategy = Mock()
        strategy.evaluate.return_value = {
            "signal": "NONE",
            "signal_parameters": {"signal": "NONE"},
        }
        storage = Mock()
        storage.save.return_value = False
        state = SimpleNamespace(
            repository=repository,
            strategy_ready=True,
            eurusd_strategy=Mock(),
            grok45_v2_candles=[],
            usdjpy_strategy=strategy,
            usdjpy_storage=storage,
        )
        request = _request(state)

        save_candle(_candle(opened), request)
        save_candle(_candle(opened), request)

        strategy.evaluate.assert_called_once()
        storage.save.assert_called_once()
        self.assertEqual(
            repository.has_algorithm_evaluation.call_args.args,
            (
                "USD/JPY",
                "Chat-gpt6/USDJPYStrategyV1",
                int(opened.timestamp()),
            ),
        )
        self.assertTrue(
            storage.save.call_args.kwargs["enqueue_notification"]
        )

    def test_existing_evaluation_marker_prevents_another_strategy_call(self) -> None:
        opened = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(
            minutes=3
        )
        repository = Mock()
        repository._is_market_open.return_value = True
        repository.save_candle.return_value = (0, 1, "", 0)
        repository.has_algorithm_evaluation.return_value = True
        strategy = Mock()
        storage = Mock()
        state = SimpleNamespace(
            repository=repository,
            strategy_ready=True,
            eurusd_strategy=Mock(),
            grok45_v2_candles=[],
            usdjpy_strategy=strategy,
            usdjpy_storage=storage,
        )

        save_candle(_candle(opened), _request(state))

        strategy.evaluate.assert_not_called()
        storage.save.assert_not_called()
        self.assertEqual(
            state.usdjpy_last_processed_timestamp,
            opened,
        )

    def test_notification_routes_fetch_and_acknowledge_usdjpy_outbox(self) -> None:
        repository = Mock()
        repository.fetch_usdjpy_notification_ids.return_value = [17, 18]
        repository.acknowledge_usdjpy_notification.return_value = True
        request = _request(SimpleNamespace(repository=repository))

        self.assertEqual(usdjpy_notifications(request, limit=100), {"ids": [17, 18]})
        self.assertEqual(
            acknowledge_usdjpy_notification(17, request),
            {"acknowledged": True},
        )
        repository.acknowledge_usdjpy_notification.assert_called_once_with(17)

    def test_repository_outbox_is_scoped_to_usdjpy_product(self) -> None:
        class FakeCursor:
            rowcount = 1

            def __init__(self) -> None:
                self.calls = []

            def execute(self, query, parameters) -> None:
                self.calls.append((query, parameters))

            def fetchall(self):
                return [(17,), (18,)]

            def close(self) -> None:
                pass

        class FakeConnection:
            def __init__(self) -> None:
                self.cursor_value = FakeCursor()
                self.committed = False

            def cursor(self):
                return self.cursor_value

            def fetchone(self):
                return None

            def commit(self) -> None:
                self.committed = True

            def rollback(self) -> None:
                raise AssertionError("Nieoczekiwany rollback.")

            def close(self) -> None:
                pass

        fetch_connection = FakeConnection()
        ack_connection = FakeConnection()
        repository = MarketDataRepository.__new__(MarketDataRepository)
        repository.database_settings = {}
        with patch(
            "app.mysql.connector.connect",
            side_effect=[fetch_connection, ack_connection],
        ):
            self.assertEqual(
                repository.fetch_usdjpy_notification_ids(100),
                [17, 18],
            )
            self.assertTrue(repository.acknowledge_usdjpy_notification(17))

        fetch_query, fetch_parameters = fetch_connection.cursor_value.calls[0]
        ack_query, ack_parameters = ack_connection.cursor_value.calls[0]
        self.assertIn("notification_pending = TRUE", fetch_query)
        self.assertEqual(fetch_parameters, ("USD/JPY", 100))
        self.assertEqual(ack_parameters, (17, "USD/JPY"))
        self.assertTrue(ack_connection.committed)


if __name__ == "__main__":
    unittest.main()
