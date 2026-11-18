"""Adapter zapisu wyników USDJPY przez istniejące repozytorium API."""

from __future__ import annotations

from typing import Any, Protocol

from .config import SAVE_NONE_SIGNALS, STRATEGY_NAME


class SignalRepository(Protocol):
    """Minimalny interfejs istniejącego repozytorium sygnałów."""

    def save_algorithm_signal(
        self,
        product: str,
        algorithm_name: str,
        signal: dict[str, object],
        candles_count: int,
        *,
        record_evaluation: bool,
        save_signal: bool,
        enqueue_notification: bool,
    ) -> bool:
        """Zapisuje sygnał i marker ewaluacji w jednej transakcji."""


class USDJPYSignalStorage:
    """Zapisuje wyniki USDJPY z ochroną przed powtórną ewaluacją."""

    def __init__(self, repository: SignalRepository) -> None:
        """Przyjmuje istniejące repozytorium bez tworzenia nowego połączenia."""
        self.repository = repository

    def save(
        self,
        result: dict[str, Any],
        candles_count: int,
        *,
        enqueue_notification: bool = True,
    ) -> bool:
        """Zapisuje skrócony JSON, pomijając NONE po wyłączeniu flagi.

        Raises:
            ValueError: Gdy wynik nie zawiera wymaganych pól do zapisu.
        """
        close_time = result.get("candle_close_time")
        open_epoch = result.get("candle_open_epoch")
        payload = result.get("signal_parameters")
        if (
            not isinstance(close_time, str)
            or isinstance(open_epoch, bool)
            or not isinstance(open_epoch, int)
            or not isinstance(payload, dict)
        ):
            raise ValueError("Wynik USDJPY nie zawiera czasu ani JSON.")

        repository_result = dict(result)
        repository_result["timestamp"] = close_time
        repository_result["signal_parameters"] = self._compact_signal_parameters(
            payload
        )
        direction = str(result.get("signal", "")).upper()
        return self.repository.save_algorithm_signal(
            str(result.get("database_symbol", "")),
            STRATEGY_NAME,
            repository_result,
            candles_count,
            record_evaluation=True,
            save_signal=direction != "NONE" or SAVE_NONE_SIGNALS,
            enqueue_notification=enqueue_notification,
        )

    @staticmethod
    def _compact_signal_parameters(payload: dict[str, Any]) -> dict[str, Any]:
        """Zwraca tylko pola wymagane w tabeli ``signals``."""
        required_fields = ("price", "signal", "timestamp", "instrument", "reason")
        if any(field not in payload for field in required_fields):
            raise ValueError("Wynik USDJPY nie zawiera wymaganych pól JSON.")
        return {field: payload[field] for field in required_fields}
