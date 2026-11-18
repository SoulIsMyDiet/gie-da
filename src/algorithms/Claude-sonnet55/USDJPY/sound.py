"""Odtwarzanie krótkiego sygnału dźwiękowego po zapisie wyniku."""

import logging
import sys
import time

from config import (
    BEEP_DURATION_MS,
    BEEP_ENABLED,
    BEEP_FREQUENCY_HZ,
    BEEP_PAUSE_SECONDS,
)

logger = logging.getLogger(__name__)


def play_signal_beep() -> bool:
    """Odtwarza dwa piknięcia; zwraca False i loguje błąd, gdy dźwięk zawiedzie."""
    if not BEEP_ENABLED:
        return False
    try:
        if sys.platform == "win32":
            import winsound

            winsound.Beep(BEEP_FREQUENCY_HZ, BEEP_DURATION_MS)
            time.sleep(BEEP_PAUSE_SECONDS)
            winsound.Beep(BEEP_FREQUENCY_HZ, BEEP_DURATION_MS)
        else:
            sys.stderr.write("\a")
            sys.stderr.flush()
            time.sleep(BEEP_PAUSE_SECONDS)
            sys.stderr.write("\a")
            sys.stderr.flush()
        return True
    except (OSError, RuntimeError) as error:
        logger.exception("Nie udało się odtworzyć dźwięku sygnału: %s", error)
        return False
