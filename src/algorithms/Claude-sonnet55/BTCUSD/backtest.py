"""Backend: historyczny test sygnałów i symulacja maksymalnie 20 minut."""

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from math import isfinite

try:
    from . import config
    from .schedule import as_utc
    from .signal_logic import generate_signal
    from .storage import BTCUSDStorage
except ImportError:
    import config
    from schedule import as_utc
    from signal_logic import generate_signal
    from storage import BTCUSDStorage


@dataclass(frozen=True)
class BacktestSettings:
    """Przechowuje ustawienia kosztu i czasu dla symulacji transakcji.

    cost_bps jest opcjonalnym, ręcznie podanym kosztem całkowitym w punktach
    bazowych; gdy go brak, koszt wynika z kwotowań świec. slippage_bps opisuje
    dodatkowy poślizg, a max_hold_minutes ogranicza czas symulacji.
    """

    cost_bps: float | None = None
    slippage_bps: float = config.ASSUMED_SLIPPAGE_BPS
    max_hold_minutes: int = config.MAX_HOLD_MINUTES

    def __post_init__(self) -> None:
        """Odrzuca koszty ujemne i horyzont przekraczający limit strategii."""
        if (
            (self.cost_bps is not None and not isfinite(self.cost_bps))
            or not isfinite(self.slippage_bps)
            or (self.cost_bps is not None and self.cost_bps < 0)
            or self.slippage_bps < 0
        ):
            raise ValueError("Koszt i poślizg nie mogą być ujemne.")
        if (
            isinstance(self.max_hold_minutes, bool)
            or not isinstance(self.max_hold_minutes, int)
            or not 1 <= self.max_hold_minutes <= config.MAX_HOLD_MINUTES
        ):
            raise ValueError("Horyzont przekracza dozwolone 20 minut.")


def _spread_width(candle: dict) -> float | None:
    """Odczytuje szerokość spreadu z kwotowań świecy.

    Args:
        candle: Świeca z opcjonalnymi cenami bid i ask.

    Returns:
        Różnicę ask-bid albo None, jeśli brakuje któregoś kwotowania.

    Raises:
        ValueError: Gdy dostępne kwotowania są nieprawidłowe.
    """
    bid, ask = candle.get("bid"), candle.get("ask")
    if bid is None or ask is None:
        return None
    bid, ask = float(bid), float(ask)
    if not isfinite(bid) or not isfinite(ask) or bid <= 0 or ask < bid:
        raise ValueError("Świeca zawiera nieprawidłowe kwotowania BID/ASK.")
    return ask - bid


def simulate_trade(
    candles: list[dict],
    index: int,
    signal: dict,
    settings: BacktestSettings,
    *,
    end_index: int | None = None,
) -> dict:
    """Symuluje TP/SL po sygnale; przy jednoczesnym trafieniu rozstrzyga na SL."""
    entry_time = as_utc(candles[index]["timestamp_utc"]) + config.CANDLE_DURATION
    entry_price = float(signal["price"])
    direction = 1 if signal["signal"] == "LONG" else -1
    entry_spread = _spread_width(candles[index])
    target = float(signal["tp_price"])
    stop = float(signal["sl_price"])
    last_index = len(candles) if end_index is None else min(end_index, len(candles))
    exit_price = None
    exit_time = None
    exit_spread = None
    outcome = "nierozstrzygnieta"

    for next_index in range(index + 1, last_index):
        candle = candles[next_index]
        opened = as_utc(candle["timestamp_utc"])
        expected_open = (
            as_utc(candles[next_index - 1]["timestamp_utc"])
            + config.CANDLE_DURATION
        )
        if opened != expected_open:
            outcome = "luka_danych"
            exit_price = None
            exit_time = None
            break
        elapsed = (
            opened + config.CANDLE_DURATION - entry_time
        ).total_seconds() / config.SECONDS_PER_MINUTE
        if elapsed > settings.max_hold_minutes:
            break
        high, low = float(candle["high"]), float(candle["low"])
        stop_hit = low <= stop if direction > 0 else high >= stop
        target_hit = high >= target if direction > 0 else low <= target
        if stop_hit:
            exit_price = min(stop, float(candle["open"])) if direction > 0 else max(
                stop, float(candle["open"])
            )
            exit_time = opened + config.CANDLE_DURATION
            exit_spread = _spread_width(candle)
            outcome = "stop_loss"
            break
        if target_hit:
            exit_price = target
            exit_time = opened + config.CANDLE_DURATION
            exit_spread = _spread_width(candle)
            outcome = "take_profit"
            break
        exit_price = float(candle["close"])
        exit_time = opened + config.CANDLE_DURATION
        exit_spread = _spread_width(candle)
        outcome = "limit_czasu"

    if exit_price is not None and exit_time is not None:
        if (
            exit_time
            < entry_time
            + timedelta(minutes=settings.max_hold_minutes)
            and outcome == "limit_czasu"
        ):
            exit_price = None
            exit_time = None
            outcome = "niewystarczajaca_historia"

    if exit_price is not None:
        gross_bps = (
            direction
            * (exit_price - entry_price)
            / entry_price
            * config.BASIS_POINTS_PER_UNIT
        )
        if settings.cost_bps is not None:
            applied_cost_bps = settings.cost_bps
        elif entry_spread is not None and exit_spread is not None:
            applied_cost_bps = (
                (entry_spread + exit_spread)
                / 2
                / entry_price
                * config.BASIS_POINTS_PER_UNIT
            )
        else:
            applied_cost_bps = None
        pnl_bps = (
            gross_bps - applied_cost_bps - settings.slippage_bps
            if applied_cost_bps is not None
            else None
        )
    else:
        pnl_bps = None
        applied_cost_bps = None
    return {
        "signal": signal["signal"],
        "setup": signal["setup"],
        "entry_price": entry_price,
        "exit_price": exit_price,
        "entry_time": entry_time.isoformat(),
        "exit_time": exit_time.isoformat() if exit_time else None,
        "hold_minutes": (
            (exit_time - entry_time).total_seconds() / 60 if exit_time else None
        ),
        "outcome": outcome,
        "pnl_bps": pnl_bps,
        "applied_cost_bps": applied_cost_bps,
    }


def _metrics(trades: list[dict]) -> dict:
    """Oblicza metryki tylko dla transakcji z możliwym do ustalenia wynikiem."""
    completed = [trade for trade in trades if trade["pnl_bps"] is not None]
    wins = [trade["pnl_bps"] for trade in completed if trade["pnl_bps"] > 0]
    losses = [trade["pnl_bps"] for trade in completed if trade["pnl_bps"] < 0]
    values = [trade["pnl_bps"] for trade in completed]
    return {
        "trades": len(trades),
        "resolved_trades": len(completed),
        "unresolved_trades": len(trades) - len(completed),
        "win_rate_percent": (
            config.PERCENT_PER_UNIT * len(wins) / len(completed)
            if completed
            else 0.0
        ),
        "expectancy_bps": sum(values) / len(values) if values else None,
        "profit_factor": (
            sum(wins) / abs(sum(losses)) if losses else None
        ),
        "net_pnl_bps": sum(values) if values else 0.0,
    }


def run_backtest(
    candles: list[dict],
    settings: BacktestSettings | None = None,
    *,
    minimum_score: int | None = None,
    start_index: int = 0,
    end_index: int | None = None,
) -> dict:
    """Ocenia każdą świecę wyłącznie na podstawie jej przeszłości i symuluje wynik."""
    settings = settings or BacktestSettings()
    ordered = sorted(candles, key=lambda row: as_utc(row["timestamp_utc"]))
    signals = []
    trades = []
    last_signal_timestamp = None
    stop_index = len(ordered) if end_index is None else min(end_index, len(ordered))
    for index, row in enumerate(ordered[:stop_index]):
        opened = as_utc(row["timestamp_utc"])
        closed = opened + config.CANDLE_DURATION
        result = generate_signal(
            ordered[: index + 1],
            now=closed,
            last_signal_timestamp=last_signal_timestamp,
            minimum_score=minimum_score,
        )
        if result["signal"] not in {"LONG", "SHORT"}:
            continue
        last_signal_timestamp = int(opened.timestamp())
        if index < start_index:
            continue
        signals.append(result)
        trades.append(
            simulate_trade(
                ordered,
                index,
                result,
                settings,
                end_index=end_index,
            )
        )
    return {
        "instrument": config.INSTRUMENT_PARAMS_NAME,
        "cost_override_bps": settings.cost_bps,
        "slippage_bps": settings.slippage_bps,
        "max_hold_minutes": settings.max_hold_minutes,
        **_metrics(trades),
        "signals": signals,
        "trades": trades,
    }


def main() -> int:
    """Uruchamia test historyczny dla zakresu zakończonego wskazaną epoką UTC."""
    parser = argparse.ArgumentParser(description="Backtest BTC/USD bez użycia przyszłości.")
    parser.add_argument("--end-epoch", type=int, required=True)
    parser.add_argument("--limit", type=int, default=config.HISTORY_LIMIT)
    parser.add_argument(
        "--cost-bps",
        type=float,
        default=None,
        help="Ręcznie podany koszt całkowity zamiast spreadu z kwotowań świec.",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=config.ASSUMED_SLIPPAGE_BPS,
    )
    args = parser.parse_args()
    end = datetime.fromtimestamp(args.end_epoch, timezone.utc)
    candles = BTCUSDStorage().load_context(end, args.limit)
    settings = BacktestSettings(args.cost_bps, args.slippage_bps)
    print(
        json.dumps(
            run_backtest(candles, settings),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
