"""Testy miesięcznego pliku błędów używanego przez lokalne API."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from error_file_logging import MonthlyErrorFileHandler, WARSAW_TIME_ZONE


def test_handler_writes_warning_and_error_as_single_lines(
    tmp_path: Path,
) -> None:
    """Rozdziela poziomy ważności i usuwa podziały wiersza z komunikatów."""
    handler = MonthlyErrorFileHandler(tmp_path)
    warning_record = logging.LogRecord(
        "test.warning",
        logging.WARNING,
        __file__,
        1,
        "Ostrzeżenie\nz drugiego wiersza",
        (),
        None,
    )
    error_record = logging.LogRecord(
        "test.error",
        logging.ERROR,
        __file__,
        2,
        "Błąd",
        (),
        None,
    )

    handler.emit(warning_record)
    handler.emit(error_record)

    current_time = datetime.now(WARSAW_TIME_ZONE)
    log_file = tmp_path / f"LogError-{current_time:%m.%Y}"
    contents = log_file.read_text(encoding="utf-8")
    assert contents.count("\n") == 2
    assert "[Warning] " in contents
    assert "[Error] " in contents
    assert "Ostrzeżenie z drugiego wiersza" in contents


def test_handler_uses_month_and_year_in_filename(tmp_path: Path) -> None:
    """Buduje osobną nazwę pliku dla każdego miesiąca i roku."""
    handler = MonthlyErrorFileHandler(tmp_path)

    assert handler._file_path(datetime(2026, 1, 1, tzinfo=WARSAW_TIME_ZONE)) == (
        tmp_path / "LogError-01.2026"
    )
    assert handler._file_path(datetime(2027, 12, 1, tzinfo=WARSAW_TIME_ZONE)) == (
        tmp_path / "LogError-12.2027"
    )
