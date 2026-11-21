"""Sprawdza historię M1 przekazywaną do budowniczego kontekstu C++."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402


def test_repository_returns_utc_prices_and_marks_open_market_gaps() -> None:
    """Zachowuje precyzję cen i wykrywa brakującą minutę sesji."""
    repository = app.MarketDataRepository.__new__(app.MarketDataRepository)
    repository.database_settings = {}
    connection = Mock()
    cursor = Mock()
    connection.cursor.return_value = cursor
    cursor.fetchall.return_value = [
        (
            datetime(2026, 11, 17, 12, 2),
            0,
            Decimal("1.1025000000"),
            Decimal("1.1030000000"),
            Decimal("1.1020000000"),
            Decimal("1.1028000000"),
        ),
        (
            datetime(2026, 11, 17, 12, 0),
            0,
            Decimal("1.1015000000"),
            Decimal("1.1020000000"),
            Decimal("1.1010000000"),
            Decimal("1.1018000000"),
        ),
    ]

    with patch("app.mysql.connector.connect", return_value=connection), patch.object(
        repository, "_is_market_open", return_value=True
    ):
        candles = repository.fetch_prompt_candle_history("EUR/USD", 1000)

    assert candles == [
        {
            "timestamp_utc": int(
                datetime(2026, 11, 17, 12, 0).replace(
                    tzinfo=app.timezone.utc
                ).timestamp()
            ),
            "open": "1.1015",
            "high": "1.102",
            "low": "1.101",
            "close": "1.1018",
            "gap_before": False,
        },
        {
            "timestamp_utc": int(
                datetime(2026, 11, 17, 12, 2).replace(
                    tzinfo=app.timezone.utc
                ).timestamp()
            ),
            "open": "1.1025",
            "high": "1.103",
            "low": "1.102",
            "close": "1.1028",
            "gap_before": True,
        },
    ]
    assert "ORDER BY DATE_SUB" in cursor.execute.call_args.args[0]
    assert connection.close.called


def test_prompt_history_endpoint_returns_tab_separated_contract() -> None:
    """Zwraca prosty TSV, który może bez JSON-parsera odczytać klient C++."""
    repository = Mock()
    repository.fetch_prompt_candle_history.return_value = [
        {
            "timestamp_utc": 1794916800,
            "open": "17.1",
            "high": "17.2",
            "low": "17.0",
            "close": "17.15",
            "gap_before": True,
        }
    ]
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(repository=repository)
        )
    )

    response = app.prompt_candle_history(
        request, symbol="USD/MXN", limit=1000
    )

    assert response.media_type.startswith("text/tab-separated-values")
    assert response.body.decode() == (
        "timestamp_utc\topen\thigh\tlow\tclose\tgap_before\n"
        "1794916800\t17.1\t17.2\t17.0\t17.15\t1"
    )
    repository.fetch_prompt_candle_history.assert_called_once_with(
        "USD/MXN", 1000
    )
