"""Testy wskaźników, czasu, luk i decyzji kierunkowych BTC/USD."""

from datetime import date, datetime, timedelta, timezone

import pytest

from claude_btcusd_test import config, indicators, schedule, signal_logic

UTC = timezone.utc


def candle(opened: datetime, close: float = 100.0) -> dict:
    """Tworzy poprawną świecę M1 do testów."""
    return {
        "timestamp_utc": opened,
        "open": close,
        "high": close + 0.1,
        "low": close - 0.1,
        "close": close,
        "volume": None,
    }


def history(count: int, start: datetime | None = None) -> list[dict]:
    """Tworzy rosnącą chronologicznie serię minut BTC/USD."""
    first = start or datetime(2026, 4, 15, 10, 21, tzinfo=UTC)
    return [
        candle(first + timedelta(minutes=index), 100.0 + index * 0.01)
        for index in range(count)
    ]


def signal_indicators() -> dict:
    """Zwraca gotowe wskaźniki i dane potrzebne do testu sygnału."""
    return {
        "atr14_usd": 1.0,
        "atr14_bps": 100.0,
        "atr_ratio": 1.0,
        "rsi5": 60.0,
        "ema9": 100.0,
        "ema21": 99.0,
        "ema55": 98.0,
        "ema20_5m": 99.0,
        "ema20_5m_slope": 0.1,
        "trend_5m": "UP",
        "efficiency_ratio_30": 0.6,
        "bb_zscore": 1.0,
        "bb_width_percentile": 10.0,
        "vwap": None,
        "vwap_distance_atr": None,
        "rel_volume": None,
        "body_ratio": 0.8,
        "donchian_high_20": 100.5,
        "donchian_low_20": 99.5,
        "range_spike_ratio": 1.0,
        "missing_candles_60m": 0,
        "max_consecutive_missing": 0,
        "data_age_minutes": 0.0,
        "segment_candles": 100,
        "m5_candles": 100,
        "volume_sma20": None,
        "macd_line": 1.0,
        "macd_signal": 0.5,
        "macd_histogram": 0.5,
        "candle_range": 0.5,
        "rsi5_previous": 55.0,
        "previous_close": 99.8,
        "previous_ema9": 100.0,
        "_ema9_series": [100.0] * 5,
        "_ema21_series": [99.0] * 5,
        "_macd_histogram_previous": 0.4,
    }


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 3, 6), datetime(2026, 3, 6, 14, 30, tzinfo=UTC)),
        (date(2026, 3, 9), datetime(2026, 3, 9, 13, 30, tzinfo=UTC)),
        (date(2026, 3, 30), datetime(2026, 3, 30, 13, 30, tzinfo=UTC)),
    ],
)
def test_us_cash_open_uses_new_york_dst(day: date, expected: datetime) -> None:
    """Otwarcie USA zmienia UTC zgodnie z kalendarzem czasu letniego."""
    assert schedule.us_cash_open(day) == expected


def test_crypto_sessions_are_based_on_utc_and_trading_is_24_7() -> None:
    """Sesja jest liczona w UTC, a weekend nie zamyka rynku BTC."""
    saturday = datetime(2026, 4, 18, 2, 0, tzinfo=UTC)

    assert schedule.is_trading_minute(saturday)
    assert schedule.is_weekend(saturday)
    assert schedule.session_name(saturday) == "ASIA"
    assert schedule.session_name(datetime(2026, 4, 15, 13, 0, tzinfo=UTC)) == "OVERLAP"


def test_ambiguous_warsaw_time_requires_explicit_offset() -> None:
    """Powtórzona jesienna godzina nie jest po cichu przypisywana do UTC."""
    ambiguous = {
        "datetime": datetime(2026, 10, 25, 2, 30),
        "open": 100,
        "high": 101,
        "low": 99,
        "close": 100,
    }

    with pytest.raises(ValueError, match="niejednoznaczny"):
        signal_logic._normalize_timestamp(ambiguous)

    ambiguous["utc_offset_minutes"] = 120
    assert signal_logic._normalize_timestamp(ambiguous) == datetime(
        2026, 10, 25, 0, 30, tzinfo=UTC
    )


def test_single_missing_minute_keeps_segment_but_two_missing_start_a_new_one() -> None:
    """Jedna brakująca minuta jest tolerowana, dwie resetują wskaźniki."""
    start = datetime(2026, 4, 15, 10, 0, tzinfo=UTC)
    one_missing = [candle(start), candle(start + timedelta(minutes=1)),
                   candle(start + timedelta(minutes=3))]
    two_missing = [candle(start), candle(start + timedelta(minutes=1)),
                   candle(start + timedelta(minutes=4))]

    assert len(indicators.split_segments(one_missing)) == 1
    assert [len(segment) for segment in indicators.split_segments(two_missing)] == [2, 1]


def test_m5_requires_all_five_closed_minutes_and_excludes_future_bucket() -> None:
    """Niekompletny koszyk M5 i koszyk jeszcze niezakończony są pomijane."""
    start = datetime(2026, 4, 15, 10, 0, tzinfo=UTC)
    complete = history(5, start)
    missing_one = complete[:3] + complete[4:]
    cutoff = start + timedelta(minutes=5)

    assert len(indicators.completed_five_minute_candles(complete, cutoff)) == 1
    assert indicators.completed_five_minute_candles(missing_one, cutoff) == []
    assert indicators.completed_five_minute_candles(
        complete, start + timedelta(minutes=4, seconds=59)
    ) == []


def test_indicators_use_wilder_initialization_and_handle_flat_rsi() -> None:
    """EMA startuje od pierwszej ceny, a RSI bez ruchu wynosi 50."""
    assert indicators.ema([10.0, 20.0], 3) == [10.0, 15.0]
    flat_rsi = indicators.rsi_wilder([10.0] * 20, period=14)
    assert flat_rsi[13] is None
    assert flat_rsi[14] == 50.0


@pytest.mark.parametrize(("direction", "trend"), [("LONG", "UP"), ("SHORT", "DOWN")])
def test_generate_signal_emits_long_and_short_with_compact_payload(
    monkeypatch: pytest.MonkeyPatch, direction: str, trend: str
) -> None:
    """Gotowy setup zwraca kierunek i wymagane pola JSON w poprawnej kolejności."""
    candles = history(100)
    current = candles[-1]
    if direction == "LONG":
        current.update(open=101.0, high=102.0, low=100.8, close=101.8)
    else:
        current.update(open=101.8, high=102.0, low=100.8, close=101.0)
    params = signal_indicators()
    params["trend_5m"] = trend

    monkeypatch.setattr(
        signal_logic,
        "calculate_indicators",
        lambda rows, now: (params, rows[-config.M1_WARMUP_CANDLES :]),
    )
    monkeypatch.setattr(
        signal_logic,
        "_candidate_setups",
        lambda rows, current_params, session: [
            {"setup": "TREND_PULLBACK", "signal": direction, "session": session}
        ],
    )
    closed = candles[-1]["timestamp_utc"] + timedelta(minutes=1)

    result = signal_logic.generate_signal(candles, now=closed)

    assert result["signal"] == direction
    assert result["timestamp"] == int(closed.timestamp())
    assert result["instrument"] == "BTCUSD"
    assert result["signal_parameters"]["instrument"] == "BTC/USD"
    assert list(result["signal_parameters"])[:5] == [
        "price",
        "signal",
        "timestamp",
        "instrument",
        "reason",
    ]
    assert len(
        __import__("json").dumps(
            result["signal_parameters"], ensure_ascii=False
        ).encode("utf-8")
    ) <= config.SIGNAL_PARAMS_MAX_BYTES
    assert result["tp_price"] is not None
    assert result["sl_price"] is not None


@pytest.mark.parametrize(
    ("direction", "close", "open_price", "histogram", "donchian_high", "donchian_low"),
    [
        ("LONG", 102.0, 101.0, 0.5, 101.5, 99.0),
        ("SHORT", 99.0, 100.0, -0.5, 101.0, 99.5),
    ],
)
def test_squeeze_breakout_detects_both_directions(
    direction: str,
    close: float,
    open_price: float,
    histogram: float,
    donchian_high: float,
    donchian_low: float,
) -> None:
    """Setup squeeze wymaga wybicia rzeczywistego zakresu i MACD zgodnego z ruchem."""
    candles = history(100)
    candles[-1].update(
        open=open_price,
        high=max(open_price, close) + 0.1,
        low=min(open_price, close) - 0.1,
        close=close,
    )
    params = signal_indicators()
    params.update(
        macd_histogram=histogram,
        donchian_high_20=donchian_high,
        donchian_low_20=donchian_low,
        bb_width_percentile=10.0,
    )

    candidates = signal_logic._candidate_setups(candles, params, "EUROPE")

    assert candidates == [
        {"setup": "SQUEEZE_BREAKOUT", "signal": direction, "session": "EUROPE"}
    ]


@pytest.mark.parametrize(
    ("direction", "values"),
    [
        (
            "LONG",
            {
                "trend_5m": "UP",
                "ema9": 102.0,
                "ema21": 101.0,
                "ema55": 100.0,
                "rsi5": 55.0,
                "rsi5_previous": 50.0,
                "macd_histogram": 0.5,
                "previous_close": 101.0,
                "previous_ema9": 102.0,
            },
        ),
        (
            "SHORT",
            {
                "trend_5m": "DOWN",
                "ema9": 98.5,
                "ema21": 99.5,
                "ema55": 100.0,
                "rsi5": 45.0,
                "rsi5_previous": 50.0,
                "macd_histogram": -0.5,
                "previous_close": 99.0,
                "previous_ema9": 98.5,
            },
        ),
    ],
)
def test_trend_pullback_requires_m1_and_m5_alignment(
    direction: str, values: dict
) -> None:
    """Trend pullback wymaga dotknięcia EMA i potwierdzenia świecy M5."""
    candles = history(100)
    candles[-1].update(
        open=101.0 if direction == "LONG" else 99.6,
        high=102.3 if direction == "LONG" else 99.7,
        low=100.8 if direction == "LONG" else 97.8,
        close=102.2 if direction == "LONG" else 98.0,
    )
    params = signal_indicators()
    params.update(values)
    params["_ema9_series"] = [102.0] * 5 if direction == "LONG" else [98.5] * 5
    params["_ema21_series"] = [101.0] * 5 if direction == "LONG" else [99.5] * 5
    params["efficiency_ratio_30"] = 0.6

    candidates = signal_logic._candidate_setups(candles, params, "EUROPE")

    assert any(candidate["signal"] == direction for candidate in candidates)
    assert any(candidate["setup"] == "TREND_PULLBACK" for candidate in candidates)


def test_sweep_reversion_requires_rejection_of_previous_extreme() -> None:
    """Sweep reversion rozpoznaje naruszenie minimum i zamknięcie z powrotem wyżej."""
    candles = history(100)
    for row in candles[-21:-1]:
        row.update(open=100.0, high=101.0, low=99.0, close=100.0)
    candles[-1].update(open=99.2, high=100.1, low=98.5, close=99.8)
    params = signal_indicators()
    params.update(
        atr14_usd=1.0,
        rsi5=20.0,
        macd_histogram=0.5,
        _macd_histogram_previous=0.1,
        bb_width_percentile=None,
        donchian_high_20=None,
        donchian_low_20=None,
    )

    candidates = signal_logic._candidate_setups(candles, params, "EUROPE")

    assert {"setup": "SWEEP_REVERSION", "signal": "LONG", "session": "EUROPE"} in candidates


def test_none_payload_has_only_five_base_fields() -> None:
    """Brak rozgrzania zapisuje zwięzły NONE z konkretnym powodem."""
    now = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)

    result = signal_logic.generate_signal(history(20, now - timedelta(minutes=20)), now=now)

    assert result["signal"] == "NONE"
    assert result["blocked_by"]
    assert list(result["signal_parameters"]) == [
        "price",
        "signal",
        "timestamp",
        "instrument",
        "reason",
    ]
    assert result["signal_parameters"]["reason"]


def test_forming_candle_is_excluded_and_future_rows_do_not_change_result() -> None:
    """Świeca otwarta w przyszłości nie wpływa na obliczony wynik."""
    rows = history(120)
    now = rows[-1]["timestamp_utc"] + timedelta(minutes=1)
    future = candle(now, close=999.0)

    baseline = signal_logic.generate_signal(rows, now=now)
    with_future = signal_logic.generate_signal(rows + [future], now=now)

    assert baseline["timestamp"] == with_future["timestamp"]
    assert baseline["price"] == with_future["price"]
    assert baseline["params"] == with_future["params"]

    forming_now = now + timedelta(seconds=30)
    excluded = signal_logic.generate_signal(rows + [future], now=forming_now)
    assert excluded["candle_open_epoch"] == int(rows[-1]["timestamp_utc"].timestamp())


def test_gap_resets_warmup_and_single_missing_does_not_reset() -> None:
    """Segment bieżący nie używa wskaźników sprzed długiej luki."""
    start = datetime(2026, 4, 15, 10, 0, tzinfo=UTC)
    rows = history(120, start)
    after_gap = [
        candle(start + timedelta(minutes=index))
        for index in range(123, 223)
    ]
    combined = rows + after_gap
    params, segment = indicators.calculate_indicators(
        combined, after_gap[-1]["timestamp_utc"] + timedelta(minutes=1)
    )

    assert len(segment) == 100
    assert params["m5_candles"] == 19
    assert params["trend_5m"] is None


def test_local_warsaw_datetime_converts_to_epoch_of_close() -> None:
    """Timestamp JSON wskazuje zamknięcie świecy, a nie jej otwarcie."""
    opened_local = datetime(2026, 4, 15, 14, 30)
    row = {
        "datetime": opened_local,
        "utc_offset_minutes": 120,
        "open": 100,
        "high": 101,
        "low": 99,
        "close": 100,
    }
    opened_utc = signal_logic._normalize_timestamp(row)

    assert opened_utc == datetime(2026, 4, 15, 12, 30, tzinfo=UTC)
