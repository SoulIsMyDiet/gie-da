"""
Front
Sygnały dźwiękowe dla algorytmu.
"""

import os
import platform
import logging
import sys

logger = logging.getLogger(__name__)


def play_signal_confirmation() -> None:
    """
    Front
    Emituje sygnał dźwiękowy potwierdzenia zapisu sygnału (2 piknięcia).
    """
    system = platform.system()

    try:
        if system == "Windows":
            import winsound

            # 2 krótkie piknięcia: 1000 Hz, 120 ms, przerwa 100 ms
            winsound.Beep(1000, 120)
            os.system("timeout /t 0.1 /nobreak >nul 2>&1")
            winsound.Beep(1000, 120)
        elif system in ("Linux", "Darwin"):
            # Linux/macOS: użyj beep lub printf
            os.system('printf "\\a" && sleep 0.12 && printf "\\a"')
        else:
            logger.warning(f"System {system} nie wspierany dla sygnału dźwiękowego.")
    except Exception as error:
        logger.warning(f"Błąd przy emisji sygnału dźwiękowego: {error}")


def play_error_alert() -> None:
    """
    Front
    Emituje sygnał błędu (4 piknięcia w wyższej częstotliwości).
    """
    system = platform.system()

    try:
        if system == "Windows":
            import winsound

            # 4 piknięcia: 1500 Hz, 100 ms
            for _ in range(4):
                winsound.Beep(1500, 100)
                os.system("timeout /t 0.05 /nobreak >nul 2>&1")
        elif system in ("Linux", "Darwin"):
            os.system('printf "\\a\\a\\a\\a"')
        else:
            logger.warning(f"System {system} nie wspierany dla sygnału błędu.")
    except Exception as error:
        logger.warning(f"Błąd przy emisji sygnału błędu: {error}")
