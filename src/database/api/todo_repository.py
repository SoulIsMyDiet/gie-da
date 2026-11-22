"""Trwały zapis zadań To-do w istniejącej bazie MySQL."""

# Backend
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import mysql.connector


class TodoRepository:
    """Udostępnia transakcyjne operacje na zadaniach głównych i podrzędnych."""

    def __init__(self, database_settings: dict[str, Any]) -> None:
        """Zapamiętuje ustawienia połączenia używane przez istniejące API."""
        self.database_settings = database_settings

    @staticmethod
    def initialize_schema(cursor: Any) -> None:
        """Tworzy brakujące tabele bez usuwania istniejących danych."""
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS todo_main_tasks (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                title LONGTEXT NOT NULL,
                weekly_key CHAR(8) NULL,
                sort_order BIGINT UNSIGNED NOT NULL DEFAULT 0,
                hidden BOOLEAN NOT NULL DEFAULT FALSE,
                created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                    ON UPDATE CURRENT_TIMESTAMP(6),
                PRIMARY KEY (id),
                UNIQUE KEY uq_todo_main_weekly_key (weekly_key),
                INDEX idx_todo_main_visibility_order (hidden, sort_order, id),
                INDEX idx_todo_main_created (created_at, id),
                CONSTRAINT chk_todo_main_weekly_key_length
                    CHECK (weekly_key IS NULL OR CHAR_LENGTH(weekly_key) = 8)
            ) ENGINE=InnoDB
            DEFAULT CHARACTER SET utf8mb4
            COLLATE utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS todo_subtasks (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                main_task_id BIGINT UNSIGNED NOT NULL,
                title LONGTEXT NOT NULL,
                completed BOOLEAN NOT NULL DEFAULT FALSE,
                sort_order BIGINT UNSIGNED NOT NULL DEFAULT 0,
                hidden BOOLEAN NOT NULL DEFAULT FALSE,
                prompt LONGTEXT NULL,
                description LONGTEXT NULL,
                created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                    ON UPDATE CURRENT_TIMESTAMP(6),
                PRIMARY KEY (id),
                INDEX idx_todo_subtasks_visibility_order
                    (main_task_id, hidden, sort_order, id),
                INDEX idx_todo_subtasks_parent_created
                    (main_task_id, created_at, id),
                CONSTRAINT chk_todo_subtasks_completed
                    CHECK (completed IN (0, 1)),
                CONSTRAINT fk_todo_subtasks_main_task
                    FOREIGN KEY (main_task_id)
                    REFERENCES todo_main_tasks (id)
                    ON DELETE CASCADE
            ) ENGINE=InnoDB
            DEFAULT CHARACTER SET utf8mb4
            COLLATE utf8mb4_unicode_ci
            """
        )
        schema_columns = (
            ("todo_main_tasks", "sort_order", "BIGINT UNSIGNED NOT NULL DEFAULT 0"),
            ("todo_main_tasks", "hidden", "BOOLEAN NOT NULL DEFAULT FALSE"),
            ("todo_subtasks", "sort_order", "BIGINT UNSIGNED NOT NULL DEFAULT 0"),
            ("todo_subtasks", "hidden", "BOOLEAN NOT NULL DEFAULT FALSE"),
        )
        for table_name, column_name, definition in schema_columns:
            TodoRepository._ensure_column(
                cursor, table_name, column_name, definition
            )

        schema_indexes = (
            (
                "todo_main_tasks",
                "idx_todo_main_visibility_order",
                "(hidden, sort_order, id)",
            ),
            (
                "todo_subtasks",
                "idx_todo_subtasks_visibility_order",
                "(main_task_id, hidden, sort_order, id)",
            ),
        )
        for table_name, index_name, columns in schema_indexes:
            TodoRepository._ensure_index(
                cursor, table_name, index_name, columns
            )

        cursor.execute(
            "UPDATE todo_main_tasks SET sort_order = id WHERE sort_order = 0"
        )
        cursor.execute(
            "UPDATE todo_subtasks SET sort_order = id WHERE sort_order = 0"
        )

    @staticmethod
    def _ensure_column(
        cursor: Any, table_name: str, column_name: str, definition: str
    ) -> None:
        """Dodaje brakującą kolumnę bez zmiany już istniejących rekordów."""
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = %s
              AND column_name = %s
            """,
            (table_name, column_name),
        )
        row = cursor.fetchone()
        if row is None or row[0] == 0:
            cursor.execute(
                f"ALTER TABLE `{table_name}` "
                f"ADD COLUMN `{column_name}` {definition}"
            )

    @staticmethod
    def _ensure_index(
        cursor: Any, table_name: str, index_name: str, columns: str
    ) -> None:
        """Dodaje indeks porządku, jeśli tabela pochodzi ze starszego schematu."""
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = %s
              AND index_name = %s
            """,
            (table_name, index_name),
        )
        row = cursor.fetchone()
        if row is None or row[0] == 0:
            cursor.execute(
                f"ALTER TABLE `{table_name}` "
                f"ADD INDEX `{index_name}` {columns}"
            )

    @contextmanager
    def _cursor(
        self, *, transactional: bool = False
    ) -> Iterator[tuple[Any, Any]]:
        """Otwiera połączenie i zawsze zwalnia jego zasoby."""
        connection = mysql.connector.connect(**self.database_settings)
        cursor = None
        try:
            if transactional:
                connection.start_transaction()
            cursor = connection.cursor(dictionary=True)
            try:
                yield connection, cursor
                if transactional:
                    connection.commit()
            except Exception:
                if transactional:
                    connection.rollback()
                raise
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                connection.close()

    def list_tasks(self) -> list[dict[str, Any]]:
        """Pobiera zadania oraz wylicza stan zadań głównych z ich podzadań."""
        with self._cursor(transactional=True) as (_, cursor):
            cursor.execute(
                """
                SELECT id, title, created_at, updated_at
                FROM todo_main_tasks
                WHERE hidden = FALSE
                ORDER BY sort_order, id
                """
            )
            main_rows = cursor.fetchall()
            tasks = {
                int(row["id"]): {
                    "id": int(row["id"]),
                    "title": row["title"],
                    "created_at": str(row["created_at"]),
                    "updated_at": str(row["updated_at"]),
                    "subtasks": [],
                }
                for row in main_rows
            }
            if not tasks:
                return []

            cursor.execute(
                """
                SELECT id, main_task_id, title, completed, hidden,
                       prompt IS NOT NULL AS prompt_exists,
                       description IS NOT NULL AS description_exists,
                       created_at, updated_at
                FROM todo_subtasks
                ORDER BY main_task_id, sort_order, id
                """
            )
            for row in cursor.fetchall():
                parent = tasks.get(int(row["main_task_id"]))
                if parent is None:
                    continue
                parent["subtasks"].append(
                    {
                        "id": int(row["id"]),
                        "title": row["title"],
                        "completed": bool(row["completed"]),
                        "hidden": bool(row["hidden"]),
                        "prompt_exists": bool(row["prompt_exists"]),
                        "description_exists": bool(row["description_exists"]),
                        "created_at": str(row["created_at"]),
                        "updated_at": str(row["updated_at"]),
                    }
                )

        return list(tasks.values())

    def create_main_task(self, title: str) -> int:
        """Zapisuje zadanie główne i zwraca jego identyfikator."""
        with self._cursor(transactional=True) as (_, cursor):
            cursor.execute(
                "INSERT INTO todo_main_tasks (title) VALUES (%s)",
                (title,),
            )
            task_id = int(cursor.lastrowid)
            cursor.execute(
                "UPDATE todo_main_tasks SET sort_order = %s WHERE id = %s",
                (task_id, task_id),
            )
            return task_id

    def create_subtask(self, main_task_id: int, title: str) -> int | None:
        """Dodaje podzadanie albo zwraca None, jeśli rodzic nie istnieje."""
        with self._cursor(transactional=True) as (_, cursor):
            cursor.execute(
                "SELECT id FROM todo_main_tasks WHERE id = %s FOR UPDATE",
                (main_task_id,),
            )
            if cursor.fetchone() is None:
                return None
            cursor.execute(
                """
                INSERT INTO todo_subtasks (main_task_id, title)
                VALUES (%s, %s)
                """,
                (main_task_id, title),
            )
            subtask_id = int(cursor.lastrowid)
            cursor.execute(
                "UPDATE todo_subtasks SET sort_order = %s WHERE id = %s",
                (subtask_id, subtask_id),
            )
            return subtask_id

    def hide_main_task(self, main_task_id: int) -> bool:
        """Ukrywa zadanie główne bez usuwania go ani jego podzadań."""
        with self._cursor(transactional=True) as (_, cursor):
            cursor.execute(
                "SELECT id FROM todo_main_tasks WHERE id = %s FOR UPDATE",
                (main_task_id,),
            )
            if cursor.fetchone() is None:
                return False
            cursor.execute(
                "UPDATE todo_main_tasks SET hidden = TRUE WHERE id = %s",
                (main_task_id,),
            )
            return True

    def hide_subtask(self, subtask_id: int) -> bool:
        """Ukrywa podzadanie, zachowując je dla obliczania statusu rodzica."""
        with self._cursor(transactional=True) as (_, cursor):
            cursor.execute(
                "SELECT id FROM todo_subtasks WHERE id = %s FOR UPDATE",
                (subtask_id,),
            )
            if cursor.fetchone() is None:
                return False
            cursor.execute(
                "UPDATE todo_subtasks SET hidden = TRUE WHERE id = %s",
                (subtask_id,),
            )
            return True

    def move_main_task(self, main_task_id: int, direction: str) -> bool | None:
        """Zmienia kolejność widocznego zadania głównego; None oznacza brak."""
        if direction not in {"up", "down"}:
            raise ValueError("Kierunek przesunięcia musi być równy up albo down.")
        with self._cursor(transactional=True) as (_, cursor):
            cursor.execute(
                """
                SELECT id, sort_order
                FROM todo_main_tasks
                WHERE hidden = FALSE
                ORDER BY sort_order, id
                FOR UPDATE
                """
            )
            rows = cursor.fetchall()
            return self._swap_visible_order(
                cursor, "todo_main_tasks", rows, main_task_id, direction
            )

    def move_subtask(
        self, subtask_id: int, direction: str
    ) -> bool | None:
        """Przesuwa podzadanie wyłącznie wśród dzieci tego samego rodzica."""
        if direction not in {"up", "down"}:
            raise ValueError("Kierunek przesunięcia musi być równy up albo down.")
        with self._cursor(transactional=True) as (_, cursor):
            cursor.execute(
                "SELECT main_task_id FROM todo_subtasks WHERE id = %s",
                (subtask_id,),
            )
            parent = cursor.fetchone()
            if parent is None:
                return None
            cursor.execute(
                """
                SELECT id, sort_order
                FROM todo_subtasks
                WHERE main_task_id = %s AND hidden = FALSE
                ORDER BY sort_order, id
                FOR UPDATE
                """,
                (parent["main_task_id"],),
            )
            rows = cursor.fetchall()
            return self._swap_visible_order(
                cursor, "todo_subtasks", rows, subtask_id, direction
            )

    @staticmethod
    def _swap_visible_order(
        cursor: Any,
        table_name: str,
        rows: list[dict[str, Any]],
        task_id: int,
        direction: str,
    ) -> bool | None:
        """Zamienia kolejność z widocznym sąsiadem bez przenoszenia ukrytych."""
        task_index = next(
            (
                index
                for index, row in enumerate(rows)
                if int(row["id"]) == task_id
            ),
            None,
        )
        if task_index is None:
            return None
        neighbor_index = task_index + (-1 if direction == "up" else 1)
        if not 0 <= neighbor_index < len(rows):
            return False

        task = rows[task_index]
        neighbor = rows[neighbor_index]
        cursor.execute(
            f"UPDATE `{table_name}` SET sort_order = %s WHERE id = %s",
            (neighbor["sort_order"], task["id"]),
        )
        cursor.execute(
            f"UPDATE `{table_name}` SET sort_order = %s WHERE id = %s",
            (task["sort_order"], neighbor["id"]),
        )
        return True

    def set_subtask_completed(
        self, subtask_id: int, completed: bool
    ) -> bool:
        """Zapisuje stan podzadania lub zwraca False, jeśli go nie ma."""
        with self._cursor(transactional=True) as (_, cursor):
            cursor.execute(
                "SELECT id FROM todo_subtasks WHERE id = %s FOR UPDATE",
                (subtask_id,),
            )
            if cursor.fetchone() is None:
                return False
            cursor.execute(
                "UPDATE todo_subtasks SET completed = %s WHERE id = %s",
                (completed, subtask_id),
            )
            return True

    def get_subtask_text(self, subtask_id: int, field: str) -> str | None:
        """Pobiera prompt lub opis; None oznacza brak zadania."""
        if field not in {"prompt", "description"}:
            raise ValueError("Nieobsługiwane pole tekstowe podzadania.")
        with self._cursor() as (_, cursor):
            cursor.execute(
                f"SELECT `{field}` FROM todo_subtasks WHERE id = %s",
                (subtask_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return row[field] or ""

    def update_subtask_text(
        self, subtask_id: int, field: str, value: str
    ) -> bool:
        """Zapisuje prompt lub opis bez narzucania limitu długości."""
        if field not in {"prompt", "description"}:
            raise ValueError("Nieobsługiwane pole tekstowe podzadania.")
        with self._cursor(transactional=True) as (_, cursor):
            cursor.execute(
                "SELECT id FROM todo_subtasks WHERE id = %s FOR UPDATE",
                (subtask_id,),
            )
            if cursor.fetchone() is None:
                return False
            cursor.execute(
                f"UPDATE todo_subtasks SET `{field}` = %s WHERE id = %s",
                (value, subtask_id),
            )
            return True

    def delete_main_task(self, main_task_id: int) -> bool:
        """Trwale usuwa zadanie główne wraz z podzadaniami przez CASCADE."""
        with self._cursor(transactional=True) as (_, cursor):
            cursor.execute(
                "DELETE FROM todo_main_tasks WHERE id = %s",
                (main_task_id,),
            )
            return cursor.rowcount == 1

    def delete_subtask(self, subtask_id: int) -> bool:
        """Trwale usuwa podzadanie; stan rodzica zostanie wyliczony ponownie."""
        with self._cursor(transactional=True) as (_, cursor):
            cursor.execute(
                "DELETE FROM todo_subtasks WHERE id = %s",
                (subtask_id,),
            )
            return cursor.rowcount == 1

    def create_weekly_review(
        self, week_key: str, view_names: list[str]
    ) -> bool:
        """Tworzy najwyżej jedno tygodniowe zadanie wraz z widokami."""
        distinct_names = list(dict.fromkeys(name.strip() for name in view_names))
        if not week_key or len(week_key) != 8:
            raise ValueError("Identyfikator tygodnia musi mieć 8 znaków.")
        if not distinct_names or any(not name for name in distinct_names):
            raise ValueError("Lista widoków nie może być pusta.")

        with self._cursor(transactional=True) as (_, cursor):
            cursor.execute(
                """
                INSERT IGNORE INTO todo_main_tasks (title, weekly_key)
                VALUES (%s, %s)
                """,
                ("Przejrzyj aplikację", week_key),
            )
            if cursor.rowcount == 1:
                main_task_id = int(cursor.lastrowid)
                cursor.execute(
                    "UPDATE todo_main_tasks SET sort_order = %s WHERE id = %s",
                    (main_task_id, main_task_id),
                )
                cursor.executemany(
                    """
                    INSERT INTO todo_subtasks (main_task_id, title)
                    VALUES (%s, %s)
                    """,
                    [(main_task_id, name) for name in distinct_names],
                )
                cursor.execute(
                    """
                    UPDATE todo_subtasks
                    SET sort_order = id
                    WHERE main_task_id = %s
                    """,
                    (main_task_id,),
                )
                return True

            cursor.execute(
                """
                SELECT id
                FROM todo_main_tasks
                WHERE weekly_key = %s
                FOR UPDATE
                """,
                (week_key,),
            )
            if cursor.fetchone() is None:
                raise RuntimeError(
                    "Nie udało się potwierdzić tygodniowego zadania."
                )
            return False
