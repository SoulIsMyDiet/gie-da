"""Sprawdza filtrowanie dat i paginację raportu świec."""

from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402


def test_repository_filters_inclusive_dates_and_limits_pages_to_ten() -> None:
    """Pobiera 10 rekordów i sprawdza następny wiersz, używając dat włącznie."""
    repository = app.MarketDataRepository.__new__(app.MarketDataRepository)
    repository.database_settings = {}
    connection = Mock()
    cursor = Mock()
    connection.cursor.return_value = cursor
    cursor.fetchall.return_value = [
        (
            "EUR/USD",
            datetime(2026, 11, 17, 12, 0),
            "1.1",
            "1.2",
            "1.0",
            "1.15",
            None,
        )
        for _ in range(11)
    ]

    with patch("app.mysql.connector.connect", return_value=connection):
        rows, has_next_page = repository.fetch_filtered_candle_page(
            date(2026, 11, 17), date(2026, 11, 18), 1
        )

    assert len(rows) == 10
    assert rows[0] == (
        "EUR/USD",
        "2026-11-17 12:00:00",
        "1.1",
        "1.2",
        "1.0",
        "1.15",
        "",
    )
    assert has_next_page
    assert "WHERE datetime >= %s AND datetime < %s" in cursor.execute.call_args.args[0]
    assert cursor.execute.call_args.args[1] == (
        date(2026, 11, 17),
        date(2026, 11, 19),
        11,
        10,
    )
    connection.close.assert_called_once()


def test_filtered_report_endpoint_returns_tsv_and_forwards_date_filter() -> None:
    """Zwraca prosty TSV, zachowując zakres i numer strony przekazane do repozytorium."""
    repository = Mock()
    repository.fetch_filtered_candle_page.return_value = (
        [("EUR/USD", "2026-11-17 12:00:00", "1.1", "1.2", "1.0", "1.15", "")],
        True,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repository=repository))
    )

    response = app.filtered_candle_report(
        request, date(2026, 11, 17), date(2026, 11, 18), page=2
    )

    assert response.media_type.startswith("text/tab-separated-values")
    assert response.body.decode() == (
        "1\nEUR/USD\t2026-11-17 12:00:00\t1.1\t1.2\t1.0\t1.15\t"
    )
    repository.fetch_filtered_candle_page.assert_called_once_with(
        date(2026, 11, 17), date(2026, 11, 18), 2
    )


def test_filtered_report_rejects_reversed_date_range() -> None:
    """Odrzuca nieprawidłowy zakres przed wykonaniem zapytania do bazy."""
    repository = Mock()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repository=repository))
    )

    with pytest.raises(app.HTTPException) as exception:
        app.filtered_candle_report(
            request, date(2026, 11, 18), date(2026, 11, 17), page=0
        )

    assert exception.value.status_code == 422
    repository.fetch_filtered_candle_page.assert_not_called()
