"""Sprawdza format czasu luk świecowych zwracanych klientowi C++."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone, tzinfo
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # noqa: E402


class CandleBackfillTimestampTests(unittest.TestCase):
    """Weryfikuje 19-znakowy kontrakt czasu używany podczas uzupełniania luk."""

    def test_missing_times_use_cpp_compatible_timestamp_format(self) -> None:
        """Zwrócone czasy nie zawierają dodatkowego tekstu po sekundach."""
        fixed_now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)

        class FixedDateTime(datetime):
            """Zwraca stały czas, aby test nie zależał od chwili uruchomienia."""

            @classmethod
            def now(cls, tz: tzinfo | None = None) -> datetime:
                """Zwraca ustalony czas w żądanej strefie."""
                return fixed_now.astimezone(tz) if tz is not None else fixed_now

        repository = app.MarketDataRepository()
        connection = Mock()
        cursor = Mock()
        connection.cursor.return_value = cursor
        cursor.fetchall.return_value = [(datetime(2026, 11, 17, 11, 57), 0)]

        with patch.object(app, "datetime", FixedDateTime), \
                patch.object(app.mysql.connector, "connect", return_value=connection), \
                patch.object(repository, "_is_market_open", return_value=True), \
                patch.object(repository, "_upsert_candle_gaps"):
            has_existing_candles, missing_times, total_missing_count = (
                repository.fetch_missing_candle_times("ES", limit=10)
            )

        self.assertTrue(has_existing_candles)
        self.assertEqual(total_missing_count, 2)
        self.assertEqual(
            missing_times,
            ["2026-11-17 11:58:00", "2026-11-17 11:59:00"],
        )
        self.assertTrue(all(len(timestamp) == 19 for timestamp in missing_times))


class IBKRCandlePersistenceTests(unittest.TestCase):
    """Weryfikuje zapis świec IBKR w osobnej tabeli i czasie warszawskim."""

    def test_ibkr_candle_is_upserted_with_warsaw_timestamp_and_quotes(self) -> None:
        """Zapis obejmuje kwotowania i oba warianty czasu letniego w Polsce."""
        cases = (
            (
                datetime(2026, 1, 15, 12, 34, tzinfo=timezone.utc),
                datetime(2026, 1, 15, 13, 34),
                60,
            ),
            (
                datetime(2026, 7, 15, 12, 34, tzinfo=timezone.utc),
                datetime(2026, 7, 15, 14, 34),
                120,
            ),
        )
        for utc_datetime, expected_local_datetime, expected_offset in cases:
            with self.subTest(utc_datetime=utc_datetime):
                repository = app.MarketDataRepository()
                candle = app.IBKRCandleRecord(
                    symbol="EUR/USD",
                    datetime=utc_datetime,
                    open=Decimal("1.10000"),
                    high=Decimal("1.10100"),
                    low=Decimal("1.09900"),
                    close=Decimal("1.10050"),
                    volume=None,
                    bid=Decimal("1.10040"),
                    ask=Decimal("1.10060"),
                )
                connection = Mock()
                cursor = Mock()
                connection.cursor.return_value = cursor

                with patch.object(
                    app.mysql.connector, "connect", return_value=connection
                ), patch.object(
                    repository, "_is_market_open", return_value=True
                ):
                    self.assertTrue(repository.save_ibkr_candle(candle))

                self.assertEqual(cursor.execute.call_count, 2)
                insert_sql, insert_values = cursor.execute.call_args_list[1].args
                self.assertIn("INSERT INTO candles_IBKR", insert_sql)
                self.assertIn("bid = VALUES(bid)", insert_sql)
                self.assertIn("ask = VALUES(ask)", insert_sql)
                self.assertEqual(insert_values[1], expected_local_datetime)
                self.assertEqual(insert_values[7:9], (
                    Decimal("1.10040"),
                    Decimal("1.10060"),
                ))
                self.assertEqual(insert_values[9], expected_offset)
                connection.commit.assert_called_once()
                connection.close.assert_called_once()

    def test_ibkr_endpoint_only_calls_the_preview_repository_method(self) -> None:
        """Endpoint nie uruchamia algorytmów ani przetwarzania zwykłych świec."""
        candle = app.IBKRCandleRecord(
            symbol="EUR/USD",
            datetime=datetime(2026, 1, 15, 12, 34, tzinfo=timezone.utc),
            open=Decimal("1.10000"),
            high=Decimal("1.10100"),
            low=Decimal("1.09900"),
            close=Decimal("1.10050"),
        )
        request = Mock()
        request.app.state.repository.save_ibkr_candle.return_value = True

        response = app.save_ibkr_candle(candle, request)

        self.assertEqual(response.body, b"1\n")
        request.app.state.repository.save_ibkr_candle.assert_called_once_with(
            candle
        )

    def test_ibkr_batch_deduplicates_before_bulk_upsert(self) -> None:
        """Paczka zapisuje unikalne znaczniki czasu jednym zapytaniem."""
        repository = app.MarketDataRepository()
        candle = app.IBKRCandleRecord(
            symbol="EUR/USD",
            datetime=datetime(2026, 1, 15, 12, 34, tzinfo=timezone.utc),
            open=Decimal("1.10000"),
            high=Decimal("1.10100"),
            low=Decimal("1.09900"),
            close=Decimal("1.10050"),
        )
        duplicate = candle.model_copy(
            update={"close": Decimal("1.10060")}
        )
        connection = Mock()
        cursor = Mock()
        connection.cursor.return_value = cursor

        with patch.object(
            app.mysql.connector, "connect", return_value=connection
        ), patch.object(
            repository, "_is_market_open", return_value=True
        ):
            saved_count = repository.save_ibkr_candles([candle, duplicate])

        self.assertEqual(saved_count, 1)
        self.assertEqual(cursor.executemany.call_count, 2)
        insert_sql, insert_values = cursor.executemany.call_args_list[1].args
        self.assertIn("INSERT INTO candles_IBKR", insert_sql)
        self.assertIn("ON DUPLICATE KEY UPDATE", insert_sql)
        self.assertEqual(len(insert_values), 1)
        self.assertEqual(insert_values[0][5], Decimal("1.10060"))
        connection.commit.assert_called_once()
        connection.close.assert_called_once()

    def test_ibkr_batch_endpoint_uses_the_batch_repository_method(self) -> None:
        """Endpoint paczkowy nie wywołuje logiki przetwarzania strategii."""
        candle = app.IBKRCandleRecord(
            symbol="EUR/USD",
            datetime=datetime(2026, 1, 15, 12, 34, tzinfo=timezone.utc),
            open=Decimal("1.10000"),
            high=Decimal("1.10100"),
            low=Decimal("1.09900"),
            close=Decimal("1.10050"),
        )
        batch = app.IBKRCandleBatch(candles=[candle])
        request = Mock()
        request.app.state.repository.save_ibkr_candles.return_value = 1

        response = app.save_ibkr_candle_batch(batch, request)

        self.assertEqual(response.body, b"1\n")
        request.app.state.repository.save_ibkr_candles.assert_called_once_with(
            [candle]
        )


if __name__ == "__main__":
    unittest.main()
