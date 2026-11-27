"""Backend: punkt wejścia i publiczny interfejs strategii BTC/USD."""

from pathlib import Path
import sys
from typing import Any

SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, SCRIPT_DIRECTORY)

try:
    from . import config
    from .signal_logic import generate_signal
    from .strategy_context import STRATEGY_CONTEXT
except ImportError:
    import config
    from signal_logic import generate_signal
    from strategy_context import STRATEGY_CONTEXT

STRATEGY_NAME = "Claude-sonnet55/BTCUSD"


class BTCUSDStrategyV1:
    """Udostępnia synchroniczną ocenę zamkniętych świec BTC/USD."""

    def evaluate(
        self,
        candles: list[dict],
        now=None,
        last_signal_timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Zwraca pełny wynik i kompaktowy JSON; ValueError opisuje błędne dane."""
        return generate_signal(
            candles,
            now=now,
            last_signal_timestamp=last_signal_timestamp,
        )


def main() -> int:
    """Uruchamia pojedynczą analizę albo tryb pętli."""
    try:
        from .trigger import main as trigger_main
    except ImportError:
        from trigger import main as trigger_main
    return trigger_main()


if __name__ == "__main__":
    sys.exit(main())
