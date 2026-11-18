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
        *,
        record_evaluation: bool,
        save_signal: bool,
        enqueue_notification: bool,
    ) -> bool:
        """Zapisuje ewaluację i opcjonalnie sygnał z trwałym powiadomieniem."""


class USDMXNSignalStorage:
    """Zapisuje pełne parametry wyniku przez istniejące mapowanie tabeli signals."""

    def __init__(self, repository: SignalRepository) -> None:
        """Przyjmuje gotowe repozytorium API; nie otwiera drugiego połączenia."""
        self.repository = repository

    def save(
        self,
        result: dict[str, Any],
        candles_count: int,
        *,
        enqueue_notification: bool = True,
    ) -> bool:
        """Zapisuje ewaluację także dla NONE, a sygnał według konfiguracji.

        Args:
            result: Wynik strategii zawierający sygnał, czas i parametry JSON.
            candles_count: Liczba świec wykorzystanych do analizy.
            enqueue_notification: Czy przygotować dźwięk po nowym zapisie.

        Returns:
            ``True`` tylko wtedy, gdy zapisano nowy rekord sygnału.

        Raises:
            ValueError: Gdy wynik nie zawiera epoki otwarcia świecy.
            Błąd bazy danych: Gdy repozytorium nie może zapisać wyniku.
        """
        if result.get("candle_open_epoch") is None:
            raise ValueError("Wynik USDMXN nie zawiera epoki otwarcia świecy.")
        payload = result.get("signal_parameters")
        compact_payload = self._compact_signal_parameters(payload)
        repository_result = dict(result)
        repository_result["signal_parameters"] = compact_payload
        return self.repository.save_algorithm_signal(
            DATABASE_SYMBOL,
            STRATEGY_NAME,
            repository_result,
            candles_count,
            record_evaluation=True,
            save_signal=should_save_signal(result),
            enqueue_notification=(
                enqueue_notification
                and str(result.get("signal", "")).upper() in {"LONG", "SHORT"}
            ),
        )

    @staticmethod
    def _compact_signal_parameters(payload: Any) -> dict[str, Any]:
        """Zwraca wymagane pola, zastępując długi powód krótkim opisem wyniku.

        Raises:
            ValueError: Gdy wynik strategii nie zawiera pełnego pakietu danych.
        """
        required_fields = ("price", "signal", "timestamp", "instrument", "reason")
        if not isinstance(payload, dict) or any(
            field not in payload for field in required_fields
        ):
            raise ValueError("Wynik USDMXN nie zawiera wymaganych pól JSON.")
        compact_payload = {field: payload[field] for field in required_fields}
        signal_reasons = {
            "NONE": "Warunki LONG ani SHORT nie zostały spełnione.",
            "LONG": "Warunki LONG zostały spełnione.",
            "SHORT": "Warunki SHORT zostały spełnione.",
        }
        compact_payload["reason"] = signal_reasons.get(
            str(payload["signal"]).upper(), payload["reason"]
        )
        return compact_payload
