"""Adapter zapisu Grok-45/USDMXN przez istniejące repozytorium API."""

from __future__ import annotations

from typing import Any, Protocol

from .USDMXNStrategyV1 import should_save_signal
from .config import DATABASE_SYMBOL, STRATEGY_NAME


class SignalRepository(Protocol):
    """Opisuje metodę zapisu strategii udostępnianą przez lokalne API."""

    def save_algorithm_signal(
        self,
        product: str,
        algorithm_name: str,
        signal: dict[str, Any],
        candles_count: int,
        *,
        record_evaluation: bool,
        save_signal: bool,
        enqueue_notification: bool,
    ) -> bool:
        """Zapisuje ewaluację oraz opcjonalny sygnał i dźwięk w jednej transakcji."""


class USDMXNSignalStorage:
    """Zapisuje wynik tej strategii odrębnie od pozostałych algorytmów USD/MXN."""

    def __init__(self, repository: SignalRepository) -> None:
        """Przyjmuje istniejące repozytorium bez otwierania drugiego połączenia."""
        self.repository = repository

    def save(
        self,
        result: dict[str, Any],
        candles_count: int,
        *,
        enqueue_notification: bool = True,
    ) -> bool:
        """Utrwala wynik i marker ewaluacji dla tej nazwy algorytmu.

        Args:
            result: Wynik zawierający sygnał, epokę świecy i pełny JSON.
            candles_count: Liczba świec użyta do obliczeń.
            enqueue_notification: Czy dodać powiadomienie do istniejącego outboxa.

        Returns:
            ``True`` tylko wtedy, gdy utworzono nowy rekord w tabeli signals.

        Raises:
            ValueError: Gdy wynik nie zawiera czasu otwarcia świecy.
            Exception: Błędy repozytorium są przekazywane do wywołującego.
        """
        if result.get("candle_open_epoch") is None:
            raise ValueError("Wynik Grok-45 USDMXN nie zawiera epoki otwarcia.")
        return self.repository.save_algorithm_signal(
            DATABASE_SYMBOL,
            STRATEGY_NAME,
            result,
            candles_count,
            record_evaluation=True,
            save_signal=should_save_signal(result),
            enqueue_notification=enqueue_notification,
        )
