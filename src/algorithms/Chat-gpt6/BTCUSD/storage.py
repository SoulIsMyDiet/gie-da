"""Zapis strategii Chat-gpt6 BTC/USD przez istniejące repozytorium API."""

from __future__ import annotations

from typing import Any, Protocol

from .config import DATABASE_SYMBOL, SAVE_NONE_SIGNALS, STRATEGY_NAME


class SignalRepository(Protocol):
    """Opisuje metodę transakcyjnego zapisu strategii w lokalnym API."""

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
    ) -> bool:
        """Zapisuje ewaluację i opcjonalny sygnał w istniejących tabelach."""


class BTCUSDSignalStorage:
    """Utrwala wynik przez MarketDataRepository bez własnego połączenia DB."""

    def __init__(self, repository: SignalRepository) -> None:
        """Przyjmuje istniejące repozytorium API."""
        self.repository = repository

    def save(
        self,
        result: dict[str, Any],
        candles_count: int,
        *,
        enqueue_notification: bool = False,
    ) -> bool:
        """Zapisuje pojedynczą ewaluację; unikalność blokuje ponowne zapisy.

        Args:
            result: Wynik strategii zawierający czas otwarcia i zamknięcia.
            candles_count: Liczba świec użyta do obliczeń.
            enqueue_notification: Czy dodać sygnał kierunkowy do outboxa.

        Returns:
            True, jeśli powstał nowy rekord sygnału LONG/SHORT/NONE.

        Raises:
            ValueError: Gdy brakuje wymaganych pól wyniku.
        """
        direction = str(result.get("signal", "")).upper()
        if direction not in {"LONG", "SHORT", "NONE"}:
            raise ValueError("Sygnał BTC/USD musi mieć wartość LONG, SHORT albo NONE.")
        open_epoch = result.get("candle_open_epoch")
        close_time = result.get("candle_close_time")
        parameters = result.get("signal_parameters")
        if (
            isinstance(open_epoch, bool)
            or not isinstance(open_epoch, int)
            or not isinstance(close_time, str)
            or not isinstance(parameters, dict)
        ):
            raise ValueError("Wynik BTC/USD nie zawiera czasu lub JSON sygnału.")
        repository_result = {
            "signal": direction,
            "price": result.get("price"),
            "timestamp": close_time,
            "candle_open_epoch": open_epoch,
            "signal_parameters": parameters,
        }
        return self.repository.save_algorithm_signal(
            DATABASE_SYMBOL,
            STRATEGY_NAME,
            repository_result,
            candles_count,
            record_evaluation=True,
            save_signal=direction != "NONE" or SAVE_NONE_SIGNALS,
            save_none_signal=direction == "NONE" and SAVE_NONE_SIGNALS,
            save_signal_potential=False,
            enqueue_notification=(
                enqueue_notification and direction in {"LONG", "SHORT"}
            ),
        )
