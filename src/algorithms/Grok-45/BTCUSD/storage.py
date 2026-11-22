"""Adapter zapisu BTCUSDStrategyV1 przez istniejące repozytorium API."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from .config import (
    BTC_SPREAD_PRICE,
    DATABASE_SYMBOL,
    SAVE_NONE_SIGNALS,
    STRATEGY_NAME,
)


# Backend
class SignalRepository(Protocol):
    """Opisuje metodę zapisu sygnału udostępnianą przez lokalne API."""

    def save_algorithm_signal(
        self,
        product: str,
        algorithm_name: str,
        signal: dict[str, Any],
        candles_count: int,
        *,
        record_evaluation: bool,
        save_signal: bool,
        save_none_signal: bool,
        save_signal_potential: bool,
        enqueue_notification: bool,
        spread_price: Decimal | None = None,
    ) -> bool:
        """Zapisuje wynik strategii i opcjonalnie kolejkuje powiadomienie."""


class BTCUSDSignalStorage:
    """Zapisuje krótki JSON sygnału przez istniejące połączenie z bazą."""

    def __init__(self, repository: SignalRepository) -> None:
        """Przyjmuje istniejące repozytorium, nie otwierając drugiego połączenia.

        Args:
            repository: Repozytorium API implementujące zapis sygnału.
        """
        self.repository = repository

    def save(
        self,
        result: dict[str, Any],
        candles_count: int,
        *,
        enqueue_notification: bool = False,
    ) -> bool:
        """Zapisuje ewaluację i — zależnie od flagi — rekord w tabeli signals.

        Args:
            result: Wynik strategii z czasem zamknięcia i obiektem JSON.
            candles_count: Liczba świec użyta do obliczeń.
            enqueue_notification: Czy dodać powiadomienie LONG/SHORT do outboxa.

        Returns:
            True, jeśli utworzono nowy rekord w tabeli signals.

        Raises:
            ValueError: Gdy brakuje wymaganych pól albo cena sygnału jest błędna.
        """
        candle_open_epoch = result.get("candle_open_epoch")
        close_time = result.get("candle_close_time")
        direction = str(result.get("signal", "")).upper()
        if candle_open_epoch is None:
            raise ValueError("Wynik BTC/USD nie zawiera epoki otwarcia świecy.")
        if not isinstance(close_time, str):
            raise ValueError("Wynik BTC/USD nie zawiera czasu zamknięcia.")
        if direction not in {"LONG", "SHORT", "NONE"}:
            raise ValueError("Wynik BTC/USD zawiera nieznany kierunek sygnału.")

        repository_result = {
            "signal": direction,
            "price": result.get("price"),
            "timestamp": close_time,
            "candle_open_epoch": candle_open_epoch,
            "signal_parameters": self._compact_signal_parameters(
                result.get("signal_parameters")
            ),
        }
        spread_price = None
        if direction in {"LONG", "SHORT"}:
            try:
                entry_price = Decimal(str(result.get("price")))
            except (InvalidOperation, TypeError, ValueError) as error:
                raise ValueError(
                    "Cena sygnału BTC/USD jest nieprawidłowa."
                ) from error
            if not entry_price.is_finite() or entry_price <= 0:
                raise ValueError("Cena sygnału BTC/USD musi być dodatnia.")
            spread_price = BTC_SPREAD_PRICE

        return self.repository.save_algorithm_signal(
            DATABASE_SYMBOL,
            STRATEGY_NAME,
            repository_result,
            candles_count,
            record_evaluation=True,
            save_signal=(
                direction != "NONE" or SAVE_NONE_SIGNALS
            ),
            save_none_signal=(
                direction == "NONE" and SAVE_NONE_SIGNALS
            ),
            save_signal_potential=True,
            enqueue_notification=(
                enqueue_notification and direction in {"LONG", "SHORT"}
            ),
            spread_price=spread_price,
        )

    @staticmethod
    def _compact_signal_parameters(payload: Any) -> dict[str, Any]:
        """Zwraca tylko pięć pól wymaganych w JSON sygnału.

        Args:
            payload: Dane JSON wygenerowane przez strategię.

        Returns:
            Obiekt JSON z ceną, kierunkiem, czasem, instrumentem i powodem.

        Raises:
            ValueError: Gdy brakuje wymaganego pola albo kierunek jest błędny.
        """
        required_fields = ("price", "signal", "timestamp", "instrument", "reason")
        if not isinstance(payload, dict) or any(
            field not in payload for field in required_fields
        ):
            raise ValueError("Wynik BTC/USD nie zawiera wymaganych pól JSON.")
        compact_payload = {field: payload[field] for field in required_fields}
        if compact_payload["instrument"] != "BTCUSD":
            raise ValueError("JSON sygnału musi wskazywać instrument BTCUSD.")
        if str(compact_payload["signal"]).upper() not in {"LONG", "SHORT", "NONE"}:
            raise ValueError("JSON sygnału zawiera nieznany kierunek.")
        return compact_payload
