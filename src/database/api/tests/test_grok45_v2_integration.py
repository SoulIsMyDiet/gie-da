"""Sprawdza połączenie Grok V2 z formatem sygnału zapisywanym przez API."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "test")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import (  # noqa: E402
    GROK45_V2_STRATEGY_NAME,
    CandleRecord,
    Grok45SignalRecord,
    MarketDataRepository,
    _generate_grok45_v2_signal,
    _grok45_candles_from_history,
    save_candle,
)
from fastapi import Request  # noqa: E402
from EURUSDStrategyV2 import Candle  # noqa: E402
from market_data import Candle as ApiCandle  # noqa: E402


def create_market_candles(trend_per_candle: float) -> list[Candle]:
    """Tworzy deterministyczne świece testowe dla wybranego kierunku rynku."""
    candles = []
    random_state = 75
    close = 1.08
    for index in range(100):
        random_state = (random_state * 1664525 + 1013904223) & 0xFFFFFFFF
        random_fraction = (random_state >> 8) / 16777216.0
        next_close = close + trend_per_candle + (
            random_fraction - 0.5
        ) * 0.00018
        open_price = close
        candles.append(
            Candle(
                timestamp=index * 60,
                open=open_price,
                high=max(open_price, next_close) + 0.00005,
                low=min(open_price, next_close) - 0.00005,
                close=next_close,
            )
        )
        close = next_close
    return candles


class FakeCursor:
    """Zbiera zapytania bez nawiązywania połączenia z bazą MySQL."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple[object, ...]]] = []
        self.rowcount = 0
        self.lastrowid = 42

    def execute(self, query: str, parameters: tuple[object, ...] = ()) -> None:
        self.queries.append((query, parameters))
        self.rowcount = 1

    def fetchone(self) -> tuple[int]:
        return (100,)


class Grok45V2IntegrationTests(unittest.TestCase):
    """Sprawdza generowanie sygnału i nazwę algorytmu zapisywaną w tabeli."""

    def test_strategy_signal_is_converted_to_api_record(self) -> None:
        signal = _generate_grok45_v2_signal(
            create_market_candles(0.000025)
        )

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.signal, "LONG")
        self.assertEqual(signal.timestamp, 5940)
        self.assertEqual(signal.instrument, "EURUSD/USD")

    def test_history_warmup_excludes_the_current_open_candle(self) -> None:
        current_minute = datetime.now(timezone.utc).replace(
            second=0, microsecond=0
        )
        history = [
            ApiCandle(
                timestamp=current_minute - timedelta(minutes=5),
                open=1.08,
                high=1.081,
                low=1.079,
                close=1.08,
            ),
            ApiCandle(
                timestamp=current_minute + timedelta(minutes=5),
                open=1.08,
                high=1.081,
                low=1.079,
                close=1.08,
            ),
        ]

        grok_history = _grok45_candles_from_history(history)

        self.assertEqual(len(grok_history), 1)
        self.assertEqual(grok_history[0].timestamp, int(history[0].timestamp.timestamp()))

    def test_signal_insert_keeps_python_and_cpp_algorithms_distinct(self) -> None:
        signal = Grok45SignalRecord(
            instrument="EURUSD/USD",
            price="1.08",
            timestamp=5940,
            signal="LONG",
            parameters={"ema9": 1.08},
        )
        repository = MarketDataRepository.__new__(MarketDataRepository)

        for expected_name in (GROK45_V2_STRATEGY_NAME, "Grok-45"):
            cursor = FakeCursor()
            was_inserted = repository._insert_grok45_signal(
                cursor,
                signal,
                algorithm_name=expected_name,
            )

            signal_insert = next(
                query for query in cursor.queries if "INSERT INTO signals" in query[0]
            )
            self.assertTrue(was_inserted)
            self.assertEqual(signal_insert[1][4], expected_name)

    def test_api_saves_chat_and_python_grok_signals_from_same_candle(self) -> None:
        candles = create_market_candles(0.000025)
        for candle in candles:
            candle.open = round(candle.open, 10)
            candle.high = round(candle.high, 10)
            candle.low = round(candle.low, 10)
            candle.close = round(candle.close, 10)
        chat_signal = object()

        class FakeChatStrategy:
            """Zwraca sygnał Chat bez zależności od implementacji strategii."""

            def process_candle(self, _: ApiCandle) -> object:
                return chat_signal

            def __deepcopy__(self, memo: dict[int, object]) -> "FakeChatStrategy":
                return self

        repository = Mock()
        repository._is_market_open.return_value = True
        repository.save_candle.return_value = (0, 1, "", True)
        chat_strategy = FakeChatStrategy()
        api_application = SimpleNamespace(
            state=SimpleNamespace(
                repository=repository,
                strategy_ready=True,
                eurusd_strategy=chat_strategy,
                grok45_v2_candles=candles[:-1],
            )
        )
        request = Request(
            {
                "type": "http",
                "app": api_application,
                "method": "POST",
                "path": "/api/candles",
                "headers": [],
            }
        )
        candle = CandleRecord(
            symbol="EURUSD/USD",
            datetime=datetime.fromtimestamp(
                candles[-1].timestamp, timezone.utc
            ),
            open=str(candles[-1].open),
            high=str(candles[-1].high),
            low=str(candles[-1].low),
            close=str(candles[-1].close),
        )

        response = save_candle(candle, request)

        repository.save_candle.assert_called_once()
        saved_candle, saved_chat_signal, saved_grok_signal = (
            repository.save_candle.call_args.args
        )
        self.assertIs(saved_candle, candle)
        self.assertIs(saved_chat_signal, chat_signal)
        self.assertIsInstance(saved_grok_signal, Grok45SignalRecord)
        self.assertEqual(saved_grok_signal.signal, "LONG")
        self.assertEqual(response.body, b"0\n1")

        save_candle(candle, request)

        repeated_grok_signal = repository.save_candle.call_args.args[2]
        self.assertIsNone(repeated_grok_signal)


if __name__ == "__main__":
    unittest.main()
