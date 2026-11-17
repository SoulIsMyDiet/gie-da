"""Adapter zapisu wyników strategii przez istniejące repozytorium API."""

from __future__ import annotations

from typing import Any, Protocol

from .USDMXNStrategyV1 import should_save_signal
from .config import DATABASE_SYMBOL, STRATEGY_NAME


class SignalRepository(Protocol):
    """Opisuje metody zapisu sygnałów udostępniane przez lokalne API."""

    def save_algorithm_signal(
        self,
        product: str,
        algorithm_name: str,
        signal: dict[str, Any],
        candles_count: int,
    ) -> bool:
        """Zapisuje sygnał i zwraca True tylko dla nowego rekordu."""


class USDMXNSignalStorage:
    """Zapisuje pełne parametry wyniku przez istniejące mapowanie tabeli signals."""

    def __init__(self, repository: SignalRepository) -> None:
        """Przyjmuje gotowe repozytorium API; nie otwiera drugiego połączenia."""
        self.repository = repository

    def save(self, result: dict[str, Any], candles_count: int) -> bool:
        """Pomija NONE według konfiguracji i zachowuje idempotencję bazy."""
        if not should_save_signal(result):
            return False
        if result.get("candle_open_epoch") is None:
            raise ValueError("Wynik USDMXN nie zawiera epoki otwarcia świecy.")
        return self.repository.save_algorithm_signal(
            DATABASE_SYMBOL,
            STRATEGY_NAME,
            result,
            candles_count,
        )
