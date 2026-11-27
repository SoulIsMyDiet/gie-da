"""Backend: chronologiczna kalibracja progu score bez zmiany konfiguracji."""

import argparse
from datetime import datetime, timezone
import json

try:
    from . import config
    from .backtest import BacktestSettings, run_backtest
    from .storage import BTCUSDStorage
except ImportError:
    import config
    from backtest import BacktestSettings, run_backtest
    from storage import BTCUSDStorage

def calibrate_thresholds(
    candles: list[dict],
    settings: BacktestSettings | None = None,
    thresholds: tuple[int, ...] = config.CALIBRATION_THRESHOLDS,
) -> dict:
    """Porównuje progi na podziale czasowym i nie zapisuje zmian do config.py."""
    ordered = sorted(candles, key=lambda row: row["timestamp_utc"])
    if len(ordered) < (
        config.M1_WARMUP_CANDLES + config.CALIBRATION_MINIMUM_TEST_TRADES
    ):
        raise ValueError("Za mało danych do kalibracji progu.")
    if any(
        isinstance(threshold, bool)
        or not isinstance(threshold, int)
        or not 0 <= threshold <= 100
        for threshold in thresholds
    ):
        raise ValueError("Progi score muszą być liczbami całkowitymi 0–100.")
    split_index = max(
        config.M1_WARMUP_CANDLES,
        int(len(ordered) * config.CALIBRATION_TRAIN_FRACTION),
    )
    settings = settings or BacktestSettings()
    results = []
    for threshold in thresholds:
        training = run_backtest(
            ordered,
            settings,
            minimum_score=threshold,
            end_index=split_index,
        )
        validation = run_backtest(
            ordered,
            settings,
            minimum_score=threshold,
            start_index=split_index,
        )
        results.append(
            {
                "minimum_score": threshold,
                "training": {
                    "trades": training["resolved_trades"],
                    "expectancy_bps": training["expectancy_bps"],
                },
                "validation": {
                    "trades": validation["resolved_trades"],
                    "expectancy_bps": validation["expectancy_bps"],
                    "win_rate_percent": validation["win_rate_percent"],
                },
            }
        )
    eligible = [
        row
        for row in results
        if (
            row["validation"]["trades"]
            >= config.CALIBRATION_MINIMUM_TEST_TRADES
        )
        and row["validation"]["expectancy_bps"] is not None
    ]
    recommended = (
        max(
            eligible,
            key=lambda row: row["validation"]["expectancy_bps"],
        )["minimum_score"]
        if eligible
        else None
    )
    return {
        "instrument": config.INSTRUMENT_PARAMS_NAME,
        "train_fraction": split_index / len(ordered),
        "recommended_score_threshold": recommended,
        "recommendation_applied_automatically": False,
        "threshold_results": results,
    }


def main() -> int:
    """Uruchamia kalibrację dla historii do wskazanej epoki UTC."""
    parser = argparse.ArgumentParser(description="Kalibracja progu score BTC/USD.")
    parser.add_argument("--end-epoch", type=int, required=True)
    parser.add_argument("--limit", type=int, default=config.HISTORY_LIMIT)
    args = parser.parse_args()
    end = datetime.fromtimestamp(args.end_epoch, timezone.utc)
    candles = BTCUSDStorage().load_context(end, args.limit)
    print(
        json.dumps(
            calibrate_thresholds(candles),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
