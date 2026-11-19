"""Punkt wejścia strategii mikrotransakcji USD/JPY."""

# Backend: krótki opis reguł strategii do wykorzystania przez przyszły walidator.
STRATEGY_CONTEXT = (
    "Instrument USD/JPY; decyzja na zamkniętych M1, z trendem/potwierdzeniem M5. "
    "LONG/SHORT: pullback do EMA21 zgodny z trendem M1/M5 i RSI, wybicie zakresu "
    "Tokio albo rewersja do wnętrza wstęg w kierunku VWAP przy niskim ER. "
    "NONE poza oknami lub gdy setup, dane albo filtry nie potwierdzają kierunku."
)

import logging
from pathlib import Path
import sys

SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)

from trigger import main as trigger_main  # noqa: E402
from trigger import run_once  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Uruchamia pojedynczą analizę bez argumentów albo obsługuje jawny tryb."""
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr)
        try:
            return run_once()
        except KeyboardInterrupt:
            return 0
        except Exception:
            logging.getLogger(__name__).exception(
                "Nie udało się wykonać jednorazowej analizy USD/JPY."
            )
            return 1
    return trigger_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
