"""Testy integracji strategii DAX40 z endpointem świec i migracją schematu."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import (  # noqa: E402
    CandleRecord,
    MarketDataRepository,
    _process_live_ibkr_candle as save_candle,
)
from fastapi import Request  # noqa: E402


def create_request(api_state: SimpleNamespace) -> Request:
    """Tworzy żądanie FastAPI powiązane z atrapami zależności endpointu."""
    app = SimpleNamespace(state=api_state)
    return Request(
        {
            "type": "http",
            "app": app,
            "method": "POST",
            "path": "/api/candles/ibkr",
            "headers": [],
        }
    )


def create_candle(candle_time: datetime) -> CandleRecord:
    """Buduje poprawną świecę GER30 z czasem otwarcia w UTC."""
    return CandleRecord(
        symbol="GER30",
        datetime=candle_time,
        open="18000.0",
        high="18002.0",
        low="17999.0",
        close="18001.0",
        volume="100",
    )


class DAX40ApiIntegrationTests(unittest.TestCase):
    """Sprawdza wykonanie algorytmu wyłącznie dla zamkniętej świecy live."""

    def setUp(self) -> None:
        """Symuluje aktywny GER30, aby testować działanie strategii."""
        self.monitored_product_patch = patch(
            "app.is_monitored_product", return_value=True
        )
        self.monitored_product_patch.start()

    def tearDown(self) -> None:
        """Przywraca rzeczywistą listę aktywnych produktów po teście."""
        self.monitored_product_patch.stop()

    def test_chat_and_claude_strategies_count_each_new_signal_record(self) -> None:
        candle_time = (
            datetime.now(timezone.utc).replace(second=0, microsecond=0)
            - timedelta(minutes=2)
        )
        repository = Mock()
        repository._is_market_open.return_value = True
        repository.save_ibkr_candle.return_value = (True, 0)
        history = [
            {
                "timestamp_utc": candle_time,
                "open": 18000.0,
                "high": 18002.0,
                "low": 17999.0,
                "close": 18001.0,
                "volume": 100.0,
            }
        ]
        dax_storage = Mock()
        dax_storage.load_context.return_value = (
            history,
            {"last_signal_time": None, "signals_today": 0},
        )
        dax_storage.save_signal.return_value = True
        dax_strategy = Mock()
        dax_strategy.evaluate.return_value = {"signal": "NONE"}
        chat_storage = Mock()
        chat_storage.save.return_value = True
        chat_strategy = Mock()
        chat_strategy.evaluate.return_value = {"signal": "NONE"}
        state = SimpleNamespace(
            repository=repository,
            strategy_ready=True,
            eurusd_strategy=object(),
            grok45_v2_candles=[],
            dax40_storage=dax_storage,
            dax40_strategy=dax_strategy,
            dax40_last_processed_timestamp=None,
            chat_dax40_storage=chat_storage,
            chat_dax40_strategy=chat_strategy,
            chat_dax40_last_processed_timestamp=None,
        )

        with patch("app.DAX40_BEEP_ENABLED", True), patch(
            "app.CHAT_DAX40_BEEP_ENABLED", True
        ):
            response = save_candle(
                create_candle(candle_time),
                create_request(state),
            )
            duplicate_response = save_candle(
                create_candle(candle_time),
                create_request(state),
            )

        self.assertEqual(response.body, b"1\n2\n")
        self.assertEqual(duplicate_response.body, b"1\n0\n")
        dax_strategy.evaluate.assert_called_once()
        chat_strategy.evaluate.assert_called_once()
        dax_storage.save_signal.assert_called_once_with({"signal": "NONE"}, 1)
        chat_storage.save.assert_called_once_with({"signal": "NONE"}, 1)
    def test_closed_live_candle_persists_signal_and_returns_saved_flag(self) -> None:
        candle_time = (
            datetime.now(timezone.utc).replace(second=0, microsecond=0)
            - timedelta(minutes=2)
        )
        repository = Mock()
        repository._is_market_open.return_value = True
        repository.save_ibkr_candle.return_value = (True, 0)
        history = [
            {
                "timestamp_utc": candle_time,
                "open": 18000.0,
                "high": 18002.0,
                "low": 17999.0,
                "close": 18001.0,
                "volume": 100.0,
            }
        ]
        dax_storage = Mock()
        dax_storage.load_context.return_value = (
            history,
            {"last_signal_time": None, "signals_today": 0},
        )
        dax_storage.save_signal.side_effect = [True, False]
        dax_strategy = Mock()
        dax_strategy.evaluate.return_value = {"signal": "LONG"}
        state = SimpleNamespace(
            repository=repository,
            strategy_ready=True,
            eurusd_strategy=object(),
            grok45_v2_candles=[],
            dax40_storage=dax_storage,
            dax40_strategy=dax_strategy,
        )

        response = save_candle(create_candle(candle_time), create_request(state))
        self.assertEqual(response.body, b"1\n1\n")
        duplicate_response = save_candle(
            create_candle(candle_time), create_request(state)
        )
        self.assertEqual(dax_strategy.evaluate.call_count, 1)
        self.assertEqual(dax_storage.save_signal.call_count, 1)
        dax_storage.save_signal.assert_any_call({"signal": "LONG"}, 1)
        self.assertEqual(duplicate_response.body, b"1\n0\n")

    def test_persisted_none_reports_new_database_insert_for_windows_sound(self) -> None:
        candle_time = (
            datetime.now(timezone.utc).replace(second=0, microsecond=0)
            - timedelta(minutes=2)
        )
        repository = Mock()
        repository._is_market_open.return_value = True
        repository.save_ibkr_candle.return_value = (True, 0)
        storage = Mock()
        storage.load_context.return_value = (
            [
                {
                    "timestamp_utc": candle_time,
                    "open": 18000.0,
                    "high": 18002.0,
                    "low": 17999.0,
                    "close": 18001.0,
                    "volume": 100.0,
                }
            ],
            {"last_signal_time": None, "signals_today": 0},
        )
        storage.save_signal.return_value = True
        strategy = Mock()
        strategy.evaluate.return_value = {"signal": "NONE"}
        state = SimpleNamespace(
            repository=repository,
            strategy_ready=True,
            eurusd_strategy=object(),
            grok45_v2_candles=[],
            dax40_storage=storage,
            dax40_strategy=strategy,
        )

        response = save_candle(create_candle(candle_time), create_request(state))

        self.assertEqual(response.body, b"1\n1\n")
        storage.save_signal.assert_called_once_with({"signal": "NONE"}, 1)

    def test_forming_candle_does_not_invoke_dax_strategy(self) -> None:
        candle_time = (
            datetime.now(timezone.utc).replace(second=0, microsecond=0)
            + timedelta(minutes=5)
        )
        repository = Mock()
        repository._is_market_open.return_value = True
        repository.save_ibkr_candle.return_value = (True, 0)
        dax_storage = Mock()
        state = SimpleNamespace(
            repository=repository,
            strategy_ready=True,
            eurusd_strategy=object(),
            grok45_v2_candles=[],
            dax40_storage=dax_storage,
            dax40_strategy=Mock(),
        )

        response = save_candle(create_candle(candle_time), create_request(state))

        dax_storage.load_context.assert_not_called()
        state.dax40_strategy.evaluate.assert_not_called()
        self.assertEqual(response.body, b"1\n0\n")

    def test_signal_enum_migration_adds_none_to_existing_database(self) -> None:
        class FakeCursor:
            def __init__(self) -> None:
                self.queries: list[str] = []
                self.signal_type = "enum('short','long')"

            def execute(self, query: str) -> None:
                self.queries.append(query)
                if "MODIFY COLUMN `signal` ENUM" in query:
                    self.signal_type = "enum('short','long','none')"

            def fetchone(self) -> tuple[str, str]:
                return ("signal", self.signal_type)

        cursor = FakeCursor()
        repository = MarketDataRepository.__new__(MarketDataRepository)
        repository._migrate_signal_enum(cursor)
        repository._migrate_signal_enum(cursor)

        alter_queries = [
            query
            for query in cursor.queries
            if "MODIFY COLUMN `signal` ENUM" in query
        ]
        self.assertEqual(len(alter_queries), 1)
        self.assertIn("'none'", alter_queries[0])

    def test_algorithm_signal_uses_one_parameterized_transaction(self) -> None:
        class FakeCursor:
            rowcount = 1
            lastrowid = 42

            def __init__(self) -> None:
                self.calls = []

            def execute(self, query, parameters) -> None:
                self.calls.append((query, parameters))

            def close(self) -> None:
                pass

        class FakeConnection:
            def __init__(self) -> None:
                self.cursor_instance = FakeCursor()
                self.committed = False
                self.closed = False

            def cursor(self) -> FakeCursor:
                return self.cursor_instance

            def commit(self) -> None:
                self.committed = True

            def rollback(self) -> None:
                raise AssertionError("Nieoczekiwany rollback.")

            def close(self) -> None:
                self.closed = True

        connection = FakeConnection()
        repository = MarketDataRepository.__new__(MarketDataRepository)
        repository.database_settings = {}
        signal = {
            "signal": "LONG",
            "price": 18001.0,
            "timestamp": "2026-01-12T09:01:00+01:00",
            "candle_open_epoch": 1768204860,
            "signal_parameters": {"reason": "no_breakout"},
        }

        with patch("app.mysql.connector.connect", return_value=connection) as connect:
            inserted = repository.save_algorithm_signal(
                "GER30",
                "Chat-gpt6/DAX40StrategyV1",
                signal,
                100,
            )

        self.assertTrue(inserted)
        connect.assert_called_once_with(**repository.database_settings)
        signal_query, signal_parameters = connection.cursor_instance.calls[0]
        potential_query, potential_parameters = connection.cursor_instance.calls[1]
        self.assertIn("INSERT IGNORE INTO signals", signal_query)
        self.assertIn("%s", signal_query)
        self.assertEqual(signal_parameters[1], "GER30")
        self.assertEqual(signal_parameters[3], "long")
        self.assertEqual(
            signal_parameters[4],
            "Chat-gpt6/DAX40StrategyV1",
        )
        self.assertEqual(
            signal_parameters[6],
            '{"reason":"no_breakout"}',
        )
        self.assertIn("INSERT INTO signal_potention", potential_query)
        self.assertEqual(potential_parameters[1], Decimal("1.50000"))
        self.assertTrue(connection.committed)
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
