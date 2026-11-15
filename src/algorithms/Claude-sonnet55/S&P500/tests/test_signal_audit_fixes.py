"""Backend: regresje oceny trendu M5, walidacji świec i diagnostyki rozgrzewki."""

import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import signal_logic

UTC = timezone.utc
CLOSED = datetime(2026, 11, 16, 14, 42, tzinfo=UTC)


def _candle(opened: datetime) -> dict:
    """Tworzy poprawną świecę M1 dla czasu otwarcia UTC; nie zgłasza wyjątków."""
    return {"timestamp_utc": opened, "open": 6000.0, "high": 6003.0,
            "low": 5997.0, "close": 6000.0, "volume": 100.0}


def _history(closed: datetime, count: int) -> list[dict]:
    """Zwraca count kolejnych świec zamkniętych do czasu closed; nie zgłasza wyjątków."""
    return [_candle(closed - timedelta(minutes=count - index)) for index in range(count)]


@pytest.mark.parametrize("setup", ["OPENING_RANGE_BREAKOUT", "VWAP_REVERSION"])
@pytest.mark.parametrize(("direction", "close", "slope", "expected"), [
    ("LONG", 101.0, 0.1, True),
    ("LONG", 101.0, 0.0, False),
    ("LONG", 101.0, -0.1, False),
    ("LONG", 100.0, 0.1, False),
    ("LONG", 99.0, 0.1, False),
    ("SHORT", 99.0, -0.1, True),
    ("SHORT", 99.0, 0.0, False),
    ("SHORT", 99.0, 0.1, False),
    ("SHORT", 100.0, -0.1, False),
    ("SHORT", 101.0, -0.1, False),
    ("LONG", None, 0.1, False),
    ("SHORT", 99.0, None, False),
])
def test_bonus_m5_requires_price_and_slope(
        setup: str, direction: str, close: float | None, slope: float | None, expected: bool) -> None:
    """Premia wymaga zgodności ceny i nachylenia, także na progach i przy brakach."""
    params = {"relative_volume": None, "m5_close": close, "m5_ema20": 100.0,
              "m5_ema20_slope": slope, "er": None, "body_ratio": 0.0}
    score, reasons = signal_logic._score_details(setup, direction, params, time(11))
    assert score == config.CORE_SCORE + (config.BONUS_SCORE if expected else 0)
    assert any("M5" in reason for reason in reasons[1:]) is expected
    params["m5_ema20"] = None
    assert signal_logic._score(setup, direction, params, time(11)) == config.CORE_SCORE


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("aligned", [True, False])
def test_orb_score_and_reported_m5_trend_agree(
        monkeypatch: pytest.MonkeyPatch, direction: str, aligned: bool) -> None:
    """Ocena wybicia i diagnostyka M5 korzystają z identycznego kierunku trendu."""
    long = direction == "LONG"
    closed = datetime(2026, 11, 16, 14, 46, tzinfo=UTC)
    rows = _history(closed, 120)
    rows[-1].update(open=6003.0 if long else 5997.0,
                    close=6003.5 if long else 5996.5,
                    high=6004.0 if long else 5997.2,
                    low=6002.8 if long else 5996.0)
    slope = (1.0 if long else -1.0) * (1 if aligned else -1)
    indicators = {"atr": 2.0, "atr_ratio": 1.0, "relative_volume": 1.3,
                  "vwap": 5990.0 if long else 6010.0, "m5_close": 6001.0 if long else 5999.0,
                  "m5_ema20": 6000.0, "m5_ema20_slope": slope, "er": None, "rsi": None,
                  "rsi_series": [], "body_ratio": 0.5, "ema55_slope": None,
                  "bb_zscore_previous": None, "bb_zscore": None}
    monkeypatch.setattr(signal_logic, "calculate_indicators", lambda segment, session, cutoff: indicators)
    monkeypatch.setattr(config, "MIN_SCORE", 90)
    result = signal_logic.generate_signal(rows, now=closed)
    assert result["setup"] == "OPENING_RANGE_BREAKOUT"
    assert result["params"]["trend_5m"] == (("UP" if long else "DOWN") if aligned else "NEUTRAL")
    assert result["score"] == (90 if aligned else 80)
    assert result["signal"] == (direction if aligned else "NONE")
    assert ("score" in result["blocked_by"]) is not aligned


@pytest.mark.parametrize(("open_offset", "elapsed"), [
    (timedelta(0), timedelta(0)),
    (timedelta(0), timedelta(seconds=59)),
    (timedelta(minutes=5), timedelta(seconds=30)),
])
@pytest.mark.parametrize("broken_field", ["open", "high", "low", "close", "volume"])
def test_invalid_unclosed_candle_is_ignored(
        open_offset: timedelta, elapsed: timedelta, broken_field: str) -> None:
    """Błędne OHLCV formowanej lub przyszłej świecy nie zmienia analizy historii."""
    now = CLOSED + elapsed
    rows = _history(CLOSED, 30)
    forming = _candle(CLOSED + open_offset)
    forming[broken_field] = float("nan")
    assert signal_logic.generate_signal(rows + [forming], now=now) == \
        signal_logic.generate_signal(rows, now=now)


@pytest.mark.parametrize("broken_field", ["open", "high", "low", "close", "volume"])
def test_invalid_closed_candle_is_rejected(broken_field: str) -> None:
    """Zamknięta świeca z błędnym OHLCV nadal powoduje błąd danych wejściowych."""
    rows = _history(CLOSED, 2)
    rows[-1][broken_field] = float("nan")
    with pytest.raises(ValueError, match="nieprawidłową świecę"):
        signal_logic.generate_signal(rows, now=CLOSED)


@pytest.mark.parametrize("stamp", [
    None, "2026-11-16T14:42:00Z", datetime(2026, 11, 16, 14, 42),
    datetime(2026, 11, 16, 15, 42, tzinfo=timezone(timedelta(hours=1))),
    datetime(2026, 11, 16, 14, 42, 1, tzinfo=UTC),
])
def test_invalid_timestamp_is_never_hidden(stamp: object) -> None:
    """Błędny czas nawet formowanej świecy uniemożliwia ustalenie jej statusu."""
    row = _candle(CLOSED)
    row["timestamp_utc"] = stamp
    with pytest.raises(ValueError):
        signal_logic.generate_signal(_history(CLOSED, 1) + [row], now=CLOSED)


def test_missing_timestamp_is_rejected() -> None:
    """Brak znacznika nie może zostać uznany za przyszłą świecę."""
    row = _candle(CLOSED)
    del row["timestamp_utc"]
    with pytest.raises(ValueError):
        signal_logic.generate_signal([row], now=CLOSED)


def test_naive_analysis_time_is_rejected() -> None:
    """Nieświadomy czas analizy nie pozwala określić chwili zamknięcia."""
    with pytest.raises(ValueError):
        signal_logic.generate_signal(_history(CLOSED, 1), now=CLOSED.replace(tzinfo=None))


def test_closing_boundary_and_duplicates() -> None:
    """Świeca jest walidowana dopiero w chwili zamknięcia; duplikaty zamkniętych są błędem."""
    row = _candle(CLOSED)
    broken = dict(row, close=float("nan"))
    assert "no_data" in signal_logic.generate_signal([broken, broken], now=CLOSED + timedelta(seconds=59))["blocked_by"]
    with pytest.raises(ValueError, match="nieprawidłową świecę"):
        signal_logic.generate_signal([broken], now=CLOSED + timedelta(minutes=1))
    with pytest.raises(ValueError, match="zduplikowane"):
        signal_logic.generate_signal([row, row], now=CLOSED + timedelta(minutes=1))


@pytest.mark.parametrize(("count", "expected", "absent"), [
    (1, "warmup", "no_setup"),
    (config.WARMUP_ORB - 1, "warmup", "no_setup"),
    (config.WARMUP_ORB, "no_setup", "warmup"),
    (120, "no_setup", "warmup"),
])
def test_before_first_window_has_appropriate_diagnostic(count: int, expected: str, absent: str) -> None:
    """O 09:42 ET brak historii i brak aktywnego setupu mają odrębne przyczyny."""
    result = signal_logic.generate_signal(_history(CLOSED, count), now=CLOSED)
    assert result["signal"] == "NONE"
    assert expected in result["blocked_by"]
    assert absent not in result["blocked_by"]
    assert "outside_session" not in result["blocked_by"]


def test_outside_session_and_no_data_diagnostics_are_preserved() -> None:
    """Poza sesją i bez danych zachowujemy dotychczasowe przyczyny blokady."""
    outside = CLOSED - timedelta(minutes=3)
    result = signal_logic.generate_signal(_history(outside, 1), now=outside)
    assert "outside_session" in result["blocked_by"]
    assert "no_setup" not in result["blocked_by"]
    assert "warmup" not in result["blocked_by"]
    assert signal_logic.generate_signal([], now=CLOSED)["blocked_by"] == ["no_data", "warmup"]
