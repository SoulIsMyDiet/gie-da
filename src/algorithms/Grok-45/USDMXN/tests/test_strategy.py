"""Testy wskaźników, sygnałów i integracji Grok-45/USDMXN."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

PACKAGE_PATH = Path(__file__).resolve().parents[1]
PACKAGE_SPEC = importlib.util.spec_from_file_location(
    "grok45_usdmxn_test",
    PACKAGE_PATH / "__init__.py",
    submodule_search_locations=[str(PACKAGE_PATH)],
)
assert PACKAGE_SPEC is not None and PACKAGE_SPEC.loader is not None
PACKAGE = importlib.util.module_from_spec(PACKAGE_SPEC)
sys.modules[PACKAGE_SPEC.name] = PACKAGE
PACKAGE_SPEC.loader.exec_module(PACKAGE)

from grok45_usdmxn_test.USDMXNStrategyV1 import (  # noqa: E402
    USDMXNStrategyV1,
    process_new_candle,
)
from grok45_usdmxn_test.indicators import (  # noqa: E402
    atr_series,
    bollinger_series,
    ema_series,
    momentum_series,
    percentile,
    rsi_series,
)

UTC = timezone.utc
ALWAYS_OPEN = lambda _symbol, _timestamp: True


def build_trending_candles(direction: str, count: int = 240) -> list[dict]:
    """Tworzy kontrolowany szereg z mocnym ruchem na końcu."""
    start = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    sign = 1 if direction == "LONG" else -1
    closes = [17.0]
    for index in range(1, count):
        if index >= count - 8:
            change = sign * 0.004
        elif index % 6 == 0:
            change = -sign * 0.0007
        else:
            change = sign * 0.0012
        closes.append(closes[-1] + change)

    candles = []
    for index, close in enumerate(closes):
        previous_close = closes[index - 1] if index else close
        candles.append(
            {
                "timestamp_utc": start + timedelta(minutes=index),
                "open": previous_close,
                "high": max(previous_close, close) + 0.0003,
                "low": min(previous_close, close) - 0.0003,
                "close": close,
                "volume": 100 + index,
            }
        )
    return candles


def evaluate(candles: list[dict]) -> dict:
    """Ocenia testowe świece po zamknięciu ostatniej minuty."""
    return USDMXNStrategyV1(ALWAYS_OPEN).evaluate(
        candles,
        now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
    )


def test_indicator_initialization_and_formulas() -> None:
    """Sprawdza podstawowe wartości EMA, RSI, ATR, pasm i momentum."""
    assert ema_series([1.0, 2.0, 3.0], 2) == pytest.approx([1.0, 5 / 3, 23 / 9])
    assert rsi_series([1.0, 2.0, 3.0, 4.0, 5.0], 4)[-1] == 100.0
    candles = [
        {"high": 12.0, "low": 9.0, "close": 11.0}
        for _ in range(14)
    ]
    assert atr_series(candles, 14)[-1] == 3.0
    middle, upper, lower, width = bollinger_series(
        [float(value) for value in range(1, 21)], 20, 2.0
    )
    assert middle[-1] == pytest.approx(10.5)
    assert upper[-1] - middle[-1] == pytest.approx(2 * (33.25**0.5))
    assert width[-1] == pytest.approx((upper[-1] - lower[-1]) / 10.5 * 100)
    assert momentum_series([100, 101, 102, 103, 104, 110], 5)[-1] == pytest.approx(10)
    assert percentile([1.0, 2.0, 3.0, 4.0], 20) == pytest.approx(1.6)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_controlled_history_generates_expected_direction(direction: str) -> None:
    """Pełna kalkulacja wskaźników spełnia wszystkie filtry danego kierunku."""
    candles = build_trending_candles(direction)
    result = evaluate(candles)
    payload = result["signal_parameters"]
    conditions = payload["conditions"][direction.lower()]
    optional_bb_branch = (
        "close_above_bb_lower_and_rsi_below_maximum"
        if direction == "LONG"
        else "close_below_bb_upper_and_rsi_above_minimum"
    )
    assert result["signal"] == direction, payload
    assert all(value for name, value in conditions.items() if name != optional_bb_branch)
    assert payload["instrument"] == "USDMXN"
    assert payload["price"] == candles[-1]["close"]
    assert payload["timestamp"] == int(candles[-1]["timestamp_utc"].timestamp())
    assert result["timestamp"] == payload["timestamp"]
    assert result["reason"] == payload["reason"]
    assert isinstance(result["candle_close_time"], str)
    assert payload["thresholds"]["max_holding_minutes"] == 20
    json.dumps(payload, allow_nan=False)


def test_each_required_filter_blocks_entry_when_false() -> None:
    """Każdy obowiązkowy warunek jest wymagany, a ścieżka BB używa operatora LUB."""
    current = {"open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0}
    base = {
        "ema_fast": 99.5,
        "ema_slow": 99.0,
        "rsi": 60.0,
        "rsi_prev": 59.0,
        "bb_middle": 100.1,
        "bb_upper": 100.5,
        "bb_lower": 99.5,
        "bb_width": 1.0,
        "atr": 1.0,
        "atr_minimum_threshold": 0.5,
        "momentum_5": 0.1,
    }
    baseline = USDMXNStrategyV1._conditions(current, base)["long"]
    assert USDMXNStrategyV1._all_required_conditions(baseline, "long")
    assert baseline["bollinger_filter"]
    assert not baseline["close_above_bb_middle"]
    assert baseline["close_above_bb_lower_and_rsi_below_maximum"]

    cases = {
        "ema_fast_above_slow": {"ema_slow": 99.5},
        "close_above_ema_fast": {"ema_fast": 100.0},
        "rsi_above_minimum": {"rsi": 52.0, "rsi_prev": 51.0},
        "rsi_increasing": {"rsi_prev": 60.0},
        "momentum_above_minimum": {"momentum_5": 0.08},
        "bollinger_filter": {"bb_middle": 100.0, "bb_lower": 100.0},
        "atr_not_extremely_low": {"atr": 0.0},
    }
    for condition_name, updates in cases.items():
        changed = USDMXNStrategyV1._conditions(current, {**base, **updates})["long"]
        assert not changed[condition_name]
        assert not USDMXNStrategyV1._all_required_conditions(changed, "long")


def test_none_and_warmup_return_serializable_diagnostics() -> None:
    """Płaski rynek i krótka historia zwracają NONE bez NaN w JSON."""
    flat = build_trending_candles("LONG")
    for candle in flat:
        candle.update(open=17.0, high=17.0, low=17.0, close=17.0)
    no_signal = evaluate(flat)
    assert no_signal["signal"] == "NONE"
    assert no_signal["signal_parameters"]["reason"] == (
        "Warunki LONG ani SHORT nie zostały spełnione."
    )
    assert not no_signal["signal_parameters"]["conditions"]["long"][
        "atr_not_extremely_low"
    ]
    json.dumps(no_signal["signal_parameters"], allow_nan=False)

    short_history = build_trending_candles("LONG", count=99)
    warmup = evaluate(short_history)
    assert warmup["signal"] == "NONE"
    assert not warmup["signal_parameters"]["data"]["warmup_ready"]


def test_single_and_long_gaps_are_used_without_reset_or_interpolation() -> None:
    """Dostępne świece pozostają w szeregu mimo pojedynczej i dłuższej luki."""
    one_missing = build_trending_candles("LONG")
    del one_missing[100]
    result = evaluate(one_missing)
    diagnostics = result["signal_parameters"]["data"]["gap_diagnostics"]
    assert result["signal"] == "LONG"
    assert diagnostics["warmup_reset"] is False
    assert diagnostics["non_contiguous_intervals"] == 1
    assert diagnostics["recent_interval_limit"] == 20
    assert len(one_missing) == result["signal_parameters"]["data"]["m1_candles_available"]

    long_gap = build_trending_candles("LONG")
    del long_gap[100:104]
    result = evaluate(long_gap)
    diagnostics = result["signal_parameters"]["data"]["gap_diagnostics"]
    assert result["signal"] == "LONG"
    assert diagnostics["warmup_reset"] is False
    assert diagnostics["delivery_pause_count"] == 1


@pytest.mark.parametrize(
    ("wall_time", "offset"),
    [(datetime(2026, 1, 5, 11), 60), (datetime(2026, 7, 5, 12), 120)],
)
def test_warsaw_timestamp_converts_to_unix_open_time(
    wall_time: datetime, offset: int
) -> None:
    """Zimowy i letni czas lokalny są zamieniane na właściwy timestamp UTC."""
    candle = {
        "datetime": wall_time,
        "utc_offset_minutes": offset,
        "open": 17.0,
        "high": 17.1,
        "low": 16.9,
        "close": 17.0,
    }
    result = USDMXNStrategyV1(ALWAYS_OPEN).evaluate(
        [candle], now=datetime(2026, 8, 1, tzinfo=UTC)
    )
    expected = wall_time.replace(
        tzinfo=timezone(timedelta(minutes=offset))
    ).astimezone(UTC)
    assert result["signal_parameters"]["timestamp"] == int(expected.timestamp())


def test_forming_duplicate_and_wrong_instrument_candles_are_rejected() -> None:
    """Niepełna, powielona lub obca instrumentowi świeca nie daje wyniku pozornego."""
    candles = build_trending_candles("LONG")
    with pytest.raises(ValueError, match="niezakończonej"):
        USDMXNStrategyV1(ALWAYS_OPEN).evaluate(
            candles, now=candles[-1]["timestamp_utc"] + timedelta(seconds=30)
        )
    with pytest.raises(ValueError, match="powtórzony timestamp"):
        evaluate([*candles, candles[-1]])
    wrong_instrument = dict(candles[-1], instrument="EUR/USD")
    with pytest.raises(ValueError, match="wyłącznie USDMXN"):
        evaluate([wrong_instrument])


def test_none_storage_flag_is_central_and_signal_name_is_independent() -> None:
    """NONE można wyłączyć jedną flagą, a nazwa strategii nie koliduje z Chat-gpt6."""
    from grok45_usdmxn_test import USDMXNStrategyV1 as strategy_module
    from grok45_usdmxn_test.config import STRATEGY_NAME
    from grok45_usdmxn_test.storage import USDMXNSignalStorage

    assert STRATEGY_NAME == "Grok-45/USDMXNStrategyV1"
    repository = Mock()
    storage = USDMXNSignalStorage(repository)
    result = {
        "signal": "NONE",
        "timestamp": 1,
        "candle_close_time": "2026-01-01T00:01:00+01:00",
        "candle_open_epoch": 1,
        "signal_parameters": {
            "price": 17.0,
            "signal": "NONE",
            "timestamp": 1,
            "instrument": "USDMXN",
            "reason": "Brak sygnału; warunki blokują wejście.",
            "indicators": {"ema_fast": 17.0},
        },
    }
    with patch.object(strategy_module, "SAVE_NONE_SIGNALS", False):
        assert storage.save(result, 100) is repository.save_algorithm_signal.return_value
    repository.save_algorithm_signal.assert_called_once_with(
        "USD/MXN",
        STRATEGY_NAME,
        {
            "signal": "NONE",
            "timestamp": result["candle_close_time"],
            "candle_open_epoch": 1,
            "signal_parameters": {
                "price": 17.0,
                "signal": "NONE",
                "timestamp": 1,
                "instrument": "USDMXN",
                "reason": "Warunki LONG ani SHORT nie zostały spełnione.",
            },
        },
        100,
        record_evaluation=True,
        save_signal=False,
        enqueue_notification=False,
    )


def test_process_new_candle_public_entry_point() -> None:
    """Punkt wejścia zwraca wymagany JSON bez bezpośredniego dostępu do bazy."""
    candles = build_trending_candles("SHORT")
    result = process_new_candle(
        candles, now=candles[-1]["timestamp_utc"] + timedelta(minutes=1)
    )
    assert result["signal"] == "SHORT"
    assert result["signal_parameters"]["timestamp"] == int(
        candles[-1]["timestamp_utc"].timestamp()
    )
