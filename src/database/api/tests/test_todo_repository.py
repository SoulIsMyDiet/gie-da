"""Sprawdza migrację, transakcje i cotygodniowe zadanie To-do."""

from __future__ import annotations

import os
import sys
from datetime import datetime as RealDateTime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as market_data_api  # noqa: E402
from todo_repository import TodoRepository  # noqa: E402


def test_todo_schema_is_additive_and_uses_cascade_foreign_key() -> None:
    """Tworzy tylko brakujące tabele i ustawia trwałe relacje danych."""
    cursor = Mock()
    cursor.fetchone.side_effect = [(1,), (1,), (1,), (1,), (1,), (1,)]

    TodoRepository.initialize_schema(cursor)

    schema_statements = "\n".join(
        call.args[0] for call in cursor.execute.call_args_list
    )
    assert "CREATE TABLE IF NOT EXISTS todo_main_tasks" in schema_statements
    assert "UNIQUE KEY uq_todo_main_weekly_key (weekly_key)" in schema_statements
    assert "CREATE TABLE IF NOT EXISTS todo_subtasks" in schema_statements
    assert "FOREIGN KEY (main_task_id)" in schema_statements
    assert "ON DELETE CASCADE" in schema_statements
    assert "prompt LONGTEXT NULL" in schema_statements
    assert "description LONGTEXT NULL" in schema_statements
    assert "sort_order BIGINT UNSIGNED NOT NULL DEFAULT 0" in schema_statements
    assert "hidden BOOLEAN NOT NULL DEFAULT FALSE" in schema_statements
    assert "DROP TABLE" not in schema_statements


def test_todo_schema_adds_new_columns_to_existing_tables() -> None:
    """Rozszerza wcześniejszą bazę o ukrywanie i kolejność bez kasowania danych."""
    cursor = Mock()
    cursor.fetchone.side_effect = [(0,), (0,), (0,), (0,), (0,), (0,)]

    TodoRepository.initialize_schema(cursor)

    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert sum(statement.startswith("ALTER TABLE") for statement in statements) == 6
    assert any("ADD COLUMN `hidden`" in statement for statement in statements)
    assert any("ADD COLUMN `sort_order`" in statement for statement in statements)
    assert any(
        "SET sort_order = id WHERE sort_order = 0" in statement
        for statement in statements
    )
    assert all("DROP TABLE" not in statement for statement in statements)


def test_list_tasks_returns_subtask_content_visibility_flags_only() -> None:
    """Nie ujawnia promptu ani opisu w danych domyślnej listy."""
    connection = Mock()
    cursor = Mock()
    connection.cursor.return_value = cursor
    cursor.fetchall.side_effect = [
        [
            {
                "id": 4,
                "title": "Przejrzyj aplikację",
                "created_at": "2026-11-23 06:00:00",
                "updated_at": "2026-11-23 06:00:00",
            }
        ],
        [
            {
                "id": 8,
                "main_task_id": 4,
                "title": "Raporty",
                "completed": 1,
                "hidden": 0,
                "prompt_exists": 1,
                "description_exists": 1,
                "created_at": "2026-11-23 06:00:00",
                "updated_at": "2026-11-23 06:00:00",
            }
        ],
    ]
    repository = TodoRepository({})

    with patch(
        "todo_repository.mysql.connector.connect", return_value=connection
    ):
        tasks = repository.list_tasks()

    assert tasks[0]["subtasks"] == [
        {
            "id": 8,
            "title": "Raporty",
            "completed": True,
            "hidden": False,
            "prompt_exists": True,
            "description_exists": True,
            "created_at": "2026-11-23 06:00:00",
            "updated_at": "2026-11-23 06:00:00",
        }
    ]
    assert "prompt" not in tasks[0]["subtasks"][0]
    assert "description" not in tasks[0]["subtasks"][0]
    assert "WHERE hidden = FALSE" in cursor.execute.call_args_list[0].args[0]
    assert "ORDER BY main_task_id, sort_order, id" in (
        cursor.execute.call_args_list[1].args[0]
    )
    connection.close.assert_called_once()


def test_todo_list_wire_format_escapes_text_and_hides_prompt_content() -> None:
    """Koduje separatory w nazwach i nie wysyła treści promptu w liście."""
    repository = Mock()
    repository.list_tasks.return_value = [
        {
            "id": 4,
            "title": "Główne\tżółć\n",
            "created_at": "2026-11-23 06:00:00",
            "updated_at": "2026-11-23 06:00:00",
            "subtasks": [
                {
                    "id": 8,
                    "title": "Podzadanie\n",
                    "completed": False,
                    "hidden": False,
                    "prompt_exists": True,
                    "description_exists": False,
                    "created_at": "2026-11-23 06:00:00",
                    "updated_at": "2026-11-23 06:00:00",
                }
            ],
        }
    ]
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(todo_repository=repository))
    )

    response = market_data_api.list_todo_tasks(request)
    lines = response.body.decode("utf-8").splitlines()

    assert lines[0].split("\t") == [
        "M",
        "4",
        "G%C5%82%C3%B3wne%09%C5%BC%C3%B3%C5%82%C4%87%0A",
        "2026-11-23 06:00:00",
        "2026-11-23 06:00:00",
    ]
    assert lines[1].split("\t") == [
        "S",
        "8",
        "4",
        "Podzadanie%0A",
        "0",
        "0",
        "1",
        "0",
        "2026-11-23 06:00:00",
        "2026-11-23 06:00:00",
    ]
    assert "sekretny" not in response.body.decode("utf-8")


def test_hiding_a_main_task_keeps_the_record_in_the_database() -> None:
    """Zapisuje flagę ukrycia zamiast usuwać zadanie główne."""
    connection = Mock()
    cursor = Mock()
    cursor.fetchone.return_value = {"id": 4}
    connection.cursor.return_value = cursor
    repository = TodoRepository({})

    with patch(
        "todo_repository.mysql.connector.connect", return_value=connection
    ):
        hidden = repository.hide_main_task(4)

    assert hidden
    assert any(
        "UPDATE todo_main_tasks SET hidden = TRUE" in call.args[0]
        for call in cursor.execute.call_args_list
    )
    assert all(
        "DELETE" not in call.args[0]
        for call in cursor.execute.call_args_list
    )
    connection.commit.assert_called_once()


def test_hiding_a_subtask_keeps_it_for_parent_completion() -> None:
    """Ukrycie nie zmienia statusu ukończenia zachowanego podzadania."""
    connection = Mock()
    cursor = Mock()
    cursor.fetchone.return_value = {"id": 8}
    connection.cursor.return_value = cursor
    repository = TodoRepository({})

    with patch(
        "todo_repository.mysql.connector.connect", return_value=connection
    ):
        hidden = repository.hide_subtask(8)

    assert hidden
    assert any(
        "UPDATE todo_subtasks SET hidden = TRUE" in call.args[0]
        for call in cursor.execute.call_args_list
    )
    connection.commit.assert_called_once()


def test_move_main_task_swaps_order_with_visible_neighbor() -> None:
    """Przesuwa zadanie główne względem drugiego widocznego zadania."""
    connection = Mock()
    cursor = Mock()
    cursor.fetchall.return_value = [
        {"id": 10, "sort_order": 10},
        {"id": 20, "sort_order": 20},
        {"id": 30, "sort_order": 30},
    ]
    connection.cursor.return_value = cursor
    repository = TodoRepository({})

    with patch(
        "todo_repository.mysql.connector.connect", return_value=connection
    ):
        moved = repository.move_main_task(20, "up")

    assert moved
    assert cursor.execute.call_args_list[-2].args[1] == (10, 20)
    assert cursor.execute.call_args_list[-1].args[1] == (20, 10)
    connection.commit.assert_called_once()


def test_move_subtask_is_limited_to_the_same_parent() -> None:
    """Zapytanie kolejności podzadań uwzględnia identyfikator rodzica."""
    connection = Mock()
    cursor = Mock()
    cursor.fetchone.return_value = {"main_task_id": 7}
    cursor.fetchall.return_value = [
        {"id": 8, "sort_order": 8},
        {"id": 9, "sort_order": 9},
    ]
    connection.cursor.return_value = cursor
    repository = TodoRepository({})

    with patch(
        "todo_repository.mysql.connector.connect", return_value=connection
    ):
        moved = repository.move_subtask(8, "down")

    assert moved
    assert cursor.execute.call_args_list[1].args[1] == (7,)
    assert "main_task_id = %s AND hidden = FALSE" in (
        cursor.execute.call_args_list[1].args[0]
    )
    assert cursor.execute.call_args_list[-2].args[1] == (9, 8)
    assert cursor.execute.call_args_list[-1].args[1] == (8, 9)


def test_move_at_list_boundary_does_not_change_order() -> None:
    """Pozostawia zadanie na miejscu, gdy nie ma sąsiada w wybranym kierunku."""
    connection = Mock()
    cursor = Mock()
    cursor.fetchall.return_value = [{"id": 10, "sort_order": 10}]
    connection.cursor.return_value = cursor
    repository = TodoRepository({})

    with patch(
        "todo_repository.mysql.connector.connect", return_value=connection
    ):
        moved = repository.move_main_task(10, "up")

    assert moved is False
    assert cursor.execute.call_count == 1


def test_hide_endpoint_returns_persistent_hidden_state() -> None:
    """Endpoint ukrywania potwierdza zapis flagi bez usunięcia rekordu."""
    repository = Mock()
    repository.hide_main_task.return_value = True
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(todo_repository=repository))
    )

    result = market_data_api.hide_todo_main_task(4, request)

    assert result == {"hidden": True}
    repository.hide_main_task.assert_called_once_with(4)


def test_move_endpoint_returns_false_at_a_list_boundary() -> None:
    """API poprawnie zwraca brak przesunięcia na początku listy."""
    repository = Mock()
    repository.move_main_task.return_value = False
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(todo_repository=repository))
    )

    result = market_data_api.move_todo_main_task(
        4, market_data_api.TodoMoveRequest(direction="up"), request
    )

    assert result == {"moved": False}
    repository.move_main_task.assert_called_once_with(4, "up")


def test_weekly_review_inserts_parent_and_children_in_one_transaction() -> None:
    """Zapisuje unikalny tydzień wraz z widokami w jednym połączeniu."""
    connection = Mock()
    cursor = Mock()
    cursor.rowcount = 1
    cursor.lastrowid = 17
    connection.cursor.return_value = cursor
    repository = TodoRepository({})

    with patch(
        "todo_repository.mysql.connector.connect", return_value=connection
    ):
        created = repository.create_weekly_review(
            "2026-W48", ["Raporty", "To-do List", "Raporty"]
        )

    assert created
    connection.start_transaction.assert_called_once()
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()
    cursor.executemany.assert_called_once()
    assert cursor.executemany.call_args.args[1] == [
        (17, "Raporty"),
        (17, "To-do List"),
    ]


def test_weekly_review_duplicate_does_not_create_children_again() -> None:
    """Pomija drugie utworzenie zadania dla tego samego tygodnia."""
    connection = Mock()
    cursor = Mock()
    cursor.rowcount = 0
    cursor.fetchone.return_value = {"id": 17}
    connection.cursor.return_value = cursor
    repository = TodoRepository({})

    with patch(
        "todo_repository.mysql.connector.connect", return_value=connection
    ):
        created = repository.create_weekly_review(
            "2026-W48", ["Raporty", "To-do List"]
        )

    assert not created
    cursor.executemany.assert_not_called()
    connection.commit.assert_called_once()


def test_weekly_review_rolls_back_if_subtask_insert_fails() -> None:
    """Cofa główne zadanie, gdy zapis któregoś widoku się nie powiedzie."""
    connection = Mock()
    cursor = Mock()
    cursor.rowcount = 1
    cursor.lastrowid = 17
    cursor.executemany.side_effect = RuntimeError("błąd zapisu")
    connection.cursor.return_value = cursor
    repository = TodoRepository({})

    with (
        patch(
            "todo_repository.mysql.connector.connect",
            return_value=connection,
        ),
        pytest.raises(RuntimeError, match="błąd zapisu"),
    ):
        repository.create_weekly_review("2026-W48", ["Raporty"])

    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


def test_weekly_endpoint_waits_until_six_on_monday(monkeypatch) -> None:
    """Nie tworzy cotygodniowego zadania przed 06:00 czasu warszawskiego."""
    repository = Mock()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(todo_repository=repository)
        )
    )

    class FixedDateTime:
        """Zwraca stałą godzinę lokalną na potrzeby testu harmonogramu."""

        @classmethod
        def now(cls, tz):
            return RealDateTime(2026, 11, 23, 5, 59, tzinfo=tz)

    monkeypatch.setattr(market_data_api, "datetime", FixedDateTime)

    result = market_data_api.create_weekly_todo_review(
        market_data_api.TodoWeeklyReviewRequest(view_names=["Raporty"]),
        request,
    )

    assert result == {
        "created": False,
        "week_key": None,
        "next_check_seconds": 60,
    }
    repository.create_weekly_review.assert_not_called()


def test_weekly_endpoint_uses_registered_views_and_iso_week(monkeypatch) -> None:
    """Przekazuje aktualne widoki i identyfikator tygodnia ISO do repozytorium."""
    repository = Mock()
    repository.create_weekly_review.return_value = True
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(todo_repository=repository)
        )
    )

    class FixedDateTime:
        """Zwraca poniedziałek po godzinie uruchomienia harmonogramu."""

        @classmethod
        def now(cls, tz):
            return RealDateTime(2026, 11, 23, 6, 0, tzinfo=tz)

    monkeypatch.setattr(market_data_api, "datetime", FixedDateTime)
    view_names = ["Raporty", "To-do List"]

    result = market_data_api.create_weekly_todo_review(
        market_data_api.TodoWeeklyReviewRequest(view_names=view_names),
        request,
    )

    assert result == {
        "created": True,
        "week_key": "2026-W48",
        "next_check_seconds": 604800,
    }
    repository.create_weekly_review.assert_called_once_with(
        "2026-W48", view_names
    )


def test_weekly_endpoint_catches_up_after_missed_monday(monkeypatch) -> None:
    """Po restarcie we wtorek tworzy zadanie dla bieżącego tygodnia ISO."""
    repository = Mock()
    repository.create_weekly_review.return_value = True
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(todo_repository=repository)
        )
    )

    class FixedDateTime:
        """Zwraca wtorek nad ranem w strefie Europe/Warsaw."""

        @classmethod
        def now(cls, tz):
            return RealDateTime(2026, 11, 24, 1, 0, tzinfo=tz)

    monkeypatch.setattr(market_data_api, "datetime", FixedDateTime)

    result = market_data_api.create_weekly_todo_review(
        market_data_api.TodoWeeklyReviewRequest(
            view_names=["Raporty", "To-do List"]
        ),
        request,
    )

    assert result == {
        "created": True,
        "week_key": "2026-W48",
        "next_check_seconds": 536400,
    }
    repository.create_weekly_review.assert_called_once()


def test_next_review_delay_accounts_for_warsaw_dst_change() -> None:
    """Wylicza rzeczywisty czas do 06:00 po zmianie czasu letniego."""
    local_now = RealDateTime(
        2026, 3, 23, 6, 0,
        tzinfo=ZoneInfo("Europe/Warsaw"),
    )

    delay = market_data_api._seconds_until_next_todo_review(local_now)

    assert delay == 601200
