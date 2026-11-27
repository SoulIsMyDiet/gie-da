"""Testy strategii, wskaźników, luk i zapisu Chat-gpt6 BTC/USD."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

PACKAGE_PATH = Path(__file__).resolve().parents[1]
PACKAGE_SPEC = importlib.util.spec_from_file_location(
    "chat_gpt6_btcusd_test",
    PACKAGE_PATH / "__init__.py",
    submodule_search_locations=[str(PACKAGE_PATH)],
)
assert PACKAGE_SPEC is not None and PACKAGE_SPEC.loader is not None
PACKAGE = importlib.util.module_from_spec(PACKAGE_SPEC)
sys.modules[PACKAGE_SPEC.name] = PACKAGE
PACKAGE_SPEC.loader.exec_module(PACKAGE)

from chat_gpt6_btcusd_test.BTCUSDStrategyV1 import (  # noqa: E402
    BTCUSDStrategyV1,
)
from chat_gpt6_btcusd_test.config import (  # noqa: E402
    DATABASE_SYMBOL,
    SAVE_NONE_SIGNALS,
    STRATEGY_NAME,
)
from chat_gpt6_btcusd_test.indicators import (  # noqa: E402
    aggregate_complete_m5,
    atr_wilder_series,
    ema_series,
    macd_series,
    rsi_wilder_series,
)
from chat_gpt6_btcusd_test.storage import BTCUSDSignalStorage  # noqa: E402
from chat_gpt6_btcusd_test import storage as storage_module  # noqa: E402

UTC = timezone.utc


def build_trending_candles(
    direction: str, count: int = 560
) -> list[dict[str, object]]:
    """Tworzy historię z czytelnym trendem i końcową korektą."""
    start = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    sign = 1.0 if direction == "LONG" else -1.0
    closes = [67_000.0]
    for index in range(1, count):
        if index >= count - 3:
            changes = (
                [-0.7, 1.5, 1.5]
                if direction == "LONG"
                else [0.7, -1.5, -1.5]
            )
            change = changes[index - (count - 3)]
        elif index % 8 == 0:
            change = -sign * 0.35
        else:
            change = sign * 0.65
        closes.append(closes[-1] + change)

    candles = []
    for index, close in enumerate(closes):
        previous_close = closes[index - 1] if index else close
        candles.append(
            {
                "timestamp_utc": start + timedelta(minutes=index),
                "open": previous_close,
                "high": max(previous_close, close) + 0.25,
                "low": min(previous_close, close) - 0.25,
                "close": close,
                "volume": 10.0 + index % 5,
            }
        )
    if direction == "LONG":
        candles[-2]["low"] = float(candles[-2]["close"]) - 4.0
    else:
        candles[-2]["high"] = float(candles[-2]["close"]) + 4.0
    return candles


def evaluate(candles: list[dict[str, object]], **kwargs) -> dict:
    """Ocenia dane testowe tuż po zamknięciu ostatniej świecy."""
    return BTCUSDStrategyV1().evaluate(
        candles,
        now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        **kwargs,
    )


def test_ema_rsi_atr_and_macd_formulas() -> None:
    """Sprawdza inicjalizację EMA oraz wskaźniki Wildera i MACD."""
    assert ema_series([1.0, 2.0, 3.0], 2) == pytest.approx(
        [1.0, 5 / 3, 23 / 9]
    )
    assert rsi_wilder_series([1.0, 2.0, 3.0, 4.0, 5.0], 4)[-1] == 100.0
    candles = [
        {"high": 12.0, "low": 9.0, "close": 11.0}
        for _ in range(14)
    ]
    assert atr_wilder_series(candles, 14)[-1] == 3.0
    line, signal, histogram = macd_series([1, 2, 3, 4], 2, 3, 2)
    assert histogram[-1] == pytest.approx(line[-1] - signal[-1])


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_strategy_emits_close_timestamp_and_numeric_parameters(
    direction: str,
) -> None:
    """Wynik zawiera wymagane wskaźniki i Unix czasu zamknięcia M1."""
    candles = build_trending_candles(direction)
    result = evaluate(candles)
    payload = result["signal_parameters"]

    assert result["signal"] == direction
    assert payload["instrument"] == "BTCUSD"
    assert payload["price"] == candles[-1]["close"]
    assert payload["timestamp"] == int(
        (candles[-1]["timestamp_utc"] + timedelta(minutes=1)).timestamp()
    )
    assert DATABASE_SYMBOL == "BTC/USD"
    assert STRATEGY_NAME == "Chat-gpt6/BTCUSDStrategyV1"
    assert SAVE_NONE_SIGNALS is True
    assert {
        "ema9_1m",
        "ema21_1m",
        "ema20_5m",
        "ema50_5m",
        "rsi14_1m",
        "atr14_1m",
        "macd_histogram_1m",
        "volume_ratio_1m",
    } <= payload.keys()
    json.dumps(payload, allow_nan=False)


def test_warmup_and_incomplete_m5_prevent_directional_signal() -> None:
    """Warm-up wymaga 100 M1 i 100 kompletnych M5."""
    short_history = build_trending_candles("LONG", count=300)
    assert evaluate(short_history)["signal"] == "NONE"

    candles = build_trending_candles("LONG", count=500)
    del candles[250]
    result = evaluate(candles)
    assert result["signal"] == "NONE"
    assert (
        result["data_quality"]["complete_m5_candles"]
        < 100
    )


def test_single_missing_minute_does_not_reset_or_invent_m5() -> None:
    """Pojedyncza luka zachowuje segment, a niekompletny kubełek M5 znika."""
    candles = build_trending_candles("LONG")
    del candles[250]
    ordered = BTCUSDStrategyV1._normalize_and_order(candles)[0]
    complete_m5 = aggregate_complete_m5(ordered)

    assert len(ordered) == len(candles)
    assert len(complete_m5) < 560 // 5
    result = evaluate(candles)
    assert result["signal"] == "LONG"
    assert result["data_quality"]["significant_gap_before_segment"] is False


def test_two_missing_minutes_reset_the_indicator_segment() -> None:
    """Dwie brakujące minuty odcinają stare wskaźniki od bieżącego segmentu."""
    candles = build_trending_candles("LONG")
    del candles[-130:-128]

    result = evaluate(candles)

    assert result["signal"] == "NONE"
    assert result["data_quality"]["significant_gap_before_segment"] is True
    assert result["data_quality"]["complete_m5_candles"] < 100


def test_unclosed_last_candle_is_excluded() -> None:
    """Świeca, której minuta jeszcze trwa, nie trafia do wyniku."""
    candles = build_trending_candles("LONG")
    now = candles[-1]["timestamp_utc"] + timedelta(seconds=30)

    result = BTCUSDStrategyV1().evaluate(candles, now=now)

    assert result["candle_open_epoch"] == int(candles[-2]["timestamp_utc"].timestamp())


def test_duplicate_timestamps_are_discarded() -> None:
    """Powtórzony timestamp nie liczy się dwukrotnie."""
    candles = build_trending_candles("LONG")
    candles.append(dict(candles[10]))

    ordered, duplicate_count = BTCUSDStrategyV1._normalize_and_order(candles)

    assert duplicate_count == 1
    assert len(ordered) == len(candles) - 1


def test_warsaw_dst_ambiguous_time_is_rejected_without_offset() -> None:
    """Jesienna powtórzona godzina nie jest arbitralnie zamieniana na UTC."""
    candle = {
        "datetime": datetime(2026, 10, 25, 2, 30),
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.0,
    }

    with pytest.raises(ValueError, match="Niejednoznaczny"):
        BTCUSDStrategyV1._parse_timestamp(candle)


def test_future_candle_does_not_change_signal() -> None:
    """Dane po chwili oceny nie wpływają na zamknięty wynik."""
    candles = build_trending_candles("LONG")
    now = candles[-1]["timestamp_utc"] + timedelta(minutes=1)
    baseline = BTCUSDStrategyV1().evaluate(candles, now=now)
    future = dict(candles[-1])
    future["timestamp_utc"] = future["timestamp_utc"] + timedelta(minutes=1)
    future.update(open=1.0, high=1_000_000.0, low=0.1, close=999_999.0)

    with_future = BTCUSDStrategyV1().evaluate(
        [*candles, future],
        now=now,
    )

    assert with_future["signal_parameters"] == baseline["signal_parameters"]


def test_none_storage_is_idempotent_and_does_not_require_spread() -> None:
    """Zapis NONE używa wspólnego repozytorium bez sztucznego spreadu."""
    repository = Mock()
    repository.save_algorithm_signal.return_value = True
    storage = BTCUSDSignalStorage(repository)
    opened = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    result = {
        "signal": "NONE",
        "price": 67_000.0,
        "candle_open_epoch": int(opened.timestamp()),
        "candle_close_time": (opened + timedelta(minutes=1)).isoformat(),
        "signal_parameters": {
            "price": 67_000.0,
            "signal": "NONE",
            "timestamp": int((opened + timedelta(minutes=1)).timestamp()),
            "instrument": "BTCUSD",
            "reason": "Brak warunków.",
        },
    }

    assert storage.save(result, 100) is True
    repository.save_algorithm_signal.assert_called_once()
    assert repository.save_algorithm_signal.call_args.kwargs[
        "save_none_signal"
    ] is True
    assert repository.save_algorithm_signal.call_args.kwargs[
        "save_signal_potential"
    ] is False


def test_save_none_flag_false_suppresses_signal_row(monkeypatch) -> None:
    """Jedna flaga pozwala wyłączyć rekordy NONE bez wyłączania ewaluacji."""
    monkeypatch.setattr(storage_module, "SAVE_NONE_SIGNALS", False)
    repository = Mock()
    storage = BTCUSDSignalStorage(repository)
    opened = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    result = {
        "signal": "NONE",
        "price": 67_000.0,
        "candle_open_epoch": int(opened.timestamp()),
        "candle_close_time": (opened + timedelta(minutes=1)).isoformat(),
        "signal_parameters": {
            "price": 67_000.0,
            "signal": "NONE",
            "timestamp": int((opened + timedelta(minutes=1)).timestamp()),
            "instrument": "BTCUSD",
            "reason": "Brak warunków.",
        },
    }

    storage.save(result, 100)

    assert repository.save_algorithm_signal.call_args.kwargs["save_signal"] is False
    assert repository.save_algorithm_signal.call_args.kwargs[
        "record_evaluation"
    ] is True
