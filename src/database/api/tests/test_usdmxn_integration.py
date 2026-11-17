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

os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import CandleRecord, MarketDataRepository, save_candle  # noqa: E402
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
    repository.has_algorithm_signal.return_value = False
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
    return SimpleNamespace(
        repository=repository,
        strategy_ready=True,
        eurusd_strategy=Mock(),
        grok45_v2_candles=[],
        usdmxn_strategy=strategy,
        usdmxn_storage=storage,
    )


class USDMXNIntegrationTests(unittest.TestCase):
    """Sprawdza idempotencję, mapowanie zapisu i liczbę dźwięków po zapisie."""

    def test_new_closed_candle_is_evaluated_once_and_requests_two_beeps(self) -> None:
        """Nowy rekord zwiększa licznik C++ o jeden, a ponowienie nie zapisuje."""
        opened = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=2)
        state = new_state(opened)
        request = create_request(state)
        with patch("app.is_monitored_product", return_value=True), patch(
            "app.USDMXN_BEEP_ENABLED", True
        ):
            first = save_candle(live_candle(opened), request)
            second = save_candle(live_candle(opened), request)

        self.assertEqual(first.body, b"0\n1")
        self.assertEqual(second.body, b"0\n0")
        state.usdmxn_strategy.evaluate.assert_called_once()
        state.usdmxn_storage.save.assert_called_once()
        state.repository.has_algorithm_signal.assert_called_once_with(
            "USD/MXN",
            "Chat-gpt6/USDMXN/USDMXNStrategyV1",
            int(opened.timestamp()),
        )

    def test_existing_database_signal_prevents_re_evaluation_after_restart(self) -> None:
        """Trwały source_timestamp blokuje ponowną ewaluację po restarcie API."""
        opened = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=2)
        state = new_state(opened)
        state.repository.has_algorithm_signal.return_value = True
        with patch("app.is_monitored_product", return_value=True):
            response = save_candle(
                live_candle(opened),
                create_request(state),
            )
        self.assertEqual(response.body, b"0\n0")
        state.usdmxn_strategy.evaluate.assert_not_called()
        state.usdmxn_storage.save.assert_not_called()

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

    def test_signal_json_and_timestamp_are_saved_without_truncating_parameters(self) -> None:
        """Istniejący zapis przekazuje kompletny JSON i epokę otwarcia."""
        class FakeCursor:
            rowcount = 1

            def execute(self, query, parameters) -> None:
                self.query = query
                self.parameters = parameters

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
            )
        self.assertTrue(inserted)
        self.assertTrue(connection.committed)
        self.assertIn("INSERT IGNORE INTO signals", connection.cursor_value.query)
        saved_json = json.loads(connection.cursor_value.parameters[6])
        self.assertEqual(saved_json, payload)
        self.assertEqual(connection.cursor_value.parameters[7], int(opened.timestamp()))


if __name__ == "__main__":
    unittest.main()
