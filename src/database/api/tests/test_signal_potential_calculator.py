"""Testy jednostkowe obliczania potencjału sygnałów."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signal_potential_calculator import SignalPotentialCalculator  # noqa: E402


class SignalPotentialCalculatorTests(unittest.TestCase):
    """Sprawdza kierunek sald, spread i progi wymaganej liczby świec."""

    def setUp(self) -> None:
        """Tworzy kalkulator używany w testach."""
        self.calculator = SignalPotentialCalculator()
        self.entry_price = Decimal("1.1000")

    def test_long_uses_high_and_keeps_negative_balances(self) -> None:
        """Saldo LONG bazuje na high, odejmuje spread i zachowuje stratę."""
        candles = [
            (Decimal("1.1001"), Decimal("1.0999")),
            (Decimal("1.1005"), Decimal("1.0998")),
        ]

        result = self.calculator.calculate("long", self.entry_price, candles)

        self.assertEqual(result["saldo_1st_candle"], Decimal("-0.0002"))
        self.assertEqual(result["saldo_2nd_candle"], Decimal("0.0002"))
        self.assertIsNone(result["saldo_3rd_candle"])
        self.assertIsNone(result["saldo_10aggr_candles"])
        self.assertIsNone(result["saldo_20_aggr_candles"])

    def test_long_aggregates_highest_high_and_records_candle_number(self) -> None:
        """Salda agregowane LONG wybierają najwyższe high w oknie."""
        candles = [
            (Decimal("1.1001"), Decimal("1.0999"))
            for _ in range(20)
        ]
        candles[12] = (Decimal("1.1020"), Decimal("1.0999"))

        result = self.calculator.calculate("long", self.entry_price, candles)

        self.assertEqual(result["saldo_10aggr_candles"], Decimal("-0.0002"))
        self.assertEqual(result["saldo_20_aggr_candles"], Decimal("0.0017"))
        self.assertEqual(result["max_candle"], 13)
        self.assertEqual(result["100zl_deal"], Decimal("4.63636"))

    def test_short_aggregates_lowest_low(self) -> None:
        """Salda SHORT wybierają najniższe low i odejmują spread."""
        candles = [
            (Decimal("1.1001"), Decimal("1.0999"))
            for _ in range(20)
        ]
        candles[6] = (Decimal("1.1001"), Decimal("1.0980"))

        result = self.calculator.calculate("short", self.entry_price, candles)

        self.assertEqual(result["saldo_1st_candle"], Decimal("-0.0002"))
        self.assertEqual(result["saldo_10aggr_candles"], Decimal("0.0017"))
        self.assertEqual(result["saldo_20_aggr_candles"], Decimal("0.0017"))
        self.assertEqual(result["max_candle"], 7)

    def test_aggregate_fields_remain_empty_until_their_windows_are_complete(self) -> None:
        """Agregaty pozostają puste, dopóki nie wpłynie 10 lub 20 świec."""
        candles = [
            (Decimal("1.1001"), Decimal("1.0999"))
            for _ in range(19)
        ]

        result = self.calculator.calculate("short", self.entry_price, candles)

        self.assertEqual(result["saldo_5th_candle"], Decimal("-0.0002"))
        self.assertEqual(result["saldo_10aggr_candles"], Decimal("-0.0002"))
        self.assertIsNone(result["saldo_20_aggr_candles"])
        self.assertIsNone(result["max_candle"])
        self.assertIsNone(result["100zl_deal"])
        self.assertIsNone(result["saldo_60_aggr_candles"])
        self.assertIsNone(result["100zl_deal_60"])

    def test_60_candle_window_uses_same_best_candle_and_profit_calculation(self) -> None:
        """Okno 60 świec wybiera ekstremum bez zmiany wyniku dla 20 świec."""
        candles = [
            (Decimal("1.1001"), Decimal("1.0999"))
            for _ in range(60)
        ]
        candles[12] = (Decimal("1.1020"), Decimal("1.0999"))
        candles[42] = (Decimal("1.1030"), Decimal("1.0999"))
        timestamps = [
            datetime(2025, 1, 1) + timedelta(minutes=index)
            for index in range(60)
        ]

        result = self.calculator.calculate(
            "long",
            self.entry_price,
            candles,
            candle_timestamps=timestamps,
        )

        self.assertEqual(result["saldo_20_aggr_candles"], Decimal("0.0017"))
        self.assertEqual(result["max_candle"], 13)
        self.assertEqual(result["100zl_deal"], Decimal("4.63636"))
        self.assertEqual(result["saldo_60_aggr_candles"], Decimal("0.0027"))
        self.assertEqual(result["100zl_deal_60"], Decimal("7.36363"))
        self.assertEqual(
            result["best_candle_60_datetime"], timestamps[42]
        )

    def test_60_candle_window_uses_lowest_low_for_short(self) -> None:
        """Okno 60 świec SHORT wybiera najniższe low."""
        candles = [
            (Decimal("1.1001"), Decimal("1.0999"))
            for _ in range(60)
        ]
        candles[47] = (Decimal("1.1001"), Decimal("1.0980"))
        timestamps = [
            datetime(2025, 1, 1) + timedelta(minutes=index)
            for index in range(60)
        ]

        result = self.calculator.calculate(
            "short",
            self.entry_price,
            candles,
            candle_timestamps=timestamps,
        )

        self.assertEqual(result["saldo_60_aggr_candles"], Decimal("0.0017"))
        self.assertEqual(result["100zl_deal_60"], Decimal("4.63636"))
        self.assertEqual(
            result["best_candle_60_datetime"], timestamps[47]
        )

    def test_rejects_invalid_direction_and_entry_price(self) -> None:
        """Nieprawidłowy kierunek lub cena wejścia powodują jawny błąd."""
        with self.assertRaises(ValueError):
            self.calculator.calculate("hold", self.entry_price, [])
        with self.assertRaises(ValueError):
            self.calculator.calculate("long", Decimal("0"), [])
        with self.assertRaises(ValueError):
            self.calculator.calculate(
                "long", self.entry_price, [], Decimal("-0.0001")
            )

    def test_uses_spread_stored_for_signal(self) -> None:
        """Kalkulator używa spreadu przekazanego z wiersza sygnału."""
        result = self.calculator.calculate(
            "long",
            self.entry_price,
            [(Decimal("1.1005"), Decimal("1.0998"))],
            Decimal("0.0005"),
        )

        self.assertEqual(result["saldo_1st_candle"], Decimal("0.0000"))

    def test_default_spreads_are_specific_to_each_product(self) -> None:
        """Każdy instrument zwraca własny spread w jednostkach swojej ceny."""
        self.assertEqual(
            self.calculator.spread_for_product("EUR/USD"),
            Decimal("0.00030"),
        )
        self.assertEqual(
            self.calculator.spread_for_product("USD/MXN"),
            Decimal("0.01000"),
        )
        self.assertEqual(
            self.calculator.spread_for_product("USD/JPY"),
            Decimal("0.03000"),
        )
        self.assertEqual(
            self.calculator.spread_for_product("XAU/USD"),
            Decimal("0.30000"),
        )
        with self.assertRaises(ValueError):
            self.calculator.spread_for_product("UNKNOWN")

    def test_reads_unix_timestamp_from_signal_parameters(self) -> None:
        """Kalkulator obsługuje epokę Unix zapisywaną przez strategie."""
        signal_time = self.calculator._signal_timestamp(
            {"timestamp": 1735689600}
        )

        self.assertEqual(signal_time, datetime(2025, 1, 1))

    def test_reads_timezone_aware_iso_timestamp_from_signal_parameters(self) -> None:
        """Kalkulator nadal obsługuje wcześniejszy format ISO 8601."""
        signal_time = self.calculator._signal_timestamp(
            {"timestamp": "2025-01-01T03:00:00+03:00"}
        )

        self.assertEqual(signal_time, datetime(2025, 1, 1))

    def test_rejects_boolean_timestamp(self) -> None:
        """Wartość logiczna nie jest mylona z liczbą sekund Unix."""
        with self.assertRaises(ValueError):
            self.calculator._signal_timestamp({"timestamp": True})

    def test_truncates_balance_values_to_five_decimal_places(self) -> None:
        """Wyniki są obcinane do pięciu miejsc bez zaokrąglania."""
        result = self.calculator.calculate(
            "long",
            self.entry_price,
            [(Decimal("1.100423456"), Decimal("1.0998"))],
        )

        self.assertEqual(result["saldo_1st_candle"], Decimal("0.00012"))

        negative_result = self.calculator.calculate(
            "long",
            self.entry_price,
            [(Decimal("1.1000"), Decimal("1.0998"))],
            Decimal("0.000345"),
        )
        self.assertEqual(negative_result["saldo_1st_candle"], Decimal("-0.00034"))


if __name__ == "__main__":
    unittest.main()
