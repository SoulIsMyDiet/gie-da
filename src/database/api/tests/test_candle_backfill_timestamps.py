"""Sprawdza format czasu luk świecowych zwracanych klientowi C++."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone, tzinfo
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


if __name__ == "__main__":
    unittest.main()
