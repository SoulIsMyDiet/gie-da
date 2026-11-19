"""Punkt wejścia strategii XAU/USD z importami płaskimi."""

# Backend: krótki opis reguł strategii do wykorzystania przez przyszły walidator.
STRATEGY_CONTEXT = (
    "Instrument XAU/USD; decyzja na zamkniętych M1. LONG/SHORT: wybicie zakresu "
    "Azji, pullback zgodny z trendem M1/M5 (ER >= 0.30) albo rewersja od skrajnego "
    "pasma Bollingera przy niskim ER, RSI i powrocie ku VWAP. Sygnał wymaga "
    "minimalnego wyniku i aktywnego okna; w pozostałych przypadkach NONE."
)

from pathlib import Path
import sys

directory = str(Path(__file__).resolve().parent)
if directory not in sys.path:
    sys.path.insert(0, directory)
api_directory = Path(directory).parent
if not (api_directory / "market_products.py").is_file():
    api_directory = Path(directory).parents[2] / "database" / "api"
if str(api_directory) not in sys.path:
    sys.path.insert(0, str(api_directory))

from trigger import main


if __name__ == "__main__":
    raise SystemExit(main())
