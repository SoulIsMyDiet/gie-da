"""Backend: jednokrotne wyzwolenie analizy wskazanej świecy XAUUSD."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import sys

from . import config
from .signal_logic import generate_signal
from .storage import XAUUSDStorage, utc_time

logger = logging.getLogger(__name__)


def analyze_candle(storage: XAUUSDStorage, opened: datetime,
                   now: datetime | None = None) -> tuple[dict, bool]:
    """Zwraca wynik i informację o nowym zapisie dla zamkniętej świecy opened.

    now jest opcjonalnym świadomym czasem. ValueError oznacza brak świecy,
    nieprawidłowy czas albo niezgodność wyniku; błędy bazy są propagowane.
    Sprawdzenie przed obliczeniem ogranicza powtórzenia, lecz równoległe procesy
    mogą obliczyć wynik jednocześnie; unikalny klucz gwarantuje jeden zapis.
    Przy SAVE_NONE_SIGNALS=False wynik NONE może być ponownie obliczony, a po
    awarii między zapisem świecy i wyniku potrzebne jest ponowne wywołanie.
    """
    opened = utc_time(opened)
    now = utc_time(now if now is not None else datetime.now(timezone.utc))
    if opened.second or opened.microsecond:
        raise ValueError("Otwarcie świecy M1 musi przypadać na pełną minutę.")
    if opened + timedelta(minutes=1) > now:
        raise ValueError("Świeca nie jest jeszcze zamknięta.")
    previous = storage.get_processed_signal(opened)
    if previous is not None:
        return previous, False
    candles = storage.load_context(opened)
    if not candles or utc_time(candles[-1]["timestamp_utc"]) != opened:
        raise ValueError("Nie znaleziono wskazanej świecy w historii.")
    if any(utc_time(candle["timestamp_utc"]) > opened for candle in candles):
        raise ValueError("Historia zawiera świece późniejsze niż analizowana.")
    result = generate_signal(candles, now=now)
    if (isinstance(result.get("candle_open_epoch"), bool)
            or result.get("candle_open_epoch") != int(opened.timestamp())):
        raise ValueError("Wynik nie dotyczy wskazanej świecy.")
    return result, storage.save_signal(result, len(candles))


class _ArgumentParser(argparse.ArgumentParser):
    """Zamienia nieprawidłowe argumenty na ValueError obsługiwany przez main."""

    def error(self, message: str) -> None:
        """Zgłasza ValueError opisujący błąd argumentu message."""
        raise ValueError(message)


def main(argv: list[str] | None = None) -> int:
    """Analizuje otwarcie z argv; zwraca 0/4 dla zapisu, 3 dla braku zapisu, 1 dla błędu.

    Tryb API nie odtwarza dźwięku: klient interpretuje kod 0. Tryb lokalny
    nie odtwarza dźwięku, gdyż brak współdzielonego bezpiecznego mechanizmu.
    """
    parser = _ArgumentParser(description="Analiza wskazanej zamkniętej świecy XAUUSD.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--api-candle-open-epoch", type=int)
    group.add_argument("--candle-open-epoch", type=int)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    try:
        args = parser.parse_args(argv)
        api_mode = args.api_candle_open_epoch is not None
        epoch = args.api_candle_open_epoch if api_mode else args.candle_open_epoch
        opened = datetime.fromtimestamp(epoch, timezone.utc)
        result, saved = analyze_candle(XAUUSDStorage(), opened)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")), flush=True)
        if not saved:
            return 3
        return 0 if api_mode and config.BEEP_ENABLED else 4
    except Exception:
        logger.exception("Nie udało się przeanalizować świecy XAUUSD.")
        return 1
