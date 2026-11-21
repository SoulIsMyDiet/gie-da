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
    signal_price: Decimal = Decimal("1.09900"),
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
        "EUR/USD",
        signal_close,
        direction,
        Decimal("1.10000"),
        stop_loss,
        take_profit,
        Decimal("0.00030"),
        status,
        opened_at or signal_close,
        signal_price,
    )


def _candle(
    minute_offset: int, low: str, high: str
) -> TransactionCandle:
    """Buduje świecę M1 z czasem otwarcia UTC."""
    return TransactionCandle(
        symbol="EUR/USD",
        datetime=datetime(
            2026, 11, 17, 12, 0, tzinfo=timezone.utc
        )
        + timedelta(minutes=minute_offset),
        high=Decimal(high),
        low=Decimal(low),
    )


def test_entry_candle_can_close_transaction_and_stop_loss_has_priority() -> None:
    """Rozlicza SL/TP na świecy wejścia, wybierając SL przy obu trafieniach."""
    cursor = Mock()
    cursor.fetchall.return_value = [
        _transaction(status="awaiting", opened_at=None)
    ]
    cursor.fetchone.return_value = (1,)

    TransactionTracker().process_candles(
        cursor, [_candle(0, "1.09400", "1.11100")]
    )

    assert cursor.execute.call_count == 4
    entry_query, entry_values = cursor.execute.call_args_list[2].args
    assert "transaction_status = 'in progress'" in entry_query
    assert entry_values == (1, datetime(2026, 11, 17, 12, 1), 41)
    close_query, close_values = cursor.execute.call_args_list[3].args
    assert "transaction_status = 'closed'" in close_query
    assert close_values == (
        0,
        Decimal("-0.0053000000"),
        Decimal("-14.45454"),
        datetime(2026, 11, 17, 12, 1),
        41,
    )


@pytest.mark.parametrize(
    ("direction", "signal_price", "low", "high", "should_open"),
    [
        ("long", Decimal("1.09900"), "1.09900", "1.10100", True),
        ("long", Decimal("1.09900"), "1.09800", "1.09990", False),
        ("long", Decimal("1.10100"), "1.09900", "1.10200", True),
        ("long", Decimal("1.10100"), "1.10010", "1.10200", False),
        ("short", Decimal("1.09900"), "1.09900", "1.10100", True),
        ("short", Decimal("1.09900"), "1.09800", "1.09990", False),
        ("short", Decimal("1.10100"), "1.09900", "1.10200", True),
        ("short", Decimal("1.10100"), "1.10010", "1.10200", False),
    ],
)
def test_entry_uses_directional_price_threshold(
    direction: str,
    signal_price: Decimal,
    low: str,
    high: str,
    should_open: bool,
) -> None:
    """Otwiera po HIGH albo LOW zależnie od położenia ENTRY względem sygnału."""
    cursor = Mock()
    cursor.fetchall.return_value = [
        _transaction(
            direction=direction,
            status="awaiting",
            opened_at=None,
            signal_price=signal_price,
        )
    ]
    cursor.fetchone.return_value = (1,)

    TransactionTracker().process_candles(cursor, [_candle(0, low, high)])

    if should_open:
        assert cursor.execute.call_count == 3
        update_query, update_values = cursor.execute.call_args_list[2].args
        assert "transaction_status = 'in progress'" in update_query
        assert update_values == (1, datetime(2026, 11, 17, 12, 1), 41)
    else:
        assert cursor.execute.call_count == 1


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
    cursor.fetchone.return_value = (1,)

    TransactionTracker().process_candles(
        cursor, [_candle(0, "1.09400", "1.11100")]
    )

    update_query, update_values = cursor.execute.call_args_list[2].args
    assert "transaction_status = 'closed'" in update_query
    assert update_values[0] == 1
    assert update_values[1] == Decimal("-0.0053000000")
    assert update_values[2] == Decimal("-14.45454")
    assert update_values[3] == datetime(2026, 11, 17, 12, 1)


def test_short_position_balance_subtracts_the_spread() -> None:
    """Liczy wynik SHORT zgodnie z kierunkiem i odejmuje spread."""
    cursor = Mock()
    cursor.fetchall.return_value = [
        _transaction(direction="short")
    ]
    cursor.fetchone.return_value = (1,)

    TransactionTracker().process_candles(
        cursor, [_candle(0, "1.08900", "1.10000")]
    )

    _, update_values = cursor.execute.call_args_list[2].args
    assert update_values[1] == Decimal("0.0097000000")


def test_transaction_duration_counts_candles_across_weekend_gap() -> None:
    """Liczy następną świecę po weekendzie jako jedną świecę różnicy."""
    cursor = Mock()
    cursor.fetchone.return_value = (1,)
    opened_at = datetime(2026, 11, 20, 22, 0)
    exit_candle_open = datetime(2026, 11, 23, 0, 0)

    elapsed_candles = TransactionTracker._count_transaction_candles(
        cursor,
        "EUR/USD",
        opened_at,
        exit_candle_open,
    )

    assert elapsed_candles == 1
    _, query_values = cursor.execute.call_args.args
    assert query_values == ("EUR/USD", opened_at, exit_candle_open)


def test_repaired_gap_recalculates_an_affected_closed_transaction() -> None:
    """Odtwarza zamkniętą transakcję po uzupełnieniu świecy z jej okresu."""
    cursor = Mock()
    signal_close = datetime(2026, 11, 17, 12, 0)
    cursor.fetchall.side_effect = [
        [
            (
                41,
                "EUR/USD",
                signal_close,
                "long",
                Decimal("1.10000"),
                Decimal("1.09500"),
                Decimal("1.11000"),
                Decimal("0.00030"),
                "closed",
                datetime(2026, 11, 17, 12, 2),
                Decimal("1.10000"),
            )
        ],
        [
            (
                datetime(2026, 11, 17, 12, 0),
                0,
                Decimal("1.10100"),
                Decimal("1.09900"),
            ),
            (
                datetime(2026, 11, 17, 12, 1),
                0,
                Decimal("1.11100"),
                Decimal("1.09400"),
            ),
        ],
    ]
    cursor.fetchone.return_value = (2,)
    repaired_candle = _candle(1, "1.09400", "1.11100")

    TransactionTracker().recalculate_transactions(cursor, [repaired_candle])

    reset_query, reset_values = cursor.execute.call_args_list[2].args
    assert "transaction_status = %s" in reset_query
    assert reset_values == (
        "in progress",
        0,
        signal_close,
        41,
        "closed",
    )
    close_query, close_values = cursor.execute.call_args_list[4].args
    assert "transaction_status = 'closed'" in close_query
    assert close_values == (
        2,
        Decimal("-0.0053000000"),
        Decimal("-14.45454"),
        datetime(2026, 11, 17, 12, 2),
        41,
    )


def test_repaired_gap_replays_an_active_transaction_from_signal_time() -> None:
    """Uwzględnia wcześniejsze wejście odnalezione w uzupełnionej luce."""
    cursor = Mock()
    signal_close = datetime(2026, 11, 17, 12, 0)
    cursor.fetchall.side_effect = [
        [
            (
                42,
                "EUR/USD",
                signal_close,
                "long",
                Decimal("1.10100"),
                Decimal("1.09500"),
                Decimal("1.11000"),
                Decimal("0.00030"),
                "in progress",
                None,
                Decimal("1.10000"),
            )
        ],
        [
            (
                datetime(2026, 11, 17, 12, 0),
                0,
                Decimal("1.10200"),
                Decimal("1.10080"),
            )
        ],
    ]
    cursor.fetchone.return_value = (1,)

    TransactionTracker().recalculate_transactions(
        cursor, [_candle(0, "1.10080", "1.10200")]
    )

    reset_query, reset_values = cursor.execute.call_args_list[2].args
    assert "time_from_open_to_close = NULL" in reset_query
    assert reset_values == (
        "awaiting",
        None,
        None,
        42,
        "in progress",
    )
    entry_query, entry_values = cursor.execute.call_args_list[4].args
    assert "transaction_status = 'in progress'" in entry_query
    assert entry_values == (1, datetime(2026, 11, 17, 12, 1), 42)


def test_in_progress_transaction_requires_an_open_time() -> None:
    """Zgłasza niespójny rekord zamiast po cichu pomijać aktywną pozycję."""
    cursor = Mock()
    transaction = list(_transaction())
    transaction[-2] = None
    cursor.fetchall.return_value = [tuple(transaction)]

    with pytest.raises(ValueError, match="czasu otwarcia"):
        TransactionTracker().process_candles(
            cursor, [_candle(0, "1.09900", "1.10100")]
        )
