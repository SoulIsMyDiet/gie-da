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

    def test_spread_uses_the_exact_ibkr_bar_for_the_signal_open_time(self) -> None:
        """Oblicza ask-bid z dokładnego znacznika źródłowej świecy UTC."""
        repository = app.MarketDataRepository()
        opened_at_utc = datetime(
            2026, 1, 15, 12, 34, tzinfo=timezone.utc
        )
        connection = Mock()
        cursor = Mock()
        connection.cursor.return_value = cursor
        cursor.fetchall.return_value = [
            (Decimal("1.10040"), Decimal("1.10060"))
        ]

        with patch(
            "app.mysql.connector.connect", return_value=connection
        ):
            spread = repository.fetch_ibkr_spread(
                "EUR/USD", int(opened_at_utc.timestamp())
            )

        self.assertEqual(spread, Decimal("0.00020"))
        query, parameters = cursor.execute.call_args.args
        self.assertIn("FROM candles_IBKR", query)
        self.assertIn("DATE_SUB", query)
        self.assertEqual(
            parameters, ("EUR/USD", datetime(2026, 1, 15, 12, 34))
        )

    def test_spread_returns_none_when_source_quotes_are_missing(self) -> None:
        """Brak świecy dokładnego czasu oznacza nieudane sprawdzenie sygnału."""
        repository = app.MarketDataRepository()
        connection = Mock()
        cursor = Mock()
        connection.cursor.return_value = cursor
        cursor.fetchall.return_value = []

        with patch(
            "app.mysql.connector.connect", return_value=connection
        ):
            spread = repository.fetch_ibkr_spread("EUR/USD", 1_800_000_000)

        self.assertIsNone(spread)

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
                ), patch.object(
                    repository.signal_potential_calculator, "update_pending"
                ), patch("app.TransactionTracker") as transaction_tracker:
                    self.assertEqual(
                        repository.save_ibkr_candle(candle), (True, 0)
                    )

                insert_sql, insert_values = next(
                    call.args
                    for call in cursor.execute.call_args_list
                    if "INSERT INTO candles_IBKR" in call.args[0]
                )
                self.assertIn("INSERT INTO candles_IBKR", insert_sql)
                self.assertIn("bid = VALUES(bid)", insert_sql)
                self.assertIn("ask = VALUES(ask)", insert_sql)
                self.assertEqual(insert_values[1], expected_local_datetime)
                self.assertEqual(insert_values[7:9], (
                    Decimal("1.10040"),
                    Decimal("1.10060"),
                ))
                self.assertEqual(insert_values[9], expected_offset)
                transaction_tracker.return_value.process_candles.assert_called_once_with(
                    cursor, [candle]
                )
                connection.commit.assert_called_once()
                connection.close.assert_called_once()

    def test_ibkr_endpoint_runs_the_live_strategy_pipeline(self) -> None:
        """Endpoint IBKR przekazuje zamkniętą świecę do logiki strategii."""
        candle = app.IBKRCandleRecord(
            symbol="EUR/USD",
            datetime=datetime(2026, 1, 15, 12, 34, tzinfo=timezone.utc),
            open=Decimal("1.10000"),
            high=Decimal("1.10100"),
            low=Decimal("1.09900"),
            close=Decimal("1.10050"),
        )
        request = Mock()
        expected_response = app.Response(content="1\n0\n")

        with patch(
            "app._process_live_ibkr_candle",
            return_value=expected_response,
        ) as process_candle:
            response = app.save_ibkr_candle(candle, request)

        self.assertEqual(response.body, b"1\n0\n")
        process_candle.assert_called_once_with(candle, request)

    def test_ibkr_endpoint_accepts_other_monitored_products(self) -> None:
        """Endpoint IBKR pozwala zapisywać pozostałe aktywne instrumenty."""
        candle = app.IBKRCandleRecord(
            symbol="AAPL",
            datetime=datetime(2026, 1, 15, 15, 34, tzinfo=timezone.utc),
            open=Decimal("250.00"),
            high=Decimal("251.00"),
            low=Decimal("249.00"),
            close=Decimal("250.50"),
        )
        request = Mock()

        with patch(
            "app._process_live_ibkr_candle",
            return_value=app.Response(content="1\n0\n"),
        ) as process_candle:
            response = app.save_ibkr_candle(candle, request)

        self.assertEqual(response.body, b"1\n0\n")
        process_candle.assert_called_once_with(candle, request)

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
        ), patch.object(
            repository.signal_potential_calculator, "update_pending"
        ), patch("app.TransactionTracker"):
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


class IBKRCandleReportTests(unittest.TestCase):
    """Weryfikuje stronicowany odczyt świec IBKR."""

    def test_report_reads_the_ibkr_table(self) -> None:
        """Raport pobiera i formatuje wiersze z candles_IBKR."""
        repository = app.MarketDataRepository()
        connection = Mock()
        cursor = Mock()
        connection.cursor.return_value = cursor
        cursor.fetchall.return_value = [
            (
                "EUR/USD",
                datetime(2026, 1, 15, 13, 34),
                "1.10000",
                "1.10100",
                "1.09900",
                "1.10050",
                None,
                None,
                None,
            )
        ]

        with patch.object(
            app.mysql.connector, "connect", return_value=connection
        ):
            rows, has_next_page = repository.fetch_ibkr_candle_page(0)

        query = cursor.execute.call_args.args[0]
        self.assertIn("FROM candles_IBKR", query)
        self.assertIn("CAST(bid AS CHAR)", query)
        self.assertIn("CAST(ask AS CHAR)", query)
        self.assertEqual(
            rows,
            [
                (
                    "EUR/USD",
                    "2026-01-15 13:34:00",
                    "1.10000",
                    "1.10100",
                    "1.09900",
                    "1.10050",
                    "",
                    "",
                    "",
                )
            ],
        )
        self.assertFalse(has_next_page)
        connection.close.assert_called_once()

    def test_report_endpoint_returns_tsv_and_pagination_flag(self) -> None:
        """Endpoint raportu przekazuje stronę IBKR i zwraca TSV."""
        request = Mock()
        request.app.state.repository.fetch_ibkr_candle_page.return_value = (
            [
                (
                    "EUR/USD",
                    "2026-01-15 13:34:00",
                    "1.10000",
                    "1.10100",
                    "1.09900",
                    "1.10050",
                    "",
                    "1.10040",
                    "1.10060",
                )
            ],
            True,
        )

        response = app.ibkr_candle_report(request, page=0)

        self.assertEqual(
            response.body,
            b"1\nEUR/USD\t2026-01-15 13:34:00\t1.10000\t1.10100"
            b"\t1.09900\t1.10050\t\t1.10040\t1.10060",
        )
        request.app.state.repository.fetch_ibkr_candle_page.assert_called_once_with(
            0
        )

    def test_comparison_report_joins_same_symbol_and_local_time_offset(self) -> None:
        """Zestawienie łączy dokładnie odpowiadające sobie rekordy obu tabel."""
        repository = app.MarketDataRepository()
        connection = Mock()
        cursor = Mock()
        connection.cursor.return_value = cursor
        cursor.fetchall.return_value = [
            (
                "EUR/USD",
                datetime(2026, 1, 15, 13, 34),
                "1.10000",
                "1.09900",
                "1.10100",
                "1.10050",
                "1.10010",
                "1.09910",
                "1.10110",
                "1.10060",
            )
        ]

        with patch.object(
            app.mysql.connector, "connect", return_value=connection
        ):
            rows, has_next_page = repository.fetch_candle_comparison_page(0)

        query = cursor.execute.call_args.args[0]
        self.assertIn("INNER JOIN candles_IBKR", query)
        self.assertIn("candles_IBKR.datetime = candles.datetime", query)
        self.assertIn(
            "candles_IBKR.utc_offset_minutes =\n"
            "                            candles.utc_offset_minutes",
            query,
        )
        self.assertEqual(
            rows,
            [
                (
                    "EUR/USD",
                    "2026-01-15 13:34:00",
                    "1.10000",
                    "1.09900",
                    "1.10100",
                    "1.10050",
                    "1.10010",
                    "1.09910",
                    "1.10110",
                    "1.10060",
                )
            ],
        )
        self.assertFalse(has_next_page)
        connection.close.assert_called_once()

    def test_comparison_endpoint_returns_nine_tsv_columns(self) -> None:
        """Pierwsza kolumna łączy instrument z czasem, a pozostałe osiem to ceny."""
        request = Mock()
        request.app.state.repository.fetch_candle_comparison_page.return_value = (
            [
                (
                    "EUR/USD",
                    "2026-01-15 13:34:00",
                    "1.10000",
                    "1.09900",
                    "1.10100",
                    "1.10050",
                    "1.10010",
                    "1.09910",
                    "1.10110",
                    "1.10060",
                )
            ],
            False,
        )

        response = app.candle_comparison_report(request, page=0)

        self.assertEqual(
            response.body,
            b"0\nEUR/USD | 2026-01-15 13:34:00\t1.10000\t1.09900"
            b"\t1.10100\t1.10050\t1.10010\t1.09910\t1.10110\t1.10060",
        )
        request.app.state.repository.fetch_candle_comparison_page.assert_called_once_with(
            0
        )


if __name__ == "__main__":
    unittest.main()
