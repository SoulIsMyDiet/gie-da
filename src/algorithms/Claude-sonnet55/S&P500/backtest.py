"""Backend: historyczny backtest bez odczytu bieżącego czasu i bez przyszłych sygnałów."""

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import config
from schedule import as_utc, cash_close, is_signal_window, is_trading_minute, segment_id
from signal_logic import generate_signal
from storage import SP500Storage


@dataclass(frozen=True)
class TradeParameters:
    """Parametry symulacji: koszt, poślizg, mnożnik wyniku i limit czasu w minutach."""

    cost_points: float = config.ASSUMED_COST_POINTS
    slippage_points: float = 0.0
    point_value: float = 1.0
    max_hold_minutes: int = config.MAX_HOLD_MINUTES

    def __post_init__(self):
        """Sprawdza parametry transakcji; niepoprawne wartości zgłaszają ValueError."""
        from math import isfinite
        if (not all(isfinite(x) for x in (self.cost_points, self.slippage_points, self.point_value))
                or self.cost_points < 0 or self.slippage_points < 0 or self.point_value <= 0
                or not isinstance(self.max_hold_minutes, int)
                or not 1 <= self.max_hold_minutes <= config.MAX_HOLD_MINUTES):
            raise ValueError("Niepoprawne parametry symulacji transakcji.")


def simulate_trade(candles: list[dict], index: int, signal: dict, params: TradeParameters) -> dict:
    """Symuluje SL, TP i koniec segmentu; równoczesne TP i SL rozstrzyga jako SL."""
    opened = as_utc(candles[index]["timestamp_utc"])
    entry_time = opened + timedelta(minutes=1)
    entry = float(signal["price"])
    stop, target = float(signal["sl_points"]), float(signal["tp_points"])
    direction = 1 if signal["signal"] == "LONG" else -1
    previous, last = opened, candles[index]
    segment = segment_id(opened)
    session_close = as_utc(cash_close(entry_time.astimezone(config.NEW_YORK).date()))
    outcome, exit_price = "koniec_danych_lub_segmentu", entry
    for row in candles[index + 1:]:
        current = as_utc(row["timestamp_utc"])
        if (current != previous + timedelta(minutes=1) or not is_trading_minute(current)
                or segment_id(current) != segment or current >= session_close
                or current >= entry_time + timedelta(minutes=params.max_hold_minutes)
                ):
            break
        last, previous = row, current
        stop_hit = float(row["low"]) <= entry - stop if direction == 1 else float(row["high"]) >= entry + stop
        target_hit = float(row["high"]) >= entry + target if direction == 1 else float(row["low"]) <= entry - target
        if stop_hit:
            stop_price = entry - direction * stop
            # Otwarcie poza SL jest gorsze od ceny stop i nie gwarantuje jej realizacji.
            opening_price = float(row["open"])
            outcome, exit_price = "stop_loss", (min(stop_price, opening_price) if direction == 1
                                                else max(stop_price, opening_price))
            break
        if target_hit:
            outcome, exit_price = "take_profit", entry + direction * target
            break
        exit_price = float(row["close"])
        outcome = "limit_czasu"
    exit_time = as_utc(last["timestamp_utc"]) + timedelta(minutes=1)
    points = direction * (exit_price - entry) - params.cost_points - params.slippage_points
    return {"signal": signal["signal"], "setup": signal["setup"], "entry": entry, "exit": exit_price,
            "entry_time": entry_time.isoformat(), "exit_time": exit_time.isoformat(),
            "hold_minutes": (exit_time - entry_time).total_seconds() / 60,
            "outcome": outcome, "pnl_points": round(points, 6), "pnl_value": round(points * params.point_value, 6),
            "warsaw_hour": entry_time.astimezone(config.WARSAW).hour}


def report(trades: list[dict], signal_count: int) -> dict:
    """Zwraca dziennik i metryki ogólne oraz według setupu i godziny Warszawy."""
    def metrics(items):
        """Oblicza metryki podanego zbioru; zerowa strata daje profit_factor None."""
        values = [x["pnl_points"] for x in items]
        wins, losses = [x for x in values if x > 0], [x for x in values if x < 0]
        return {"trades": len(values), "win_rate_percent": 100 * len(wins) / len(values) if values else 0,
                "average_win_points": sum(wins) / len(wins) if wins else 0,
                "average_loss_points": sum(losses) / len(losses) if losses else 0,
                "expectancy_points": sum(values) / len(values) if values else 0,
                "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
                "pnl_points": sum(values)}
    equity = peak = drawdown = 0.0
    for trade in sorted(trades, key=lambda x: x["exit_time"]):
        equity += trade["pnl_points"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    setups = sorted({x["setup"] for x in trades})
    hours = sorted({x["warsaw_hour"] for x in trades})
    return {"instrument": config.DB_SYMBOL, "total_signals": signal_count, **metrics(trades),
            "maximum_drawdown_points": drawdown,
            "by_setup": {x: metrics([t for t in trades if t["setup"] == x]) for x in setups},
            "by_warsaw_hour": {str(x): metrics([t for t in trades if t["warsaw_hour"] == x]) for x in hours},
            "journal": trades}


def run_backtest(candles: list[dict], params: TradeParameters | None = None) -> dict:
    """Analizuje historię chronologicznie i symuluje transakcje z zadanymi parametrami."""
    params = params or TradeParameters()
    ordered = sorted(candles, key=lambda x: as_utc(x["timestamp_utc"]))
    trades, last, counts, orb = [], None, {}, {}
    for index, row in enumerate(ordered):
        opened = as_utc(row["timestamp_utc"])
        closed = opened + timedelta(minutes=1)
        if not is_trading_minute(opened) or not is_signal_window(closed):
            continue
        day = closed.astimezone(config.NEW_YORK).date()
        result = generate_signal(
            ordered[:index + 1], last, counts.get(day, 0), orb.get(day, []), now=closed
        )
        if result["signal"] not in ("LONG", "SHORT"):
            continue
        last = closed
        counts[day] = counts.get(day, 0) + 1
        if result["setup"] == "OPENING_RANGE_BREAKOUT":
            orb.setdefault(day, []).append(result["signal"])
        trades.append(simulate_trade(ordered, index, result, params))
    return report(trades, sum(counts.values()))


def main() -> None:
    """Pobiera historię do jawnie podanego epoch UTC i wypisuje raport JSON."""
    parser = argparse.ArgumentParser(description="Backtest historyczny ES.")
    parser.add_argument("--end-epoch", type=int, required=True, help="Ostatnie otwarcie świecy UTC.")
    parser.add_argument("--limit", type=int, default=10000)
    parser.add_argument("--cost", type=float, default=config.ASSUMED_COST_POINTS)
    parser.add_argument("--slippage", type=float, default=0.0)
    parser.add_argument("--point-value", type=float, default=1.0)
    parser.add_argument("--max-hold", type=int, default=config.MAX_HOLD_MINUTES)
    args = parser.parse_args()
    params = TradeParameters(args.cost, args.slippage, args.point_value, args.max_hold)
    cutoff = datetime.fromtimestamp(args.end_epoch, timezone.utc)
    candles, _ = SP500Storage().load_context(cutoff, args.limit)
    print(json.dumps(run_backtest(candles, params), ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()
