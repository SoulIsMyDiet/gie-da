"""Backend: punkt wejścia procesu analizy świecy XAUUSD."""

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
