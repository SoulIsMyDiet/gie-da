"""Ładuje strategię w odizolowanym pakiecie dla testów."""

import importlib.util
import sys
from pathlib import Path

STRATEGY_DIRECTORY = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "claude_btcusd_test"
PACKAGE_SPEC = importlib.util.spec_from_file_location(
    PACKAGE_NAME,
    STRATEGY_DIRECTORY / "__init__.py",
    submodule_search_locations=[str(STRATEGY_DIRECTORY)],
)
if PACKAGE_SPEC is None or PACKAGE_SPEC.loader is None:
    raise ImportError("Nie można załadować pakietu testowego BTC/USD.")

PACKAGE = importlib.util.module_from_spec(PACKAGE_SPEC)
sys.modules[PACKAGE_NAME] = PACKAGE
PACKAGE_SPEC.loader.exec_module(PACKAGE)
