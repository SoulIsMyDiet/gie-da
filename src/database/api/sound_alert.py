"""
Backend
Moduł do generowania sygnałów dźwiękowych przy wykryciu luk w świecach.
"""

import os
import platform
import sys
from pathlib import Path


def play_gap_alert(number_of_beeps: int = 3, frequency: int = 1000, duration_ms: int = 200) -> None:
    """
    Emituje sygnał dźwiękowy (pikniecia) przy wykryciu luki w świecach.
    
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
            # Na Linuxie i macOS użyj komendy beep lub echo z \x07
            os.system(f'printf "\\a" && sleep 0.2')
        else:
            print(f"Ostrzeżenie: System {system} nie wspierany dla sygnału dźwiękowego.", file=sys.stderr)
    except Exception as error:
        print(f"Błąd przy emisji sygnału dźwiękowego: {error}", file=sys.stderr)


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
            print(f"Ostrzeżenie: System {system} nie wspierany dla sygnału dźwiękowego.", file=sys.stderr)
    except Exception as error:
        print(f"Błąd przy emisji sygnału błędu: {error}", file=sys.stderr)
