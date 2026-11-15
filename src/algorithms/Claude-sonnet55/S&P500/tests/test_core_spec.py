"""Backend: testy pełnej specyfikacji strategii ES, bez bazy i zegara systemowego."""

import json
import os
import subprocess
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import signal_logic
from indicators import calculate_indicators, completed_five_minute_candles
from schedule import cash_close, cash_open, expected_candle_opens, is_signal_window, is_trading_minute, segment_id

UTC = timezone.utc


def test_default_and_overridden_state_file():
    """Stan pętli domyślnie trafia do katalogu domowego i pozwala zmienić ścieżkę."""
    directory = Path(config.__file__).resolve().parent
    script = "import config; print(config.STATE_FILE)"
    env = os.environ.copy()
    env.pop("SP500_STATE_FILE", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    output = subprocess.check_output([sys.executable, "-c", script], cwd=directory, env=env, text=True)
    assert Path(output.strip()) == Path.home() / ".gie_da" / "sp500_state.json"
    override = directory / "stan_testowy.json"
    env["SP500_STATE_FILE"] = str(override)
    output = subprocess.check_output([sys.executable, "-c", script], cwd=directory, env=env, text=True)
    assert Path(output.strip()) == override


def bar(stamp, close=6000, volume=100):
    """Przygotowuje poprawną świecę dla czasu UTC, ceny i wolumenu."""
    return {"timestamp_utc": stamp, "open": close, "high": close + 3,
            "low": close - 3, "close": close, "volume": volume}


def bars(closed, count=120):
    """Tworzy historię minut kończącą się w zamknięciu closed."""
    return [bar(closed - timedelta(minutes=count - index)) for index in range(count)]


def fake_indicators():
    """Zwraca spójny zestaw wskaźników dla konstrukcji setupów."""
    return {"atr": 2.0, "atr_ratio": 1.0, "relative_volume": 1.5, "vwap": 5990.0,
            "m5_close": 6002.0, "m5_ema20": 6000.0, "m5_ema20_slope": 1.0,
            "ema9": 6002.0, "ema21": 6000.0, "ema55": 5998.0, "ema55_slope": 0.0,
            "ema21_series": [6000.0] * 5, "er": 0.5, "rsi": 50.0,
            "rsi_series": [35.0, 40.0, 42.0, 50.0], "body_ratio": 0.6,
            "lower_wick_ratio": 0.4, "upper_wick_ratio": 0.4,
            "bb_zscore": 0.0, "bb_zscore_previous": -2.0}


def score(rows, values, **kwargs):
    """Ocenia świece przy kontrolowanych wskaźnikach i czasie zamknięcia."""
    with patch.object(signal_logic, "calculate_indicators", return_value=values):
        return signal_logic.generate_signal(rows, now=rows[-1]["timestamp_utc"] + timedelta(minutes=1), **kwargs)


def test_cash_schedule_dst_and_early_close():
    """Weryfikuje pełne granice, święta i niezależny harmonogram pobierania."""
    day = date(2026, 11, 16)
    with patch.object(config, "EARLY_CLOSE_DATES", [day]), patch.object(config, "NO_TRADE_DATES", []):
        assert cash_open(day).time() == time(9, 30)
        assert cash_close(day).time() == time(13)
        assert is_signal_window(datetime(2026, 11, 16, 17, 50, tzinfo=UTC))
        assert not is_signal_window(datetime(2026, 11, 16, 17, 51, tzinfo=UTC))
    with patch.object(config, "NO_TRADE_DATES", [day]):
        assert is_trading_minute(datetime(2026, 11, 16, 15, tzinfo=UTC))
        assert not is_signal_window(datetime(2026, 11, 16, 15, tzinfo=UTC))
    assert is_signal_window(datetime(2026, 3, 9, 13, 40, tzinfo=UTC))
    assert is_signal_window(datetime(2026, 11, 2, 20, 50, tzinfo=UTC))
    assert not is_signal_window(datetime(2026, 11, 2, 20, 51, tzinfo=UTC))


def test_segments_cross_midnight_and_gaps_but_not_scheduled_break():
    """Segment od niedzieli 17 i codziennie 18 ET trwa przez północ i luki danych."""
    sunday = datetime(2026, 11, 15, 22, tzinfo=UTC)
    monday = datetime(2026, 11, 16, 15, tzinfo=UTC)
    assert segment_id(sunday) == segment_id(monday)
    monday_evening = datetime(2026, 11, 16, 23, tzinfo=UTC)
    assert segment_id(monday_evening) == segment_id(datetime(2026, 11, 17, 15, tzinfo=UTC))
    assert segment_id(monday) != segment_id(monday_evening)
    with pytest.raises(ValueError):
        segment_id(datetime(2026, 11, 16, 22, 30, tzinfo=UTC))
    assert len(expected_candle_opens(datetime(2026, 11, 16, 21, 59, tzinfo=UTC),
                                     datetime(2026, 11, 16, 23, 1, tzinfo=UTC))) == 2


def test_data_gap_warmup_and_staleness():
    """Luki blokują zgodnie z progami bez resetowania segmentu, wiek mierzony od zamknięcia."""
    closed = datetime(2026, 11, 16, 15, 20, tzinfo=UTC)
    rows = bars(closed)
    one = rows[:-10] + rows[-9:]
    result = signal_logic.generate_signal(one, now=closed + timedelta(minutes=3))
    assert result["params"]["segment_candles"] == len(one)
    assert result["params"]["missing_candles_60m"] == 1
    assert "data_gap" not in result["blocked_by"] and "stale_data" not in result["blocked_by"]
    result = signal_logic.generate_signal(rows[:-10] + rows[-8:], now=closed + timedelta(minutes=3, seconds=1))
    assert "data_gap" in result["blocked_by"] and "stale_data" in result["blocked_by"]
    assert "warmup" in signal_logic.generate_signal(rows[-2:], now=closed)["blocked_by"]
    assert "warmup" in signal_logic.generate_signal([])["blocked_by"]


def test_indicator_zero_and_missing_volume_and_completed_m5():
    """Brak/zero wolumenu nie tworzy VWAP ani dzielenia przez zero; M5 nie widzi przyszłości."""
    rows = bars(datetime(2026, 11, 16, 15, 30, tzinfo=UTC))
    for row in rows:
        row["volume"] = 0
    values = calculate_indicators(rows, rows, datetime(2026, 11, 16, 15, 30, tzinfo=UTC))
    assert values["vwap"] is None and values["relative_volume"] is None
    rows[-1]["volume"] = None
    values = calculate_indicators(rows, rows, datetime(2026, 11, 16, 15, 30, tzinfo=UTC))
    assert values["vwap"] is None and values["relative_volume"] is None
    start = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    bucket = [bar(start + timedelta(minutes=i)) for i in range(5)]
    assert not completed_five_minute_candles(bucket, start + timedelta(minutes=4))
    assert not completed_five_minute_candles(bucket[:3], start + timedelta(minutes=5))
    assert len(completed_five_minute_candles(bucket[:4], start + timedelta(minutes=5))) == 1


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_orb_first_breakout_and_daily_direction(direction):
    """ORB wymaga pierwszego wybicia i pozwala tylko raz na dany kierunek."""
    closed = datetime(2026, 11, 16, 15, 5, tzinfo=UTC)
    rows = bars(closed)
    values = fake_indicators()
    if direction == "LONG":
        rows[-1].update(open=6003.0, low=6002.8, high=6004.0, close=6003.5)
    else:
        rows[-1].update(open=5997.0, low=5996.0, high=5997.2, close=5996.5)
        values.update(vwap=6010, m5_close=5998, m5_ema20=6000)
    result = score(rows, values)
    assert result["signal"] == direction, result
    assert result["setup"] == "OPENING_RANGE_BREAKOUT" and result["score"] >= 70
    assert result["instrument"] == "S&P500" and result["timestamp_epoch"] == int(closed.timestamp())
    assert datetime.fromisoformat(result["timestamp"]).utcoffset() == timedelta(hours=1)
    assert score(rows, values, orb_directions_today=[direction])["signal"] == "NONE"
    rows[-2]["close"] = 6003.2 if direction == "LONG" else 5996.8
    rows[-2]["high"] = max(rows[-2]["high"], rows[-2]["close"])
    rows[-2]["low"] = min(rows[-2]["low"], rows[-2]["close"])
    assert score(rows, values)["signal"] == "NONE"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_pullback_recent_ema_touch_and_rsi(direction):
    """Pullback wymaga dotknięcia historycznej EMA, RSI i kierunkowego M5."""
    rows = bars(datetime(2026, 11, 16, 15, 30, tzinfo=UTC))
    values = fake_indicators()
    for row in rows[-5:]:
        row.update(open=6000, close=6000, low=5997, high=6003)
    if direction == "LONG":
        rows[-4]["low"] = 6000
        rows[-1].update(open=6000, close=6001, high=6002, low=5999)
    else:
        rows[-4]["high"] = 6000
        rows[-1].update(open=6000, close=5999, high=6001, low=5998)
        values.update(vwap=6010, m5_close=5998, m5_ema20=6000, m5_ema20_slope=-1,
                      ema9=5998, ema21=6000, ema55=6002, rsi=50,
                      rsi_series=[65, 60, 58, 50])
    result = score(rows, values)
    assert result["signal"] == direction and result["setup"] == "TREND_PULLBACK", result
    rows[-4]["low" if direction == "LONG" else "high"] = 5997 if direction == "LONG" else 6003
    assert score(rows, values)["signal"] == "NONE"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_reversion_entry_and_vwap_target_cap(direction):
    """Powrót w pasmo wymaga knota, skrajnego RSI i celu ograniczonego VWAP."""
    rows = bars(datetime(2026, 11, 16, 17, tzinfo=UTC))
    values = fake_indicators()
    values.update(er=0.1, ema55_slope=0, bb_zscore_previous=-2.1, bb_zscore=-1.9,
                  rsi=30, rsi_series=[50, 30, 20, 30], vwap=6003, m5_close=6002)
    rows[-1].update(open=5999.5, close=6000, low=5999, high=6000.5)
    if direction == "SHORT":
        rows[-1].update(open=6000.5, close=6000, low=5999.5, high=6001)
        values.update(bb_zscore_previous=2.1, bb_zscore=1.9, rsi=70,
                      rsi_series=[50, 70, 80, 70], vwap=5997, m5_close=5998)
    result = score(rows, values)
    assert result["signal"] == direction and result["setup"] == "VWAP_REVERSION", result
    assert result["tp_points"] == 3 and abs(result["tp_price"] - 6000) == 3
    values["vwap"] = 6001.5 if direction == "LONG" else 5998.5
    values["atr"] = 1
    result = score(rows, values)
    assert result["signal"] == "NONE" and "target_too_small" in result["blocked_by"]


def test_no_lookahead_and_validation():
    """Świeca przyszła nie wchodzi do wskaźników, a niepoprawne znaczniki są odrzucane."""
    cutoff = datetime(2026, 11, 16, 15, 30, tzinfo=UTC)
    rows = bars(cutoff)
    future = bar(cutoff, close=9000)
    assert calculate_indicators(rows + [future], rows + [future], cutoff) == calculate_indicators(rows, rows, cutoff)
    bad = [dict(rows[-1], timestamp_utc=rows[-1]["timestamp_utc"].astimezone(config.WARSAW))]
    with pytest.raises(ValueError):
        signal_logic.generate_signal(bad, now=cutoff)
    with pytest.raises(ValueError):
        signal_logic.generate_signal([rows[-1], rows[-1]], now=cutoff)
    json.dumps(signal_logic.generate_signal(rows, now=cutoff), allow_nan=False)


def test_generate_signal_ignores_forming_and_future_candles():
    """Publiczna analiza zwraca identyczny wynik z danymi przyszłymi i bez nich."""
    closed = datetime(2026, 11, 16, 15, 20, tzinfo=UTC)
    rows = bars(closed)
    expected = signal_logic.generate_signal(rows, now=closed)
    forming = bar(closed, close=9000)
    future = bar(closed + timedelta(minutes=5), close=10000)
    actual = signal_logic.generate_signal(rows + [future, forming], now=closed)
    assert actual == expected
    assert actual["price"] == rows[-1]["close"]
    assert actual["timestamp_epoch"] == int(closed.timestamp())


def test_generate_signal_no_closed_candles():
    """Wyłącznie formujące się świece dają NONE i diagnostykę braku danych."""
    now = datetime(2026, 11, 16, 15, 20, tzinfo=UTC)
    result = signal_logic.generate_signal([bar(now), bar(now + timedelta(minutes=1))], now=now)
    assert result["signal"] == "NONE"
    assert result["price"] is None and result["timestamp"] is None
    assert "no_data" in result["blocked_by"]
    json.dumps(result, allow_nan=False)


def test_segment_survives_midnight_and_scheduled_gap():
    """Nie zeruje EMA o północy ET ani po rzeczywistym braku kilku minut."""
    closed = datetime(2026, 11, 17, 15, 20, tzinfo=UTC)
    rows = bars(closed, count=900)
    rows = [row for row in rows if is_trading_minute(row["timestamp_utc"])]
    rows = rows[:-20] + rows[-18:]
    result = signal_logic.generate_signal(rows, now=closed)
    assert result["params"]["segment_candles"] == len(rows)
    assert "data_gap" in result["blocked_by"]


def test_conflicting_candidates_and_highest_score():
    """Przeciwne setupy blokują transakcję; zgodny kierunek wybiera najlepszą ocenę."""
    rows = bars(datetime(2026, 11, 16, 15, 20, tzinfo=UTC))
    values = fake_indicators()
    with patch.object(signal_logic, "calculate_indicators", return_value=values), \
            patch.object(signal_logic, "_candidates", return_value=[("OPENING_RANGE_BREAKOUT", "LONG", 80), ("TREND_PULLBACK", "SHORT", 90)]):
        result = signal_logic.generate_signal(rows, now=rows[-1]["timestamp_utc"] + timedelta(minutes=1))
    assert result["signal"] == "NONE" and "conflicting_setups" in result["blocked_by"]
    with patch.object(signal_logic, "calculate_indicators", return_value=fake_indicators()), \
            patch.object(signal_logic, "_candidates", return_value=[("OPENING_RANGE_BREAKOUT", "LONG", 80), ("TREND_PULLBACK", "LONG", 90)]):
        result = signal_logic.generate_signal(rows, now=rows[-1]["timestamp_utc"] + timedelta(minutes=1))
    assert result["signal"] == "LONG" and result["setup"] == "TREND_PULLBACK" and result["score"] == 90


def test_blackouts_and_regime():
    """Blokady wydarzeń stosują ET, a pośredni ER daje diagnostykę regime_unclear."""
    closed = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)
    rows = bars(closed)
    values = fake_indicators()
    values["er"] = 0.25
    result = score(rows, values)
    assert "news_blackout" in result["blocked_by"] and "regime_unclear" in result["blocked_by"]
    assert signal_logic._blackout(closed + timedelta(minutes=2))
    assert not signal_logic._blackout(closed + timedelta(minutes=3))


def test_early_close_blackout_tracks_session_close():
    """Blokada końcowa podąża za skróconą sesją i nie stosuje starego czasu zamknięcia."""
    day = date(2026, 11, 16)
    with patch.object(config, "EARLY_CLOSE_DATES", [day]):
        assert not signal_logic._blackout(datetime(2026, 11, 16, 17, 54, tzinfo=UTC))
        assert signal_logic._blackout(datetime(2026, 11, 16, 17, 55, tzinfo=UTC))
        assert signal_logic._blackout(datetime(2026, 11, 16, 17, 59, tzinfo=UTC))
        assert signal_logic._blackout(datetime(2026, 11, 16, 18, 0, tzinfo=UTC))
        assert not signal_logic._blackout(datetime(2026, 11, 16, 20, 55, tzinfo=UTC))
    assert signal_logic._blackout(datetime(2026, 11, 16, 20, 55, tzinfo=UTC))
    assert signal_logic._blackout(datetime(2026, 11, 16, 21, 0, tzinfo=UTC))


def test_high_impact_day_is_informational_and_params_have_aliases():
    """Dzień wysokiego wpływu nie blokuje setupu; pola JSON odpowiadają obliczeniom."""
    closed = datetime(2026, 11, 16, 15, 5, tzinfo=UTC)
    rows = bars(closed)
    rows[-1].update(open=6003.0, low=6002.8, high=6004.0, close=6003.5)
    with patch.object(config, "HIGH_IMPACT_DAYS", [date(2026, 11, 16)]):
        result = score(rows, fake_indicators())
    assert result["signal"] == "LONG"
    assert result["setup"] == "OPENING_RANGE_BREAKOUT"
    params = result["params"]
    assert params["high_impact_day"] is True
    assert params["atr14_points"] == params["atr"] == 2
    assert params["atr14_bps"] == pytest.approx(2 / 6003.5 * 10000)
    assert params["rsi5"] == params["rsi"]
    assert params["ema20_5m"] == params["m5_ema20"]
    assert params["ema20_5m_slope"] == params["m5_ema20_slope"]
    assert params["trend_5m"] == "UP"
    assert params["efficiency_ratio_30"] == params["er"]
    assert params["vwap_distance_atr"] == pytest.approx((6003.5 - 5990) / 2)
    assert params["rel_volume"] == params["relative_volume"]
    assert params["or_width_points"] == params["or_width"]


def test_indicator_aliases_are_safe_when_unavailable():
    """Brak rozgrzania nie tworzy wymyślonych wskaźników i nie powoduje dzielenia przez zero."""
    closed = datetime(2026, 11, 16, 15, 20, tzinfo=UTC)
    result = signal_logic.generate_signal(bars(closed, count=1), now=closed)
    params = result["params"]
    assert params["atr14_points"] is None
    assert params["atr14_bps"] is None
    assert params["vwap_distance_atr"] is None
    assert params["ema20_5m"] is None
    assert params["ema20_5m_slope"] is None
    assert params["trend_5m"] is None
    assert params["high_impact_day"] is False
    json.dumps(result, allow_nan=False)


def test_indicator_numeric_values_and_latest_m5():
    """Sprawdza ATR Wildera, relatywny wolumen, ER oraz ukończone koszyki M5."""
    closed = datetime(2026, 11, 16, 15, 30, tzinfo=UTC)
    rows = bars(closed)
    for index, row in enumerate(rows):
        row.update(open=6000 + index, close=6000 + index,
                   high=6001 + index, low=5999 + index, volume=100)
    values = calculate_indicators(rows, rows, closed)
    assert values["atr"] == pytest.approx(2)
    assert values["atr_bps"] == pytest.approx(2 / 6119 * 10000)
    assert values["relative_volume"] == 1
    assert values["er"] == 1
    assert values["rsi"] == 100
    assert values["m5_count"] == 24
    assert values["m5_close"] == 6119
    assert values["m5_ema20_slope"] > 0


def test_storage_restores_orb_direction():
    """Stan odczytuje nazwę ORB zwracaną przez strategię."""
    from unittest.mock import Mock
    from storage import SP500Storage
    cursor = Mock()
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = [{"signal": "long", "signal_parameters": {"setup": "OPENING_RANGE_BREAKOUT"}}]
    state = SP500Storage._load_state(cursor, datetime(2026, 11, 16, 15, 20, tzinfo=UTC))
    assert state["orb_directions_today"] == ["LONG"]


def test_backtest_tracks_orb_direction():
    """Kolejna historyczna decyzja widzi kierunek wcześniejszego sygnału ORB."""
    from backtest import run_backtest
    rows = bars(datetime(2026, 11, 16, 15, 20, tzinfo=UTC), count=2)
    observed = []
    def fake(history, last, count, directions, now):
        """Zwraca ORB i zapamiętuje stan przekazany przez backtest."""
        observed.append(list(directions))
        return {"signal": "LONG", "setup": "OPENING_RANGE_BREAKOUT", "price": 6000, "sl_points": 2, "tp_points": 2}
    with patch("backtest.generate_signal", side_effect=fake):
        run_backtest(rows)
    assert observed == [[], ["LONG"]]
