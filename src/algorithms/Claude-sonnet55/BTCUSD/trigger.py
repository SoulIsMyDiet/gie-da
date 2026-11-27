"""Backend: analiza po zamknięciu świecy oraz samodzielna pętla BTC/USD."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time

import mysql.connector

try:
    from . import config
    from .schedule import as_utc
    from .signal_logic import generate_signal
    from .sound import play_signal_confirmation
    from .storage import BTCUSDStorage
except ImportError:
    import config
    from schedule import as_utc
    from signal_logic import generate_signal
    from sound import play_signal_confirmation
    from storage import BTCUSDStorage

logger = logging.getLogger(__name__)


def _missing_candle_result(opened: datetime, now: datetime) -> dict:
    """Buduje diagnostykę minuty, której nie ma w bazie; wynik nie jest zapisywany."""
    opened_utc = as_utc(opened)
    closed = opened_utc + config.CANDLE_DURATION
    result = generate_signal([], now=now)
    result.update(
        candle_open_time=opened_utc.astimezone(config.WARSAW).isoformat(),
        candle_open_epoch=int(opened_utc.timestamp()),
        timestamp=int(closed.timestamp()),
        timestamp_epoch=int(closed.timestamp()),
        candle_close_time=closed.astimezone(config.WARSAW).isoformat(),
        blocked_by=["missing_candle"],
        reason="Brak zamkniętej świecy BTC/USD w lokalnej bazie danych.",
    )
    result["signal_parameters"] = {
        "price": None,
        "signal": "NONE",
        "timestamp": int(closed.timestamp()),
        "instrument": config.INSTRUMENT_PARAMS_NAME,
        "reason": result["reason"],
    }
    return result


def _already_processed_result(opened: datetime, now: datetime) -> dict:
    """Buduje lokalny wynik informacyjny bez ponownego zapisu ewaluacji."""
    opened_utc = as_utc(opened)
    closed = opened_utc + config.CANDLE_DURATION
    result = generate_signal([], now=now)
    result.update(
        candle_open_time=opened_utc.astimezone(config.WARSAW).isoformat(),
        candle_open_epoch=int(opened_utc.timestamp()),
        timestamp=int(closed.timestamp()),
        timestamp_epoch=int(closed.timestamp()),
        candle_close_time=closed.astimezone(config.WARSAW).isoformat(),
        blocked_by=["already_processed"],
        reason="Ta świeca BTC/USD została już wcześniej oceniona.",
    )
    result["signal_parameters"].update(
        timestamp=int(closed.timestamp()),
        reason=result["reason"],
    )
    return result


def analyze_candle(
    storage: BTCUSDStorage,
    opened: datetime,
    now: datetime | None = None,
) -> tuple[dict, bool]:
    """Analizuje jedną zamkniętą świecę; zwraca wynik i informację o nowym zapisie."""
    opened_utc = as_utc(opened)
    reference = as_utc(now or datetime.now(timezone.utc))
    if opened_utc.second or opened_utc.microsecond:
        raise ValueError("Otwarcie świecy M1 musi wypadać na pełnej minucie.")
    if opened_utc + config.CANDLE_DURATION > reference:
        raise ValueError("Nie można oceniać świecy, która jeszcze się tworzy.")
    if storage.has_processed(opened_utc):
        logger.info("Świeca %s była już oceniona.", opened_utc.isoformat())
        return _already_processed_result(opened_utc, reference), False

    candles = storage.load_context(opened_utc)
    if not candles or as_utc(candles[-1]["timestamp_utc"]) != opened_utc:
        return _missing_candle_result(opened_utc, reference), False

    last_signal_timestamp = storage.last_signal_timestamp(
        opened_utc,
        config.COOLDOWN_MINUTES * config.SECONDS_PER_MINUTE,
    )
    result = generate_signal(
        candles,
        now=reference,
        last_signal_timestamp=last_signal_timestamp,
    )
    saved = storage.save(
        result,
        len(candles),
        enqueue_notification=config.BEEP_ENABLED,
    )
    logger.info(
        "BTC/USD: świeca %s, wynik %s, zapis=%s, blokady=%s",
        opened_utc.isoformat(),
        result["signal"],
        saved,
        result["blocked_by"],
    )
    return result, saved


def latest_closed_open(now: datetime) -> datetime:
    """Zwraca nominalne otwarcie ostatniej zamkniętej minuty UTC."""
    current = as_utc(now)
    return current.replace(second=0, microsecond=0) - config.CANDLE_DURATION


def run_latest(storage: BTCUSDStorage, now: datetime | None = None) -> tuple[dict, bool]:
    """Analizuje najnowszą istniejącą, zamkniętą świecę."""
    reference = as_utc(now or datetime.now(timezone.utc))
    opened = storage.latest_closed_open(reference)
    if opened is None:
        return generate_signal([], now=reference), False
    return analyze_candle(storage, opened, reference)


def load_checkpoint(path: Path) -> datetime | None:
    """Odczytuje ostatnią obsłużoną minutę; uszkodzony plik zgłasza błąd."""
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as state_file:
        value = json.load(state_file)["last_handled_open"]
    opened = as_utc(datetime.fromisoformat(value))
    if opened.second or opened.microsecond:
        raise ValueError("Minuta w pliku stanu nie jest wyrównana.")
    return opened


def save_checkpoint(path: Path, opened: datetime) -> None:
    """Atomowo zapisuje postęp pętli, aby restart nie powtarzał obsłużonej minuty."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as state_file:
            temporary_path = Path(state_file.name)
            json.dump(
                {"last_handled_open": as_utc(opened).isoformat()},
                state_file,
            )
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _emit(result: dict) -> None:
    """Wypisuje pełny wynik jako pojedynczy obiekt JSON na stdout."""
    print(
        json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
        flush=True,
    )


def run_loop(storage: BTCUSDStorage, checkpoint_path: Path) -> None:
    """Przetwarza minuty po kolei i po utracie bazy ponawia odczyt bez przesunięcia stanu."""
    checkpoint = load_checkpoint(checkpoint_path)
    latest = latest_closed_open(datetime.now(timezone.utc))
    processed = storage.latest_processed_open()
    if processed is not None and (checkpoint is None or processed > checkpoint):
        checkpoint = processed
    if checkpoint is None:
        checkpoint = latest - config.CANDLE_DURATION

    while True:
        now = datetime.now(timezone.utc)
        latest = latest_closed_open(now)
        opened = checkpoint + config.CANDLE_DURATION
        if opened > latest:
            time.sleep(config.POLL_INTERVAL_SECONDS)
            continue
        deadline = (
            opened
            + config.CANDLE_DURATION
            + timedelta(seconds=config.WAIT_FOR_CANDLE_SECONDS)
        )
        try:
            result, saved = analyze_candle(storage, opened, now)
        except mysql.connector.Error:
            logger.exception(
                "Błąd bazy dla świecy %s; ponawianie bez zmiany stanu.",
                opened.isoformat(),
            )
            time.sleep(config.POLL_INTERVAL_SECONDS)
            continue
        if "missing_candle" in result.get("blocked_by", []):
            if now < deadline:
                time.sleep(config.POLL_INTERVAL_SECONDS)
                continue
            logger.warning(
                "Pominięto minutę %s: świeca nie dotarła w ciągu %s sekund.",
                opened.isoformat(),
                config.WAIT_FOR_CANDLE_SECONDS,
            )
            save_checkpoint(checkpoint_path, opened)
            checkpoint = opened
            continue
        save_checkpoint(checkpoint_path, opened)
        checkpoint = opened
        if "already_processed" in result.get("blocked_by", []):
            continue
        if saved and result["signal"] in {"LONG", "SHORT"} and config.BEEP_ENABLED:
            play_signal_confirmation()
        _emit(result)


def main() -> int:
    """Uruchamia analizę jednorazową, pętlę lub wskazaną minutę dla integracji."""
    parser = argparse.ArgumentParser(
        description="Strategia mikrotransakcji dla zamkniętych świec BTC/USD."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--loop", action="store_true", help="Śledź nowe zamknięte świece.")
    group.add_argument(
        "--api-candle-open-epoch",
        type=int,
        help="Oceń otwarcie wskazanej świecy UTC w sekundach.",
    )
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    try:
        storage = BTCUSDStorage()
        logger.info("Uruchomiono strategię Claude-sonnet55 BTC/USD.")
        if arguments.loop:
            run_loop(storage, config.STATE_FILE)
            return 0
        if arguments.api_candle_open_epoch is not None:
            opened = datetime.fromtimestamp(
                arguments.api_candle_open_epoch, timezone.utc
            )
            result, saved = analyze_candle(storage, opened)
            _emit(result)
            return 0 if saved else 3
        result, saved = run_latest(storage)
        if saved and result["signal"] in {"LONG", "SHORT"} and config.BEEP_ENABLED:
            play_signal_confirmation()
        _emit(result)
        return 0
    except KeyboardInterrupt:
        logger.info("Przerwano działanie strategii.")
        return 0
    except (OSError, ValueError, RuntimeError, mysql.connector.Error):
        logger.exception("Nie udało się uruchomić strategii BTC/USD.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
