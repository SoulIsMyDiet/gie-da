"""Jednorazowa analiza i pętla oczekująca na zamknięte świece USD/JPY."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import sys
from threading import Event
from typing import Callable

import mysql.connector

import config
from schedule import UTC, expected_candle_opens, is_trading_minute
from signal_logic import generate_signal
from sound import play_signal_beep
from storage import (
    USDJPYStorage,
    default_state_path,
    read_loop_state,
    write_loop_state,
)

logger = logging.getLogger(__name__)


def analyze_candle(
    storage: USDJPYStorage,
    opened: datetime,
    now: datetime | None = None,
) -> tuple[dict, bool]:
    """Analizuje i zapisuje jedną zamkniętą świecę, chroniąc przed duplikatem.

    Raises:
        ValueError: Gdy czas jest nieprawidłowy, świeca otwarta lub brak jej w bazie.
        mysql.connector.Error: Gdy operacja bazy danych zawiedzie.
    """
    if opened.tzinfo is None or opened.utcoffset() is None:
        raise ValueError("Czas otwarcia świecy musi zawierać strefę.")
    opened = opened.astimezone(UTC)
    now = datetime.now(UTC) if now is None else now
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Czas bieżący musi zawierać strefę.")
    now = now.astimezone(UTC)
    if opened.second or opened.microsecond:
        raise ValueError("Otwarcie świecy M1 musi przypadać na pełną minutę.")
    closed = opened + timedelta(seconds=config.CANDLE_DURATION_SECONDS)
    if closed > now:
        raise ValueError("Nie można analizować formowanej świecy.")
    closed_epoch = int(closed.timestamp())
    previous = storage.get_processed_signal(closed_epoch)
    if previous is not None:
        return previous, False
    candles = storage.load_context(opened)
    if not candles or candles[-1]["timestamp_utc"].astimezone(UTC) != opened:
        raise ValueError("Nie znaleziono wskazanej świecy w historii.")
    signal_history = storage.signal_history(closed)
    result = generate_signal(candles, now=now, signal_history=signal_history)
    result_opened = datetime.fromisoformat(result["candle_open_time"]).astimezone(UTC)
    if result_opened != opened or result["timestamp"] != closed_epoch:
        raise ValueError("Wynik strategii nie dotyczy wskazanej świecy.")
    return result, storage.save_signal(result, len(candles))


def _initial_cursor(storage: USDJPYStorage, state_path) -> int | None:
    """Wznawia pracę z najpóźniejszego wiarygodnego stanu lokalnego lub bazy."""
    state_cursor = read_loop_state(state_path)
    processed_close = storage.latest_processed_close_epoch()
    database_cursor = processed_close - config.CANDLE_DURATION_SECONDS if processed_close else None
    candidates = [value for value in (state_cursor, database_cursor) if value is not None]
    if candidates:
        cursor = max(candidates)
    else:
        latest_open = storage.latest_candle_open_epoch()
        cursor = (
            latest_open - config.CANDLE_DURATION_SECONDS
            if latest_open is not None
            else None
        )
    if cursor is not None:
        write_loop_state(state_path, cursor)
    return cursor


def run_loop(
    storage: USDJPYStorage | None = None,
    stop_event: Event | None = None,
    state_path=None,
    now_provider: Callable[[], datetime] | None = None,
    wait: Callable[[float], None] | None = None,
) -> None:
    """Odpytuje bazę co sekundę i analizuje każdą nową zamkniętą minutę raz."""
    storage = storage or USDJPYStorage()
    stop_event = stop_event or Event()
    state_path = state_path or default_state_path()
    now_provider = now_provider or (lambda: datetime.now(UTC))
    wait = wait or (lambda seconds: stop_event.wait(seconds))
    cursor = None
    cursor_initialized = False
    retry_seconds = 1.0
    while not stop_event.is_set():
        try:
            if not cursor_initialized:
                cursor = _initial_cursor(storage, state_path)
                cursor_initialized = True
            now = now_provider()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("Zegar pętli musi zwracać czas ze strefą.")
            now = now.astimezone(UTC)
            if cursor is None:
                latest = storage.latest_candle_open_epoch()
                if latest is None:
                    wait(config.POLL_INTERVAL_SECONDS)
                    continue
                cursor = latest - config.CANDLE_DURATION_SECONDS
                write_loop_state(state_path, cursor)

            next_open_epoch = cursor + config.CANDLE_DURATION_SECONDS
            next_open = datetime.fromtimestamp(next_open_epoch, UTC)
            if not is_trading_minute(next_open):
                future = expected_candle_opens(
                    next_open, next_open + timedelta(days=8)
                )
                if not future:
                    wait(60.0)
                    continue
                next_open = future[0]
                next_open_epoch = int(next_open.timestamp())
                cursor = next_open_epoch - config.CANDLE_DURATION_SECONDS
                write_loop_state(state_path, cursor)
                delay = max(
                    1.0,
                    min(60.0, (next_open - now).total_seconds()),
                )
                wait(delay)
                continue

            closed_at = next_open + timedelta(seconds=config.CANDLE_DURATION_SECONDS)
            deadline = closed_at + timedelta(seconds=config.WAIT_FOR_CANDLE_SECONDS)
            if now < closed_at:
                wait(
                    min(
                        config.POLL_INTERVAL_SECONDS,
                        max(0.1, (closed_at - now).total_seconds()),
                    )
                )
                continue
            if storage.has_candle(next_open_epoch):
                result, saved = analyze_candle(storage, next_open, now)
                if saved and config.BEEP_ENABLED:
                    play_signal_beep()
                logger.info(
                    "Przetworzono USD/JPY %s: %s (%s).",
                    next_open.isoformat(),
                    result["signal"],
                    "zapisano" if saved else "duplikat",
                )
                cursor = next_open_epoch
                write_loop_state(state_path, cursor)
            elif now >= deadline:
                logger.warning(
                    "Pominięto brakującą minutę USD/JPY: %s; brak świecy po %s s.",
                    next_open.isoformat(),
                    config.WAIT_FOR_CANDLE_SECONDS,
                )
                cursor = next_open_epoch
                write_loop_state(state_path, cursor)
            else:
                wait(config.POLL_INTERVAL_SECONDS)
            retry_seconds = 1.0
        except KeyboardInterrupt:
            logger.info("Pętla strategii USD/JPY została zatrzymana.")
            return
        except mysql.connector.Error:
            logger.exception(
                "Błąd bazy w pętli USD/JPY; ponowienie za %.1f s.",
                retry_seconds,
            )
            wait(retry_seconds)
            retry_seconds = min(config.RETRY_MAX_SECONDS, retry_seconds * 2)
        except Exception:
            logger.exception(
                "Błąd pętli USD/JPY; ponowienie za %.1f s.", retry_seconds
            )
            wait(retry_seconds)
            retry_seconds = min(config.RETRY_MAX_SECONDS, retry_seconds * 2)


def run_once(
    storage: USDJPYStorage | None = None,
    now: datetime | None = None,
) -> int:
    """Analizuje najnowszą zamkniętą świecę i drukuje jeden JSON na stdout."""
    storage = storage or USDJPYStorage()
    now = datetime.now(UTC) if now is None else now
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Czas jednorazowej analizy musi zawierać strefę.")
    latest = storage.latest_candle_open_epoch()
    if latest is None:
        raise ValueError("Tabela candles nie zawiera świec USD/JPY.")
    opened = datetime.fromtimestamp(latest, UTC)
    result, saved = analyze_candle(storage, opened, now)
    print(
        json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
        flush=True,
    )
    if saved and config.BEEP_ENABLED:
        play_signal_beep()
    return 0 if saved else 3


class _ArgumentParser(argparse.ArgumentParser):
    """Przekazuje błędne argumenty jako ValueError do wspólnej obsługi main."""

    def error(self, message: str) -> None:
        """Zgłasza błąd składni argumentów."""
        raise ValueError(message)


def main(argv: list[str] | None = None) -> int:
    """Uruchamia pętlę, analizę pojedynczą albo świecę wskazaną epoką."""
    parser = _ArgumentParser(description="Strategia mikrotransakcji USD/JPY.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--loop", action="store_true")
    group.add_argument("--candle-open-epoch", type=int)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    try:
        args = parser.parse_args(argv)
        if args.candle_open_epoch is None:
            run_loop()
            return 0
        opened = datetime.fromtimestamp(args.candle_open_epoch, UTC)
        result, saved = analyze_candle(USDJPYStorage(), opened)
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ),
            flush=True,
        )
        if saved and config.BEEP_ENABLED:
            play_signal_beep()
        return 0 if saved else 3
    except KeyboardInterrupt:
        logger.info("Pętla strategii USD/JPY została zatrzymana.")
        return 0
    except Exception:
        logger.exception("Nie udało się uruchomić strategii USD/JPY.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
