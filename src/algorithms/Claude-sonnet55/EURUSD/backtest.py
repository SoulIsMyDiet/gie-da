#!/usr/bin/env python3
"""
Backend
Prosty backtest algorytmu na historii z bazy danych.
Symuluje każdy sygnał i liczy zyski/straty (TP/SL/time stop).
"""

import logging
import sys
from datetime import timedelta
from typing import List, Dict, Optional, Tuple

from config import (
    INSTRUMENT,
    PIP,
    ASSUMED_COST_PIPS,
    MAX_HOLD_MINUTES,
    SL_MULTIPLIER,
    TP_MULTIPLIER,
)
from EURUSDStrategyV1 import EURUSDStrategyV1
from storage import CandleDatabase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class BacktestEngine:
    """Backend - Silnik backtestowy."""

    def __init__(self):
        """Inicjalizuje engine."""
        self.db = CandleDatabase()
        self.strategy = EURUSDStrategyV1()
        self.trades: List[Dict] = []
        self.signals: List[Dict] = []

    def run_backtest(self, symbol: str = INSTRUMENT, limit: int = 2000) -> Dict:
        """
        Backend
        Uruchamia backtest na historii świec.
        Zwraca raport z metrykamii.
        """
        logger.info(f"Pobieranie {limit} świec dla backtestów...")
        candles = self.db.fetch_candles(symbol, limit=limit)

        if len(candles) < 100:
            logger.error("Za mało świec do backtestów")
            return {}

        logger.info(f"Backtest {len(candles)} świec...")

        # Warm-up
        self.strategy.warm_up(candles[:100])

        # Symuluj świeca po świecy
        for i in range(100, len(candles)):
            window = candles[:i + 1]

            # Generuj sygnał
            signal = self.strategy.generate_signal(window)

            if signal["signal"] in ("LONG", "SHORT"):
                self.signals.append(signal)

                # Symuluj transakcję
                entry_price = signal["price"]
                entry_time = signal["timestamp"]
                sl_pips = signal.get("sl_pips", 1.5)
                tp_pips = signal.get("tp_pips", 2.0)
                max_hold = MAX_HOLD_MINUTES

                # Szukaj TP/SL w następnych świecach
                result = self._simulate_trade(
                    window[i:],
                    signal["signal"],
                    entry_price,
                    sl_pips,
                    tp_pips,
                    max_hold,
                )

                if result:
                    self.trades.append(result)
                    logger.info(
                        f"{result['signal']} {result['outcome']}: "
                        f"{result['pnl_pips']:.2f} pips"
                    )

        # Generuj raport
        return self._generate_report()

    @staticmethod
    def _simulate_trade(
        candles: List[Dict],
        direction: str,
        entry_price: float,
        sl_pips: float,
        tp_pips: float,
        max_hold_minutes: int,
    ) -> Optional[Dict]:
        """Backend - Symuluje pojedynczą transakcję."""
        sl_level = entry_price - sl_pips * PIP if direction == "LONG" else entry_price + sl_pips * PIP
        tp_level = entry_price + tp_pips * PIP if direction == "LONG" else entry_price - tp_pips * PIP

        entry_time = candles[0]["datetime_utc"]

        for i, candle in enumerate(candles[1:], start=1):
            current_time = candle["datetime_utc"]
            time_held = (current_time - entry_time).total_seconds() / 60

            # Time stop
            if time_held > max_hold_minutes:
                exit_price = candle["close"]
                pnl_pips = (exit_price - entry_price) / PIP if direction == "LONG" else (entry_price - exit_price) / PIP
                pnl_pips -= ASSUMED_COST_PIPS  # Odejmij spread
                return {
                    "signal": direction,
                    "entry": entry_price,
                    "exit": exit_price,
                    "pnl_pips": pnl_pips,
                    "outcome": "time_stop",
                    "hold_minutes": time_held,
                }

            # Sprawdź TP/SL
            high = candle["high"]
            low = candle["low"]

            # Conservative: jeśli oba poziomy dotknięte, załóż SL pierwszy
            if direction == "LONG":
                if low <= sl_level:
                    pnl_pips = -sl_pips - ASSUMED_COST_PIPS
                    return {
                        "signal": direction,
                        "entry": entry_price,
                        "exit": sl_level,
                        "pnl_pips": pnl_pips,
                        "outcome": "sl_hit",
                        "hold_minutes": time_held,
                    }
                if high >= tp_level:
                    pnl_pips = tp_pips - ASSUMED_COST_PIPS
                    return {
                        "signal": direction,
                        "entry": entry_price,
                        "exit": tp_level,
                        "pnl_pips": pnl_pips,
                        "outcome": "tp_hit",
                        "hold_minutes": time_held,
                    }
            else:  # SHORT
                if high >= sl_level:
                    pnl_pips = -sl_pips - ASSUMED_COST_PIPS
                    return {
                        "signal": direction,
                        "entry": entry_price,
                        "exit": sl_level,
                        "pnl_pips": pnl_pips,
                        "outcome": "sl_hit",
                        "hold_minutes": time_held,
                    }
                if low <= tp_level:
                    pnl_pips = tp_pips - ASSUMED_COST_PIPS
                    return {
                        "signal": direction,
                        "entry": entry_price,
                        "exit": tp_level,
                        "pnl_pips": pnl_pips,
                        "outcome": "tp_hit",
                        "hold_minutes": time_held,
                    }

        return None

    def _generate_report(self) -> Dict:
        """Backend - Generuje raport z metrykami."""
        if not self.trades:
            return {
                "status": "no_trades",
                "total_signals": len(self.signals),
                "total_trades": 0,
                "message": "Brak zamkniętych transakcji w backtescie",
            }

        wins = [t for t in self.trades if t["pnl_pips"] > 0]
        losses = [t for t in self.trades if t["pnl_pips"] < 0]

        total_pnl = sum(t["pnl_pips"] for t in self.trades)
        avg_pnl = total_pnl / len(self.trades) if self.trades else 0
        win_rate = len(wins) / len(self.trades) if self.trades else 0

        report = {
            "total_signals": len(self.signals),
            "total_trades": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": f"{win_rate * 100:.1f}%",
            "total_pnl_pips": f"{total_pnl:.2f}",
            "avg_pnl_pips": f"{avg_pnl:.2f}",
            "max_win_pips": f"{max((t['pnl_pips'] for t in wins), default=0):.2f}",
            "max_loss_pips": f"{min((t['pnl_pips'] for t in losses), default=0):.2f}",
        }

        return report


def main():
    """Frontend - Entry point."""
    try:
        engine = BacktestEngine()
        report = engine.run_backtest(INSTRUMENT, limit=1000)

        logger.info("=" * 60)
        logger.info("RAPORT BACKTESTOWY")
        logger.info("=" * 60)
        for key, value in report.items():
            logger.info(f"{key}: {value}")

    except Exception as e:
        logger.error(f"Błąd backtestowy: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
