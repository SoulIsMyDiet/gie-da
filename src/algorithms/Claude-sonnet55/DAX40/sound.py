"""Odtwarza potwierdzenie dźwiękowe bez przerywania działania strategii."""

import logging
import sys
import time

from .config import BEEP_ENABLED

logger = logging.getLogger(__name__)


def play_signal_confirmation() -> bool:
    """Odtwarza dwa piknięcia 1000 Hz; na innych systemach używa dzwonka terminala."""
    if not BEEP_ENABLED:
        return False
    try:
        import winsound
    except ImportError:
        print("\a", end="", file=sys.stderr, flush=True)
        time.sleep(0.1)
        print("\a", end="", file=sys.stderr, flush=True)
        return True
    try:
        winsound.Beep(1000, 120)
        time.sleep(0.1)
        winsound.Beep(1000, 120)
    except (OSError, RuntimeError) as error:
        logger.error("Nie udało się odtworzyć sygnału dźwiękowego: %s", error)
        return False
    return True
