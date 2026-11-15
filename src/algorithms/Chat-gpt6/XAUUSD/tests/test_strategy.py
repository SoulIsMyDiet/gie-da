"""Testy rdzenia XAUUSD na sztucznych danych, bez dostępu do bazy."""

import importlib
import json
import math
from datetime import datetime, timedelta, timezone

import pytest

BASE = "src.algorithms.Chat-gpt6.XAUUSD."
indicators = importlib.import_module(BASE + "indicators")
schedule = importlib.import_module(BASE + "schedule")
logic = importlib.import_module(BASE + "signal_logic")
config = importlib.import_module(BASE + "config")
UTC = timezone.utc
START = datetime(2026, 1, 5, tzinfo=UTC)


def test_integration_configuration():
    """Sprawdza uzgodniony kontrakt ustawień integracji i symbol rynku."""
    assert config.DB_SYMBOL == "XAU/USD"
    assert config.STRATEGY_NAME == "Chat-gpt6/XAUUSDStrategyV1"
    assert config.SAVE_NONE_SIGNALS is True
    assert config.HISTORY_LIMIT == 2500
    assert config.BEEP_ENABLED is True
    assert config.CONFIG.market_symbol == config.DB_SYMBOL


def candles(count=690, direction=1, start=START):
    """Tworzy powtarzalne świece z ruchem trendowym i korektami."""
    result = []
    for index in range(count):
        price = 2000 + direction * (0.02 * index + 0.25 * math.sin(index * 0.4))
        result.append({"timestamp_utc": start + timedelta(minutes=index),
                       "open": price - 0.01 * direction, "high": price + 0.08,
                       "low": price - 0.08, "close": price, "volume": 100.0})
    return result


def test_indicator_reference_values():
    """Porównuje EMA, RSI, ATR i BB z niezależnymi ręcznymi wynikami."""
    assert indicators.ema([1, 2, 3], 3) == pytest.approx([1, 1.5, 2.25])
    assert indicators.rsi([1, 2, 1, 2, 3], 3) == pytest.approx(
        [None, None, None, 200 / 3, 700 / 9])
    rows = [{"high": 11, "low": 9, "close": 10},
            {"high": 13, "low": 11, "close": 12},
            {"high": 12, "low": 9, "close": 10},
            {"high": 11, "low": 9, "close": 10}]
    assert indicators.atr(rows, 3) == pytest.approx([None, None, 8 / 3, 22 / 9])
    bands = indicators.bollinger([1, 2, 3], 3, 2)
    assert bands["middle"][-1] == pytest.approx(2)
    assert bands["upper"][-1] == pytest.approx(2 + 2 * math.sqrt(2 / 3))
    assert indicators.rsi([3] * 4, 3)[-1] == 50


def test_m5_closed_complete_and_independent():
    """Sprawdza granice UTC, OHLCV, brak uzupełniania i brak lookahead."""
    rows = candles(11)
    first = indicators.aggregate_m5(rows, START + timedelta(minutes=9, seconds=59))
    assert len(first) == 1
    assert first[0]["open"] == rows[0]["open"]
    assert first[0]["close"] == rows[4]["close"]
    assert first[0]["high"] == max(row["high"] for row in rows[:5])
    assert first[0]["low"] == min(row["low"] for row in rows[:5])
    assert first[0]["volume"] == 500
    assert len(indicators.aggregate_m5(rows[:7] + rows[8:], START + timedelta(minutes=11))) == 1
    assert len(indicators.aggregate_m5(rows, START + timedelta(minutes=10))) == 2
    rows[2]["volume"] = None
    assert indicators.aggregate_m5(rows, START + timedelta(minutes=10))[0]["volume"] is None


@pytest.mark.parametrize("direction,expected", [(1, "LONG"), (-1, "SHORT")])
def test_real_signals_and_risk(direction, expected):
    """Sprawdza rzeczywiste LONG/SHORT, punktację, czas i uzgodnione SL/TP."""
    rows = candles(direction=direction)
    result = logic.generate_signal(rows, START + timedelta(minutes=len(rows)))
    assert result["signal"] == expected
    params = result["signal_parameters"]
    assert params["selected_setup"] == "TREND_PULLBACK"
    assert params["candidates"][expected]["score"] >= config.CONFIG.minimum_score
    assert params["strength"] == "strong"
    assert params["risk"]["stop_loss"] == pytest.approx(
        result["price"] - direction * 1.5 * params["indicators"]["m1"]["atr"])
    assert params["risk"]["take_profit"] == pytest.approx(
        result["price"] + direction * 2 * params["indicators"]["m1"]["atr"])
    assert params["risk"]["max_hold_minutes"] == 20
    assert result["candle_open_epoch"] == int(rows[-1]["timestamp_utc"].timestamp())
    assert datetime.fromisoformat(result["timestamp"]) == rows[-1]["timestamp_utc"] + timedelta(minutes=1)
    json.dumps(result, allow_nan=False)


def test_no_lookahead_on_last_m1_and_m5():
    """Odrzuca bieżącą niezakończoną świecę, nawet jeśli jest w wejściu."""
    rows = candles(701)
    now = START + timedelta(minutes=700, seconds=30)
    base = logic.generate_signal(rows[:700], now)
    rows[-1]["close"] += 30
    rows[-1]["high"] += 30
    assert logic.generate_signal(rows, now) == base


def test_m1_and_m5_histories_are_independent():
    """Porównuje EMA M5 z niezależnym obliczeniem ze świec M5, nie M1."""
    rows = candles()
    now = START + timedelta(minutes=len(rows))
    result = logic.generate_signal(rows, now)
    m5 = indicators.aggregate_m5(rows, now)
    expected = indicators.ema([row["close"] for row in m5], 9)[-1]
    assert result["signal_parameters"]["indicators"]["m5"]["ema_9"] == pytest.approx(expected)
    assert expected != pytest.approx(result["signal_parameters"]["indicators"]["m1"]["ema_9"])


@pytest.mark.parametrize("missing,reset", [(1, False), (2, False), (3, True)])
def test_unexpected_gaps_and_warmup(missing, reset):
    """Rozróżnia tolerowane luki od resetu po trzech oczekiwanych minutach."""
    rows = candles()
    rows = rows[:300] + rows[300 + missing:]
    result = logic.generate_signal(rows, START + timedelta(minutes=690))
    state = result["signal_parameters"]["gap_state"]
    assert state["gaps"][0]["missing_expected_minutes"] == missing
    assert state["gaps"][0]["reset"] is reset
    assert state["reset_count"] == int(reset)
    assert result["signal_parameters"]["warmup"]["ready"] is (not reset)
    if reset:
        assert result["signal"] == "NONE"


@pytest.mark.parametrize("direction,expected", [(1, "LONG"), (-1, "SHORT")])
def test_breakout_and_rsi_hard_gate(direction, expected):
    """Wykrywa wybicie w obu kierunkach i blokuje je przy skrajnym RSI."""
    rows = candles(direction=direction)
    rows[-1]["close"] += direction * 0.5
    rows[-1]["high"] = max(rows[-1]["high"], rows[-1]["close"] + 0.01)
    rows[-1]["low"] = min(rows[-1]["low"], rows[-1]["close"] - 0.01)
    result = logic.generate_signal(rows, START + timedelta(minutes=690))
    assert result["signal"] == expected
    assert "MOMENTUM_BREAKOUT" in result["signal_parameters"]["candidates"][expected]["setups"]
    rows[-1]["close"] += direction * 0.3
    rows[-1]["high"] = max(rows[-1]["high"], rows[-1]["close"] + 0.01)
    rows[-1]["low"] = min(rows[-1]["low"], rows[-1]["close"] - 0.01)
    result = logic.generate_signal(rows, START + timedelta(minutes=690))
    assert result["signal"] == "NONE"
    assert not result["signal_parameters"]["candidates"][expected]["checks"]["rsi_momentum"]


def test_m5_opposition_blocks_even_high_score():
    """Samo przekroczenie progu punktowego nie zastępuje wymaganego kierunku M5."""
    row = {"close": 105, "low": 100, "high": 106}
    previous = {"low": 99, "high": 104}
    m1 = {"ema_9": 103, "ema_21": 102, "ema_50": 101, "rsi": 60, "atr": 1}
    prior = {"ema_9": 102, "rsi": 55, "atr": 1}
    m5 = {"ema_9": 100, "ema_21": 101, "ema_50": 102}
    candidate = logic._evaluate_direction("LONG", row, previous, m1, prior, m5, 100, 104, 98)
    assert candidate["score"] >= config.CONFIG.minimum_score
    assert not candidate["checks"]["m5_not_opposed"]
    assert not candidate["eligible"]


def test_trailing_missing_minutes_and_market_closure():
    """Zaległe świece są blokowane, a trzy brakujące minuty resetują rozgrzewkę."""
    rows = candles()
    result = logic.generate_signal(rows, START + timedelta(minutes=693))
    assert result["signal"] == "NONE"
    assert result["signal_parameters"]["gap_state"]["trailing_missing_expected_minutes"] == 3
    assert result["signal_parameters"]["gap_state"]["trailing_reset"]


def test_scheduled_break_and_weekend_not_a_reset():
    """Planowane przerwy nie są traktowane jako brakujące minuty."""
    before = datetime(2026, 1, 5, 21, 59, tzinfo=UTC)
    after = datetime(2026, 1, 5, 23, 0, tzinfo=UTC)
    assert schedule.missing_expected(before, after) == 0
    friday = datetime(2026, 1, 9, 21, 59, tzinfo=UTC)
    sunday = datetime(2026, 1, 11, 23, 0, tzinfo=UTC)
    assert schedule.missing_expected(friday, sunday) == 0
    rows = candles(1, start=friday) + candles(1, start=sunday)
    result = logic.generate_signal(rows, sunday + timedelta(minutes=1))
    assert result["signal_parameters"]["gap_state"]["reset_count"] == 0


def test_warm_history_survives_weekend_and_vwap_starts_again():
    """Rozgrzewka trwa przez weekend, ale VWAP obejmuje tylko nową sesję."""
    cursor = datetime(2026, 1, 9, 15, 0, tzinfo=UTC)
    rows = []
    while len(rows) < 700:
        if schedule.market_open(cursor):
            rows.extend(candles(1, start=cursor))
        cursor += timedelta(minutes=1)
    result = logic.generate_signal(rows, cursor)
    params = result["signal_parameters"]
    assert params["gap_state"]["reset_count"] == 0
    assert params["warmup"]["ready"]
    vwap = params["indicators"]["vwap"]
    assert vwap["session_start_utc"] == "2026-01-11T23:00:00+00:00"
    assert vwap["volume"] == vwap["candles"] * 100
    assert vwap["complete"]


def test_dst_and_market_profile_overlay(monkeypatch):
    """Weryfikuje kalendarz ET/PL, DST oraz użycie bazowego market_products."""
    assert not schedule.market_open(datetime(2026, 3, 9, 21, 0, tzinfo=UTC))
    assert schedule.market_open(datetime(2026, 3, 9, 22, 0, tzinfo=UTC))
    assert not schedule.market_open(datetime(2026, 3, 8, 21, 0, tzinfo=UTC))
    assert schedule.market_open(datetime(2026, 3, 8, 22, 0, tzinfo=UTC))
    assert schedule.session_start(datetime(2026, 3, 8, 22, 0, tzinfo=UTC)).hour == 22
    assert not schedule.market_open(datetime(2026, 11, 1, 22, 0, tzinfo=UTC))
    assert schedule.market_open(datetime(2026, 11, 1, 23, 0, tzinfo=UTC))
    assert schedule.session_start(datetime(2026, 11, 1, 23, 0, tzinfo=UTC)).hour == 23
    result = logic.generate_signal(candles(1, start=datetime(2026, 3, 9, 21, 59, tzinfo=UTC)),
                                   datetime(2026, 3, 9, 22, 1, tzinfo=UTC))
    assert result["price"] is None  # Minuta 17:59 ET jest poza sesją.
    winter = logic.generate_signal(candles(1), START + timedelta(minutes=1))
    summer_start = datetime(2026, 7, 6, 0, 0, tzinfo=UTC)
    summer = logic.generate_signal(candles(1, start=summer_start), summer_start + timedelta(minutes=1))
    assert winter["candle_open_time"].endswith("+01:00")
    assert summer["candle_open_time"].endswith("+02:00")
    monkeypatch.setattr(schedule, "is_market_open", lambda symbol, instant: False)
    assert not schedule.market_open(START)


def test_session_vwap_weekend_and_missing_volume():
    """Nowa sesja niedzielna nie dziedziczy piątkowego wolumenu."""
    friday = datetime(2026, 1, 9, 21, 59, tzinfo=UTC)
    sunday = datetime(2026, 1, 11, 23, 0, tzinfo=UTC)
    rows = candles(1, start=friday) + candles(1, start=sunday)
    rows[0]["volume"] = 100000
    rows[1]["close"] = 2010
    rows[1]["high"] = 2011
    vwap = indicators.session_vwap(rows)
    assert vwap["volume"] == 100
    assert vwap["value"] == pytest.approx((rows[1]["high"] + rows[1]["low"] + 2010) / 3)
    rows[1]["volume"] = None
    assert indicators.session_vwap(rows)["value"] is None


def test_invalid_empty_and_volume():
    """Dane puste lub uszkodzone nie generują sygnałów ani wartości NaN."""
    assert logic.generate_signal([], START)["signal"] == "NONE"
    for change in ({"close": float("nan")}, {"volume": -1},
                   {"timestamp_utc": datetime(2026, 1, 5)}, {"high": 1}):
        rows = candles()
        rows[-1].update(change)
        result = logic.generate_signal(rows, START + timedelta(minutes=690))
        assert result["signal"] == "NONE"
        assert result["price"] is None
        json.dumps(result, allow_nan=False)
    rows = candles()
    rows[-1]["volume"] = None
    result = logic.generate_signal(rows, START + timedelta(minutes=690))
    assert result["signal"] == "NONE"
    assert not result["signal_parameters"]["safety_checks"]["vwap_available"]
    rows = candles()
    for row in rows:
        row["volume"] = 0
    assert not logic.generate_signal(rows, START + timedelta(minutes=690))[
        "signal_parameters"]["safety_checks"]["vwap_available"]
    assert logic.generate_signal(rows, datetime(2026, 1, 5))["signal"] == "NONE"


def test_direction_conflict_is_none(monkeypatch):
    """Nawet sprzeczne kwalifikujące się kierunki nie prowadzą do transakcji."""
    monkeypatch.setattr(logic, "_evaluate_direction", lambda *args: {"eligible": True})
    result = logic.generate_signal(candles(), START + timedelta(minutes=690))
    assert result["signal"] == "NONE"
    assert "Konflikt kierunków." in result["signal_parameters"]["reasons"]
