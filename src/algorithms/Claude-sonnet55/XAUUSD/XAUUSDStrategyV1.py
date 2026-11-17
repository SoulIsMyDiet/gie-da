"""Punkt wejścia strategii XAU/USD z importami płaskimi."""

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
