"""Testy jednostkowe obliczania potencjału sygnałów."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

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

        self.assertEqual(result["saldo1can"], Decimal("-0.0002"))
        self.assertEqual(result["saldo2can"], Decimal("0.0002"))
        self.assertIsNone(result["saldo3can"])
        self.assertIsNone(result["saldo60can"])
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
        self.assertEqual(result["best_candle_20_minute"], 13)
        self.assertEqual(result["100zl_deal_20"], Decimal("4.63636"))

    def test_short_aggregates_lowest_low(self) -> None:
        """Salda SHORT wybierają najniższe low i odejmują spread."""
        candles = [
            (Decimal("1.1001"), Decimal("1.0999"))
            for _ in range(20)
        ]
        candles[6] = (Decimal("1.1001"), Decimal("1.0980"))

        result = self.calculator.calculate("short", self.entry_price, candles)

        self.assertEqual(result["saldo1can"], Decimal("-0.0002"))
        self.assertEqual(result["saldo_10aggr_candles"], Decimal("0.0017"))
        self.assertEqual(result["saldo_20_aggr_candles"], Decimal("0.0017"))
        self.assertEqual(result["best_candle_20_minute"], 7)

    def test_aggregate_fields_remain_empty_until_their_windows_are_complete(self) -> None:
        """Agregaty pozostają puste, dopóki nie wpłynie 10 lub 20 świec."""
        candles = [
            (Decimal("1.1001"), Decimal("1.0999"))
            for _ in range(19)
        ]

        result = self.calculator.calculate("short", self.entry_price, candles)

        self.assertEqual(result["saldo5can"], Decimal("-0.0002"))
        self.assertEqual(result["saldo_10aggr_candles"], Decimal("-0.0002"))
        self.assertIsNone(result["saldo_20_aggr_candles"])
        self.assertIsNone(result["best_candle_20_minute"])
        self.assertIsNone(result["100zl_deal_20"])
        self.assertIsNone(result["saldo_60_aggr_candles"])
        self.assertIsNone(result["best_candle_60_minute"])
        self.assertIsNone(result["100zl_deal_60"])

    def test_calculates_individual_balances_for_all_60_candles(self) -> None:
        """Liczy osobne saldo dla każdej z pierwszych 60 zamkniętych świec."""
        candles = [
            (Decimal("1.1001"), Decimal("1.0999"))
            for _ in range(60)
        ]
        candles[59] = (Decimal("1.1020"), Decimal("1.0999"))

        result = self.calculator.calculate(
            "long", self.entry_price, candles
        )

        self.assertEqual(result["saldo1can"], Decimal("-0.0002"))
        self.assertEqual(result["saldo60can"], Decimal("0.0017"))
        self.assertEqual(
            [
                key
                for key in result
                if key.startswith("saldo") and key.endswith("can")
            ],
            [f"saldo{candle_number}can" for candle_number in range(1, 61)],
        )

    def test_60_candle_window_records_best_candle_number(self) -> None:
        """Okno 60 świec zapisuje numer świecy z najlepszym ekstremum."""
        candles = [
            (Decimal("1.1001"), Decimal("1.0999"))
            for _ in range(60)
        ]
        candles[12] = (Decimal("1.1020"), Decimal("1.0999"))
        candles[42] = (Decimal("1.1030"), Decimal("1.0999"))

        result = self.calculator.calculate(
            "long",
            self.entry_price,
            candles,
        )

        self.assertEqual(result["saldo_20_aggr_candles"], Decimal("0.0017"))
        self.assertEqual(result["best_candle_20_minute"], 13)
        self.assertEqual(result["100zl_deal_20"], Decimal("4.63636"))
        self.assertEqual(result["saldo_60_aggr_candles"], Decimal("0.0027"))
        self.assertEqual(result["best_candle_60_minute"], 43)
        self.assertEqual(result["100zl_deal_60"], Decimal("7.36363"))

    def test_60_candle_window_uses_lowest_low_for_short(self) -> None:
        """Okno 60 świec SHORT wybiera najniższe low."""
        candles = [
            (Decimal("1.1001"), Decimal("1.0999"))
            for _ in range(60)
        ]
        candles[47] = (Decimal("1.1001"), Decimal("1.0980"))
        result = self.calculator.calculate(
            "short",
            self.entry_price,
            candles,
        )

        self.assertEqual(result["saldo_60_aggr_candles"], Decimal("0.0017"))
        self.assertEqual(result["best_candle_60_minute"], 48)
        self.assertEqual(result["100zl_deal_60"], Decimal("4.63636"))

    def test_60_candle_number_uses_one_based_positions_at_window_edges(self) -> None:
        """Numer najlepszej świecy mieści się w zakresie 1–60."""
        candles = [
            (Decimal("1.1001"), Decimal("1.0999"))
            for _ in range(60)
        ]
        candles[0] = (Decimal("1.1010"), Decimal("1.0999"))
        first_candle_result = self.calculator.calculate(
            "long", self.entry_price, candles
        )
        self.assertEqual(first_candle_result["best_candle_60_minute"], 1)

        candles[0] = (Decimal("1.1001"), Decimal("1.0999"))
        candles[59] = (Decimal("1.1020"), Decimal("1.0999"))
        last_candle_result = self.calculator.calculate(
            "long", self.entry_price, candles
        )
        self.assertEqual(last_candle_result["best_candle_60_minute"], 60)

    def test_update_pending_uses_candle_source_selected_by_signal_id(self) -> None:
        """Dobiera źródło świec do granicznego numeru sygnału."""
        cursor = Mock()
        candle_rows = [
            (Decimal("1.1001"), Decimal("1.0999"))
            for _ in range(60)
        ]
        candle_rows[42] = (Decimal("1.1030"), Decimal("1.0999"))
        cursor.fetchall.side_effect = [
            [
                (
                    5230,
                    "EUR/USD",
                    Decimal("1.1000"),
                    "long",
                    Decimal("0.00030"),
                    {"timestamp": "2025-01-01T00:00:00Z"},
                ),
                (
                    5231,
                    "EUR/USD",
                    Decimal("1.1000"),
                    "long",
                    Decimal("0.00030"),
                    {"timestamp": "2025-01-01T00:00:00Z"},
                ),
            ],
            candle_rows,
            candle_rows,
        ]

        self.calculator.update_pending(cursor)

        pending_query = cursor.execute.call_args_list[0].args[0]
        legacy_query = cursor.execute.call_args_list[1].args[0]
        legacy_update_query, legacy_update_values = (
            cursor.execute.call_args_list[2].args
        )
        ibkr_query = cursor.execute.call_args_list[3].args[0]
        ibkr_update_query, ibkr_update_values = (
            cursor.execute.call_args_list[4].args
        )
        self.assertIn("p.best_candle_60_minute IS NULL", pending_query)
        self.assertIn("FROM candles\n", legacy_query)
        self.assertIn("FROM candles_IBKR\n", ibkr_query)
        self.assertIn(
            "`saldo60can` = COALESCE(`saldo60can`, %s)",
            legacy_update_query,
        )
        self.assertIn("best_candle_60_minute", legacy_update_query)
        self.assertEqual(legacy_update_values[59], Decimal("-0.00020"))
        self.assertEqual(legacy_update_values[65], Decimal("0.0027"))
        self.assertEqual(legacy_update_values[-1], 5230)
        self.assertEqual(ibkr_update_values[-1], 5231)

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

        self.assertEqual(result["saldo1can"], Decimal("0.0000"))

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

        self.assertEqual(result["saldo1can"], Decimal("0.00012"))

        negative_result = self.calculator.calculate(
            "long",
            self.entry_price,
            [(Decimal("1.1000"), Decimal("1.0998"))],
            Decimal("0.000345"),
        )
        self.assertEqual(negative_result["saldo1can"], Decimal("-0.00034"))


if __name__ == "__main__":
    unittest.main()
