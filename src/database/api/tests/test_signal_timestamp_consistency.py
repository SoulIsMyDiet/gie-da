"""Sprawdza, że sygnały zapisują czas zamknięcia świecy."""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import (  # noqa: E402
    Grok45SignalRecord,
    MarketDataRepository,
    WARSAW_TIME_ZONE,
)
from signal_serializer import Signal  # noqa: E402


def test_chatgpt_eurusd_stores_the_signal_candle_close_time():
    """Zapisuje w `signals.datetime` czas zamknięcia wskazany w wyniku."""
    closed = datetime(2026, 1, 5, 21, 1, tzinfo=timezone.utc)
    signal = Signal(
        direction="LONG",
        timestamp=closed,
        price=1.08,
        ema9_1m=1.08,
        ema21_1m=1.07,
        ema9_5m=1.08,
        ema21_5m=1.07,
        rsi14_1m=60.0,
        atr14_1m=0.001,
        candles_count=100,
    )
    cursor = Mock()
    cursor.rowcount = 1
    cursor.lastrowid = 42

    MarketDataRepository._insert_signal(
        cursor, signal, Decimal("0.00045")
    )

    insert_query, values = cursor.execute.call_args_list[0].args
    assert "source_timestamp" in insert_query
    assert values[0] == closed.astimezone(WARSAW_TIME_ZONE).replace(tzinfo=None)
    assert values[7] == int((closed - timedelta(minutes=1)).timestamp())
    _, potential_values = cursor.execute.call_args_list[1].args
    assert potential_values[1] == Decimal("0.00045")


def test_grok_eurusd_stores_close_time_and_keeps_open_epoch_as_deduplication_key():
    """Zapisuje czas zamknięcia, zachowując epokę otwarcia jako klucz wewnętrzny."""
    opened_epoch = 5940
    closed = datetime.fromtimestamp(opened_epoch + 60, timezone.utc)
    signal = Grok45SignalRecord(
        instrument="EUR/USD",
        price=Decimal("1.08"),
        timestamp=opened_epoch,
        signal="LONG",
        parameters={"ema9": 1.08},
    )
    repository = MarketDataRepository.__new__(MarketDataRepository)
    cursor = Mock()
    cursor.fetchone.return_value = (1,)
    cursor.rowcount = 1
    cursor.lastrowid = 42

    repository._insert_grok45_signal(
        cursor, signal, spread_price=Decimal("0.00045")
    )

    insert_query, values = next(
        call.args
        for call in cursor.execute.call_args_list
        if "INSERT INTO signals" in call.args[0]
    )
    assert "INSERT INTO signals" in insert_query
    assert values[0] == closed.astimezone(WARSAW_TIME_ZONE).replace(tzinfo=None)
    assert json.loads(values[6])["timestamp"] == opened_epoch + 60
    assert values[7] == opened_epoch
    _, potential_values = cursor.execute.call_args_list[-1].args
    assert potential_values[1] == Decimal("0.00045")


def test_grok_signal_keeps_id_when_spread_lookup_uses_the_same_cursor():
    """Zachowuje ID sygnału, gdy zapytanie o spread nadpisuje lastrowid."""
    signal = Grok45SignalRecord(
        instrument="EUR/USD",
        price=Decimal("1.08"),
        timestamp=5940,
        signal="LONG",
        parameters={"ema9": 1.08},
    )
    repository = MarketDataRepository.__new__(MarketDataRepository)
    cursor = Mock()
    cursor.fetchone.return_value = (1,)
    cursor.fetchall.return_value = [
        (Decimal("1.08"), Decimal("1.08045"))
    ]
    cursor.rowcount = 1
    cursor.lastrowid = 42

    def clear_lastrowid_for_spread_lookup(query, _parameters):
        if "SELECT bid, ask" in query:
            cursor.lastrowid = 0

    cursor.execute.side_effect = clear_lastrowid_for_spread_lookup

    repository._insert_grok45_signal(cursor, signal)

    potential_query, potential_values = next(
        call.args
        for call in cursor.execute.call_args_list
        if "INSERT INTO signal_potention" in call.args[0]
    )
    assert "INSERT INTO signal_potention" in potential_query
    assert potential_values == (42, Decimal("0.00045"))
