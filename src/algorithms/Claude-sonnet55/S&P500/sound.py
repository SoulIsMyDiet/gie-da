"""Front: lokalne powiadomienie dźwiękowe dla ręcznego uruchomienia."""

import logging
import sys
import time

import config


def play_signal_confirmation() -> None:
    """Odtwarza dwa krótkie dźwięki; błąd urządzenia nie cofa zapisu w bazie."""
    try:
        if sys.platform == "win32":
            import winsound

            winsound.Beep(config.BEEP_FREQUENCY_HZ, config.BEEP_DURATION_MS)
            time.sleep(config.BEEP_PAUSE_MS / 1000)
            winsound.Beep(config.BEEP_FREQUENCY_HZ, config.BEEP_DURATION_MS)
        else:
            sys.stderr.write("\a")
            sys.stderr.flush()
            time.sleep(config.BEEP_PAUSE_MS / 1000)
            sys.stderr.write("\a")
            sys.stderr.flush()
    except (ImportError, RuntimeError, OSError, ValueError) as error:
        logging.getLogger(__name__).warning("Nie udało się odtworzyć dźwięku: %s", error)
