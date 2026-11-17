"""Analiza rozkładu zmienności i wolumenu bez automatycznej zmiany progów."""

import argparse
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

import numpy as np
import pandas as pd

from config import (
    CANDLE_DURATION_SECONDS,
    RANGE_ASIA_END,
    RANGE_ASIA_MIN_COVERAGE,
    RANGE_ASIA_START,
)
from indicators import build_m1_indicators
from schedule import (
    LONDON,
    expected_candle_opens,
    is_trading_minute,
    segment_id,
    session_name,
)
from storage import XAUUSDStorage

UTC = timezone.utc
PERCENTILES = (5, 25, 50, 75, 95)
logger = logging.getLogger(__name__)


def calibration_report(
    candles: list[dict[str, Any]], now: datetime | None = None
) -> dict[str, Any]:
    """Zwraca percentyle ATR, zakresu Azji i wolumenu według sesji."""
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Czas kalibracji musi zawierać strefę czasową.")
    now = now.astimezone(UTC)
    closed_candles = []
    for candle in candles:
        stamp = candle.get("timestamp_utc")
        if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("Czas każdej świecy musi zawierać strefę czasową.")
        stamp = stamp.astimezone(UTC)
        if (
            stamp + timedelta(seconds=CANDLE_DURATION_SECONDS) <= now
            and is_trading_minute(stamp)
        ):
            closed_candles.append({**candle, "timestamp_utc": stamp})

    groups: dict[str, list[dict[str, Any]]] = {}
    for candle in closed_candles:
        groups.setdefault(segment_id(candle["timestamp_utc"]), []).append(candle)
    observations: dict[str, list[dict[str, float]]] = {
        "LONDON": [], "OVERLAP": [], "NY": []
    }
    asia_ranges: dict[str, float] = {}
    for segment in groups.values():
        frame = pd.DataFrame(segment).sort_values("timestamp_utc")
        frame = build_m1_indicators(frame)
        for _, row in frame.iterrows():
            name = session_name(row["timestamp_utc"])
            if name in observations and pd.notna(row["atr14_usd"]):
                observations[name].append({
                    "atr14_usd": float(row["atr14_usd"]),
                    "atr14_bps": float(row["atr14_bps"]),
                    "rel_volume": (
                        float(row["rel_volume"])
                        if pd.notna(row["rel_volume"])
                        else float("nan")
                    ),
                })
        london_days = {item["timestamp_utc"].astimezone(LONDON).date() for item in segment}
        for day in london_days:
            start = datetime.combine(day, RANGE_ASIA_START, tzinfo=LONDON)
            end = datetime.combine(day, RANGE_ASIA_END, tzinfo=LONDON)
            expected = expected_candle_opens(start, end)
            asia = [
                item for item in segment
                if start.astimezone(UTC)
                <= item["timestamp_utc"]
                < end.astimezone(UTC)
            ]
            coverage = (
                len({item["timestamp_utc"] for item in asia}) / len(expected)
                if expected
                else 0.0
            )
            if asia and coverage >= RANGE_ASIA_MIN_COVERAGE:
                asia_ranges[day.isoformat()] = max(item["high"] for item in asia) - min(
                    item["low"] for item in asia
                )
    report = {}
    for session, values in observations.items():
        report[session] = {}
        for key in ("atr14_usd", "atr14_bps", "rel_volume"):
            series = np.asarray([item[key] for item in values], dtype=float)
            series = series[np.isfinite(series)]
            report[session][key] = (
                {str(percentile): float(np.percentile(series, percentile))
                 for percentile in PERCENTILES}
                if len(series)
                else {}
            )
    ranges = np.asarray(list(asia_ranges.values()), dtype=float)
    report["ASIA_RANGE_USD"] = (
        {str(percentile): float(np.percentile(ranges, percentile))
         for percentile in PERCENTILES}
        if len(ranges)
        else {}
    )
    report["suggestions"] = _suggestions(report)
    return report


def _suggestions(report: dict[str, Any]) -> list[str]:
    """Proponuje ręczną ocenę progów na podstawie median i percentyli."""
    suggestions = []
    sessions = [report.get(name, {}) for name in ("LONDON", "OVERLAP", "NY")]
    atr_medians = [
        session.get("atr14_usd", {}).get("50")
        for session in sessions
        if session.get("atr14_usd", {}).get("50") is not None
    ]
    if atr_medians:
        suggestions.append(
            "Porównaj ATR_MIN_USD i ATR_MAX_USD z percentylami 5/95 "
            f"(mediany sesyjne: {', '.join(f'{value:.3f}' for value in atr_medians)}); "
            "zmień progi ręcznie dopiero po ocenie wyników."
        )
    asia_median = report.get("ASIA_RANGE_USD", {}).get("50")
    if asia_median is not None:
        suggestions.append(
            f"Mediana zakresu Azji to {asia_median:.3f} USD; porównaj ją z "
            "RANGE_MIN_USD/RANGE_MAX_USD przed ewentualną ręczną zmianą."
        )
    if not suggestions:
        suggestions.append("Brak wystarczających danych do zaproponowania zmian progów.")
    return suggestions


def main(argv: list[str] | None = None) -> int:
    """Odczytuje historię i drukuje kalibrację bez modyfikowania config.py."""
    parser = argparse.ArgumentParser(description="Kalibracja opisowa XAU/USD.")
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        data = XAUUSDStorage().load_context(datetime.now(UTC), args.limit)
        report = calibration_report(data)
        logger.info("Kalibracja XAU/USD (bez zmian w progach): %s", report)
        return 0
    except Exception:
        logger.exception("Kalibracja XAU/USD nie powiodła się.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
