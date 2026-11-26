"""Oblicza i zapisuje wyniki hipotetycznych transakcji dla sygnałów."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any, Sequence
from zoneinfo import ZoneInfo

WARSAW_TIME_ZONE = ZoneInfo("Europe/Warsaw")
SINGLE_CANDLE_BALANCE_COLUMNS = tuple(
    f"saldo{candle_number}can" for candle_number in range(1, 61)
)
LEGACY_CANDLE_SOURCE_MAX_SIGNAL_ID = 5230


class SignalPotentialCalculator:
    """Wylicza salda sygnałów na podstawie kolejnych zamkniętych świec M1."""

    SPREAD_PRICE = Decimal("0.00030")
    SPREADS_BY_PRODUCT = {
        "EUR/USD": Decimal("0.00030"),
        "EUR/USD": Decimal("0.00030"),
        "USD/MXN": Decimal("0.01000"),
        "USD/JPY": Decimal("0.03000"),
        "XAU/USD": Decimal("0.30000"),
        "ES": Decimal("0.60000"),
        "GER30": Decimal("1.50000"),
    }
    DEPOSIT_PLN = Decimal("100")
    LEVERAGE = Decimal("30")
    BALANCE_DECIMAL_PLACES = Decimal("0.00001")

    @classmethod
    def spread_for_product(cls, product: str) -> Decimal:
        """Zwraca przyjęty spread dla instrumentu lub zgłasza brak konfiguracji.

        Args:
            product: Symbol instrumentu zapisany w kolumnie ``signals.product``.

        Returns:
            Szacowany spread wyrażony w jednostkach ceny instrumentu.

        Raises:
            ValueError: Gdy instrument nie ma skonfigurowanego spreadu.
        """
        try:
            return cls.SPREADS_BY_PRODUCT[product]
        except KeyError as error:
            raise ValueError(
                f"Brak domyślnego spreadu dla instrumentu {product!r}."
            ) from error

    def calculate(
        self,
        direction: str,
        entry_price: Decimal,
        candles: Sequence[tuple[Decimal, Decimal]],
        spread_price: Decimal | None = None,
    ) -> dict[str, Decimal | int | None]:
        """Liczy salda dla 60 kolejnych świec oraz wyniki dłuższych okien.

        Args:
            direction: Kierunek sygnału: ``long`` albo ``short``.
            entry_price: Cena wejścia sygnału.
            candles: Świece w kolejności czasu, jako pary ``(high, low)``.
            spread_price: Spread odejmowany od każdej zmiany ceny.

        Returns:
            Słownik obliczonych wartości; wyniki niekompletnych okien mają
            wartość ``None``.

        Raises:
            ValueError: Gdy kierunek, cena wejścia lub spread są nieprawidłowe.
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
            column_name: None
            for column_name in SINGLE_CANDLE_BALANCE_COLUMNS
        }
        results.update({
            "saldo_10aggr_candles": None,
            "saldo_20_aggr_candles": None,
            "best_candle_20_minute": None,
            "100zl_deal_20": None,
            "best_candle_60_minute": None,
            "saldo_60_aggr_candles": None,
            "100zl_deal_60": None,
        })

        for index, (high, low) in enumerate(candles[:60]):
            results[SINGLE_CANDLE_BALANCE_COLUMNS[index]] = self._balance(
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
            results["best_candle_20_minute"] = selected_index + 1
            results["100zl_deal_20"] = (
                self.DEPOSIT_PLN * self.LEVERAGE * balance_20 / entry_price
            )

        # Backend
        if len(candles) >= 60:
            selected_index = self._best_candle_index(
                normalized_direction, candles[:60]
            )
            balance_60 = self._balance(
                normalized_direction,
                entry_price,
                *candles[selected_index],
                effective_spread,
            )
            results["saldo_60_aggr_candles"] = balance_60
            results["best_candle_60_minute"] = selected_index + 1
            results["100zl_deal_60"] = (
                self.DEPOSIT_PLN * self.LEVERAGE * balance_60 / entry_price
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
        """Uzupełnia brakujące salda dla maksymalnie 60 kolejnych świec.

        Pobiera wyłącznie świece M1 zapisane po zamknięciu świecy sygnałowej.
        Sygnały do ID 5230 korzystają z tabeli ``candles``, a nowsze z
        ``candles_IBKR``. Istniejące wartości pozostają bez zmian. Błędy
        odczytu, niepoprawne dane JSON lub błędy zapisu są przekazywane wyżej,
        aby transakcja mogła zostać wycofana.
        """
        product_filter = ""
        parameters: tuple[Any, ...] = ()
        if product_symbols is not None:
            if not product_symbols:
                return
            placeholders = ", ".join(["%s"] * len(product_symbols))
            product_filter = f"AND s.product IN ({placeholders})"
            parameters = tuple(sorted(product_symbols))

        missing_single_candle_conditions = " OR ".join(
            f"p.`{column_name}` IS NULL"
            for column_name in SINGLE_CANDLE_BALANCE_COLUMNS
        )
        cursor.execute(
            f"""
            SELECT p.signal_id, s.product, s.price, s.`signal`,
                   p.average_spread,
                   s.signal_parameters
            FROM signal_potention AS p
            JOIN signals AS s ON s.id = p.signal_id
            WHERE (
                    {missing_single_candle_conditions}
                OR p.saldo_20_aggr_candles IS NULL
                OR p.best_candle_60_minute IS NULL
                OR p.saldo_60_aggr_candles IS NULL
                OR p.`100zl_deal_60` IS NULL
            )
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
            candle_table = (
                "candles"
                if signal_id <= LEGACY_CANDLE_SOURCE_MAX_SIGNAL_ID
                else "candles_IBKR"
            )
            cursor.execute(
                f"""
                SELECT high, low
                FROM {candle_table}
                WHERE symbol = %s
                    AND datetime >= %s
                    AND DATE_SUB(datetime, INTERVAL utc_offset_minutes MINUTE) >= %s
                ORDER BY DATE_SUB(datetime, INTERVAL utc_offset_minutes MINUTE)
                LIMIT 60
                """,
                (product, search_from_local, signal_timestamp),
            )
            candle_rows = cursor.fetchall()
            candles = [
                (Decimal(str(high)), Decimal(str(low)))
                for high, low in candle_rows
            ]
            balances = self.calculate(
                direction,
                Decimal(str(entry_price)),
                candles,
                Decimal(str(spread_price)),
            )
            persisted_result_columns = (
                *SINGLE_CANDLE_BALANCE_COLUMNS,
                "saldo_10aggr_candles",
                "saldo_20_aggr_candles",
                "best_candle_20_minute",
                "100zl_deal_20",
                "best_candle_60_minute",
                "saldo_60_aggr_candles",
                "100zl_deal_60",
            )
            update_assignments = ",\n".join(
                f"`{column_name}` = COALESCE(`{column_name}`, %s)"
                for column_name in persisted_result_columns
            )
            cursor.execute(
                f"""
                UPDATE signal_potention
                SET {update_assignments}
                WHERE signal_id = %s
                """,
                tuple(
                    balances[column_name]
                    for column_name in persisted_result_columns
                )
                + (signal_id,),
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
        """Odczytuje czas świecy z JSON jako ISO 8601 lub sekundy Unix."""
        if isinstance(signal_parameters, str):
            payload = json.loads(signal_parameters)
        elif isinstance(signal_parameters, dict):
            payload = signal_parameters
        else:
            raise ValueError("Parametry sygnału mają nieobsługiwany format JSON.")

        timestamp = payload.get("timestamp")
        if isinstance(timestamp, int) and not isinstance(timestamp, bool):
            try:
                return datetime.fromtimestamp(
                    timestamp, timezone.utc
                ).replace(tzinfo=None)
            except (OverflowError, OSError, ValueError) as error:
                raise ValueError(
                    "Parametry sygnału nie zawierają prawidłowego czasu."
                ) from error
        if not isinstance(timestamp, str):
            raise ValueError("Parametry sygnału nie zawierają prawidłowego czasu.")
        try:
            parsed_timestamp = datetime.fromisoformat(
                timestamp.replace("Z", "+00:00")
            )
        except ValueError as error:
            raise ValueError(
                "Parametry sygnału nie zawierają prawidłowego czasu."
            ) from error
        if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
            raise ValueError("Czas sygnału musi zawierać strefę czasową.")
        return parsed_timestamp.astimezone(timezone.utc).replace(tzinfo=None)
