"""Backtest świeca po świecy bez wykorzystania przyszłych danych."""

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import logging
from math import inf
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from config import ASSUMED_COST_USD, MAX_HOLD_MINUTES, PRICE_DECIMALS
from schedule import is_trading_minute, segment_id, session_name
from signal_logic import generate_signal
from storage import XAUUSDStorage

UTC = timezone.utc
WARSAW = ZoneInfo("Europe/Warsaw")
logger = logging.getLogger(__name__)


def _trade_result(
    candles: list[dict[str, Any]], signal_index: int, signal: dict[str, Any]
) -> dict[str, Any] | None:
    """Simuluje pozycję do TP, SL, limitu czasu lub końca segmentu."""
    direction = signal["signal"]
    entry = float(signal["price"])
    tp = float(signal["tp_price"])
    sl = float(signal["sl_price"])
    candle_open = datetime.fromisoformat(signal["candle_open_time"]).astimezone(UTC)
    opened = candle_open + timedelta(minutes=1)
    trade_segment = segment_id(candle_open)
    deadline = opened + timedelta(minutes=MAX_HOLD_MINUTES)
    last_price = entry
    exit_reason = "time_stop"
    exit_time = opened
    for offset, candle in enumerate(candles[signal_index + 1 :], start=1):
        index = signal_index + offset
        candle_time = candle["timestamp_utc"]
        candle_close = candle_time + timedelta(minutes=1)
        if candle_close > deadline:
            exit_price = last_price
            exit_time = deadline
            exit_reason = "time_stop"
            break
        if not is_trading_minute(candle_time):
            exit_time = candles[index - 1]["timestamp_utc"] + timedelta(minutes=1)
            exit_price = last_price
            exit_reason = "segment_end"
            break
        if segment_id(candle_time) != trade_segment:
            exit_time = candles[index - 1]["timestamp_utc"] + timedelta(minutes=1)
            exit_price = last_price
            exit_reason = "segment_end"
            break
        high, low = float(candle["high"]), float(candle["low"])
        if direction == "LONG":
            if low <= sl:
                exit_price, exit_reason = sl, "stop_loss"
                exit_time = candle_close
                break
            if high >= tp:
                exit_price, exit_reason = tp, "take_profit"
                exit_time = candle_time + timedelta(minutes=1)
                break
        else:
            if high >= sl:
                exit_price, exit_reason = sl, "stop_loss"
                exit_time = candle_time + timedelta(minutes=1)
                break
            if low <= tp:
                exit_price, exit_reason = tp, "take_profit"
                exit_time = candle_time + timedelta(minutes=1)
                break
        last_price = float(candle["close"])
        exit_time = candle_time + timedelta(minutes=1)
        elapsed_minutes = (exit_time - opened).total_seconds() / 60
        if elapsed_minutes >= MAX_HOLD_MINUTES:
            exit_price = last_price
            break
    else:
        return None
    pnl_before_cost = exit_price - entry if direction == "LONG" else entry - exit_price
    return {
        "direction": direction,
        "setup": signal["setup"],
        "session": session_name(opened),
        "hour_pl": opened.astimezone(WARSAW).hour,
        "entry_time": opened.isoformat(),
        "exit_time": exit_time.isoformat(),
        "entry_price": round(entry, PRICE_DECIMALS),
        "exit_price": round(exit_price, PRICE_DECIMALS),
        "exit_reason": exit_reason,
        "pnl": pnl_before_cost - ASSUMED_COST_USD,
    }


def _summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Wylicza podstawowe metryki portfela dla listy transakcji."""
    if not trades:
        return {
            "trades": 0, "win_rate": 0.0, "average_win": 0.0,
            "average_loss": 0.0, "expectancy": 0.0, "profit_factor": 0.0,
            "max_drawdown": 0.0,
        }
    profits = [trade["pnl"] for trade in trades if trade["pnl"] > 0]
    losses = [trade["pnl"] for trade in trades if trade["pnl"] < 0]
    curve = peak = drawdown = 0.0
    for trade in trades:
        curve += trade["pnl"]
        peak = max(peak, curve)
        drawdown = max(drawdown, peak - curve)
    loss_total = abs(sum(losses))
    return {
        "trades": len(trades),
        "win_rate": len(profits) / len(trades),
        "average_win": sum(profits) / len(profits) if profits else 0.0,
        "average_loss": sum(losses) / len(losses) if losses else 0.0,
        "expectancy": sum(trade["pnl"] for trade in trades) / len(trades),
        "profit_factor": sum(profits) / loss_total if loss_total else inf,
        "max_drawdown": drawdown,
    }


def run_backtest(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Testuje strategię po jednej świecy i zwraca metryki oraz transakcje."""
    ordered = sorted(candles, key=lambda candle: candle["timestamp_utc"])
    trades = []
    signal_count = 0
    signal_history: dict[str, Any] = {
        "last_signal_epoch": None,
        "signals_today": 0,
        "range_breakout_directions_today": [],
    }
    signal_day = None
    index = 0
    while index < len(ordered):
        candle = ordered[index]
        now = candle["timestamp_utc"] + timedelta(minutes=1)
        current_day = (
            candle["timestamp_utc"] + timedelta(minutes=1)
        ).astimezone(WARSAW).date()
        if signal_day != current_day:
            signal_day = current_day
            signal_history["signals_today"] = 0
            signal_history["range_breakout_directions_today"] = []
        available = ordered[: index + 1]
        try:
            result = generate_signal(available, now=now, signal_history=signal_history)
        except ValueError:
            index += 1
            continue
        if result["signal"] not in ("LONG", "SHORT"):
            index += 1
            continue
        signal_count += 1
        trade = _trade_result(ordered, index, result)
        signal_history["last_signal_epoch"] = result["timestamp"]
        signal_history["signals_today"] += 1
        if result["setup"] == "RANGE_BREAKOUT":
            signal_history["range_breakout_directions_today"].append(result["signal"])
        if trade is None:
            break
        trades.append(trade)
        exit_stamp = datetime.fromisoformat(trade["exit_time"])
        while index + 1 < len(ordered) and ordered[index + 1]["timestamp_utc"] < exit_stamp:
            index += 1
        index += 1
    breakdowns: dict[str, dict[str, Any]] = {}
    for key in ("setup", "session", "hour_pl"):
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for trade in trades:
            grouped[str(trade[key])].append(trade)
        breakdowns[key] = {name: _summary(group) for name, group in grouped.items()}
    return {
        "signal_count": signal_count,
        "overall": _summary(trades),
        "breakdowns": breakdowns,
        "trades": trades,
    }


def main(argv: list[str] | None = None) -> int:
    """Odczytuje historię z MySQL i drukuje raport backtestu."""
    parser = argparse.ArgumentParser(description="Backtest strategii XAU/USD.")
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        candles = XAUUSDStorage().load_context(datetime.now(UTC), args.limit)
        report = run_backtest(candles)
        logger.info("Wynik backtestu: %s", report["overall"])
        for category, values in report["breakdowns"].items():
            logger.info("Podział %s: %s", category, values)
        return 0
    except Exception:
        logger.exception("Backtest XAU/USD nie powiódł się.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
