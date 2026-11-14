"""Tryb historyczny bez zapisu sygnałów i bez odtwarzania dźwięku."""

from __future__ import annotations

from typing import Any, Sequence

from .DAX40StrategyV1 import Candle, DAX40StrategyV1


def run_historical_backtest(
    candles: Sequence[Candle],
    market_is_open,
) -> dict[str, Any]:
    """Uruchamia te same reguły na historii, przekazując wyłącznie prefiksy danych."""
    strategy = DAX40StrategyV1(market_is_open)
    return strategy.backtest(candles)
