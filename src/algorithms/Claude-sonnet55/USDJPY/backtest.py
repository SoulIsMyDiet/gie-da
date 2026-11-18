"""Backtest świeca po świecy, bez wykorzystania przyszłych danych."""

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from zoneinfo import ZoneInfo

from config import (
    ASSUMED_COST_PIPS,
    CANDLE_DURATION_SECONDS,
    MAX_HOLD_MINUTES,
    PIP_SIZE,
    PRICE_DECIMALS,
)
from schedule import WARSAW, is_trading_minute, segment_id, session_name
from signal_logic import generate_signal
from storage import USDJPYStorage

UTC = timezone.utc
TOKYO = ZoneInfo("Asia/Tokyo")
logger = logging.getLogger(__name__)


def _trade_result(
    candles: list[dict[str, Any]], signal_index: int, signal: dict[str, Any]
) -> dict[str, Any] | None:
    """Symuluje pozycję do TP, SL, limitu 20 minut lub końca segmentu."""
    direction = signal["signal"]
    entry = float(signal["price"])
    take_profit = float(signal["tp_price"])
    stop_loss = float(signal["sl_price"])
    opened = datetime.fromisoformat(signal["candle_open_time"]).astimezone(UTC)
    entry_time = opened + timedelta(seconds=CANDLE_DURATION_SECONDS)
    deadline = entry_time + timedelta(minutes=MAX_HOLD_MINUTES)
    trade_segment = segment_id(opened)
    last_price = entry
    last_close_time = entry_time
    exit_reason = "time_stop"
    exit_price = entry
    for candle in candles[signal_index + 1 :]:
        candle_open = candle["timestamp_utc"].astimezone(UTC)
        candle_close = candle_open + timedelta(seconds=CANDLE_DURATION_SECONDS)
        if candle_close > deadline:
            exit_price = last_price
            last_close_time = deadline
            exit_reason = "time_stop"
            break
        if (
            not is_trading_minute(candle_open)
            or segment_id(candle_open) != trade_segment
        ):
            exit_price = last_price
            exit_reason = "segment_end"
            break
        high, low = float(candle["high"]), float(candle["low"])
        if direction == "LONG":
            if low <= stop_loss:
                exit_price, exit_reason = stop_loss, "stop_loss"
                last_close_time = candle_close
                break
            if high >= take_profit:
                exit_price, exit_reason = take_profit, "take_profit"
                last_close_time = candle_close
                break
        else:
            if high >= stop_loss:
                exit_price, exit_reason = stop_loss, "stop_loss"
                last_close_time = candle_close
                break
            if low <= take_profit:
                exit_price, exit_reason = take_profit, "take_profit"
                last_close_time = candle_close
                break
        last_price = float(candle["close"])
        last_close_time = candle_close
        if (
            not is_trading_minute(candle_close)
            or segment_id(candle_close) != trade_segment
        ):
            exit_price = last_price
            exit_reason = "segment_end"
            break
        if candle_close >= deadline:
            exit_price = last_price
            exit_reason = "time_stop"
            break
    else:
        if last_close_time < deadline:
            return None

    raw_change = exit_price - entry if direction == "LONG" else entry - exit_price
    profit_pips = raw_change / PIP_SIZE - ASSUMED_COST_PIPS
    return {
        "direction": direction,
        "setup": signal["setup"],
        "session": session_name(entry_time),
        "hour_pl": entry_time.astimezone(WARSAW).hour,
        "entry_time": entry_time.isoformat(),
        "exit_time": last_close_time.isoformat(),
        "entry_price": round(entry, PRICE_DECIMALS),
        "exit_price": round(exit_price, PRICE_DECIMALS),
        "exit_reason": exit_reason,
        "pnl_pips": profit_pips,
    }


def _summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Wylicza win rate, expectancy, profit factor i maksymalne obsunięcie w pipsach."""
    if not trades:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "average_win_pips": 0.0,
            "average_loss_pips": 0.0,
            "expectancy_pips": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pips": 0.0,
        }
    profits = [trade["pnl_pips"] for trade in trades if trade["pnl_pips"] > 0]
    losses = [trade["pnl_pips"] for trade in trades if trade["pnl_pips"] < 0]
    curve = peak = drawdown = 0.0
    for trade in trades:
        curve += trade["pnl_pips"]
        peak = max(peak, curve)
        drawdown = max(drawdown, peak - curve)
    loss_total = abs(sum(losses))
    return {
        "trades": len(trades),
        "win_rate": len(profits) / len(trades),
        "average_win_pips": sum(profits) / len(profits) if profits else 0.0,
        "average_loss_pips": sum(losses) / len(losses) if losses else 0.0,
        "expectancy_pips": sum(trade["pnl_pips"] for trade in trades) / len(trades),
        "profit_factor": sum(profits) / loss_total if loss_total else None,
        "max_drawdown_pips": drawdown,
    }


def run_backtest(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Analizuje świece chronologicznie i raportuje wyniki zamkniętych pozycji."""
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
        opened = candle["timestamp_utc"].astimezone(UTC)
        close_time = opened + timedelta(seconds=CANDLE_DURATION_SECONDS)
        current_day = opened.astimezone(TOKYO).date()
        if signal_day != current_day:
            signal_day = current_day
            signal_history["signals_today"] = 0
            signal_history["range_breakout_directions_today"] = []
        try:
            result = generate_signal(
                ordered[: index + 1],
                now=close_time,
                signal_history=signal_history,
            )
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
            signal_history["range_breakout_directions_today"].append(
                result["signal"]
            )
        if trade is None:
            break
        trades.append(trade)
        exit_time = datetime.fromisoformat(trade["exit_time"]).astimezone(UTC)
        while (
            index + 1 < len(ordered)
            and ordered[index + 1]["timestamp_utc"] < exit_time
        ):
            index += 1
        index += 1

    breakdowns = {}
    for category in ("setup", "session", "hour_pl"):
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for trade in trades:
            grouped[str(trade[category])].append(trade)
        breakdowns[category] = {
            name: _summary(group) for name, group in grouped.items()
        }
    return {
        "signal_count": signal_count,
        "overall": _summary(trades),
        "breakdowns": breakdowns,
        "trades": trades,
    }


def main(argv: list[str] | None = None) -> int:
    """Odczytuje historię USD/JPY i drukuje raport backtestu."""
    parser = argparse.ArgumentParser(description="Backtest USD/JPY.")
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        candles = USDJPYStorage().load_context(datetime.now(UTC), args.limit)
        report = run_backtest(candles)
        logger.info("Wynik backtestu USD/JPY: %s", report["overall"])
        for category, values in report["breakdowns"].items():
            logger.info("Podział %s: %s", category, values)
        return 0
    except Exception:
        logger.exception("Backtest USD/JPY nie powiódł się.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
