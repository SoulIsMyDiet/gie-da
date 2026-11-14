#!/usr/bin/env python3
"""Backend
Backtest GER30 z konserwatywnym wykonaniem SL-przed-TP i kosztami w punktach.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta, timezone
from typing import Any

if __package__:
    from .config import (
        ASSUMED_COST_POINTS,
        INSTRUMENT,
        MAX_HOLD_MINUTES,
        WARSAW_TIMEZONE,
    )
    from .DAX40StrategyV1 import DAX40StrategyV1
    from .schedule import is_trading_minute, segment_id, session_end_after
    from .storage import DAX40Storage
else:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from DAX40.config import (
        ASSUMED_COST_POINTS,
        INSTRUMENT,
        MAX_HOLD_MINUTES,
        WARSAW_TIMEZONE,
    )
    from DAX40.DAX40StrategyV1 import DAX40StrategyV1
    from DAX40.schedule import is_trading_minute, segment_id, session_end_after
    from DAX40.storage import DAX40Storage

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Symuluje sygnały, wyjścia i podstawowe wyniki na historycznych świecach."""

    def __init__(self, strategy: DAX40StrategyV1 | None = None) -> None:
        """Przygotowuje strategię i strukturę wyników."""
        self.strategy = strategy or DAX40StrategyV1()

    def run(self, candles: list[dict[str, Any]]) -> dict[str, Any]:
        """Uruchamia analizę świeca po świecy i zwraca raport zagregowany."""
        ordered = sorted(candles, key=lambda candle: candle["timestamp_utc"])
        trades = []
        last_signal_time = None
        count_by_day: dict[object, int] = {}
        orb_directions_by_day: dict[object, list[str]] = {}
        for index, candle in enumerate(ordered):
            candle_time = candle["timestamp_utc"].astimezone(timezone.utc)
            local_day = candle_time.astimezone(WARSAW_TIMEZONE).date()
            result = self.strategy.evaluate(
                ordered[: index + 1],
                now=candle_time + timedelta(minutes=1, seconds=1),
                last_signal_time=last_signal_time,
                signals_today=count_by_day.get(local_day, 0),
                orb_directions_today=orb_directions_by_day.get(local_day, []),
            )
            if result["signal"] not in ("LONG", "SHORT"):
                continue
            last_signal_time = candle_time + timedelta(minutes=1)
            count_by_day[local_day] = count_by_day.get(local_day, 0) + 1
            if result["setup"] == "OPENING_RANGE_BREAKOUT":
                orb_directions_by_day.setdefault(local_day, []).append(
                    result["signal"]
                )
            trade = self._simulate_trade(ordered, index, result)
            if trade is not None:
                trade["setup"] = result["setup"]
                trade["warsaw_hour"] = candle_time.astimezone(
                    WARSAW_TIMEZONE
                ).hour
                trades.append(trade)
        return self._report(trades, count_by_day)

    @staticmethod
    def _simulate_trade(
        candles: list[dict[str, Any]], signal_index: int, signal: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Symuluje TP/SL/time-stop; gdy oba poziomy są dotknięte, wybiera SL."""
        direction = signal["signal"]
        entry = float(signal["price"])
        stop = float(signal["sl_points"])
        target = float(signal["tp_points"])
        sign = 1 if direction == "LONG" else -1
        candle_open = candles[signal_index]["timestamp_utc"].astimezone(timezone.utc)
        start = candle_open + timedelta(minutes=1)
        previous_time = candle_open
        start_segment = segment_id(candle_open)
        last_candle = candles[signal_index]
        for candle in candles[signal_index + 1 :]:
            current_time = candle["timestamp_utc"].astimezone(timezone.utc)
            if (
                not is_trading_minute(current_time)
                or current_time >= session_end_after(candle_open)
                or segment_id(current_time) != start_segment
            ):
                break
            if (current_time - start).total_seconds() / 60 >= MAX_HOLD_MINUTES:
                break
            if (current_time - previous_time).total_seconds() > 60:
                break
            last_candle = candle
            previous_time = current_time
            stop_hit = (
                float(candle["low"]) <= entry - stop
                if direction == "LONG"
                else float(candle["high"]) >= entry + stop
            )
            target_hit = (
                float(candle["high"]) >= entry + target
                if direction == "LONG"
                else float(candle["low"]) <= entry - target
            )
            if stop_hit:
                return {
                    "signal": direction,
                    "entry": entry,
                    "exit": entry - sign * stop,
                    "pnl_points": -stop - ASSUMED_COST_POINTS,
                    "outcome": "stop_loss",
                    "hold_minutes": (current_time - start).total_seconds() / 60,
                }
            if target_hit:
                return {
                    "signal": direction,
                    "entry": entry,
                    "exit": entry + sign * target,
                    "pnl_points": target - ASSUMED_COST_POINTS,
                    "outcome": "take_profit",
                    "hold_minutes": (current_time - start).total_seconds() / 60,
                }
        exit_price = float(last_candle["close"])
        pnl = (exit_price - entry) * sign - ASSUMED_COST_POINTS
        return {
            "signal": direction,
            "entry": entry,
            "exit": exit_price,
            "pnl_points": pnl,
            "outcome": "time_stop_or_segment_end",
            "hold_minutes": max(
                0.0,
                (
                    last_candle["timestamp_utc"].astimezone(timezone.utc)
                    + timedelta(minutes=1)
                    - start
                ).total_seconds()
                / 60,
            ),
        }

    @staticmethod
    def _report(trades: list[dict[str, Any]], count_by_day: dict[object, int]) -> dict[str, Any]:
        """Buduje metryki ogólne i podsumowania według setupu oraz godziny."""
        pnl_values = [float(trade["pnl_points"]) for trade in trades]
        wins = [value for value in pnl_values if value > 0]
        losses = [value for value in pnl_values if value < 0]
        equity = peak = max_drawdown = 0.0
        for value in pnl_values:
            equity += value
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))

        def summarize(group: list[dict[str, Any]]) -> dict[str, Any]:
            """Oblicza metryki porównywalne dla wybranego podzbioru transakcji."""
            values = [float(trade["pnl_points"]) for trade in group]
            group_wins = [value for value in values if value > 0]
            group_losses = [value for value in values if value < 0]
            profit = sum(group_wins)
            loss = abs(sum(group_losses))
            return {
                "signals": len(group),
                "win_rate_percent": round(100 * len(group_wins) / len(group), 2)
                if group
                else 0.0,
                "average_win_points": round(sum(group_wins) / len(group_wins), 4)
                if group_wins
                else 0.0,
                "average_loss_points": round(sum(group_losses) / len(group_losses), 4)
                if group_losses
                else 0.0,
                "expectancy_points": round(sum(values) / len(values), 4)
                if values
                else 0.0,
                "profit_factor": round(profit / loss, 4) if loss else None,
            }

        setups = sorted({str(trade["setup"]) for trade in trades})
        hours = sorted({int(trade["warsaw_hour"]) for trade in trades})
        return {
            "instrument": INSTRUMENT,
            "total_signals": sum(count_by_day.values()),
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_percent": round(100 * len(wins) / len(trades), 2) if trades else 0.0,
            "total_pnl_points": round(sum(pnl_values), 4),
            "average_pnl_points": round(sum(pnl_values) / len(trades), 4) if trades else 0.0,
            "average_win_points": round(sum(wins) / len(wins), 4) if wins else 0.0,
            "average_loss_points": round(sum(losses) / len(losses), 4) if losses else 0.0,
            "expectancy_points": round(sum(pnl_values) / len(trades), 4) if trades else 0.0,
            "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
            "maximum_drawdown_points": round(max_drawdown, 4),
            "by_setup": {
                setup: summarize([trade for trade in trades if trade["setup"] == setup])
                for setup in setups
            },
            "by_warsaw_hour": {
                str(hour): summarize(
                    [trade for trade in trades if trade["warsaw_hour"] == hour]
                )
                for hour in hours
            },
            "trades": trades,
        }


def main() -> None:
    """Pobiera historię MySQL i drukuje raport backtestowy jako JSON."""
    logging.basicConfig(level=logging.INFO)
    candles, _ = DAX40Storage().load_context(limit=10000)
    report = BacktestEngine().run(candles)
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()
