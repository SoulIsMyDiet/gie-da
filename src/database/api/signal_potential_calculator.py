"""Oblicza i zapisuje wyniki hipotetycznych transakcji dla sygnałów."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any, Sequence
from zoneinfo import ZoneInfo

WARSAW_TIME_ZONE = ZoneInfo("Europe/Warsaw")


class SignalPotentialCalculator:
    """Wylicza salda sygnałów na podstawie kolejnych zamkniętych świec M1."""

    SPREAD_PRICE = Decimal("0.0003")
    DEPOSIT_PLN = Decimal("100")
    LEVERAGE = Decimal("30")
    BALANCE_DECIMAL_PLACES = Decimal("0.00001")

    def calculate(
        self,
        direction: str,
        entry_price: Decimal,
        candles: Sequence[tuple[Decimal, Decimal]],
        spread_price: Decimal | None = None,
    ) -> dict[str, Decimal | int | None]:
        """Liczy dostępne salda dla kierunku, ceny wejścia i świec (high, low).

        Zwraca `None` dla pól, dla których nie zebrano jeszcze wymaganej liczby
        świec. Rzuca `ValueError`, jeśli kierunek lub cena wejścia są nieprawidłowe.
        """
        normalized_direction = direction.lower()
        if normalized_direction not in ("long", "short"):
            raise ValueError("Kierunek sygnału musi mieć wartość long albo short.")
        if entry_price <= 0:
            raise ValueError("Cena wejścia musi być większa od zera.")
        effective_spread = (
            self.SPREAD_PRICE if spread_price is None else spread_price
        )
        if effective_spread < 0:
            raise ValueError("Spread nie może być ujemny.")

        results: dict[str, Decimal | int | None] = {
            "saldo_1st_candle": None,
            "saldo_2nd_candle": None,
            "saldo_3rd_candle": None,
            "saldo_4th_candle": None,
            "saldo_5th_candle": None,
            "saldo_10aggr_candles": None,
            "saldo_20_aggr_candles": None,
            "max_candle": None,
            "100zl_deal": None,
        }

        single_candle_columns = (
            "saldo_1st_candle",
            "saldo_2nd_candle",
            "saldo_3rd_candle",
            "saldo_4th_candle",
            "saldo_5th_candle",
        )
        for index, (high, low) in enumerate(candles[:5]):
            results[single_candle_columns[index]] = self._balance(
                normalized_direction, entry_price, high, low, effective_spread
            )

        if len(candles) >= 10:
            results["saldo_10aggr_candles"] = self._aggregate_balance(
                normalized_direction, entry_price, candles[:10], effective_spread
            )

        if len(candles) >= 20:
            selected_index = self._best_candle_index(
                normalized_direction, candles[:20]
            )
            balance_20 = self._balance(
                normalized_direction,
                entry_price,
                *candles[selected_index],
                effective_spread,
            )
            results["saldo_20_aggr_candles"] = balance_20
            results["max_candle"] = selected_index + 1
            results["100zl_deal"] = (
                self.DEPOSIT_PLN * self.LEVERAGE * balance_20 / entry_price
            )

        for field_name, value in results.items():
            if isinstance(value, Decimal):
                results[field_name] = value.quantize(
                    self.BALANCE_DECIMAL_PLACES, rounding=ROUND_DOWN
                )

        return results

    def update_pending(
        self,
        cursor: Any,
        product_symbols: set[str] | None = None,
    ) -> None:
        """Uzupełnia dostępne salda dla sygnałów, które nie mają jeszcze 20 świec.

        Pobiera wyłącznie świece M1 zapisane po zamknięciu świecy sygnałowej.
        Błędy odczytu, niepoprawne dane JSON lub błędy zapisu są przekazywane
        wyżej, aby transakcja zapisująca świece mogła zostać wycofana.
        """
        product_filter = ""
        parameters: tuple[Any, ...] = ()
        if product_symbols is not None:
            if not product_symbols:
                return
            placeholders = ", ".join(["%s"] * len(product_symbols))
            product_filter = f"AND s.product IN ({placeholders})"
            parameters = tuple(sorted(product_symbols))

        cursor.execute(
            f"""
            SELECT p.signal_id, s.product, s.price, s.`signal`, p.spread,
                   s.signal_parameters
            FROM signal_potention AS p
            JOIN signals AS s ON s.id = p.signal_id
            WHERE p.saldo_20_aggr_candles IS NULL
                AND s.`signal` IN ('long', 'short')
                {product_filter}
            ORDER BY p.signal_id
            """,
            parameters,
        )
        pending_signals = cursor.fetchall()

        for (
            signal_id,
            product,
            entry_price,
            direction,
            spread_price,
            signal_parameters,
        ) in pending_signals:
            signal_timestamp = self._signal_timestamp(signal_parameters)
            local_timestamp = signal_timestamp.replace(
                tzinfo=timezone.utc
            ).astimezone(WARSAW_TIME_ZONE)
            search_from_local = (
                local_timestamp.replace(tzinfo=None) - timedelta(hours=1)
            )
            cursor.execute(
                """
                SELECT high, low
                FROM candles
                WHERE symbol = %s
                    AND datetime >= %s
                    AND DATE_SUB(datetime, INTERVAL utc_offset_minutes MINUTE) >= %s
                ORDER BY DATE_SUB(datetime, INTERVAL utc_offset_minutes MINUTE)
                LIMIT 20
                """,
                (product, search_from_local, signal_timestamp),
            )
            candles = [
                (Decimal(str(high)), Decimal(str(low)))
                for high, low in cursor.fetchall()
            ]
            balances = self.calculate(
                direction,
                Decimal(str(entry_price)),
                candles,
                Decimal(str(spread_price)),
            )
            cursor.execute(
                """
                UPDATE signal_potention
                SET saldo_1st_candle = COALESCE(saldo_1st_candle, %s),
                    saldo_2nd_candle = COALESCE(saldo_2nd_candle, %s),
                    saldo_3rd_candle = COALESCE(saldo_3rd_candle, %s),
                    saldo_4th_candle = COALESCE(saldo_4th_candle, %s),
                    saldo_5th_candle = COALESCE(saldo_5th_candle, %s),
                    saldo_10aggr_candles =
                        COALESCE(saldo_10aggr_candles, %s),
                    saldo_20_aggr_candles =
                        COALESCE(saldo_20_aggr_candles, %s),
                    max_candle = COALESCE(max_candle, %s),
                    `100zl_deal` = COALESCE(`100zl_deal`, %s)
                WHERE signal_id = %s
                """,
                (
                    balances["saldo_1st_candle"],
                    balances["saldo_2nd_candle"],
                    balances["saldo_3rd_candle"],
                    balances["saldo_4th_candle"],
                    balances["saldo_5th_candle"],
                    balances["saldo_10aggr_candles"],
                    balances["saldo_20_aggr_candles"],
                    balances["max_candle"],
                    balances["100zl_deal"],
                    signal_id,
                ),
            )

    @classmethod
    def _balance(
        cls,
        direction: str,
        entry_price: Decimal,
        high: Decimal,
        low: Decimal,
        spread_price: Decimal,
    ) -> Decimal:
        """Zwraca zmianę kursu po odjęciu spreadu, bez obcinania strat."""
        if direction == "short":
            return entry_price - low - spread_price
        return high - entry_price - spread_price

    @classmethod
    def _aggregate_balance(
        cls,
        direction: str,
        entry_price: Decimal,
        candles: Sequence[tuple[Decimal, Decimal]],
        spread_price: Decimal,
    ) -> Decimal:
        """Liczy saldo z najlepszego ekstremum w podanym oknie świec."""
        selected_index = cls._best_candle_index(direction, candles)
        return cls._balance(
            direction, entry_price, *candles[selected_index], spread_price
        )

    @staticmethod
    def _best_candle_index(
        direction: str,
        candles: Sequence[tuple[Decimal, Decimal]],
    ) -> int:
        """Zwraca indeks najwyższego high LONG lub najniższego low SHORT."""
        if direction == "long":
            return max(range(len(candles)), key=lambda index: candles[index][0])
        return min(range(len(candles)), key=lambda index: candles[index][1])

    @staticmethod
    def _signal_timestamp(signal_parameters: Any) -> datetime:
        """Odczytuje UTC zamknięcia świecy sygnałowej z dokumentu JSON."""
        if isinstance(signal_parameters, str):
            payload = json.loads(signal_parameters)
        elif isinstance(signal_parameters, dict):
            payload = signal_parameters
        else:
            raise ValueError("Parametry sygnału mają nieobsługiwany format JSON.")

        timestamp = payload.get("timestamp")
        if not isinstance(timestamp, str):
            raise ValueError("Parametry sygnału nie zawierają prawidłowego czasu.")
        parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
            raise ValueError("Czas sygnału musi zawierać strefę czasową.")
        return parsed_timestamp.astimezone(timezone.utc).replace(tzinfo=None)
