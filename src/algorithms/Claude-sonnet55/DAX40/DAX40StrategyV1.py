#!/usr/bin/env python3
"""Backend
Główna klasa strategii mikrotransakcji dla indeksu GER30/DAX40.
"""

import json
import logging
import argparse
import time
from datetime import datetime, timedelta, timezone
from typing import Any

if __package__:
    from .config import BEEP_ENABLED, LOOP_POLL_SECONDS
    from .signal_logic import generate_signal
    from .sound import play_signal_confirmation
    from .storage import DAX40Storage
else:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from DAX40.config import BEEP_ENABLED, LOOP_POLL_SECONDS
    from DAX40.signal_logic import generate_signal
    from DAX40.sound import play_signal_confirmation
    from DAX40.storage import DAX40Storage

logger = logging.getLogger(__name__)
# pisze program w c++ słuzący do zapisywania momentów (sygnałow) swiadczacych o tym że wlansie jest dobry moment na dokonanie mikrotransakcji. Przez mikrotranskacje rozumiem wykonia operacji typu long/short i sfinalizowanie tej transkacji w ciagu max 20 minut.

# do tego celu sciagam z api giełdy swiece 1 minutowe co minute i zapisuje je w bazie. Nie mam tam niestety wglądu do aktualnego spreada(ani bid ani ask) wiec muszą mi wystarczyc pozostale paramtery.

# Wymysl prosze optymalny algorytm do znadowanie takich sygnałów ze teraz jeste czas na dokonanie transkacji i sam algorytm ma być z kolei napisany w pythonie.

# Chciałbym aby ten algorytm działał dobzre na instrumencie DAX 40 (GER30) i chcilabym aby ten algorytm byl dedykowany dla tego instrumentu

# Algorytm ten ma zwracać jsona z parametrami zawieracjaego nazwe instrumentu(w tym prypadku DAX40), aktualna cene instrumentu(tu pewnie bedzie to cena zamkniecia ostatniej swiecy), aktualny czas(timestamp), rodzaj transakcji (u mnie nazwa tego parametru to "signal" o wartosci 'LONG' albo 'SHORT' [lub 'NONE' - ale tylko dla testów w sumie])  oraz te paramtry na podstawie których algorytm wyliczy czy to dobry moment na transakcje krótkoterminową

# Każdy sygnał ma zostać zapisany do bazy danych do tablicy signals. Jesli copilot nie wie jak to niech dopyta o sczegóły.

# Przy kazdym zapisie progam ma wydac sygnał dzwiekowy składajacy sie z 2 pyknięć.

# Główny plik wykonujacy algorytm ma sie nazywac DAX40StrategyV1 a wszytkie pliki zwiazane z tym algorytmem maja sie znalesc w folderze src/alghorithms/Claude-sonnet55/DAX40

# prosze aby tymczasowo algorytm zapisywal do tabeli signals równiez te sygnały o statusie "NONE" (poza 'SHORT' i 'LONG') abym zobaczyl czy algorytm w ogole działa. jednak w taki sposob by mozna bylo szybko wycofac te zmiane bo docelowo bedziemy zapisywac do tabeli signals tylke te sygnaly o statusie 'SHORT' lub 'LONG'

# datetime w tabeli candles zawiera date otwarcia swiecy naszego czasu polskiego popros copilota o zapisanie wszytkich parametrów wskaznikow do signal_parameters.

# pojedyncza luka w swiecach nie ma przeszkadzac w stworzeniu ewentualnego sygnału. i pamietaj o przerwach w dostawaniu swiec który mozesz wywnioskowac z kodu. wiec te przerwy nie powinny byc traktowane jako luka

# Do napisania kodu uzywam copilota, ale Ciebie poprosze o przygotowanie instrukcji dla copilota aby taki algorytm napisał.

# przygotuj mi tez tresc audytu zeby sie upewniec czy na pewno dobrze zaimplmentowal algorytm

class DAX40StrategyV1:
    """Generuje i zapisuje ocenę każdej zamkniętej świecy M1 GER30."""

    def __init__(self, storage: DAX40Storage | None = None) -> None:
        """Tworzy strategię, opcjonalnie z przygotowanym repozytorium danych."""
        self.storage = storage or DAX40Storage()

    def evaluate(
        self,
        candles: list[dict[str, Any]],
        now: datetime | None = None,
        last_signal_time: datetime | None = None,
        signals_today: int = 0,
        orb_directions_today: list[str] | None = None,
    ) -> dict[str, Any]:
        """Oblicza wynik dla historii i stanu ostatnich sygnałów przekazanych przez bazę."""
        return generate_signal(
            candles,
            now,
            last_signal_time,
            signals_today,
            orb_directions_today,
        )

    def run_once(self) -> dict[str, Any]:
        """Pobiera kontekst z MySQL i oblicza wynik dla najnowszej świecy."""
        candles, state = self.storage.load_context()
        return self.evaluate(
            candles,
            last_signal_time=state["last_signal_time"],
            signals_today=state["signals_today"],
            orb_directions_today=state.get("orb_directions_today", []),
        )

    def run_and_save(self) -> tuple[dict[str, Any], bool]:
        """Oblicza i atomowo zapisuje wynik, zwracając informację o nowym wierszu."""
        candles, state = self.storage.load_context()
        result = self.evaluate(
            candles,
            last_signal_time=state["last_signal_time"],
            signals_today=state["signals_today"],
            orb_directions_today=state.get("orb_directions_today", []),
        )
        if result.get("candle_open_time") is None:
            return result, False
        saved = self.storage.save_signal(result, len(candles))
        if saved and BEEP_ENABLED:
            play_signal_confirmation()
        return result, saved

    @staticmethod
    def to_json(result: dict[str, Any]) -> str:
        """Serializuje odpowiedź z zakazem wartości NaN/Infinity."""
        return json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def main() -> None:
    """Uruchamia jednorazową analizę lub oczekuje w pętli na nową zamkniętą świecę."""
    parser = argparse.ArgumentParser(description="Strategia mikrotransakcji GER30.")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Sprawdzaj bazę i analizuj każdą nową zamkniętą świecę.",
    )
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    strategy = DAX40StrategyV1()
    if not arguments.loop:
        result, _ = strategy.run_and_save()
        print(strategy.to_json(result))
        return

    last_processed_time = None
    while True:
        candles, state = strategy.storage.load_context()
        latest_time = candles[-1]["timestamp_utc"] if candles else None
        candle_is_closed = (
            latest_time is not None
            and latest_time + timedelta(minutes=1) <= datetime.now(timezone.utc)
        )
        if candle_is_closed and latest_time != last_processed_time:
            result = strategy.evaluate(
                candles,
                last_signal_time=state["last_signal_time"],
                signals_today=state["signals_today"],
                orb_directions_today=state.get("orb_directions_today", []),
            )
            if result.get("candle_open_time") is not None:
                saved = strategy.storage.save_signal(result, len(candles))
                if saved and BEEP_ENABLED:
                    play_signal_confirmation()
            print(strategy.to_json(result))
            last_processed_time = latest_time
        time.sleep(LOOP_POLL_SECONDS)


if __name__ == "__main__":
    main()
