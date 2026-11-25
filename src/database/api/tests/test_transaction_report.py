"""Sprawdza filtrowanie, paginację i podsumowania raportu transakcji."""

from __future__ import annotations

import os
import sys
from datetime import date, datetime
from decimal import Decimal
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


def test_repository_returns_signal_datetime_and_ids_as_report_columns() -> None:
    """Udostępnia czas sygnału oraz identyfikatory obu rekordów."""
    repository = app.MarketDataRepository.__new__(app.MarketDataRepository)
    repository.database_settings = {}
    connection = Mock()
    cursor = Mock()
    connection.cursor.return_value = cursor
    cursor.fetchall.return_value = [
        (datetime(2026, 11, 1, 8, 30), 5184, 992, "EUR/USD")
    ]

    with patch("app.mysql.connector.connect", return_value=connection):
        rows, summary, has_next_page = (
            repository.fetch_transaction_report_page(
                date(2026, 11, 1),
                date(2026, 11, 2),
                0,
                [
                    "signal_datetime",
                    "signal_id",
                    "transaction_id",
                    "product",
                ],
                {},
            )
        )

    query, values = cursor.execute.call_args.args
    assert "SELECT s.`datetime`, s.id, t.id, s.product" in query
    assert rows == [["2026-11-01 08:30:00.000000", "5184", "992", "EUR/USD"]]
    assert summary == ["", "", "", ""]
    assert not has_next_page
    assert values == (date(2026, 11, 1), date(2026, 11, 3), 11, 0)
    connection.close.assert_called_once()


def test_repository_filters_transactions_and_summarizes_all_matches() -> None:
    """Używa zakresu dat i filtrów parametryzowanych, a podsumowanie liczy dla całości."""
    repository = app.MarketDataRepository.__new__(app.MarketDataRepository)
    repository.database_settings = {}
    connection = Mock()
    cursor = Mock()
    connection.cursor.return_value = cursor
    cursor.fetchall.return_value = [
        ("EUR/USD", Decimal("1.25"), Decimal("10.00"))
        for _ in range(11)
    ]
    cursor.fetchone.return_value = (Decimal("1.10"), Decimal("100.00"))

    with patch("app.mysql.connector.connect", return_value=connection):
        rows, summary, has_next_page = (
            repository.fetch_transaction_report_page(
                date(2026, 11, 1),
                date(2026, 11, 2),
                0,
                ["product", "saldo_20_aggr_candles", "deal_20"],
                {
                    "product": ["EUR/USD"],
                    "deal_20": ["positive"],
                },
            )
        )

    assert len(rows) == 10
    assert rows[0] == ["EUR/USD", "1.25", "10.00"]
    assert summary == ["", "Średnia: 1.10", "Suma: 100.00"]
    assert has_next_page
    data_query, data_values = cursor.execute.call_args_list[0].args
    assert "JOIN signal_potention AS p" in data_query
    assert "s.product IN (%s)" in data_query
    assert "p.`100zl_deal_20` >= 0" in data_query
    assert "LIMIT %s OFFSET %s" in data_query
    assert data_values == (
        date(2026, 11, 1),
        date(2026, 11, 3),
        "EUR/USD",
        11,
        0,
    )
    assert cursor.execute.call_args_list[1].args[1] == data_values[:-2]
    connection.close.assert_called_once()


def test_report_returns_both_spreads_and_averages_only_true_spread() -> None:
    """Nie podsumowuje stałego spreadu, ale liczy średnią spreadu transakcji."""
    repository = app.MarketDataRepository.__new__(app.MarketDataRepository)
    repository.database_settings = {}
    connection = Mock()
    cursor = Mock()
    connection.cursor.return_value = cursor
    cursor.fetchall.return_value = [
        (Decimal("0.00100"), Decimal("0.00200"))
    ]
    cursor.fetchone.return_value = (Decimal("0.00300"),)

    with patch("app.mysql.connector.connect", return_value=connection):
        rows, summary, has_next_page = (
            repository.fetch_transaction_report_page(
                date(2026, 11, 1),
                date(2026, 11, 2),
                0,
                ["average_spread", "true_spread"],
                {},
            )
        )

    data_query, data_values = cursor.execute.call_args_list[0].args
    summary_query, summary_values = cursor.execute.call_args_list[1].args
    assert "SELECT p.average_spread, t.true_spread" in data_query
    assert "AVG(t.true_spread)" in summary_query
    assert "AVG(p.average_spread)" not in summary_query
    assert data_values == (date(2026, 11, 1), date(2026, 11, 3), 11, 0)
    assert summary_values == data_values[:-2]
    assert rows == [["0.00100", "0.00200"]]
    assert summary == ["", "Średnia: 0.00300"]
    assert not has_next_page
    connection.close.assert_called_once()


def test_repository_uses_minute_and_ai_point_ranges_as_filters() -> None:
    """Zamienia przedziały czasu i punktów AI na granice SQL."""
    repository = app.MarketDataRepository.__new__(app.MarketDataRepository)
    repository.database_settings = {}
    connection = Mock()
    cursor = Mock()
    connection.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (None, None)

    with patch("app.mysql.connector.connect", return_value=connection):
        rows, summary, has_next_page = (
            repository.fetch_transaction_report_page(
                date(2026, 11, 1),
                date(2026, 11, 2),
                0,
                ["time_to_open", "ai_pts"],
                {
                    "time_to_open": ["0", "1-5", "21+"],
                    "ai_pts": ["81-100"],
                },
            )
        )

    query, values = cursor.execute.call_args_list[0].args
    assert "t.time_to_open = %s" in query
    assert "t.time_to_open BETWEEN %s AND %s" in query
    assert "t.time_to_open >= %s" in query
    assert "ad.ai_pts BETWEEN %s AND %s" in query
    assert values == (
        date(2026, 11, 1),
        date(2026, 11, 3),
        0,
        1,
        5,
        21,
        81,
        100,
        11,
        0,
    )
    assert rows == []
    assert summary == ["", ""]
    assert not has_next_page


@pytest.mark.parametrize(
    ("selected_era", "expected_condition"),
    [
        ("legacy", "s.id <= %s"),
        ("new", "s.id > %s"),
    ],
)
def test_repository_filters_signal_era_independently_of_report_columns(
    selected_era: str, expected_condition: str
) -> None:
    """Filtruje sygnały po stałej granicy niezależnie od widocznych kolumn."""
    repository = app.MarketDataRepository.__new__(app.MarketDataRepository)
    repository.database_settings = {}
    connection = Mock()
    cursor = Mock()
    connection.cursor.return_value = cursor
    cursor.fetchall.return_value = []

    with patch("app.mysql.connector.connect", return_value=connection):
        repository.fetch_transaction_report_page(
            date(2026, 11, 1),
            date(2026, 11, 2),
            0,
            ["signal_datetime"],
            {"signal_era": [selected_era]},
        )

    query, values = cursor.execute.call_args.args
    assert expected_condition in query
    assert values == (
        date(2026, 11, 1),
        date(2026, 11, 3),
        app.LEGACY_SIGNAL_MAX_ID,
        11,
        0,
    )


def test_repository_refreshes_dynamic_filter_values_from_database() -> None:
    """Pobiera tylko opcje używane przez listy wielokrotnego wyboru."""
    repository = app.MarketDataRepository.__new__(app.MarketDataRepository)
    repository.database_settings = {}
    connection = Mock()
    cursor = Mock()
    connection.cursor.return_value = cursor
    cursor.fetchall.side_effect = [
        [("EUR/USD",)],
        [("Grok 45",)],
        [("Verifier",)],
    ]

    with patch("app.mysql.connector.connect", return_value=connection):
        options = repository.fetch_transaction_report_options()

    assert options == {
        "product": ["EUR/USD"],
        "algorithm_name": ["Grok 45"],
        "ai_name": ["Verifier"],
    }
    assert len(cursor.execute.call_args_list) == 3
    assert all(
        "SELECT DISTINCT" in call.args[0]
        for call in cursor.execute.call_args_list
    )
    assert all(
        "ai_decision_reason_msg" not in call.args[0]
        for call in cursor.execute.call_args_list
    )
    connection.close.assert_called_once()


def test_report_endpoint_returns_encoded_rows_summary_and_pagination() -> None:
    """Zwraca wybrane kolumny oraz wiersz podsumowania jako bezpieczny TSV."""
    repository = Mock()
    repository.fetch_transaction_report_page.return_value = (
        [["EUR/USD", "123.4"]],
        ["", "Suma: 246.8"],
        True,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repository=repository))
    )
    report = app.TransactionReportRequest(
        columns=["product", "transaction_100zl_deal"],
        filters={"product": ["EUR/USD"]},
    )

    response = app.filtered_transaction_report(
        request, report, date(2026, 11, 1), date(2026, 11, 2), page=3
    )

    assert response.media_type.startswith("text/tab-separated-values")
    assert response.body.decode() == (
        "1\n1\nEUR%2FUSD\t123.4\n\tSuma%3A%20246.8"
    )
    repository.fetch_transaction_report_page.assert_called_once_with(
        date(2026, 11, 1),
        date(2026, 11, 2),
        3,
        ["product", "transaction_100zl_deal"],
        {"product": ["EUR/USD"]},
    )


def test_report_endpoint_accepts_signal_era_without_a_display_column() -> None:
    """Pozwala filtrować legacy bez dodawania go do widocznych kolumn."""
    repository = Mock()
    repository.fetch_transaction_report_page.return_value = ([], [], False)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repository=repository))
    )
    report = app.TransactionReportRequest(
        columns=["signal_datetime"],
        filters={"signal_era": ["legacy"]},
    )

    response = app.filtered_transaction_report(
        request, report, date(2026, 11, 1), date(2026, 11, 2), page=0
    )

    assert response.status_code == 200
    repository.fetch_transaction_report_page.assert_called_once_with(
        date(2026, 11, 1),
        date(2026, 11, 2),
        0,
        ["signal_datetime"],
        {"signal_era": ["legacy"]},
    )


def test_report_endpoint_rejects_more_than_eight_column_units() -> None:
    """Odrzuca wybór przekraczający limit, licząc argumenty AI jako dwie kolumny."""
    repository = Mock()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repository=repository))
    )
    report = app.TransactionReportRequest(
        columns=[
            "product",
            "algorithm_name",
            "signal_status",
            "saldo_20_aggr_candles",
            "best_candle_20_minute",
            "deal_20",
            "saldo_60_aggr_candles",
            "ai_decision_reason_msg",
        ]
    )

    with pytest.raises(app.HTTPException) as exception:
        app.filtered_transaction_report(
            request, report, date(2026, 11, 1), date(2026, 11, 2), page=0
        )

    assert exception.value.status_code == 422
    repository.fetch_transaction_report_page.assert_not_called()


def test_options_endpoint_returns_current_values_with_encoded_text() -> None:
    """Udostępnia dynamiczne opcje bez rozbijania tekstów zawierających separatory TSV."""
    repository = Mock()
    repository.fetch_transaction_report_options.return_value = {
        "product": ["EUR/USD"],
        "algorithm_name": ["Grok\t45\nv2"],
        "ai_name": ["Verifier"],
    }
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repository=repository))
    )

    response = app.transaction_report_options(request)
    response_text = response.body.decode()

    assert "product\tEUR%2FUSD\tEUR%2FUSD" in response_text
    assert (
        "algorithm_name\tGrok%09"
        "45%0Av2\tGrok%09"
        "45%0Av2"
    ) in response_text
    assert "ai_decision_reason_msg" not in response_text
    assert "signal_era\tnew\tNowe" in response_text
    assert "signal_era\tlegacy\tLegacy" in response_text
    repository.fetch_transaction_report_options.assert_called_once_with()
