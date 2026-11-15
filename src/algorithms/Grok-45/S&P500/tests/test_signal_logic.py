"""Backend: deterministyczne testy wskaźników i sygnałów Grok-45/S&P500."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from indicators import adx_series, atr_series, calculate_indicators, ema_series, rsi_series
from signal_logic import generate_signal

UTC = timezone.utc


def make_history(count=100, direction="up"):
    """Tworzy powtarzalne świece minutowe z kontrolowanym kierunkiem."""
    start = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    rows = []
    for index in range(count):
        close = 100.0 + index * (0.2 if direction == "up" else -0.2)
        opening = close - 0.1 if direction == "up" else close + 0.1
        rows.append({
            "timestamp_utc": start + timedelta(minutes=index),
            "open": opening,
            "high": max(opening, close) + 0.4,
            "low": min(opening, close) - 0.4,
            "close": close,
            "volume": 100 + index,
        })
    return rows


def as_frame(rows):
    """Zamienia świece testowe na DataFrame wejściowy publicznej funkcji."""
    return pd.DataFrame(rows)


def test_ema_rsi_atr_and_volume_are_deterministic():
    """Kontroluje inicjalizacje EMA/RSI/ATR oraz SMA i relatywny wolumen."""
    assert ema_series([1, 2, 3, 4], 3) == [None, None, 2.0, 3.0]
    assert rsi_series([1, 2, 3, 2, 3, 4], 2)[2] == 100.0
    assert rsi_series([1, 2, 3, 2, 3, 4], 2)[3] == pytest.approx(50.0)
    candles = [
        {"open": 10, "high": 12, "low": 9, "close": 11},
        {"open": 11, "high": 13, "low": 10, "close": 12},
        {"open": 12, "high": 14, "low": 11, "close": 13},
    ]
    assert atr_series(candles, 2)[1] == pytest.approx(3.0)
    rows = make_history()
    result = generate_signal(as_frame(rows), now=rows[-1]["timestamp_utc"] + timedelta(minutes=1))
    params = result["signal_parameters"]
    assert params["volume_sma20"] == pytest.approx(sum(range(180, 200)) / 20)
    assert params["relative_volume"] == pytest.approx(199 / 189.5)


def test_adx_wilder_uses_previous_candle_and_has_expected_trend_value():
    """Weryfikuje dodatni DI dla trendu wzrostowego i poprawne opóźnienie ADX."""
    candles = []
    for index in range(40):
        close = 100 + index
        candles.append({"open": close - 0.5, "high": close + 1, "low": close - 1, "close": close})
    adx, plus_di, minus_di = adx_series(candles, 14)
    assert adx[26] is None
    assert adx[27] == pytest.approx(100.0)
    assert plus_di[14] > minus_di[14]
    assert minus_di[14] == 0


def test_one_missing_minute_is_tolerated_but_longer_gap_resets_segment():
    """Jedna brakująca minuta zachowuje segment, a luka dłuższa niż dwie go dzieli."""
    rows = make_history(100)
    now = rows[-1]["timestamp_utc"] + timedelta(minutes=1)
    one_missing = rows[:40] + rows[41:]
    result = generate_signal(as_frame(one_missing), now=now)
    assert result["signal_parameters"]["segment_candles"] == 99
    long_gap = rows[:40] + rows[43:]
    result = generate_signal(as_frame(long_gap), now=now)
    assert result["signal_parameters"]["segment_candles"] == 57


def test_tr_wont_carry_across_a_segment_gap():
    """Generator nie przenosi poprzedniego TR przez lukę i zachowuje rozgrzewkę."""
    rows = make_history(100)
    rows[39].update(open=500.0, high=501.0, low=499.0, close=500.0)
    for row in rows[40:]:
        row["timestamp_utc"] += timedelta(minutes=3)
    now = rows[-1]["timestamp_utc"] + timedelta(minutes=1)
    result = generate_signal(as_frame(rows), now=now)
    assert result["signal_parameters"]["segment_candles"] == 60
    assert result["signal_parameters"]["atr14"] == pytest.approx(0.9)


def test_dst_repeated_hour_requires_an_explicit_offset():
    """Powtórzona godzina jesiennej zmiany czasu jest rozstrzygana offsetem UTC."""
    wall = datetime(2026, 10, 25, 2, 30)
    rows = make_history(50)
    frame = pd.DataFrame(rows)
    frame["datetime"] = (
        [wall + timedelta(minutes=index) for index in range(30)]
        + [datetime(2026, 10, 25, 2, 0) + timedelta(minutes=index) for index in range(20)]
    )
    frame["utc_offset_minutes"] = [120] * 30 + [60] * 20
    frame = frame.drop(columns=["timestamp_utc"])
    result = generate_signal(frame, now=datetime(2026, 10, 25, 1, 21, tzinfo=UTC))
    assert result["candle_open_epoch"] == int(datetime(2026, 10, 25, 1, 19, tzinfo=UTC).timestamp())
    frame = frame.drop(columns=["utc_offset_minutes"])
    with pytest.raises(ValueError, match="utc_offset_minutes"):
        generate_signal(frame, now=datetime(2026, 10, 25, 1, 21, tzinfo=UTC))
    invalid_spring_time = datetime(2026, 3, 29, 2, 30, tzinfo=config.WARSAW)
    spring_frame = as_frame([dict(rows[-1], datetime=invalid_spring_time)])
    spring_frame = spring_frame.drop(columns=["timestamp_utc"])
    spring_frame["datetime"] = pd.Series([invalid_spring_time], dtype=object)
    with pytest.raises(ValueError, match="poprawny offset"):
        generate_signal(spring_frame, now=datetime(2026, 3, 29, 1, 31, tzinfo=UTC))


def test_empty_forming_stale_and_zero_volume_return_none_json_safe():
    """Brak, świeca formująca, przeterminowanie i zerowy wolumen dają NONE."""
    empty = generate_signal(pd.DataFrame())
    assert empty["signal"] == "NONE" and empty["price"] is None and empty["timestamp"] is None
    rows = make_history(50)
    opened = rows[-1]["timestamp_utc"]
    assert generate_signal(as_frame([rows[-1]]), now=opened)["price"] is None
    stale = generate_signal(as_frame(rows), now=opened + timedelta(minutes=5))
    assert stale["signal"] == "NONE"
    rows[-1]["volume"] = 0
    no_volume = generate_signal(as_frame(rows), now=opened + timedelta(minutes=1))
    assert no_volume["signal"] == "NONE"
    rows[-1]["volume"] = None
    missing_volume = generate_signal(as_frame(rows), now=opened + timedelta(minutes=1))
    assert missing_volume["signal"] == "NONE"
    for response in (empty, stale, no_volume, missing_volume):
        json.dumps(response, allow_nan=False)


@pytest.mark.parametrize(("direction", "expected"), [("up", "LONG"), ("down", "SHORT")])
def test_public_signal_directions(direction, expected):
    """Oba kierunki mają jawnie rozróżnione filtry i wynik JSON-serializowalny."""
    rows = make_history(100, direction)
    latest_close = rows[-1]["close"]
    if direction == "up":
        indicators = {
            "ema9": latest_close + 1, "ema21": latest_close - 0.5,
            "ema21_series": [latest_close - 0.5] * 5,
            "ema50": latest_close - 1, "rsi14": 50.0, "atr14": 1.0,
            "volume_sma20": 100.0, "relative_volume": 1.5, "adx14": 25.0,
            "plus_di14": 30.0, "minus_di14": 10.0, "lower_wick_ratio": 0.0,
            "upper_wick_ratio": 0.0, "bullish_candle": True, "bearish_candle": False,
        }
    else:
        indicators = {
            "ema9": latest_close - 1, "ema21": latest_close + 0.5,
            "ema21_series": [latest_close + 0.5] * 5,
            "ema50": latest_close + 1, "rsi14": 50.0, "atr14": 1.0,
            "volume_sma20": 100.0, "relative_volume": 1.5, "adx14": 25.0,
            "plus_di14": 10.0, "minus_di14": 30.0, "lower_wick_ratio": 0.0,
            "upper_wick_ratio": 0.0, "bullish_candle": False, "bearish_candle": True,
        }
    with patch("signal_logic.calculate_indicators", return_value=indicators):
        result = generate_signal(as_frame(rows), now=rows[-1]["timestamp_utc"] + timedelta(minutes=1))
    json.dumps(result, allow_nan=False)
    assert result["signal"] == expected
    assert result["signal_parameters"]["score_max"] == 100
    assert result["signal_parameters"]["score"] == 100
    assert all(result["signal_parameters"]["conditions"].values())


def test_future_candle_does_not_change_last_closed_signal():
    """Dodanie świecy formującej lub przyszłej nie zmienia analizy ostatniego zamknięcia."""
    rows = make_history(100)
    now = rows[-1]["timestamp_utc"] + timedelta(minutes=1)
    expected = generate_signal(as_frame(rows), now=now)
    future = dict(rows[-1], timestamp_utc=now + timedelta(minutes=5), close=9999)
    forming = dict(rows[-1], timestamp_utc=now, close=8888)
    actual = generate_signal(as_frame(rows + [future, forming]), now=now)
    assert actual == expected


def test_output_timestamps_and_configured_identity():
    """Odpowiedź zachowuje instrument, symbol strategii i czasy Warsaw/epoch."""
    rows = make_history(50)
    result = generate_signal(as_frame(rows), now=rows[-1]["timestamp_utc"] + timedelta(minutes=1))
    assert config.DB_SYMBOL == "ES"
    assert result["instrument"] == "S&P500"
    assert result["algorithm_name"] == "Grok-45/S&P500"
    assert datetime.fromisoformat(result["timestamp"]).utcoffset() is not None
    assert result["candle_open_epoch"] == int(rows[-1]["timestamp_utc"].timestamp())


def test_output_indicator_names_and_unavailable_values():
    """Udostępnia wymagane klucze i rzeczywisty wolumen również przy braku rozgrzewki.

    Argumenty:
        Brak.
    Zwraca:
        None; sprawdza poprawność kontraktu za pomocą asercji.
    Wyjątki:
        AssertionError: Gdy kształt wyniku lub wartości są niepoprawne.
    """
    expected_aliases = {
        "ema_9": "ema9", "ema_21": "ema21", "ema_50": "ema50",
        "rsi_14": "rsi14", "atr_14": "atr14", "volume_sma_20": "volume_sma20",
        "adx_14": "adx14", "plus_di": "plus_di14", "minus_di": "minus_di14",
    }
    rows = make_history()
    now = rows[-1]["timestamp_utc"] + timedelta(minutes=1)
    result = generate_signal(as_frame(rows), now=now)
    params = result["signal_parameters"]
    assert result["max_hold_minutes"] == 20
    for name, old_name in expected_aliases.items():
        assert params[name] == params[old_name]
        assert params[name] is not None
    assert params["volume"] == rows[-1]["volume"]
    assert params["relative_volume"] is not None

    for frame, expected_volume in (
        (pd.DataFrame(), None),
        (as_frame(rows[-1:]), rows[-1]["volume"]),
        (as_frame([dict(rows[-1], volume=0)]), 0.0),
    ):
        response = generate_signal(frame, now=now)
        values = response["signal_parameters"]
        assert response["max_hold_minutes"] == 20
        assert values["volume"] == expected_volume
        assert all(values[key] is None for key in expected_aliases)
        assert values["relative_volume"] is None
        json.dumps(response, allow_nan=False)


@pytest.mark.parametrize(("direction", "expected"), [("up", "LONG"), ("down", "SHORT")])
def test_pullback_accepts_opposite_body_with_rejection_wick(direction, expected):
    """Knot może potwierdzić wejście mimo przeciwnego kierunku korpusu świecy.

    Argumenty:
        direction: Kierunek historii wejściowej.
        expected: Oczekiwany sygnał kierunkowy.
    Zwraca:
        None; sprawdza także alternatywny układ EMA21 i EMA50.
    Wyjątki:
        AssertionError: Gdy którykolwiek z filtrów nie działa zgodnie z kontraktem.
    """
    rows = make_history(100, direction)
    close = rows[-1]["close"]
    if expected == "LONG":
        rows[-1].update(open=close + 0.1, high=close + 0.2, low=close - 2)
        ema21, ema50, ema9 = close - 0.5, close - 0.25, close - 0.4
        plus_di, minus_di = 30.0, 10.0
    else:
        rows[-1].update(open=close - 0.1, high=close + 2, low=close - 0.2)
        ema21, ema50, ema9 = close + 0.5, close + 0.25, close + 0.4
        plus_di, minus_di = 10.0, 30.0
    rows[-1]["volume"] = 400
    candles = as_frame(rows)
    now = rows[-1]["timestamp_utc"] + timedelta(minutes=1)
    control = {
        "ema9": ema9, "ema21": ema21, "ema50": ema50,
        "ema21_series": [ema21] * 5, "rsi14": 50.0, "atr14": 0.005,
        "adx14": 25.0, "plus_di14": plus_di, "minus_di14": minus_di,
    }

    def controlled_indicators(segment, settings):
        """Łączy rzeczywiste wartości świecy i wolumenu z kontrolowanymi wskaźnikami.

        Argumenty:
            segment: Przekazany do strategii segment świec.
            settings: Konfiguracja okresów wskaźników.
        Zwraca:
            Słownik wskaźników z nadpisanymi wartościami kontrolnymi.
        Wyjątki:
            ValueError: Gdy segment nie jest poprawny.
        """
        values = calculate_indicators(segment, settings)
        values.update(control)
        return values

    with patch("signal_logic.calculate_indicators", side_effect=controlled_indicators):
        result = generate_signal(candles, now=now)
        assert result["signal"] == expected
        assert result["signal_parameters"]["score"] == 100
        assert result["signal_parameters"]["conditions"]["pullback"]
        assert result["signal_parameters"]["conditions"]["candle"]
        assert result["signal_parameters"]["atr_14"] == 0.005

        control["ema50"] = close
        blocked = generate_signal(candles, now=now)
        assert blocked["signal"] == "NONE"
        assert not blocked["signal_parameters"]["conditions"]["ema_trend"]
        control["ema50"] = ema50
        control["ema9"] = ema21
        blocked = generate_signal(candles, now=now)
        assert blocked["signal"] == "NONE"
        assert not blocked["signal_parameters"]["conditions"]["ema_trend"]
        control["ema9"] = ema9
        control["ema21"] = close
        blocked = generate_signal(candles, now=now)
        assert blocked["signal"] == "NONE"
        assert not blocked["signal_parameters"]["conditions"]["pullback"]
        control["ema21"] = ema21
        candles.loc[candles.index[-1], "open"] = close
        candles.loc[candles.index[-1], "high"] = close + 2
        candles.loc[candles.index[-1], "low"] = close - 2
        blocked = generate_signal(candles, now=now)
        assert blocked["signal"] == "NONE"
        assert blocked["signal_parameters"]["conditions"]["pullback"]
        assert not blocked["signal_parameters"]["conditions"]["candle"]
        control["atr14"] = 0.0
        blocked = generate_signal(candles, now=now)
        assert blocked["signal"] == "NONE"
        assert not blocked["signal_parameters"]["conditions"]["atr"]
