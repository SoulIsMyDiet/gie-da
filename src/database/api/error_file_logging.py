"""Zapisuje ostrzeżenia i błędy API do miesięcznych plików tekstowych."""

# Backend
from __future__ import annotations

import logging
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from types import TracebackType
from zoneinfo import ZoneInfo

WARSAW_TIME_ZONE = ZoneInfo("Europe/Warsaw")
_HANDLER_MARKER = "_gieda_monthly_error_handler"
_HOOKS_CONFIGURED = False


class MonthlyErrorFileHandler(logging.Handler):
    """Dopisuje każdy wpis WARNING lub ERROR do pliku właściwego dla miesiąca."""

    def __init__(self, directory: Path) -> None:
        """Tworzy folder logów i plik dla bieżącego miesiąca.

        Args:
            directory: Folder współdzielony przez API i aplikację C++.

        Raises:
            OSError: Gdy nie można utworzyć folderu albo pliku logu.
        """
        super().__init__(level=logging.WARNING)
        self._directory = directory
        self._directory.mkdir(parents=True, exist_ok=True)
        self._file_path(datetime.now(WARSAW_TIME_ZONE)).touch(exist_ok=True)

    def emit(self, record: logging.LogRecord) -> None:
        """Zapisuje pojedynczy wpis z datą, godziną i poziomem ważności.

        Args:
            record: Wpis utworzony przez standardowy moduł logging.
        """
        try:
            current_time = datetime.now(WARSAW_TIME_ZONE)
            severity = (
                "Warning"
                if record.levelno < logging.ERROR
                else "Error"
            )
            message = record.getMessage()
            if record.exc_info:
                traceback = logging.Formatter().formatException(
                    record.exc_info
                )
                message = f"{message} | {traceback}"
            message = re.sub(r"\s+", " ", message).strip()
            line = (
                f"[{severity}] {current_time:%Y-%m-%d %H:%M:%S} "
                f"| Python:{record.name} | {message}\n"
            )
            with self._file_path(current_time).open(
                "a", encoding="utf-8", newline="\n"
            ) as log_file:
                log_file.write(line)
                log_file.flush()
        except Exception:
            self.handleError(record)

    def _file_path(self, current_time: datetime) -> Path:
        """Buduje nazwę pliku z miesiącem i rokiem wskazanej daty.

        Args:
            current_time: Data używana do wybrania pliku.

        Returns:
            Ścieżka w formacie ``LogError-MM.YYYY``.
        """
        return self._directory / (
            f"LogError-{current_time:%m.%Y}"
        )


def configure_error_file_logging(directory: Path) -> None:
    """Włącza zapisywanie ostrzeżeń i błędów wraz z wyjątkami wątków.

    Args:
        directory: Folder współdzielony przez API i aplikację C++.

    Raises:
        OSError: Gdy nie można przygotować folderu albo pliku logu.
    """
    global _HOOKS_CONFIGURED

    root_logger = logging.getLogger()
    existing_handler = next(
        (
            handler
            for handler in root_logger.handlers
            if getattr(handler, _HANDLER_MARKER, False)
        ),
        None,
    )
    if existing_handler is None:
        file_handler = MonthlyErrorFileHandler(directory)
        setattr(file_handler, _HANDLER_MARKER, True)
        root_logger.addHandler(file_handler)
    else:
        file_handler = existing_handler
    if root_logger.level == logging.NOTSET or root_logger.level > logging.WARNING:
        root_logger.setLevel(logging.WARNING)

    for logger_name in ("uvicorn.error", "uvicorn.access"):
        server_logger = logging.getLogger(logger_name)
        if not server_logger.propagate and file_handler not in server_logger.handlers:
            server_logger.addHandler(file_handler)
    logging.captureWarnings(True)

    if not _HOOKS_CONFIGURED:
        _install_thread_exception_hook()
        _install_main_exception_hook()
        _HOOKS_CONFIGURED = True


def _install_thread_exception_hook() -> None:
    """Przekazuje nieobsłużone wyjątki wątków do systemu logowania."""
    previous_hook = threading.excepthook

    def log_thread_exception(
        arguments: threading.ExceptHookArgs,
    ) -> None:
        logging.getLogger("threading").error(
            "Nieobsłużony wyjątek w wątku %s.",
            arguments.thread.name if arguments.thread else "nieznany",
            exc_info=(
                arguments.exc_type,
                arguments.exc_value,
                arguments.exc_traceback,
            ),
        )
        previous_hook(arguments)

    threading.excepthook = log_thread_exception


def _install_main_exception_hook() -> None:
    """Zapisuje wyjątki, które nie zostały obsłużone w głównym wątku."""
    previous_hook = sys.excepthook

    def log_main_exception(
        exception_type: type[BaseException],
        exception: BaseException,
        traceback: TracebackType | None,
    ) -> None:
        logging.getLogger("uncaught").critical(
            "Nieobsłużony wyjątek głównego wątku.",
            exc_info=(exception_type, exception, traceback),
        )
        previous_hook(exception_type, exception, traceback)

    sys.excepthook = log_main_exception
