"""Sprawdza kalendarze sesji i aktywną listę produktów."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_products import (  # noqa: E402
    MARKET_PROFILES,
    get_monitored_symbols,
    get_open_symbols,
    is_market_open,
)


class MarketProductsTests(unittest.TestCase):
    """Weryfikuje sesje Forex, rynku akcji USA i rynku krypto."""

    def test_forex_session_opens_sunday_and_closes_friday_in_new_york_time(self) -> None:
        before_sunday_open = datetime(2026, 11, 15, 21, 59, tzinfo=timezone.utc)
        sunday_open = datetime(2026, 11, 15, 22, 0, tzinfo=timezone.utc)
        before_friday_close = datetime(2026, 11, 20, 21, 59, tzinfo=timezone.utc)
        friday_close = datetime(2026, 11, 20, 22, 0, tzinfo=timezone.utc)

        self.assertFalse(is_market_open("EURUSD/USD", before_sunday_open))
        self.assertTrue(is_market_open("EURUSD/USD", sunday_open))
        self.assertTrue(is_market_open("EURUSD/USD", before_friday_close))
        self.assertFalse(is_market_open("EURUSD/USD", friday_close))

    def test_us_equity_profile_respects_daily_exchange_session(self) -> None:
        profile = MARKET_PROFILES["us_equity"]

        self.assertFalse(
            profile.is_open(datetime(2026, 11, 18, 14, 29, tzinfo=timezone.utc))
        )
        self.assertTrue(
            profile.is_open(datetime(2026, 11, 18, 14, 30, tzinfo=timezone.utc))
        )
        self.assertFalse(
            profile.is_open(datetime(2026, 11, 18, 21, 0, tzinfo=timezone.utc))
        )
        self.assertFalse(
            profile.is_open(datetime(2026, 11, 21, 15, 0, tzinfo=timezone.utc))
        )

    def test_crypto_profile_is_open_all_days(self) -> None:
        profile = MARKET_PROFILES["crypto_24_7"]
        timestamps = (
            datetime(2026, 11, 15, 3, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 21, 23, 59, tzinfo=timezone.utc),
        )

        self.assertTrue(all(profile.is_open(timestamp) for timestamp in timestamps))

    def test_only_eurusd_is_active_and_open_symbols_follow_its_schedule(self) -> None:
        self.assertEqual(get_monitored_symbols(), ("EURUSD/USD",))
        self.assertEqual(
            get_open_symbols(datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)),
            ("EURUSD/USD",),
        )
        self.assertEqual(
            get_open_symbols(datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)),
            (),
        )

    def test_unknown_symbols_are_not_treated_as_configured_products(self) -> None:
        with self.assertRaisesRegex(ValueError, "nie jest skonfigurowany"):
            is_market_open(
                "AAPL", datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)
            )


if __name__ == "__main__":
    unittest.main()
