"""Backend: wyzwalanie analizy po zapisie świecy lub w ręcznej pętli."""

import argparse
from copy import deepcopy
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from mysql.connector import Error as MySQLError
from schedule import as_utc, is_signal_window, is_trading_minute
from signal_logic import generate_signal
from sound import play_signal_confirmation
from storage import SP500Storage

logger = logging.getLogger(__name__)


def _stale_saved_result(previous: dict, opened: datetime, now: datetime) -> dict:
    """Tworzy nieaktywną kopię zapisanego JSON dla świecy opened i czasu analizy now.

    Zachowuje historyczną cenę, znaczniki czasu i parametry. Poza sesją usuwa
    blokadę luki, lecz zachowuje jej liczniki; nie zmienia zapisanego wyniku
    ani nie przechwytuje błędów danych.
    """
    result = _blocked_candle_result(opened, now, "stale_data")
    result.update(deepcopy(previous))
    result["params"]["data_age_minutes"] = (now - (opened + timedelta(minutes=1))).total_seconds() / 60
    if "stale_data" not in result["blocked_by"]:
        result["blocked_by"].append("stale_data")
    if not is_signal_window(now.replace(second=0, microsecond=0)) and "outside_session" not in result["blocked_by"]:
        result["blocked_by"].append("outside_session")
    if "outside_session" in result["blocked_by"]:
        result["blocked_by"] = [reason for reason in result["blocked_by"] if reason != "data_gap"]
    result.update(signal="NONE", setup=None, score=0, tp_price=None, sl_price=None,
                  tp_points=None, sl_points=None, reasons=[])
    return result


def _blocked_candle_result(opened: datetime, now: datetime, reason: str) -> dict:
    """Zwraca pełny wynik NONE dla świecy opened i przyczyny reason bez liczenia wskaźników.

    now to świadomy czas analizy. Czasy w wyniku opisują wskazaną świecę,
    a nie zastępcze dane; niepoprawny czas zgłasza ValueError.
    """
    result = generate_signal([], now=now)
    closed = opened + timedelta(minutes=1)
    result.update(
        candle_open_time=opened.astimezone(config.WARSAW).isoformat(),
        candle_open_epoch=int(opened.timestamp()),
        timestamp=closed.astimezone(config.WARSAW).isoformat(),
        timestamp_epoch=int(closed.timestamp()),
        blocked_by=[reason],
    )
    return result


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
        return _blocked_candle_result(opened, now, "forming_candle"), False
    in_session = is_trading_minute(opened) and is_signal_window(opened + timedelta(minutes=1))
    if not in_session and not allow_outside:
        return _blocked_candle_result(opened, now, "outside_session"), False
    current_minute = now.replace(second=0, microsecond=0)
    outside_now = allow_outside and not is_signal_window(current_minute)
    stale = now - (opened + timedelta(minutes=1)) > timedelta(minutes=config.MAX_STALE_MINUTES)
    previous = (storage.get_processed_signal(opened)
                if not outside_now or (stale and in_session) else None)
    if previous is not None:
        logger.debug("Świeca %s była już przeanalizowana.", opened.isoformat())
        if stale:
            return _stale_saved_result(previous, opened, now), False
        return previous, False
    candles, state = storage.load_context(opened)
    if not candles or as_utc(candles[-1]["timestamp_utc"]) != opened:
        logger.debug("Oczekiwanie na świecę %s w bazie.", opened.isoformat())
        return _blocked_candle_result(opened, now, "missing_candle"), False
    result = generate_signal(candles, now=now, **state)
    # Koniec okna obejmuje całą minutę, również przy opóźnionych danych.
    if (in_session and is_signal_window(current_minute) and not is_signal_window(now)
            and "stale_data" in result["blocked_by"]
            and "outside_session" in result["blocked_by"]):
        result["blocked_by"].remove("outside_session")
    if outside_now:
        if "outside_session" not in result["blocked_by"]:
            result["blocked_by"].append("outside_session")
        result["blocked_by"] = [reason for reason in result["blocked_by"] if reason != "data_gap"]
        result.update(signal="NONE", setup=None, score=0, tp_price=None, sl_price=None,
                      tp_points=None, sl_points=None)
    if previous is not None or not in_session or outside_now or "outside_session" in result.get("blocked_by", []):
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
        result = generate_signal([], now=now)
        if not is_signal_window(now.replace(second=0, microsecond=0)):
            result["blocked_by"].append("outside_session")
        return result, False
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
    """Obsługuje minuty po stanie z pliku; błędy MySQL ponawia bez zmiany stanu.

    Checkpoint ogranicza powtórki po restarcie, ale awaria po zapisie sygnału
    i przed zapisem pliku może spowodować ponowną próbę analizy.
    Bez checkpointu pętla zaczyna od ostatniej zamkniętej minuty; zapis w signals
    sprawdza indywidualnie. Niezapisane wyniki NONE nie mają wtedy pamięci.
    """
    checkpoint = load_checkpoint(checkpoint_path)
    latest = latest_closed_open(datetime.now(timezone.utc))
    if checkpoint is None:
        checkpoint = latest - timedelta(minutes=1)

    while True:
        now = datetime.now(timezone.utc)
        opened = checkpoint + timedelta(minutes=1)
        if opened > latest_closed_open(now):
            time.sleep(config.POLL_INTERVAL_SECONDS)
            continue
        closed = opened + timedelta(minutes=1)
        if not is_trading_minute(opened) or not is_signal_window(closed):
            checkpoint = opened
            continue
        deadline = closed + timedelta(seconds=config.WAIT_FOR_CANDLE_SECONDS)
        try:
            result, saved = analyze_candle(storage, opened, now)
        except MySQLError:
            logger.exception("Błąd bazy dla minuty %s; ponawianie odczytu.", opened.isoformat())
            time.sleep(config.POLL_INTERVAL_SECONDS)
            continue
        if "missing_candle" in result.get("blocked_by", []):
            if now < deadline:
                time.sleep(config.POLL_INTERVAL_SECONDS)
                continue
            logger.warning("Pominięto minutę %s: brak świecy po czasie oczekiwania.", opened.isoformat())
            save_checkpoint(checkpoint_path, opened)
            checkpoint = opened
            continue
        save_checkpoint(checkpoint_path, opened)
        checkpoint = opened
        if saved and config.BEEP_ENABLED:
            play_signal_confirmation()
        _emit(result)


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
    except (OSError, ValueError, RuntimeError, MySQLError) as error:
        logger.exception("Błąd działania strategii: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
