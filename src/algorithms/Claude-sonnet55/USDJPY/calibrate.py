"""Opisowa kalibracja progów USD/JPY; skrypt nie zmienia konfiguracji."""

import argparse
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

import numpy as np
import pandas as pd

import config
from indicators import build_m1_indicators
from schedule import TOKYO, UTC, expected_candle_opens, is_trading_minute, segment_id, session_name
from storage import USDJPYStorage

PERCENTILES = (5, 25, 50, 75, 95)
logger = logging.getLogger(__name__)


def _percentiles(values: list[float]) -> dict[str, float]:
    """Zwraca wymagane percentyle ze skończonych obserwacji."""
    numeric = np.asarray(values, dtype=float)
    numeric = numeric[np.isfinite(numeric)]
    return (
        {
            str(percentile): float(np.percentile(numeric, percentile))
            for percentile in PERCENTILES
        }
        if numeric.size
        else {}
    )


def calibration_report(
    candles: list[dict[str, Any]], now: datetime | None = None
) -> dict[str, Any]:
    """Raportuje rozkłady ATR, zakresu Tokio, skoków i wolumenu według sesji."""
    now = datetime.now(UTC) if now is None else now
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Czas kalibracji musi zawierać strefę.")
    now = now.astimezone(UTC)
    closed = []
    for candle in candles:
        stamp = candle.get("timestamp_utc")
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("Czas każdej świecy musi zawierać strefę.")
        stamp = stamp.astimezone(UTC)
        if (
            stamp + timedelta(seconds=config.CANDLE_DURATION_SECONDS) <= now
            and is_trading_minute(stamp)
        ):
            closed.append({**candle, "timestamp_utc": stamp})
    by_segment: dict[str, list[dict[str, Any]]] = {}
    for candle in closed:
        by_segment.setdefault(segment_id(candle["timestamp_utc"]), []).append(candle)
    sessions: dict[str, dict[str, list[float]]] = {
        name: {
            "atr14_pips": [],
            "range_spike_ratio": [],
            "rel_volume": [],
            "tokyo_range_pips": [],
        }
        for name in ("TOKYO", "LONDON", "OVERLAP", "NY", "LATE")
    }
    tokyo_ranges = []
    for segment in by_segment.values():
        frame = pd.DataFrame(segment).sort_values("timestamp_utc").reset_index(drop=True)
        frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
        frame = build_m1_indicators(frame)
        range_values = frame["high"] - frame["low"]
        average = range_values.rolling(
            config.RANGE_SPIKE_LOOKBACK,
            min_periods=config.RANGE_SPIKE_LOOKBACK,
        ).mean()
        baseline = range_values.rolling(
            config.RANGE_SPIKE_BASELINE,
            min_periods=config.RANGE_SPIKE_BASELINE,
        ).median()
        frame["range_spike_ratio"] = average / baseline.replace(0, np.nan)
        for _, row in frame.iterrows():
            group = sessions[session_name(row["timestamp_utc"])]
            for key in group:
                value = row.get(key)
                if value is not None and pd.notna(value):
                    group[key].append(float(value))
        tokyo_days = {
            candle["timestamp_utc"].astimezone(TOKYO).date()
            for candle in segment
        }
        for tokyo_day in tokyo_days:
            start = datetime.combine(
                tokyo_day, config.TOKYO_RANGE_START_JST, tzinfo=TOKYO
            )
            end = datetime.combine(
                tokyo_day, config.TOKYO_RANGE_END_JST, tzinfo=TOKYO
            )
            expected = expected_candle_opens(start, end)
            selected = [
                candle
                for candle in segment
                if start.astimezone(UTC)
                <= candle["timestamp_utc"]
                < end.astimezone(UTC)
            ]
            coverage = (
                len({candle["timestamp_utc"] for candle in selected}) / len(expected)
                if expected
                else 0.0
            )
            if selected and coverage >= config.TOKYO_RANGE_MIN_COVERAGE:
                range_pips = (
                    max(candle["high"] for candle in selected)
                    - min(candle["low"] for candle in selected)
                ) / config.PIP_SIZE
                tokyo_ranges.append(range_pips)
                sessions["TOKYO"]["tokyo_range_pips"].append(range_pips)
    report = {
        name: {key: _percentiles(values) for key, values in metrics.items()}
        for name, metrics in sessions.items()
    }
    report["TOKYO_RANGE_PIPS"] = _percentiles(tokyo_ranges)
    report["suggestions"] = _suggestions(report)
    return report


def _suggestions(report: dict[str, Any]) -> list[str]:
    """Tworzy opisowe rekomendacje progów bez automatycznej zmiany config.py."""
    suggestions = []
    atr_medians = [
        report.get(session, {}).get("atr14_pips", {}).get("50")
        for session in ("TOKYO", "LONDON", "OVERLAP", "NY")
    ]
    atr_medians = [value for value in atr_medians if value is not None]
    if atr_medians:
        suggestions.append(
            "Porównaj ATR_MIN_PIPS/ATR_MAX_PIPS z percentylami 5/95 "
            f"(mediany sesyjne: {', '.join(f'{value:.2f}' for value in atr_medians)})."
        )
    range_median = report.get("TOKYO_RANGE_PIPS", {}).get("50")
    if range_median is not None:
        suggestions.append(
            f"Mediana zakresu Tokio wynosi {range_median:.2f} pipsów; "
            "oceń ją względem RANGE_MIN_PIPS/RANGE_MAX_PIPS."
        )
    if not suggestions:
        suggestions.append("Brak wystarczających danych do propozycji progów.")
    return suggestions


def main(argv: list[str] | None = None) -> int:
    """Odczytuje świece z MySQL i raportuje kalibrację bez zmian parametrów."""
    parser = argparse.ArgumentParser(description="Kalibracja USD/JPY.")
    parser.add_argument("--limit", type=int, default=config.HISTORY_LIMIT)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        candles = USDJPYStorage().load_context(datetime.now(UTC), args.limit)
        logger.info("Raport kalibracji USD/JPY: %s", calibration_report(candles))
        return 0
    except Exception:
        logger.exception("Kalibracja USD/JPY nie powiodła się.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
