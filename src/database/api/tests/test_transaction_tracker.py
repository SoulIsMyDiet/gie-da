"""Testy cyklu życia transakcji na podstawie zamkniętych świec M1."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transaction_tracker import (  # noqa: E402
    TransactionCandle,
    TransactionTracker,
)


def _transaction(
    *,
    direction: str = "long",
    status: str = "in progress",
    opened_at: datetime | None = None,
) -> tuple[object, ...]:
    """Zwraca wiersz bazy dla przykładowej transakcji."""
    signal_close = datetime(2026, 11, 17, 12, 0)
    stop_loss = Decimal("1.09500")
    take_profit = Decimal("1.11000")
    if direction == "short":
        stop_loss = Decimal("1.10500")
        take_profit = Decimal("1.09000")
    return (
        41,
        "EURUSD/USD",
        signal_close,
        direction,
        Decimal("1.10000"),
        stop_loss,
        take_profit,
        Decimal("0.00030"),
        status,
        opened_at or signal_close,
    )


def _candle(
    minute_offset: int, low: str, high: str
) -> TransactionCandle:
    """Buduje świecę M1 z czasem otwarcia UTC."""
    return TransactionCandle(
        symbol="EURUSD/USD",
        datetime=datetime(
            2026, 11, 17, 12, 0, tzinfo=timezone.utc
        )
        + timedelta(minutes=minute_offset),
        high=Decimal(high),
        low=Decimal(low),
    )


def test_entry_candle_does_not_also_close_transaction() -> None:
    """Po wejściu w świecy M1 kontrola TP/SL zaczyna się od kolejnej świecy."""
    cursor = Mock()
    cursor.fetchall.return_value = [
        _transaction(status="awaiting", opened_at=None)
    ]
    cursor.fetchone.return_value = (1,)

    TransactionTracker().process_candles(
        cursor, [_candle(0, "1.09900", "1.11100")]
    )

    assert cursor.execute.call_count == 3
    update_query, update_values = cursor.execute.call_args_list[2].args
    assert "transaction_status = 'in progress'" in update_query
    assert update_values == (1, datetime(2026, 11, 17, 12, 1), 41)


def test_time_to_open_is_the_number_of_m1_candles_not_wall_time() -> None:
    """Zapisuje numer świecy wejścia nawet wtedy, gdy między świecami jest luka."""
    cursor = Mock()
    cursor.fetchall.return_value = [
        _transaction(status="awaiting", opened_at=None)
    ]
    cursor.fetchone.return_value = (2,)

    TransactionTracker().process_candles(
        cursor, [_candle(30, "1.09900", "1.10100")]
    )

    _, update_values = cursor.execute.call_args_list[2].args
    assert update_values == (2, datetime(2026, 11, 17, 12, 31), 41)


def test_long_position_uses_stop_loss_when_both_levels_are_touched() -> None:
    """W świecy dotykającej SL i TP wybiera konserwatywnie Stop Loss."""
    cursor = Mock()
    cursor.fetchall.return_value = [_transaction()]

    TransactionTracker().process_candles(
        cursor, [_candle(0, "1.09400", "1.11100")]
    )

    update_query, update_values = cursor.execute.call_args_list[1].args
    assert "transaction_status = 'closed'" in update_query
    assert update_values[1] == Decimal("-0.0053000000")
    assert update_values[2] == Decimal("-14.45454")
    assert update_values[3] == datetime(2026, 11, 17, 12, 1)


def test_short_position_balance_subtracts_the_spread() -> None:
    """Liczy wynik SHORT zgodnie z kierunkiem i odejmuje spread."""
    cursor = Mock()
    cursor.fetchall.return_value = [
        _transaction(direction="short")
    ]

    TransactionTracker().process_candles(
        cursor, [_candle(0, "1.08900", "1.10000")]
    )

    _, update_values = cursor.execute.call_args_list[1].args
    assert update_values[1] == Decimal("0.0097000000")


def test_in_progress_transaction_requires_an_open_time() -> None:
    """Zgłasza niespójny rekord zamiast po cichu pomijać aktywną pozycję."""
    cursor = Mock()
    transaction = list(_transaction())
    transaction[-1] = None
    cursor.fetchall.return_value = [tuple(transaction)]

    with pytest.raises(ValueError, match="czasu otwarcia"):
        TransactionTracker().process_candles(
            cursor, [_candle(0, "1.09900", "1.10100")]
        )
