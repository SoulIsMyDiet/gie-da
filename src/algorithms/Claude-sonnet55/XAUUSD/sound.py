"""Dźwięk po udanym zapisie sygnału."""

import logging
import sys
import time

from config import BEEP_ENABLED

logger = logging.getLogger(__name__)


def play_signal_beep() -> bool:
    """Odtwarza dwa krótkie sygnały; błąd dźwięku nie przerywa strategii."""
    if not BEEP_ENABLED:
        return False
    try:
        if sys.platform == "win32":
            import winsound

            winsound.Beep(1000, 120)
            time.sleep(0.1)
            winsound.Beep(1000, 120)
        else:
            sys.stdout.write("\a")
            sys.stdout.flush()
            time.sleep(0.1)
            sys.stdout.write("\a")
            sys.stdout.flush()
        return True
    except Exception:
        logger.exception("Nie udało się odtworzyć dźwięku sygnału XAU/USD.")
        return False
