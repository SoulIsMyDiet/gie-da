"""Backend: jednorazowa analiza wskazanej, zamkniętej świecy ES."""

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time

import pandas as pd
from mysql.connector import Error as MySQLError

# Backend: harmonogram jest współdzielony z API, również w obrazie kontenera.
api_directory = Path(__file__).resolve().parent.parent
if not (api_directory / "market_products.py").is_file():
    api_directory = Path(__file__).resolve().parents[3] / "database" / "api"
sys.path.insert(0, str(api_directory))
from market_products import is_market_open

import config
from signal_logic import generate_signal
from sound import play_signal_confirmation
from storage import InvalidSignalPrice, SP500Storage, validated_price

logger = logging.getLogger(__name__)

# Backend
POLL_SECONDS = 5
LOOKBACK_MINUTES = 15


class ProcessedCandles:
    """Przechowuje lokalne znaczniki świec; ścieżka path wskazuje plik SQLite.

    Błędy dostępu i SQLite są przekazywane wywołującemu. Znaczniki nie tworzą
    transakcji z MySQL i nie stanowią blokady między replikami kontenera.
    """

    def __init__(self, path: str):
        """Otwiera plik path i tworzy w nim tabelę znaczników."""
        self.connection = sqlite3.connect(path)
        self.connection.execute("CREATE TABLE IF NOT EXISTS processed (epoch INTEGER PRIMARY KEY)")
        self.connection.commit()

    def known(self, since: datetime) -> set[int]:
        """Zwraca zbiór znaczników od czasu since; błędy SQLite są propagowane."""
        rows = self.connection.execute(
            "SELECT epoch FROM processed WHERE epoch >= ?", (int(since.timestamp()),)
        )
        return {row[0] for row in rows}

    def mark(self, opened: datetime) -> None:
        """Trwale oznacza świecę opened; błędy SQLite są propagowane."""
        self.connection.execute("INSERT OR IGNORE INTO processed VALUES (?)", (int(opened.timestamp()),))
        self.connection.commit()

    def prune(self, before: datetime) -> None:
        """Usuwa znaczniki starsze niż before; błędy SQLite są propagowane."""
        self.connection.execute("DELETE FROM processed WHERE epoch < ?", (int(before.timestamp()),))
        self.connection.commit()

    def close(self) -> None:
        """Zamyka połączenie SQLite; błędy SQLite są propagowane."""
        self.connection.close()


class StrategyLoop:
    """Sprawdza bazę przez storage i zapisuje znaczniki w state.

    Aktywny sygnał wymaga zamknięcia świecy po uruchomieniu procesu.
    Odczyt i zapis mogą zgłaszać wyjątki, które obsługuje pętla nadrzędna.
    """

    def __init__(self, storage: SP500Storage, state: ProcessedCandles, clock=None):
        """Zapamiętuje storage, state i opcjonalny zegar clock."""
        self.storage = storage
        self.state = state
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.started_at = _aware_utc(self.clock())

    def poll(self, now: datetime) -> int:
        """Przetwarza zamknięte świece i zwraca liczbę potwierdzeń dźwiękowych.

        Błędy odczytu lub zapisu są propagowane, a znacznik powstaje dopiero po
        poprawnym przetworzeniu. Świece sprzed startu nie dają aktywnego sygnału.
        """
        now = _aware_utc(now)
        if not any(
            is_market_open(config.DB_SYMBOL, now - timedelta(minutes=minute))
            for minute in range(LOOKBACK_MINUTES + 2)
        ):
            return 0
        candles = self.storage.recent_closed_candles(now, LOOKBACK_MINUTES)
        known = self.state.known(now - timedelta(minutes=LOOKBACK_MINUTES + 2))
        confirmations = 0
        invalid_prices = 0
        for opened in candles:
            epoch = int(opened.timestamp())
            if epoch in known:
                continue
            allow_active = opened + timedelta(minutes=1) > self.started_at
            try:
                result, saved = analyze_candle(
                    self.storage, opened, self.clock(), allow_active=allow_active,
                    notify_client=True,
                )
            except InvalidSignalPrice:
                invalid_prices += 1
                continue
            if result["signal_parameters"].get("reason") in ("missing_candle", "forming_candle"):
                continue
            self.state.mark(opened)
            known.add(epoch)
            if (saved and allow_active and config.BEEP_ENABLED
                    and self.clock() - (opened + timedelta(minutes=1))
                    <= timedelta(minutes=config.MAX_STALE_MINUTES)):
                confirmations += 1
        self.state.prune(now - timedelta(hours=2))
        if invalid_prices:
            raise InvalidSignalPrice(f"Nie przetworzono {invalid_prices} świec bez prawidłowej ceny.")
        return confirmations


def _aware_utc(value: datetime) -> datetime:
    """Zwraca value w UTC; ValueError oznacza brak świadomej strefy czasowej."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Czas musi zawierać strefę czasową.")
    return value.astimezone(timezone.utc)


def run_loop() -> int:
    """Uruchamia ciągłą pętlę MySQL; zwraca kod 1 po błędzie pliku stanu.

    Przejściowe błędy bazy są logowane i ponawiane po przerwie. Powiadomienia
    odczytuje klient przez API, bez odtwarzania dźwięku w kontenerze.
    """
    path = os.environ.get("GROK_SP500_STATE_PATH", str(Path.home() / "grok_sp500_state.sqlite3"))
    try:
        state = ProcessedCandles(path)
    except (OSError, sqlite3.Error):
        logger.exception("Nie można otworzyć pliku stanu Grok S&P500.")
        return 1
    try:
        loop = StrategyLoop(SP500Storage(), state)
        while True:
            try:
                loop.poll(datetime.now(timezone.utc))
            except (MySQLError, InvalidSignalPrice):
                logger.exception("Błąd pętli Grok S&P500; ponawiam po przerwie.")
            except sqlite3.Error:
                logger.exception("Błąd pliku stanu Grok S&P500; uruchomienie wymaga ponowienia.")
                return 1
            time.sleep(POLL_SECONDS)
    finally:
        state.close()


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


def _neutralize_restart(result: dict) -> dict:
    """Zwraca kopię result bez aktywnego sygnału dla świecy sprzed startu pętli.

    Błędna struktura parametrów zgłasza wyjątek; oryginalny wynik nie jest zmieniany.
    """
    safe_result = deepcopy(result)
    safe_result["signal"] = "NONE"
    parameters = safe_result["signal_parameters"]
    parameters["reason"] = "Świeca zamknęła się przed uruchomieniem pętli."
    parameters["reasons"] = list(parameters.get("reasons", []))
    if "before_loop_start" not in parameters["reasons"]:
        parameters["reasons"].append("before_loop_start")
    return safe_result


def analyze_candle(storage: SP500Storage, opened: datetime, now: datetime | None = None,
                   allow_active: bool = True, notify_client: bool = False) -> tuple[dict, bool]:
    """Analizuje jedną świecę opened i zwraca parę (wynik, nowy_zapis).

    now to opcjonalny świadomy czas analizy, allow_active wyłącza sygnał aktywny,
    a notify_client zleca powiadomienie aplikacji zamiast lokalnego dźwięku.
    Formowana lub brakująca świeca nie
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
        if stale:
            return _neutralize_stale(previous, opened, now), False
        return (_neutralize_restart(previous) if not allow_active else previous), False

    candles = storage.load_context(opened)
    if candles.empty or candles.iloc[-1]["timestamp_utc"] != opened:
        return _blocked_result(opened, now, "missing_candle"), False
    result = generate_signal(candles, now=now)
    validated_price(result)
    if result.get("candle_open_epoch") != int(opened.timestamp()):
        raise ValueError("Wynik nie dotyczy wskazanej świecy.")
    if stale:
        result = _neutralize_stale(result, opened, now)
    elif not allow_active:
        result = _neutralize_restart(result)
    audio_notification = bool(notify_client and config.BEEP_ENABLED and allow_active and not stale)
    return result, storage.save_signal(result, len(candles), audio_notification=audio_notification)


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
    group.add_argument("--loop", action="store_true", help="Ciągłe sprawdzanie bazy dla API.")
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    try:
        args = parser.parse_args(argv)
        if args.loop:
            return run_loop()
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
