"""Odtwarza krótkie dźwięki dla powiadomień i błędów API."""

import logging
import platform
import sys
import time

LOGGER = logging.getLogger(__name__)


def play_alert(number_of_beeps: int = 3, frequency: int = 1000, duration_ms: int = 200) -> None:
    """
    Odtwarza wskazaną liczbę piknięć dla powiadomienia lub błędu API.
    
    Parametry:
    - number_of_beeps: liczba piknięć (domyślnie 3)
    - frequency: częstotliwość dźwięku w Hz (domyślnie 1000)
    - duration_ms: czas trwania każdego piknięcia w milisekundach (domyślnie 200)
    """
    system = platform.system()
    
    try:
        if system == "Windows":
            import winsound
            for _ in range(number_of_beeps):
                winsound.Beep(frequency, duration_ms)
        elif system in ("Linux", "Darwin"):
            for _ in range(number_of_beeps):
                sys.stdout.write("\\a")
                sys.stdout.flush()
                time.sleep(duration_ms / 1000)
        else:
            LOGGER.warning(
                "System %s nie jest obsługiwany przy odtwarzaniu dźwięku.",
                system,
            )
    except Exception:
        LOGGER.exception("Błąd przy emisji sygnału dźwiękowego.")


def play_connection_error_alert() -> None:
    """
    Emituje specjalny sygnał dźwiękowy przy błędzie połączenia.
    Składa się z 4 piknięć w wyższej częstotliwości.
    """
    system = platform.system()
    
    try:
        if system == "Windows":
            import winsound
            for _ in range(4):
                winsound.Beep(1500, 150)
        elif system in ("Linux", "Darwin"):
            os.system(f'printf "\\a\\a\\a\\a"')
        else:
            LOGGER.warning(
                "System %s nie jest obsługiwany przy dźwiękowym sygnale błędu.",
                system,
            )
    except Exception:
        LOGGER.exception("Błąd przy emisji dźwiękowego sygnału błędu.")
