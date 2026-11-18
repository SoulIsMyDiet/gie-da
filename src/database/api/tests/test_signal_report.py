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
    assert "p.`100zl_deal`" in query
