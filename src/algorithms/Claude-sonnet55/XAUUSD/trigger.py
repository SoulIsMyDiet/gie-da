"""Wyzwalanie analizy pojedynczej świecy i domyślnej pętli monitorującej."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import sys
from threading import Event
from typing import Callable

import mysql.connector

import config
from signal_logic import generate_signal
from sound import play_signal_beep
from storage import (
    XAUUSDStorage,
    default_state_path,
    read_loop_state,
    utc_time,
    write_loop_state,
)
from schedule import is_trading_minute
from schedule import expected_candle_opens

UTC = timezone.utc
logger = logging.getLogger(__name__)


def analyze_candle(
    storage: XAUUSDStorage,
    opened: datetime,
    now: datetime | None = None,
) -> tuple[dict, bool]:
    """Analizuje zamkniętą świecę, zapisuje wynik raz i zwraca stan zapisu.

    Błędne czasy lub brak świecy zgłaszają ValueError; błędy bazy są propagowane.
    """
    opened = utc_time(opened)
    now = utc_time(now if now is not None else datetime.now(UTC))
    if opened.second or opened.microsecond:
        raise ValueError("Otwarcie świecy M1 musi przypadać na pełną minutę.")
    closed = opened + timedelta(minutes=1)
    if closed > now:
        raise ValueError("Świeca nie jest jeszcze zamknięta.")
    already_processed = storage.get_processed_signal(int(closed.timestamp()))
    if already_processed is not None:
        return already_processed, False
    candles = storage.load_context(opened)
    if not candles or utc_time(candles[-1]["timestamp_utc"]) != opened:
        raise ValueError("Nie znaleziono wskazanej świecy w historii.")
    if any(utc_time(candle["timestamp_utc"]) > opened for candle in candles):
        raise ValueError("Historia zawiera świece późniejsze niż analizowana.")
    history = storage.signal_history(closed)
    result = generate_signal(candles, now=now, signal_history=history)
    result_opened = utc_time(datetime.fromisoformat(result["candle_open_time"]))
    if result_opened != opened or result["timestamp"] != int(closed.timestamp()):
        raise ValueError("Wynik strategii nie dotyczy wskazanej świecy.")
    return result, storage.save_signal(result, len(candles))


def _initial_cursor(storage: XAUUSDStorage, state_path) -> int | None:
    """Wznawia pętlę ze stanu lokalnego lub ostatniego zapisu w bazie."""
    cursor = read_loop_state(state_path)
    if cursor is not None:
        return cursor
    last_close = storage.latest_processed_close_epoch()
    if last_close is not None:
        cursor = last_close - 60
    else:
        latest_open = storage.latest_candle_open_epoch()
        cursor = latest_open - 60 if latest_open is not None else None
    if cursor is not None:
        write_loop_state(state_path, cursor)
    return cursor


def run_loop(
    storage: XAUUSDStorage | None = None,
    stop_event: Event | None = None,
    state_path=None,
    now_provider: Callable[[], datetime] | None = None,
    wait: Callable[[float], None] | None = None,
) -> None:
    """Czeka na każdą nową zamkniętą minutę i analizuje ją dokładnie jeden raz."""
    storage = storage or XAUUSDStorage()
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
            now = utc_time(now_provider())
            if cursor is None:
                latest = storage.latest_candle_open_epoch()
                if latest is None:
                    wait(config.POLL_INTERVAL_SECONDS)
                    continue
                cursor = latest - 60
                write_loop_state(state_path, cursor)
            next_open_epoch = cursor + 60
            next_open = datetime.fromtimestamp(next_open_epoch, UTC)
            if not is_trading_minute(next_open):
                next_expected = expected_candle_opens(
                    next_open, next_open + timedelta(days=4)
                )
                if not next_expected:
                    wait(60.0)
                    continue
                next_open = next_expected[0]
                next_open_epoch = int(next_open.timestamp())
                cursor = next_open_epoch - 60
                write_loop_state(state_path, cursor)
                delay = max(1.0, min(60.0, (next_open - now).total_seconds()))
                wait(delay)
                continue
            deadline = next_open + timedelta(
                seconds=60 + config.WAIT_FOR_CANDLE_SECONDS
            )
            if now < next_open + timedelta(minutes=1):
                wait(min(
                    config.POLL_INTERVAL_SECONDS,
                    (next_open + timedelta(minutes=1) - now).total_seconds(),
                ))
                continue
            if storage.has_candle(next_open_epoch):
                result, saved = analyze_candle(storage, next_open, now)
                if saved and config.BEEP_ENABLED:
                    play_signal_beep()
                logger.info(
                    "Przetworzono świecę XAU/USD %s: %s (%s).",
                    next_open.isoformat(),
                    result["signal"],
                    "zapisano" if saved else "duplikat",
                )
                cursor = next_open_epoch
                write_loop_state(state_path, cursor)
            elif now >= deadline:
                logger.warning(
                    "Pominięto brakującą świecę XAU/USD otwartą %s po %s sekundach.",
                    next_open.isoformat(),
                    config.WAIT_FOR_CANDLE_SECONDS,
                )
                cursor = next_open_epoch
                write_loop_state(state_path, cursor)
            else:
                wait(config.POLL_INTERVAL_SECONDS)
            retry_seconds = 1.0
        except mysql.connector.Error:
            logger.exception("Błąd bazy w pętli XAU/USD; ponowienie za %.1f s.", retry_seconds)
            wait(retry_seconds)
            retry_seconds = min(30.0, retry_seconds * 2)
        except Exception:
            logger.exception("Błąd pętli XAU/USD; ponowienie za %.1f s.", retry_seconds)
            wait(retry_seconds)
            retry_seconds = min(config.RETRY_MAX_SECONDS, retry_seconds * 2)


class _ArgumentParser(argparse.ArgumentParser):
    """Przekazuje błędne argumenty jako ValueError do wspólnej obsługi main."""

    def error(self, message: str) -> None:
        """Zgłasza błąd argumentu message."""
        raise ValueError(message)


def main(argv: list[str] | None = None) -> int:
    """Uruchamia domyślną pętlę albo analizę wskazanej świecy."""
    parser = _ArgumentParser(description="Strategia mikrotransakcji XAU/USD.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--loop", action="store_true")
    group.add_argument("--api-candle-open-epoch", type=int)
    group.add_argument("--candle-open-epoch", type=int)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    try:
        args = parser.parse_args(argv)
        if args.api_candle_open_epoch is None and args.candle_open_epoch is None:
            run_loop()
            return 0
        api_mode = args.api_candle_open_epoch is not None
        epoch = args.api_candle_open_epoch if api_mode else args.candle_open_epoch
        opened = datetime.fromtimestamp(epoch, UTC)
        result, saved = analyze_candle(XAUUSDStorage(), opened)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")), flush=True)
        if not saved:
            return 3
        if not api_mode and config.BEEP_ENABLED:
            play_signal_beep()
        return 0 if config.BEEP_ENABLED else 4
    except KeyboardInterrupt:
        logger.info("Pętla strategii XAU/USD została zatrzymana.")
        return 0
    except Exception:
        logger.exception("Nie udało się uruchomić strategii XAU/USD.")
        return 1
