"""Sprawdza dane zwracane do tabeli sygnałów aplikacji Windows."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import MarketDataRepository  # noqa: E402


def test_signal_report_includes_ai_decision_and_profit_after_20_minutes() -> None:
    """Zwraca decyzję AI i wynik z tabeli potencjału dla sygnału."""
    connection = Mock()
    cursor = Mock()
    connection.cursor.return_value = cursor
    cursor.fetchall.return_value = [
        (
            42,
            datetime(2026, 11, 18, 12, 30, 0),
            "EUR/USD",
            "1.10485",
            "long",
            "Chat-gpt6/EURUSDStrategyV1",
            '{"decision":"buy"}',
            "--",
            250,
            '{"reason":"test"}',
            "3.25000",
        )
    ]
    repository = MarketDataRepository.__new__(MarketDataRepository)
    repository.database_settings = {}

    with patch("app.mysql.connector.connect", return_value=connection):
        rows, has_next_page = repository.fetch_signal_page(0)

    assert not has_next_page
    assert rows == [
        (
            "42",
            "2026-11-18 12:30:00",
            "EUR/USD",
            "1.10485",
            "long",
            "Chat-gpt6/EURUSDStrategyV1",
            '{"decision":"buy"}',
            "--",
            "250",
            '{"reason":"test"}',
            "3.25000",
        )
    ]
    query = cursor.execute.call_args.args[0]
    assert "LEFT JOIN signal_potention AS p ON p.signal_id = s.id" in query
    assert "p.`100zl_deal_20`" in query


def test_signal_potential_schema_preserves_values_when_old_and_new_columns_coexist() -> None:
    """Migruje nazwy kolumn, zachowując istniejące wartości z obu wersji."""
    decimal_columns = {
        "saldo_1st_candle",
        "saldo_2nd_candle",
        "saldo_3rd_candle",
        "saldo_4th_candle",
        "saldo_5th_candle",
        "saldo_10aggr_candles",
        "saldo_20_aggr_candles",
        "saldo_60_aggr_candles",
        "100zl_deal",
        "100zl_deal_20",
        "100zl_deal_60",
        "spread",
        "average_spread",
    }
    initial_columns = {
        *decimal_columns,
        "signal_id",
        "max_candle",
        "best_candle_20_minute",
        "best_candle_60_datetime",
    }
    migrated_columns = initial_columns - {
        "max_candle",
        "100zl_deal",
        "spread",
    }
    cursor = Mock()
    cursor.fetchall.side_effect = [
        [
            (column, "decimal(20,5)" if column in decimal_columns else "int")
            for column in initial_columns
        ],
        [
            (column, "decimal(20,5)" if column in decimal_columns else "int")
            for column in migrated_columns
        ],
    ]
    repository = MarketDataRepository.__new__(MarketDataRepository)

    repository._migrate_signal_potention_schema(cursor)

    statements = [call.args[0] for call in cursor.execute.call_args_list]
    for old_name, new_name in (
        ("max_candle", "best_candle_20_minute"),
        ("100zl_deal", "100zl_deal_20"),
        ("spread", "average_spread"),
    ):
        assert any(
            f"COALESCE(`{new_name}`, `{old_name}`)" in statement
            for statement in statements
        )
        assert any(
            f"DROP COLUMN `{old_name}`" in statement
            for statement in statements
        )
