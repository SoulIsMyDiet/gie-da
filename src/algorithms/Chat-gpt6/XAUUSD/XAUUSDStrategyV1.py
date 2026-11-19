"""Backend: punkt wejścia procesu analizy świecy XAUUSD."""

# Backend: krótki opis reguł strategii do wykorzystania przez przyszły walidator.
STRATEGY_CONTEXT = (
    "Instrument XAU/USD; zamknięte M1 z potwierdzeniem M5. LONG/SHORT wymagają "
    "zgodnego trendu EMA M1/M5, RSI z momentum i położenia ceny względem VWAP "
    "oraz jednego setupu: pullback przy EMA9 po korekcie albo wybicie poza "
    "pasmo Bollingera i lokalne maksimum/minimum. Wymagane są filtry sesji, "
    "świeżości, danych i minimalnej punktacji; inaczej NONE."
)

import sys
from pathlib import Path
from types import ModuleType

# Backend: kalendarz korzysta z tego samego profilu co API.
directory = Path(__file__).resolve().parent
api_directory = directory.parent
if not (api_directory / "market_products.py").is_file():
    api_directory = directory.parents[2] / "database" / "api"
sys.path.insert(0, str(api_directory))

# Backend: osobna przestrzeń nazw zapobiega kolizjom modułów config strategii.
package = ModuleType("xauusd_strategy")
package.__path__ = [str(directory)]
sys.modules[package.__name__] = package
from xauusd_strategy.trigger import main


if __name__ == "__main__":
    raise SystemExit(main())
