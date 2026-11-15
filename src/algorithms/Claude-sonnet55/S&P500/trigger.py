"""Backend: wyzwalanie analizy po zapisie świecy lub w ręcznej pętli."""

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from schedule import as_utc, is_signal_window, is_trading_minute
from signal_logic import generate_signal
from sound import play_signal_confirmation
from storage import SP500Storage

logger = logging.getLogger(__name__)


def analyze_candle(
    storage: SP500Storage,
    opened: datetime,
    now: datetime | None = None,
    allow_outside: bool = False,
) -> tuple[dict, bool]:
    """Analizuje wskazaną zamkniętą świecę; błędy danych i bazy są przekazywane dalej."""
    opened = as_utc(opened)
    now = as_utc(now if now is not None else datetime.now(timezone.utc))
    if opened.second or opened.microsecond:
        raise ValueError("Otwarcie świecy M1 musi przypadać na pełną minutę.")
    if opened + timedelta(minutes=1) > now:
        logger.info("Świeca %s nie jest jeszcze zamknięta.", opened.isoformat())
        return {"signal": "NONE", "blocked_by": ["forming_candle"]}, False
    in_session = is_trading_minute(opened) and is_signal_window(opened + timedelta(minutes=1))
    if not in_session and not allow_outside:
        return {"signal": "NONE", "blocked_by": ["outside_session"]}, False
    previous = storage.get_processed_signal(opened)
    if previous is not None:
        logger.debug("Świeca %s była już przeanalizowana.", opened.isoformat())
        return previous, False
    candles, state = storage.load_context(opened)
    if not candles or as_utc(candles[-1]["timestamp_utc"]) != opened:
        logger.debug("Oczekiwanie na świecę %s w bazie.", opened.isoformat())
        return {"signal": "NONE", "blocked_by": ["missing_candle"]}, False
    result = generate_signal(candles, now=now, **state)
    if not in_session:
        return result, False
    return result, storage.save_signal(result, len(candles))


def latest_closed_open(now: datetime) -> datetime:
    """Zwraca otwarcie ostatniej zamkniętej minuty dla czasu now."""
    return as_utc(now).replace(second=0, microsecond=0) - timedelta(minutes=1)


def run_latest(storage: SP500Storage, now: datetime | None = None) -> tuple[dict, bool]:
    """Ocenia najnowszą zamkniętą świecę w bazie, także gdy jest opóźniona."""
    now = as_utc(now if now is not None else datetime.now(timezone.utc))
    opened = storage.latest_closed_open(now)
    if opened is None:
        logger.warning("Brak zamkniętych świec ES w bazie.")
        return generate_signal([], now=now), False
    return analyze_candle(storage, opened, now, allow_outside=True)


def load_checkpoint(path: Path) -> datetime | None:
    """Odczytuje ostatnią obsłużoną minutę UTC; uszkodzony plik zgłasza błąd."""
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as file:
        payload = json.load(file)
    stamp = datetime.fromisoformat(payload["last_handled_open"])
    stamp = as_utc(stamp)
    if stamp.second or stamp.microsecond:
        raise ValueError("Nieprawidłowa minuta w pliku stanu.")
    return stamp


def save_checkpoint(path: Path, opened: datetime) -> None:
    """Atomowo zapisuje minutę UTC, aby restart nie powtarzał analiz i luk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as file:
            temporary_path = Path(file.name)
            json.dump({"last_handled_open": as_utc(opened).isoformat()}, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _emit(result: dict) -> None:
    """Wypisuje wynik strategii jako pojedynczy obiekt JSON."""
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")), flush=True)


def run_loop(storage: SP500Storage, checkpoint_path: Path) -> None:
    """Czeka na każdą minutę do wyznaczonego limitu, utrwalając stan po jej obsłudze."""
    checkpoint = load_checkpoint(checkpoint_path)
    stored = storage.latest_processed_open()
    if stored is not None and (checkpoint is None or stored > checkpoint):
        checkpoint = stored
    latest = latest_closed_open(datetime.now(timezone.utc))
    earliest_live_checkpoint = latest - timedelta(minutes=1)
    if checkpoint is not None and checkpoint < earliest_live_checkpoint:
        logger.warning(
            "Po restarcie pominięto przeterminowane minuty od %s do %s.",
            (checkpoint + timedelta(minutes=1)).isoformat(),
            earliest_live_checkpoint.isoformat(),
        )
        checkpoint = earliest_live_checkpoint
        save_checkpoint(checkpoint_path, checkpoint)
    elif checkpoint is None:
        checkpoint = earliest_live_checkpoint

    while True:
        now = datetime.now(timezone.utc)
        opened = checkpoint + timedelta(minutes=1)
        if opened > latest_closed_open(now):
            time.sleep(config.POLL_INTERVAL_SECONDS)
            continue
        closed = opened + timedelta(minutes=1)
        if not is_trading_minute(opened) or not is_signal_window(closed):
            checkpoint = opened
            save_checkpoint(checkpoint_path, checkpoint)
            continue
        deadline = closed + timedelta(seconds=config.WAIT_FOR_CANDLE_SECONDS)
        if now > deadline:
            logger.warning("Pominięto minutę %s: upłynął czas oczekiwania na świecę.", opened.isoformat())
            checkpoint = opened
            save_checkpoint(checkpoint_path, checkpoint)
            continue
        result, saved = analyze_candle(storage, opened, now)
        if "missing_candle" in result.get("blocked_by", []):
            time.sleep(config.POLL_INTERVAL_SECONDS)
            continue
        if saved and config.BEEP_ENABLED:
            play_signal_confirmation()
        _emit(result)
        checkpoint = opened
        save_checkpoint(checkpoint_path, checkpoint)


def main() -> int:
    """Uruchamia analizę, ręczną pętlę albo izolowany proces API."""
    parser = argparse.ArgumentParser(description="Strategia mikrotransakcji kontraktu ES.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--loop", action="store_true", help="Sprawdzaj nowe zamknięte minuty.")
    group.add_argument("--api-candle-open-epoch", type=int, help="Oceń otwarcie świecy UTC w sekundach.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    try:
        storage = SP500Storage()
        if args.api_candle_open_epoch is not None:
            opened = datetime.fromtimestamp(args.api_candle_open_epoch, timezone.utc)
            result, saved = analyze_candle(storage, opened)
            _emit(result)
            return (0 if config.BEEP_ENABLED else 4) if saved else 3
        if args.loop:
            run_loop(storage, Path(config.STATE_FILE))
            return 0
        result, saved = run_latest(storage)
        if saved and config.BEEP_ENABLED:
            play_signal_confirmation()
        _emit(result)
        return 0
    except KeyboardInterrupt:
        logger.info("Przerwano działanie strategii.")
        return 1 if args.api_candle_open_epoch is not None else 0
    except (OSError, ValueError, RuntimeError) as error:
        logger.exception("Błąd działania strategii: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
