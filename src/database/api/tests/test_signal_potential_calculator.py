"""Testy jednostkowe obliczania potencjału sygnałów."""

from __future__ import annotations

import sys
import unittest
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
        self.assertEqual(
            result["100zl_deal"],
            Decimal("100") * Decimal("30") * Decimal("0.0017")
            / self.entry_price,
        )

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

    def test_rejects_invalid_direction_and_entry_price(self) -> None:
        """Nieprawidłowy kierunek lub cena wejścia powodują jawny błąd."""
        with self.assertRaises(ValueError):
            self.calculator.calculate("hold", self.entry_price, [])
        with self.assertRaises(ValueError):
            self.calculator.calculate("long", Decimal("0"), [])


if __name__ == "__main__":
    unittest.main()
