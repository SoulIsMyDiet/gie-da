"""Backend: jednorazowa analiza wskazanej, zamkniętej świecy ES."""

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import logging
import sys

import pandas as pd

import config
from signal_logic import generate_signal
from sound import play_signal_confirmation
from storage import SP500Storage

logger = logging.getLogger(__name__)


def _blocked_result(opened: datetime, now: datetime, reason: str) -> dict:
    """Zwraca JSON NONE dla świecy opened i przyczyny reason w czasie now.

    Czasy muszą być świadome; wyjątki generowania sygnału są przekazywane dalej.
    """
    result = generate_signal(pd.DataFrame(), now=now)
    result.update(
        candle_open_time=opened.astimezone(config.WARSAW).isoformat(),
        candle_open_epoch=int(opened.timestamp()),
        timestamp=(opened + timedelta(minutes=1)).astimezone(config.WARSAW).isoformat(),
        signal="NONE",
    )
    result["signal_parameters"]["reason"] = reason
    result["signal_parameters"]["reasons"] = [reason]
    return result


def _neutralize_stale(result: dict, opened: datetime, now: datetime) -> dict:
    """Zwraca nieaktywną kopię result dla przestarzałej świecy opened w czasie now.

    Nie zmienia zapisanego wyniku; błędna struktura parametrów zgłasza wyjątek.
    """
    safe_result = deepcopy(result)
    safe_result["signal"] = "NONE"
    parameters = safe_result["signal_parameters"]
    parameters["reason"] = "Świeca jest nieaktualna."
    parameters["reasons"] = list(parameters.get("reasons", []))
    if "stale_data" not in parameters["reasons"]:
        parameters["reasons"].append("stale_data")
    parameters["data_age_minutes"] = (now - (opened + timedelta(minutes=1))).total_seconds() / 60
    return safe_result


def analyze_candle(storage: SP500Storage, opened: datetime, now: datetime | None = None) -> tuple[dict, bool]:
    """Analizuje jedną świecę opened i zwraca parę (wynik, nowy_zapis).

    now to opcjonalny świadomy czas analizy. Formowana lub brakująca świeca nie
    jest zapisywana; duplikat nie jest ponownie obliczany. ValueError zgłasza
    błędne czasy, a błędy danych i bazy są przekazywane dalej.
    """
    if not isinstance(opened, datetime) or opened.tzinfo is None or opened.utcoffset() is None:
        raise ValueError("Otwarcie musi mieć strefę czasową.")
    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Czas analizy musi mieć strefę czasową.")
    opened, now = opened.astimezone(timezone.utc), now.astimezone(timezone.utc)
    if opened.second or opened.microsecond:
        raise ValueError("Otwarcie świecy M1 musi przypadać na pełną minutę.")
    if opened + timedelta(minutes=1) > now:
        return _blocked_result(opened, now, "forming_candle"), False

    stale = now - (opened + timedelta(minutes=1)) > timedelta(minutes=config.MAX_STALE_MINUTES)
    previous = storage.get_processed_signal(opened)
    if previous is not None:
        return (_neutralize_stale(previous, opened, now) if stale else previous), False

    candles = storage.load_context(opened)
    if candles.empty or candles.iloc[-1]["timestamp_utc"] != opened:
        return _blocked_result(opened, now, "missing_candle"), False
    result = generate_signal(candles, now=now)
    if result.get("candle_open_epoch") != int(opened.timestamp()):
        raise ValueError("Wynik nie dotyczy wskazanej świecy.")
    if stale:
        result = _neutralize_stale(result, opened, now)
    return result, storage.save_signal(result, len(candles))


class _ArgumentParser(argparse.ArgumentParser):
    """Parser CLI zamieniający błędy argumentów na wyjątki obsługiwane przez main."""

    def error(self, message: str) -> None:
        """Zgłasza ValueError z komunikatem message zamiast kończyć proces kodem 2."""
        raise ValueError(message)


def main(argv: list[str] | None = None) -> int:
    """Obsługuje argumenty argv i wypisuje jeden JSON; zwraca kod 0, 1, 3 albo 4.

    Kod 0 oznacza nowy zapis i włączony dźwięk, 4 zapis bez dźwięku, 3 brak
    zapisu, a 1 błąd. W trybie API dźwięk odtwarza aplikacja, nie proces.
    Błędy są jawnie logowane do stderr i nie są przedstawiane jako sukces.
    """
    parser = _ArgumentParser(description="Analiza wskazanej zamkniętej świecy ES.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--api-candle-open-epoch", type=int, help="Otwarcie świecy UTC w sekundach, tryb API.")
    group.add_argument("--candle-open-epoch", type=int, help="Otwarcie świecy UTC w sekundach, tryb lokalny.")
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    try:
        args = parser.parse_args(argv)
        api_mode = args.api_candle_open_epoch is not None
        epoch = args.api_candle_open_epoch if api_mode else args.candle_open_epoch
        opened = datetime.fromtimestamp(epoch, timezone.utc)
        result, saved = analyze_candle(SP500Storage(), opened)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")), flush=True)
        if not saved:
            return 3
        if config.BEEP_ENABLED and not api_mode:
            play_signal_confirmation()
        return 0 if config.BEEP_ENABLED else 4
    except Exception:
        logger.exception("Nie udało się przeanalizować świecy ES.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
