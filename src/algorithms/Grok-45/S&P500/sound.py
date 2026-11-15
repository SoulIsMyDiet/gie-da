"""Backend: lokalne potwierdzenie dźwiękowe po zapisie sygnału."""

import logging
import sys
import time

import config

logger = logging.getLogger(__name__)


def play_signal_confirmation() -> None:
    """Odtwarza dwa dźwięki z ustawień config; nic nie zwraca.

    Błędy odtwarzania są logowane, ponieważ zapis został już zatwierdzony i
    jego potwierdzenie nie może zostać uznane za nieudane z powodu głośnika.
    """
    try:
        if sys.platform == "win32":
            import winsound
            winsound.Beep(config.BEEP_FREQUENCY_HZ, config.BEEP_DURATION_MS)
            time.sleep(config.BEEP_PAUSE_MS / 1000)
            winsound.Beep(config.BEEP_FREQUENCY_HZ, config.BEEP_DURATION_MS)
        else:
            print("\a", end="", file=sys.stderr, flush=True)
            time.sleep(config.BEEP_PAUSE_MS / 1000)
            print("\a", end="", file=sys.stderr, flush=True)
    except Exception:
        logger.exception("Nie udało się odtworzyć potwierdzenia dźwiękowego.")
