"""Testy przepływu świec USD/MXN do strategii, bazy i powiadomienia C++."""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import mysql.connector

os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import (  # noqa: E402
    CandleBatch,
    CandleRecord,
    GROK45_USDMXN_STRATEGY_NAME,
    MarketDataRepository,
    acknowledge_usdmxn_notification,
    save_candle,
    save_candle_batch,
    usdmxn_notifications,
)
from fastapi import Request  # noqa: E402

UTC = timezone.utc


def create_request(state: SimpleNamespace) -> Request:
    """Tworzy lokalne żądanie API z atrapami repozytorium i strategii."""
    app = SimpleNamespace(state=state)
    return Request(
        {
            "type": "http",
            "app": app,
            "method": "POST",
            "path": "/api/candles",
            "headers": [],
        }
    )


def live_candle(opened: datetime) -> CandleRecord:
    """Buduje poprawną świecę USD/MXN z czasem otwarcia UTC."""
    return CandleRecord(
        symbol="USD/MXN",
        datetime=opened,
        open="17.0",
        high="17.1",
        low="16.9",
        close="17.05",
        volume=None,
    )


def new_state(opened: datetime, saved: bool = True) -> SimpleNamespace:
    """Przygotowuje atrapy dla jednej nowej świecy zamkniętej."""
    repository = Mock()
    repository._is_market_open.return_value = True
    repository.save_candle.return_value = (0, 1, "", 0)
    repository.has_algorithm_evaluation.return_value = False
    repository.fetch_recent_candle_history.return_value = [
        {
            "timestamp_utc": opened,
            "open": 17.0,
            "high": 17.1,
            "low": 16.9,
            "close": 17.05,
            "volume": None,
        }
    ]
    strategy = Mock()
    strategy.evaluate.return_value = {
        "signal": "NONE",
        "candle_open_epoch": int(opened.timestamp()),
        "signal_parameters": {"reason": "warmup"},
    }
    storage = Mock()
    storage.save.return_value = saved
    grok_strategy = Mock()
    grok_strategy.evaluate.return_value = {
        "signal": "NONE",
        "candle_open_epoch": int(opened.timestamp()),
        "signal_parameters": {"reason": "warmup"},
    }
    grok_storage = Mock()
    grok_storage.save.return_value = saved
    return SimpleNamespace(
        repository=repository,
        strategy_ready=True,
        eurusd_strategy=Mock(),
        grok45_v2_candles=[],
        usdmxn_strategy=strategy,
        usdmxn_storage=storage,
        grok45_usdmxn_strategy=grok_strategy,
        grok45_usdmxn_storage=grok_storage,
    )


class USDMXNIntegrationTests(unittest.TestCase):
    """Sprawdza ewaluacje, trwałe powiadomienia i mapowanie zapisu."""

    def test_new_closed_candle_runs_both_strategies_once_with_notifications(self) -> None:
        """Obie strategie niezależnie oceniają tę samą świecę i używają outboxa."""
        opened = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=2)
        state = new_state(opened)
        request = create_request(state)
        with patch("app.is_monitored_product", return_value=True), patch(
            "app.USDMXN_BEEP_ENABLED", True
        ):
            first = save_candle(live_candle(opened), request)
            second = save_candle(live_candle(opened), request)

        self.assertEqual(first.body, b"0\n0")
        self.assertEqual(second.body, b"0\n0")
        state.usdmxn_strategy.evaluate.assert_called_once()
        state.usdmxn_storage.save.assert_called_once_with(
            state.usdmxn_strategy.evaluate.return_value,
            1,
            enqueue_notification=True,
        )
        state.grok45_usdmxn_strategy.evaluate.assert_called_once()
        state.grok45_usdmxn_storage.save.assert_called_once_with(
            state.grok45_usdmxn_strategy.evaluate.return_value,
            1,
            enqueue_notification=True,
        )
        self.assertEqual(
            state.repository.has_algorithm_evaluation.call_args_list,
            [
                unittest.mock.call(
                    "USD/MXN",
                    "Chat-gpt6/USDMXN/USDMXNStrategyV1",
                    int(opened.timestamp()),
                ),
                unittest.mock.call(
                    "USD/MXN",
                    GROK45_USDMXN_STRATEGY_NAME,
                    int(opened.timestamp()),
                ),
            ],
        )
        state.repository.fetch_recent_candle_history.assert_called_once()

    def test_existing_database_signal_prevents_re_evaluation_after_restart(self) -> None:
        """Trwały source_timestamp blokuje ponowną ewaluację po restarcie API."""
        opened = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=2)
        state = new_state(opened)
        state.repository.has_algorithm_evaluation.return_value = True
        with patch("app.is_monitored_product", return_value=True):
            response = save_candle(
                live_candle(opened),
                create_request(state),
            )
        self.assertEqual(response.body, b"0\n0")
        state.usdmxn_strategy.evaluate.assert_not_called()
        state.usdmxn_storage.save.assert_not_called()
        state.grok45_usdmxn_strategy.evaluate.assert_not_called()
        state.grok45_usdmxn_storage.save.assert_not_called()

    def test_failed_or_disabled_save_does_not_request_a_beep(self) -> None:
        """Brak nowego zapisu nie zwiększa licznika dźwięku odpowiedzi API."""
        opened = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=2)
        state = new_state(opened, saved=False)
        with patch("app.is_monitored_product", return_value=True), patch(
            "app.USDMXN_BEEP_ENABLED", True
        ):
            response = save_candle(
                live_candle(opened),
                create_request(state),
            )
        self.assertEqual(response.body, b"0\n0")
        state.usdmxn_storage.save.assert_called_once()
        state.grok45_usdmxn_storage.save.assert_called_once()

    def test_batch_saves_closed_usd_fx_candles_and_evaluates_strategies(self) -> None:
        """Paczka ocenia niezależne strategie USD/JPY bez dźwięków historycznych."""
        opened = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=2)
        usd_mxn = live_candle(opened)
        usd_jpy = CandleRecord(
            symbol="USD/JPY",
            datetime=opened,
            open="150.0",
            high="150.1",
            low="149.9",
            close="150.05",
            volume=None,
        )
        state = new_state(opened)
        state.repository.save_candles.return_value = (0, 2, "", 0)
        state.repository.fetch_candle_history.return_value = []
        state.repository.fetch_recent_candle_history.side_effect = (
            lambda symbol, _limit, _cutoff: [
                {
                    "timestamp_utc": opened,
                    "open": 17.0 if symbol == "USD/MXN" else 150.0,
                    "high": 17.1 if symbol == "USD/MXN" else 150.1,
                    "low": 16.9 if symbol == "USD/MXN" else 149.9,
                    "close": 17.05 if symbol == "USD/MXN" else 150.05,
                    "volume": None,
                }
            ]
        )
        state.usdjpy_strategy = Mock()
        state.usdjpy_strategy.evaluate.return_value = {
            "signal": "NONE",
            "candle_open_epoch": int(opened.timestamp()),
            "signal_parameters": {"reason": "warmup"},
        }
        state.usdjpy_storage = Mock()
        state.usdjpy_storage.save.return_value = True
        request = create_request(state)
        batch = CandleBatch(candles=[usd_mxn, usd_jpy])

        with patch("app.is_monitored_product", return_value=True), patch(
            "app._run_claude_usdjpy_batch",
            return_value=[
                {
                    "candle_open_epoch": int(opened.timestamp()),
                    "signal": "NONE",
                    "saved": True,
                }
            ],
        ) as claude_usdjpy:
            response = save_candle_batch(batch, request)

        self.assertEqual(response.status_code, 200)
        claude_usdjpy.assert_called_once_with([opened])
        state.usdmxn_strategy.evaluate.assert_called_once()
        state.grok45_usdmxn_strategy.evaluate.assert_called_once()
        state.usdjpy_strategy.evaluate.assert_called_once()
        for storage in (
            state.usdmxn_storage,
            state.grok45_usdmxn_storage,
            state.usdjpy_storage,
        ):
            self.assertFalse(
                storage.save.call_args.kwargs["enqueue_notification"]
            )

    def test_recent_history_reconstructs_warsaw_winter_and_summer_offsets(self) -> None:
        """Odczyt uwzględnia zapisane CET/CEST bez traktowania czasu jako UTC."""
        class FakeCursor:
            def execute(self, query, parameters) -> None:
                self.query = query
                self.parameters = parameters

            def fetchall(self):
                return [
                    (
                        datetime(2026, 7, 5, 12),
                        120,
                        Decimal("17"),
                        Decimal("18"),
                        Decimal("16"),
                        Decimal("17.5"),
                        None,
                    ),
                    (
                        datetime(2026, 1, 5, 11),
                        60,
                        Decimal("17"),
                        Decimal("18"),
                        Decimal("16"),
                        Decimal("17.5"),
                        Decimal("10"),
                    ),
                ]

            def close(self) -> None:
                pass

        class FakeConnection:
            def __init__(self) -> None:
                self.cursor_value = FakeCursor()

            def cursor(self):
                return self.cursor_value

            def close(self) -> None:
                pass

        connection = FakeConnection()
        repository = MarketDataRepository.__new__(MarketDataRepository)
        repository.database_settings = {}
        cutoff = datetime(2026, 7, 5, 10, 0, tzinfo=UTC)
        with patch("app.mysql.connector.connect", return_value=connection):
            candles = repository.fetch_recent_candle_history(
                "USD/MXN", 1200, cutoff
            )
        self.assertEqual(
            [candle["timestamp_utc"] for candle in candles],
            [
                datetime(2026, 1, 5, 10, 0, tzinfo=UTC),
                datetime(2026, 7, 5, 10, 0, tzinfo=UTC),
            ],
        )
        self.assertIsNone(candles[1]["volume"])
        self.assertIn("DATE_SUB", connection.cursor_value.query)
        self.assertEqual(connection.cursor_value.parameters[0], "USD/MXN")

    def test_none_without_signal_record_still_persists_evaluation_marker(self) -> None:
        """NONE może być pominięty w signals, ale blokuje powtórną ewaluację."""
        class FakeCursor:
            rowcount = 1
            lastrowid = 41

            def __init__(self) -> None:
                self.calls = []

            def execute(self, query, parameters) -> None:
                self.calls.append((query, parameters))

            def close(self) -> None:
                pass

        class FakeConnection:
            def __init__(self) -> None:
                self.cursor_value = FakeCursor()
                self.committed = False

            def cursor(self):
                return self.cursor_value

            def commit(self) -> None:
                self.committed = True

            def rollback(self) -> None:
                raise AssertionError("Nieoczekiwany rollback.")

            def close(self) -> None:
                pass

        opened = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
        result = {
            "signal": "NONE",
            "price": 17.05,
            "timestamp": (opened + timedelta(minutes=1)).isoformat(),
            "candle_open_epoch": int(opened.timestamp()),
            "signal_parameters": {"signal": "NONE"},
        }
        connection = FakeConnection()
        repository = MarketDataRepository.__new__(MarketDataRepository)
        repository.database_settings = {}
        with patch("app.mysql.connector.connect", return_value=connection):
            inserted = repository.save_algorithm_signal(
                "USD/MXN",
                "Chat-gpt6/USDMXN/USDMXNStrategyV1",
                result,
                100,
                record_evaluation=True,
                save_signal=False,
            )

        self.assertFalse(inserted)
        self.assertTrue(connection.committed)
        self.assertEqual(len(connection.cursor_value.calls), 1)
        self.assertIn(
            "INSERT IGNORE INTO algorithm_evaluations",
            connection.cursor_value.calls[0][0],
        )

    def test_failed_signal_insert_rolls_back_evaluation_and_outbox(self) -> None:
        """Błąd zapisu sygnału nie pozostawia markera ani powiadomienia."""
        class FakeCursor:
            rowcount = 1
            lastrowid = 41

            def __init__(self) -> None:
                self.calls = 0

            def execute(self, query, parameters) -> None:
                self.calls += 1
                if self.calls == 2:
                    raise mysql.connector.Error("test insert failure")

            def close(self) -> None:
                pass

        class FakeConnection:
            def __init__(self) -> None:
                self.cursor_value = FakeCursor()
                self.committed = False
                self.rolled_back = False

            def cursor(self):
                return self.cursor_value

            def commit(self) -> None:
                self.committed = True

            def rollback(self) -> None:
                self.rolled_back = True

            def close(self) -> None:
                pass

        opened = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
        result = {
            "signal": "LONG",
            "price": 17.05,
            "timestamp": (opened + timedelta(minutes=1)).isoformat(),
            "candle_open_epoch": int(opened.timestamp()),
            "signal_parameters": {"signal": "LONG"},
        }
        connection = FakeConnection()
        repository = MarketDataRepository.__new__(MarketDataRepository)
        repository.database_settings = {}
        with patch("app.mysql.connector.connect", return_value=connection):
            with self.assertRaises(mysql.connector.Error):
                repository.save_algorithm_signal(
                    "USD/MXN",
                    "Chat-gpt6/USDMXN/USDMXNStrategyV1",
                    result,
                    100,
                    record_evaluation=True,
                )

        self.assertFalse(connection.committed)
        self.assertTrue(connection.rolled_back)

    def test_disabled_beep_does_not_enqueue_notification(self) -> None:
        """Wyłączenie dźwięku zachowuje sygnał, ale nie zostawia outboxa."""
        class FakeCursor:
            rowcount = 1
            lastrowid = 41

            def __init__(self) -> None:
                self.calls = []

            def execute(self, query, parameters) -> None:
                self.calls.append(query)

            def close(self) -> None:
                pass

        class FakeConnection:
            def __init__(self) -> None:
                self.cursor_value = FakeCursor()
                self.committed = False

            def cursor(self):
                return self.cursor_value

            def commit(self) -> None:
                self.committed = True

            def rollback(self) -> None:
                raise AssertionError("Nieoczekiwany rollback.")

            def close(self) -> None:
                pass

        opened = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
        result = {
            "signal": "LONG",
            "price": 17.05,
            "timestamp": (opened + timedelta(minutes=1)).isoformat(),
            "candle_open_epoch": int(opened.timestamp()),
            "signal_parameters": {"signal": "LONG"},
        }
        connection = FakeConnection()
        repository = MarketDataRepository.__new__(MarketDataRepository)
        repository.database_settings = {}
        with patch("app.mysql.connector.connect", return_value=connection):
            self.assertTrue(
                repository.save_algorithm_signal(
                    "USD/MXN",
                    "Chat-gpt6/USDMXN/USDMXNStrategyV1",
                    result,
                    100,
                    record_evaluation=True,
                    enqueue_notification=False,
                )
            )

        self.assertTrue(connection.committed)
        self.assertEqual(len(connection.cursor_value.calls), 2)

    def test_evaluation_lookup_checks_marker_and_legacy_signal_rows(self) -> None:
        """Po migracji wykrywane są też sygnały zapisane przed dodaniem outboxa."""
        class FakeCursor:
            def execute(self, query, parameters) -> None:
                self.query = query
                self.parameters = parameters

            def fetchone(self):
                return (1,)

            def close(self) -> None:
                pass

        class FakeConnection:
            def __init__(self) -> None:
                self.cursor_value = FakeCursor()

            def cursor(self):
                return self.cursor_value

            def close(self) -> None:
                pass

        connection = FakeConnection()
        repository = MarketDataRepository.__new__(MarketDataRepository)
        repository.database_settings = {}
        with patch("app.mysql.connector.connect", return_value=connection):
            exists = repository.has_algorithm_evaluation(
                "USD/MXN",
                "Chat-gpt6/USDMXN/USDMXNStrategyV1",
                123,
            )

        self.assertTrue(exists)
        self.assertIn("FROM algorithm_evaluations", connection.cursor_value.query)
        self.assertIn("FROM signals", connection.cursor_value.query)
        self.assertEqual(connection.cursor_value.parameters, (
            "USD/MXN",
            "Chat-gpt6/USDMXN/USDMXNStrategyV1",
            123,
            "USD/MXN",
            "Chat-gpt6/USDMXN/USDMXNStrategyV1",
            123,
        ))

    def test_usdmxn_outbox_returns_pending_ids_and_acknowledges_delivery(self) -> None:
        """C++ odbiera i potwierdza dźwięki niezależnie od strategii USD/MXN."""
        class FakeCursor:
            rowcount = 1

            def __init__(self) -> None:
                self.calls = []

            def execute(self, query, parameters) -> None:
                self.query = query
                self.parameters = parameters
                self.calls.append((query, parameters))

            def fetchall(self):
                return [(11,), (12,)]

            def close(self) -> None:
                pass

        class FakeConnection:
            def __init__(self) -> None:
                self.cursor_value = FakeCursor()

            def cursor(self):
                return self.cursor_value

            def commit(self) -> None:
                pass

            def close(self) -> None:
                pass

        repository = MarketDataRepository.__new__(MarketDataRepository)
        repository.database_settings = {}
        connection = FakeConnection()
        with patch("app.mysql.connector.connect", return_value=connection):
            self.assertEqual(repository.fetch_usdmxn_notification_ids(100), [11, 12])
            self.assertTrue(repository.acknowledge_usdmxn_notification(11))
        fetch_query, fetch_parameters = connection.cursor_value.calls[0]
        self.assertIn("notification_pending = TRUE", fetch_query)
        self.assertNotIn("algorithm_name", fetch_query)
        self.assertEqual(fetch_parameters, ("USD/MXN", 100))
        ack_query, ack_parameters = connection.cursor_value.calls[1]
        self.assertIn("notification_pending = TRUE", ack_query)
        self.assertNotIn("algorithm_name", ack_query)
        self.assertEqual(ack_parameters, (11, "USD/MXN"))

    def test_usdmxn_notification_endpoints_expose_and_ack_pending_id(self) -> None:
        """Endpointy przekazują oczekujące zdarzenie i potwierdzają jego ID."""
        repository = Mock()
        repository.fetch_usdmxn_notification_ids.return_value = [11]
        repository.acknowledge_usdmxn_notification.return_value = True
        request = create_request(SimpleNamespace(repository=repository))

        self.assertEqual(usdmxn_notifications(request, 100), {"ids": [11]})
        self.assertEqual(
            acknowledge_usdmxn_notification(11, request),
            {"acknowledged": True},
        )
        repository.acknowledge_usdmxn_notification.assert_called_once_with(11)

    def test_signal_json_and_timestamp_are_saved_without_truncating_parameters(self) -> None:
        """Istniejący zapis przekazuje kompletny JSON i epokę otwarcia."""
        class FakeCursor:
            rowcount = 1
            lastrowid = 71

            def __init__(self) -> None:
                self.calls = []

            def execute(self, query, parameters) -> None:
                self.calls.append((query, parameters))

            def close(self) -> None:
                pass

        class FakeConnection:
            def __init__(self) -> None:
                self.cursor_value = FakeCursor()
                self.committed = False

            def cursor(self):
                return self.cursor_value

            def commit(self) -> None:
                self.committed = True

            def rollback(self) -> None:
                raise AssertionError("Nieoczekiwany rollback.")

            def close(self) -> None:
                pass

        opened = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
        close_time = opened + timedelta(minutes=1)
        payload = {
            "instrument": "USDMXN",
            "price": 17.05,
            "timestamp": int(opened.timestamp()),
            "signal": "NONE",
            "reason": "warmup",
            "indicators": {"ema9_m1": None},
        }
        result = {
            "signal": "NONE",
            "price": 17.05,
            "timestamp": close_time.astimezone(
                timezone(timedelta(hours=1))
            ).isoformat(),
            "candle_open_epoch": int(opened.timestamp()),
            "signal_parameters": payload,
        }
        connection = FakeConnection()
        repository = MarketDataRepository.__new__(MarketDataRepository)
        repository.database_settings = {}
        with patch("app.mysql.connector.connect", return_value=connection):
            inserted = repository.save_algorithm_signal(
                "USD/MXN",
                "Chat-gpt6/USDMXN/USDMXNStrategyV1",
                result,
                600,
                record_evaluation=True,
            )
        self.assertTrue(inserted)
        self.assertTrue(connection.committed)
        self.assertIn(
            "INSERT IGNORE INTO algorithm_evaluations",
            connection.cursor_value.calls[0][0],
        )
        self.assertIn(
            "INSERT IGNORE INTO signals",
            connection.cursor_value.calls[1][0],
        )
        self.assertIn(
            "UPDATE algorithm_evaluations",
            connection.cursor_value.calls[2][0],
        )
        saved_json = json.loads(connection.cursor_value.calls[1][1][6])
        self.assertEqual(saved_json, payload)
        self.assertEqual(
            connection.cursor_value.calls[1][1][7],
            int(opened.timestamp()),
        )


if __name__ == "__main__":
    unittest.main()
