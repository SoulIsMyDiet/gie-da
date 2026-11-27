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

from app import GROK45_V2_STRATEGY_NAME, MarketDataRepository  # noqa: E402


def test_signal_report_returns_aggregated_decision_without_price() -> None:
    """Zwraca decyzję zagregowaną, pomija cenę i nie duplikuje sygnałów."""
    connection = Mock()
    cursor = Mock()
    connection.cursor.return_value = cursor
    cursor.fetchall.return_value = [
        (
            42,
            datetime(2026, 11, 18, 12, 30, 0),
            "EUR/USD",
            "long",
            "Chat-gpt6/EURUSDStrategyV1",
            "ZGODA",
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
            "long",
            "Chat-gpt6/EURUSDStrategyV1",
            "ZGODA",
            "--",
            "250",
            '{"reason":"test"}',
            "3.25000",
        )
    ]
    query = cursor.execute.call_args.args[0]
    assert "LEFT JOIN signal_potention AS p ON p.signal_id = s.id" in query
    assert "p.`100zl_deal_20`" in query
    assert "CAST(s.price AS CHAR)" not in query
    assert "LEFT JOIN ai_decisions AS ad" in query
    assert "GROUP BY t.signal_id" in query
    assert "ai_summary.accept_count, 0) > 0" in query
    assert "verification_job.status = 'completed'" in query
    assert "ELSE 'OCZEKUJE'" in query
    assert "THEN 'ODRZUCENIE'" in query


def test_signal_report_recovers_grok_name_from_legacy_spread_value() -> None:
    """Rozpoznaje stare rekordy, w których spread trafił do nazwy Groka."""
    connection = Mock()
    cursor = Mock()
    connection.cursor.return_value = cursor
    cursor.fetchall.return_value = [
        (
            43,
            datetime(2026, 11, 27, 14, 53, 0),
            "EUR/USD",
            "short",
            GROK45_V2_STRATEGY_NAME,
            "OCZEKUJE",
            "--",
            100,
            (
                '{"instrument":"EUR/USD","price":1.095,"timestamp":1795787580,'
                '"signal":"SHORT","parameters":{"ema9":1.095}}'
            ),
            "",
        )
    ]
    repository = MarketDataRepository.__new__(MarketDataRepository)
    repository.database_settings = {}

    with patch("app.mysql.connector.connect", return_value=connection):
        rows, _ = repository.fetch_signal_page(0)

    assert rows[0][4] == GROK45_V2_STRATEGY_NAME
    query, parameters = cursor.execute.call_args.args
    assert "REGEXP '^[0-9]+([.][0-9]+)?$'" in query
    assert "JSON_CONTAINS_PATH" in query
    assert parameters[0] == GROK45_V2_STRATEGY_NAME


def test_signal_potential_schema_migrates_60_minute_datetime_to_rank() -> None:
    """Zmienia nazwy sald i układa 60 świec kolejno przed agregatami."""
    old_single_candle_columns = {
        "saldo_1st_candle",
        "saldo_2nd_candle",
        "saldo_3rd_candle",
        "saldo_4th_candle",
        "saldo_5th_candle",
    }
    single_candle_columns = {
        f"saldo{candle_number}can"
        for candle_number in range(1, 61)
    }
    decimal_columns = {
        *old_single_candle_columns,
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
        "id",
        "signal_id",
        "max_candle",
        "best_candle_20_minute",
        "best_candle_60_datetime",
    }
    migrated_columns = initial_columns - {
        *old_single_candle_columns,
        "max_candle",
        "100zl_deal",
        "spread",
        "best_candle_60_datetime",
    }
    migrated_columns.update(single_candle_columns)
    migrated_columns.add("best_candle_60_minute")
    migrated_column_order = [
        "id",
        "signal_id",
        *[f"saldo{index}can" for index in range(1, 6)],
        *sorted(migrated_columns - single_candle_columns - {"id", "signal_id"}),
        *[f"saldo{index}can" for index in range(6, 61)],
    ]
    cursor = Mock()
    cursor.fetchall.side_effect = [
        [
            (
                column,
                "datetime"
                if column == "best_candle_60_datetime"
                else (
                    "decimal(20,5)"
                    if column in decimal_columns
                    else "int"
                ),
            )
            for column in initial_columns
        ],
        [
            (
                column,
                "decimal(20,5)"
                if column in decimal_columns or column in single_candle_columns
                else "int",
            )
            for column in migrated_columns
        ],
        [(column, "decimal(20,5)") for column in migrated_column_order],
    ]
    repository = MarketDataRepository.__new__(MarketDataRepository)

    repository._migrate_signal_potention_schema(cursor)

    statements = [call.args[0] for call in cursor.execute.call_args_list]
    for old_name, new_name in zip(
        sorted(old_single_candle_columns),
        [f"saldo{index}can" for index in range(1, 6)],
    ):
        assert any(
            f"CHANGE COLUMN `{old_name}` `{new_name}`" in statement
            for statement in statements
        )
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
    assert any(
        "SET best_candle_60_datetime = NULL" in statement
        for statement in statements
    )
    assert any(
        "CHANGE COLUMN best_candle_60_datetime" in statement
        and "best_candle_60_minute INT NULL DEFAULT NULL" in statement
        for statement in statements
    )
    assert any(
        "MODIFY COLUMN `saldo60can`" in statement
        and "AFTER `saldo59can`" in statement
        for statement in statements
    )
