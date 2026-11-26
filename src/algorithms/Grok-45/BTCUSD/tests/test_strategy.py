"""Testy wskaźników, sygnałów i zapisu strategii BTC/USD."""

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
    "grok45_btcusd_test",
    PACKAGE_PATH / "__init__.py",
    submodule_search_locations=[str(PACKAGE_PATH)],
)
assert PACKAGE_SPEC is not None and PACKAGE_SPEC.loader is not None
PACKAGE = importlib.util.module_from_spec(PACKAGE_SPEC)
sys.modules[PACKAGE_SPEC.name] = PACKAGE
PACKAGE_SPEC.loader.exec_module(PACKAGE)

from grok45_btcusd_test.BTCUSDStrategyV1 import (  # noqa: E402
    BTCUSDStrategyV1,
    STRATEGY_CONTEXT,
)
from grok45_btcusd_test.config import (  # noqa: E402
    DATABASE_SYMBOL,
    STRATEGY_NAME,
)
from grok45_btcusd_test.indicators import (  # noqa: E402
    atr_wilder,
    ema_series,
    rsi_wilder,
    volume_sma,
)
from grok45_btcusd_test.storage import BTCUSDSignalStorage  # noqa: E402
from grok45_btcusd_test import storage as storage_module  # noqa: E402

UTC = timezone.utc


def build_trending_candles(direction: str, count: int = 60) -> list[dict]:
    """Tworzy serię z trendem, umiarkowanym RSI i świecą wybiciową na końcu."""
    start = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    sign = 1 if direction == "LONG" else -1
    closes = [67_000.0]
    changes = [sign * 0.3, sign * -0.2] * (count - 2)
    for change in changes[: count - 2]:
        closes.append(closes[-1] + change)
    closes.append(closes[-1] + sign * 0.5)

    candles = []
    for index, close in enumerate(closes):
        previous_close = closes[index - 1] if index else close
        candles.append(
            {
                "timestamp_utc": start + timedelta(minutes=index),
                "open": previous_close,
                "high": max(previous_close, close) + 0.05,
                "low": min(previous_close, close) - 0.05,
                "close": close,
                "volume": 200.0 if index == count - 1 else 100.0,
            }
        )
    return candles


def evaluate(candles: list[dict], **kwargs) -> dict:
    """Ocenia testowe świece po zamknięciu ostatniej minuty."""
    return BTCUSDStrategyV1().evaluate(
        candles,
        now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
        **kwargs,
    )


def test_indicator_formulas() -> None:
    """Sprawdza EMA, RSI Wildera, SMA wolumenu oraz ATR Wildera."""
    assert ema_series([1.0, 2.0, 3.0], 2) == pytest.approx(
        [1.0, 5 / 3, 23 / 9]
    )
    assert rsi_wilder([1.0, 2.0, 3.0, 4.0, 5.0], 4) == 100.0
    assert volume_sma([10.0, 20.0, 30.0], 3) == 20.0
    candles = [
        {"high": 12.0, "low": 9.0, "close": 11.0}
        for _ in range(14)
    ]
    assert atr_wilder(candles, 14) == 3.0


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_rules_generate_expected_signal_and_exact_json(direction: str) -> None:
    """Spełnienie wszystkich filtrów generuje poprawny, krótki obiekt JSON."""
    candles = build_trending_candles(direction)
    result = evaluate(candles)
    payload = result["signal_parameters"]

    assert result["signal"] == direction
    assert set(payload) == {"price", "signal", "timestamp", "instrument", "reason"}
    assert payload["instrument"] == "BTCUSD"
    assert payload["signal"] == direction
    assert payload["price"] == candles[-1]["close"]
    assert payload["timestamp"] == int(
        (candles[-1]["timestamp_utc"] + timedelta(minutes=1)).timestamp()
    )
    assert STRATEGY_NAME == "Grok-45/BTCUSDStrategyV1"
    assert DATABASE_SYMBOL == "BTC/USD"
    assert "Strategia mikrotransakcji BTC/USD M1 V1" in STRATEGY_CONTEXT
    json.dumps(payload, allow_nan=False)


def test_dataframe_input_returns_only_the_required_signal_fields() -> None:
    """Interfejs to_dict pozwala użyć DataFrame bez zależności od Pandas."""
    candles = build_trending_candles("LONG")

    class CandleFrame:
        """Udaje DataFrame dla testu formatu wejściowego."""

        def to_dict(self, orient: str = "dict") -> object:
            """Zwraca świece w formacie rekordów wymaganym przez strategię."""
            assert orient == "records"
            return candles

    signal = BTCUSDStrategyV1().generate_signal(
        CandleFrame(),
        now=candles[-1]["timestamp_utc"] + timedelta(minutes=1),
    )

    assert set(signal) == {"price", "signal", "timestamp", "instrument", "reason"}
    assert signal["signal"] == "LONG"


@pytest.mark.parametrize("volume", [None, float("nan"), -1.0, True])
def test_invalid_volume_is_ignored_for_btc_signals(volume) -> None:
    """Nieobecny lub błędny wolumen nie blokuje sygnału BTC/USD."""
    candles = build_trending_candles("LONG")
    for candle in candles:
        candle["volume"] = volume

    result = evaluate(candles)

    assert result["signal"] == "LONG"
    assert "wolumen" not in result["signal_parameters"]["reason"].lower()
    assert set(result["signal_parameters"]) == {
        "price",
        "signal",
        "timestamp",
        "instrument",
        "reason",
    }


def test_single_gap_does_not_block_a_signal() -> None:
    """Wskaźniki liczą dostępne świece bez interpolowania pojedynczej luki."""
    candles = build_trending_candles("LONG")
    del candles[25]

    assert evaluate(candles)["signal"] == "LONG"


def test_cooldown_forces_none_for_a_recent_directional_signal() -> None:
    """Sygnał LONG lub SHORT z ostatnich czterech minut blokuje nowe wejście."""
    candles = build_trending_candles("LONG")
    previous_signal_open = int(candles[-1]["timestamp_utc"].timestamp()) - 3 * 60

    result = evaluate(candles, last_signal_timestamp=previous_signal_open)

    assert result["signal"] == "NONE"
    assert "Cooldown" in result["signal_parameters"]["reason"]


def test_warsaw_naive_datetime_is_converted_to_utc() -> None:
    """Czas bez strefy w kolumnie datetime jest interpretowany jako Europe/Warsaw."""
    candles = build_trending_candles("LONG")
    local_open = candles[-1]["timestamp_utc"].astimezone(
        timezone(timedelta(hours=1))
    ).replace(tzinfo=None)
    candles[-1]["datetime"] = local_open
    candles[-1].pop("timestamp_utc")
    local_timezone = timezone(timedelta(hours=1))
    local_close = local_open.replace(tzinfo=local_timezone) + timedelta(minutes=1)

    result = BTCUSDStrategyV1().evaluate(
        candles,
        now=local_close,
    )

    assert result["signal_parameters"]["timestamp"] == int(
        local_close.timestamp()
    )


@pytest.mark.parametrize(
    "local_time",
    [datetime(2026, 10, 25, 2, 30), datetime(2026, 3, 29, 2, 30)],
)
def test_ambiguous_or_nonexistent_warsaw_time_is_rejected(
    local_time: datetime,
) -> None:
    """Nie zgaduje offsetu przy zmianie czasu letniego w Warszawie."""
    candles = build_trending_candles("LONG")
    candles[-1]["datetime"] = local_time
    candles[-1].pop("timestamp_utc")

    with pytest.raises(ValueError, match="Lokalny czas"):
        BTCUSDStrategyV1().evaluate(candles, now=datetime.now(UTC))


def test_short_history_and_open_candle_do_not_generate_entries() -> None:
    """Krótka historia daje NONE, a świeca niezamknięta jest odrzucana."""
    candles = build_trending_candles("LONG", count=30)
    warmup = evaluate(candles)
    assert warmup["signal"] == "NONE"
    assert "rozgrzania" in warmup["signal_parameters"]["reason"]

    full_history = build_trending_candles("LONG")
    with pytest.raises(ValueError, match="niezakończonej"):
        BTCUSDStrategyV1().evaluate(
            full_history,
            now=full_history[-1]["timestamp_utc"] + timedelta(seconds=30),
        )


def test_storage_skips_none_and_keeps_signal_json_compact() -> None:
    """Adapter nie zapisuje NONE ani nie dodaje zbędnych pól JSON."""
    repository = Mock()
    storage = BTCUSDSignalStorage(repository)
    payload = {
        "price": 67_000.0,
        "signal": "NONE",
        "timestamp": 1_000,
        "instrument": "BTCUSD",
        "reason": "Warunki nie zostały spełnione.",
        "diagnostics": {"ema8": 66_999.0},
    }
    result = {
        "signal": "NONE",
        "price": payload["price"],
        "timestamp": "2026-01-01T00:01:00+01:00",
        "candle_open_epoch": 1,
        "candle_close_time": "2026-01-01T00:01:00+01:00",
        "signal_parameters": payload,
    }

    storage.save(result, 60)

    repository.save_algorithm_signal.assert_called_once_with(
        "BTC/USD",
        "Grok-45/BTCUSDStrategyV1",
        {
            "signal": "NONE",
            "price": payload["price"],
            "timestamp": result["candle_close_time"],
            "candle_open_epoch": 1,
            "signal_parameters": {
                "price": payload["price"],
                "signal": "NONE",
                "timestamp": 1_000,
                "instrument": "BTCUSD",
                "reason": payload["reason"],
            },
        },
        60,
        record_evaluation=True,
        save_signal=False,
        save_none_signal=False,
        save_signal_potential=True,
        enqueue_notification=False,
    )


def test_storage_leaves_spread_lookup_to_the_api_for_directional_signals() -> None:
    """Pozostawia API pobranie rzeczywistego spreadu świecy IBKR."""
    repository = Mock()
    storage = BTCUSDSignalStorage(repository)
    result = {
        "signal": "LONG",
        "price": 67_000.0,
        "candle_open_epoch": 1,
        "candle_close_time": "2026-01-01T00:01:00+01:00",
        "signal_parameters": {
            "price": 67_000.0,
            "signal": "LONG",
            "timestamp": 1_000,
            "instrument": "BTCUSD",
            "reason": "Wybicie wzrostowe.",
        },
    }

    storage.save(result, 60)

    assert repository.save_algorithm_signal.call_args.kwargs[
        "save_signal_potential"
    ] is True
    assert "spread_price" not in repository.save_algorithm_signal.call_args.kwargs


def test_none_signals_are_not_saved_by_default() -> None:
    """Zapisuje ewaluację, ale nie tworzy rekordu sygnału NONE."""
    assert storage_module.SAVE_NONE_SIGNALS is False

    repository = Mock()
    storage = BTCUSDSignalStorage(repository)
    result = {
        "signal": "NONE",
        "price": 67_000.0,
        "candle_open_epoch": 1,
        "candle_close_time": "2026-01-01T00:01:00+01:00",
        "signal_parameters": {
            "price": 67_000.0,
            "signal": "NONE",
            "timestamp": 1_000,
            "instrument": "BTCUSD",
            "reason": "Brak wybicia.",
        },
    }

    storage.save(result, 60)

    assert (
        repository.save_algorithm_signal.call_args.kwargs["record_evaluation"]
        is True
    )
    assert repository.save_algorithm_signal.call_args.kwargs["save_signal"] is False
    assert (
        repository.save_algorithm_signal.call_args.kwargs["save_none_signal"]
        is False
    )
