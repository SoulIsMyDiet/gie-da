"""Zapis wyników DAX40 przez istniejący obiekt repozytorium API."""

from __future__ import annotations

from typing import Any, Protocol

from .DAX40StrategyV1 import should_save_signal
from .config import ALGORITHM_NAME, DATABASE_SYMBOL


class SignalRepository(Protocol):
    """Opisuje istniejące repozytorium API wymagane do zapisu sygnału."""

    def save_algorithm_signal(
        self,
        product: str,
        algorithm_name: str,
        signal: dict[str, Any],
        candles_count: int,
    ) -> bool:
        """Zapisuje sygnał transakcyjnie i zwraca True tylko dla nowego rekordu."""


class DAX40SignalStorage:
    """Zapisuje sygnały Chat-gpt6 w istniejącej tabeli signals."""

    def __init__(self, repository: SignalRepository) -> None:
        """Przyjmuje istniejący adapter bazy API bez tworzenia nowego połączenia."""
        self.repository = repository

    def save(self, result: dict[str, Any], candles_count: int) -> bool:
        """Pomija NONE po wyłączeniu flagi albo przekazuje zapis do repozytorium."""
        if not should_save_signal(result):
            return False
        if result.get("candle_open_epoch") is None:
            return False
        return self.repository.save_algorithm_signal(
            DATABASE_SYMBOL,
            ALGORITHM_NAME,
            result,
            candles_count,
        )
