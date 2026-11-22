"""Lokalne API zapisujące świece rynkowe w MySQL."""

# Backend
import copy
import importlib.util
import json
import logging
import math
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Literal
from zoneinfo import ZoneInfo
from urllib.parse import quote

import mysql.connector
from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from error_file_logging import configure_error_file_logging  # noqa: E402
from todo_repository import TodoRepository  # noqa: E402

algorithm_directory = Path(__file__).resolve().parent / "algorithm"
if not algorithm_directory.is_dir():
    algorithm_directory = (
        Path(__file__).resolve().parents[2]
        / "algorithms"
        / "Chat-gpt6"
        / "EURUSD"
    )
sys.path.insert(0, str(algorithm_directory))
grok_algorithm_directory = Path(__file__).resolve().parent / "grok_algorithm"
if not grok_algorithm_directory.is_dir():
    grok_algorithm_directory = (
        Path(__file__).resolve().parents[2]
        / "algorithms"
        / "Grok-45"
        / "EURUSD"
    )
sys.path.insert(0, str(grok_algorithm_directory))
dax_algorithm_directory = (
    Path(__file__).resolve().parent / "dax_algorithm"
)
if not dax_algorithm_directory.is_dir():
    dax_algorithm_directory = (
        Path(__file__).resolve().parents[2]
        / "algorithms"
        / "Claude-sonnet55"
        / "DAX40"
    )
dax_package_name = "claude_sonnet55_dax40"
dax_package_spec = importlib.util.spec_from_file_location(
    dax_package_name,
    dax_algorithm_directory / "__init__.py",
    submodule_search_locations=[str(dax_algorithm_directory)],
)
if dax_package_spec is None or dax_package_spec.loader is None:
    raise ImportError("Nie można załadować pakietu strategii DAX40.")
dax_package = importlib.util.module_from_spec(dax_package_spec)
sys.modules[dax_package_name] = dax_package
dax_package_spec.loader.exec_module(dax_package)

chat_dax_algorithm_directory = Path(__file__).resolve().parent / "chat_dax_algorithm"
if not chat_dax_algorithm_directory.is_dir():
    chat_dax_algorithm_directory = (
        Path(__file__).resolve().parents[2]
        / "algorithms"
        / "Chat-gpt6"
        / "DAX40"
    )
chat_dax_package_name = "chat_gpt6_dax40"
chat_dax_package_spec = importlib.util.spec_from_file_location(
    chat_dax_package_name,
    chat_dax_algorithm_directory / "__init__.py",
    submodule_search_locations=[str(chat_dax_algorithm_directory)],
)
if chat_dax_package_spec is None or chat_dax_package_spec.loader is None:
    raise ImportError("Nie można załadować pakietu strategii Chat-gpt6 DAX40.")
chat_dax_package = importlib.util.module_from_spec(chat_dax_package_spec)
sys.modules[chat_dax_package_name] = chat_dax_package
chat_dax_package_spec.loader.exec_module(chat_dax_package)

usdmxn_algorithm_directory = Path(__file__).resolve().parent / "usdmxn_algorithm"
if not usdmxn_algorithm_directory.is_dir():
    usdmxn_algorithm_directory = (
        Path(__file__).resolve().parents[2]
        / "algorithms"
        / "Chat-gpt6"
        / "USDMXN"
    )
usdmxn_package_name = "chat_gpt6_usdmxn"
usdmxn_package_spec = importlib.util.spec_from_file_location(
    usdmxn_package_name,
    usdmxn_algorithm_directory / "__init__.py",
    submodule_search_locations=[str(usdmxn_algorithm_directory)],
)
if usdmxn_package_spec is None or usdmxn_package_spec.loader is None:
    raise ImportError("Nie można załadować pakietu strategii USDMXN.")
usdmxn_package = importlib.util.module_from_spec(usdmxn_package_spec)
sys.modules[usdmxn_package_name] = usdmxn_package
usdmxn_package_spec.loader.exec_module(usdmxn_package)

usdjpy_algorithm_directory = Path(__file__).resolve().parent / "usdjpy_algorithm"
if not usdjpy_algorithm_directory.is_dir():
    usdjpy_algorithm_directory = (
        Path(__file__).resolve().parents[2]
        / "algorithms"
        / "Chat-gpt6"
        / "USDJPY"
    )
usdjpy_package_name = "chat_gpt6_usdjpy"
usdjpy_package_spec = importlib.util.spec_from_file_location(
    usdjpy_package_name,
    usdjpy_algorithm_directory / "__init__.py",
    submodule_search_locations=[str(usdjpy_algorithm_directory)],
)
if usdjpy_package_spec is None or usdjpy_package_spec.loader is None:
    raise ImportError("Nie można załadować pakietu strategii USDJPY.")
usdjpy_package = importlib.util.module_from_spec(usdjpy_package_spec)
sys.modules[usdjpy_package_name] = usdjpy_package
usdjpy_package_spec.loader.exec_module(usdjpy_package)

grok45_usdmxn_algorithm_directory = (
    Path(__file__).resolve().parent / "grok_usdmxn_algorithm"
)
if not grok45_usdmxn_algorithm_directory.is_dir():
    grok45_usdmxn_algorithm_directory = (
        Path(__file__).resolve().parents[2]
        / "algorithms"
        / "Grok-45"
        / "USDMXN"
    )
grok45_usdmxn_package_name = "grok45_usdmxn"
grok45_usdmxn_package_spec = importlib.util.spec_from_file_location(
    grok45_usdmxn_package_name,
    grok45_usdmxn_algorithm_directory / "__init__.py",
    submodule_search_locations=[str(grok45_usdmxn_algorithm_directory)],
)
if grok45_usdmxn_package_spec is None or grok45_usdmxn_package_spec.loader is None:
    raise ImportError("Nie można załadować strategii Grok-45 USD/MXN.")
grok45_usdmxn_package = importlib.util.module_from_spec(
    grok45_usdmxn_package_spec
)
sys.modules[grok45_usdmxn_package_name] = grok45_usdmxn_package
grok45_usdmxn_package_spec.loader.exec_module(grok45_usdmxn_package)

btcusd_algorithm_directory = Path(__file__).resolve().parent / "btc_algorithm"
if not btcusd_algorithm_directory.is_dir():
    btcusd_algorithm_directory = (
        Path(__file__).resolve().parents[2]
        / "algorithms"
        / "Grok-45"
        / "BTCUSD"
    )
btcusd_package_name = "grok45_btcusd"
btcusd_package_spec = importlib.util.spec_from_file_location(
    btcusd_package_name,
    btcusd_algorithm_directory / "__init__.py",
    submodule_search_locations=[str(btcusd_algorithm_directory)],
)
if btcusd_package_spec is None or btcusd_package_spec.loader is None:
    raise ImportError("Nie można załadować strategii Grok-45 BTC/USD.")
btcusd_package = importlib.util.module_from_spec(btcusd_package_spec)
sys.modules[btcusd_package_name] = btcusd_package
btcusd_package_spec.loader.exec_module(btcusd_package)

from EURUSDStrategyV1 import EURUSDStrategyV1, STRATEGY_NAME  # noqa: E402
from EURUSDStrategyV2 import (  # noqa: E402
    Candle as Grok45Candle,
    EURUSDStrategyV2,
    STRATEGY_NAME as GROK45_V2_STRATEGY_NAME,
)
GROK45_CPP_STRATEGY_NAME = "Grok-45/EURUSDC++StrategyV1"
from market_products import (  # noqa: E402
    get_monitored_symbols,
    get_open_symbols,
    is_market_open,
    is_monitored_product,
)
from prompt_templates import (  # noqa: E402
    INITIAL_SIGNAL_REVIEW_PROMPT,
    PROMPT_TEMPLATE_VERSION,
)
from ai_verification import AIVerificationWorker  # noqa: E402
from sound_alert import play_alert  # noqa: E402
from market_data import Candle  # noqa: E402
from signal_potential_calculator import SignalPotentialCalculator  # noqa: E402
from signal_serializer import Signal  # noqa: E402
from transaction_tracker import TransactionTracker  # noqa: E402
from daily_sentences import (  # noqa: E402
    create_daily_sentences_table,
    fetch_daily_sentences,
)
from claude_sonnet55_dax40.DAX40StrategyV1 import (  # noqa: E402
    DAX40StrategyV1,
)
from claude_sonnet55_dax40.config import (  # noqa: E402
    BEEP_ENABLED as DAX40_BEEP_ENABLED,
)
from claude_sonnet55_dax40.storage import DAX40Storage  # noqa: E402
from chat_gpt6_dax40.DAX40StrategyV1 import (  # noqa: E402
    DAX40StrategyV1 as ChatDAX40Strategy,
)
from chat_gpt6_dax40.config import (  # noqa: E402
    BEEP_ENABLED as CHAT_DAX40_BEEP_ENABLED,
    HISTORY_LIMIT as CHAT_DAX40_HISTORY_LIMIT,
)
from chat_gpt6_dax40.storage import DAX40SignalStorage  # noqa: E402
from chat_gpt6_usdmxn.USDMXNStrategyV1 import (  # noqa: E402
    USDMXNStrategyV1,
)
from chat_gpt6_usdmxn.config import (  # noqa: E402
    BEEP_ENABLED as USDMXN_BEEP_ENABLED,
    DATABASE_SYMBOL as USDMXN_DATABASE_SYMBOL,
    HISTORY_LIMIT as USDMXN_HISTORY_LIMIT,
    STRATEGY_NAME as USDMXN_STRATEGY_NAME,
)
from chat_gpt6_usdmxn.storage import USDMXNSignalStorage  # noqa: E402
from chat_gpt6_usdjpy.USDJPYStrategyV1 import (  # noqa: E402
    USDJPYStrategyV1,
)
from chat_gpt6_usdjpy.config import (  # noqa: E402
    BEEP_ENABLED as USDJPY_BEEP_ENABLED,
    DATABASE_SYMBOL as USDJPY_DATABASE_SYMBOL,
    HISTORY_LIMIT as USDJPY_HISTORY_LIMIT,
    STRATEGY_NAME as USDJPY_STRATEGY_NAME,
)
from chat_gpt6_usdjpy.storage import USDJPYSignalStorage  # noqa: E402
from grok45_usdmxn.USDMXNStrategyV1 import (  # noqa: E402
    USDMXNStrategyV1 as Grok45USDMXNStrategy,
)
from grok45_usdmxn.config import (  # noqa: E402
    BEEP_ENABLED as GROK45_USDMXN_BEEP_ENABLED,
    HISTORY_LIMIT as GROK45_USDMXN_HISTORY_LIMIT,
    STRATEGY_NAME as GROK45_USDMXN_STRATEGY_NAME,
)
from grok45_usdmxn.storage import (  # noqa: E402
    USDMXNSignalStorage as Grok45USDMXNSignalStorage,
)
from grok45_btcusd.BTCUSDStrategyV1 import (  # noqa: E402
    BTCUSDStrategyV1,
)
from grok45_btcusd.config import (  # noqa: E402
    BEEP_ENABLED as BTCUSD_BEEP_ENABLED,
    COOLDOWN_SECONDS as BTCUSD_COOLDOWN_SECONDS,
    DATABASE_SYMBOL as BTCUSD_DATABASE_SYMBOL,
    HISTORY_LIMIT as BTCUSD_HISTORY_LIMIT,
    STRATEGY_NAME as BTCUSD_STRATEGY_NAME,
)
from grok45_btcusd.storage import BTCUSDSignalStorage  # noqa: E402

WARSAW_TIME_ZONE = ZoneInfo("Europe/Warsaw")
MAX_BACKFILL_CANDLES = 1000
GROK45_ANALYSIS_WINDOW = 100
SP500_PROCESS_TIMEOUT_SECONDS = 30
logger = logging.getLogger(__name__)


def _format_api_timestamp(timestamp: datetime) -> str:
    """Formatuje czas API do 19-znakowej postaci wymaganej przez klienta C++.

    Args:
        timestamp: Data i czas, które mają zostać zwrócone klientowi.

    Returns:
        Tekst w formacie ``YYYY-MM-DD HH:MM:SS``.
    """
    return timestamp.strftime("%Y-%m-%d %H:%M:%S")


class SP500StrategyError(RuntimeError):
    """Sygnalizuje błąd procesu strategii po zapisaniu świecy w bazie."""


class XAUUSDStrategyError(RuntimeError):
    """Sygnalizuje błąd analizy XAUUSD po zapisaniu świecy."""


class ClaudeUSDJPYStrategyError(RuntimeError):
    """Sygnalizuje błąd procesu strategii Claude USD/JPY."""


def _saved_xauusd_signal(
    completed: subprocess.CompletedProcess[str],
    strategy_name: str,
    candle_open_time: datetime,
) -> tuple[str, bool] | None:
    """Odczytuje kierunek zapisanego sygnału albo informuje o braku nowego wpisu.

    Kod 3 oznacza, że strategia nie utworzyła nowego rekordu. Kody 0 i 4
    oznaczają zapis; kod 4 może wystąpić, gdy dźwięk strategii jest wyłączony.

    Returns:
        Para (kierunek, czy dźwięk jest dozwolony) lub None dla duplikatu.

    Raises:
        XAUUSDStrategyError: Gdy proces lub jego wynik nie potwierdza poprawnego zapisu.
    """
    if completed.returncode == 3:
        logger.debug(
            "Strategia %s nie utworzyła nowego wpisu dla świecy %s (kod 3).",
            strategy_name,
            candle_open_time.isoformat(),
        )
        return None
    if completed.returncode not in {0, 4}:
        logger.error(
            "Strategia %s zakończyła się błędem %s: %s",
            strategy_name,
            completed.returncode,
            completed.stderr.strip(),
        )
        raise XAUUSDStrategyError(
            f"Strategia {strategy_name} zakończyła się błędem."
        )
    try:
        result = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        logger.error(
            "Strategia %s zwróciła niepoprawny JSON dla świecy %s: %s",
            strategy_name,
            candle_open_time.isoformat(),
            completed.stderr.strip(),
        )
        raise XAUUSDStrategyError(
            f"Strategia {strategy_name} zwróciła niepoprawny wynik."
        ) from error
    signal = result.get("signal") if isinstance(result, dict) else None
    if not isinstance(signal, str) or signal.upper() not in {
        "LONG",
        "SHORT",
        "NONE",
    }:
        logger.error(
            "Strategia %s nie zwróciła poprawnego kierunku dla świecy %s.",
            strategy_name,
            candle_open_time.isoformat(),
        )
        raise XAUUSDStrategyError(
            f"Strategia {strategy_name} nie zwróciła poprawnego sygnału."
        )
    return signal.upper(), completed.returncode == 0


def _run_xauusd_strategy(
    candle_open_time: datetime,
) -> tuple[str, bool] | None:
    """Uruchamia strategię Chat-gpt6 XAU/USD dla świecy UTC.

    Returns:
        Para (kierunek, czy dźwięk jest dozwolony) lub None dla duplikatu.

    Raises:
        XAUUSDStrategyError: Gdy nie można uruchomić procesu albo wynik jest błędny.
    """
    algorithm_directory = Path(__file__).resolve().parent / "xauusd_algorithm"
    if not algorithm_directory.is_dir():
        algorithm_directory = (
            Path(__file__).resolve().parents[2] / "algorithms" / "Chat-gpt6" / "XAUUSD"
        )
    command = [
        sys.executable, str(algorithm_directory / "XAUUSDStrategyV1.py"),
        "--api-candle-open-epoch", str(int(candle_open_time.timestamp())),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True,
            timeout=SP500_PROCESS_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise XAUUSDStrategyError("Nie udało się uruchomić strategii XAUUSD.") from error
    return _saved_xauusd_signal(
        completed, "Chat-gpt6/XAUUSDStrategyV1", candle_open_time
    )


def _run_claude_xauusd_strategy(
    candle_open_time: datetime,
) -> tuple[str, bool] | None:
    """Uruchamia strategię Claude XAU/USD dla świecy UTC.

    Returns:
        Para (kierunek, czy dźwięk jest dozwolony) lub None dla duplikatu.

    Raises:
        XAUUSDStrategyError: Gdy nie można uruchomić procesu albo wynik jest błędny.
    """
    algorithm_directory = (
        Path(__file__).resolve().parent / "claude_xauusd_algorithm"
    )
    if not algorithm_directory.is_dir():
        algorithm_directory = (
            Path(__file__).resolve().parents[2]
            / "algorithms"
            / "Claude-sonnet55"
            / "XAUUSD"
        )
    command = [
        sys.executable,
        str(algorithm_directory / "XAUUSDStrategyV1.py"),
        "--api-candle-open-epoch",
        str(int(candle_open_time.timestamp())),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=SP500_PROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise XAUUSDStrategyError(
            "Nie udało się uruchomić strategii Claude XAU/USD."
        ) from error
    return _saved_xauusd_signal(
        completed, "Claude-sonnet55/XAUUSDStrategyV1", candle_open_time
    )


def _run_claude_usdjpy_strategy(
    candle_open_time: datetime,
) -> tuple[str, bool] | None:
    """Uruchamia Claude USD/JPY dla zamkniętej świecy przez tryb API.

    Returns:
        Para (kierunek, czy dźwięk jest dozwolony) lub None dla duplikatu.

    Raises:
        ClaudeUSDJPYStrategyError: Gdy proces nie może zapisać albo zwraca błędny wynik.
    """
    algorithm_directory = (
        Path(__file__).resolve().parent / "claude_usdjpy_algorithm"
    )
    if not algorithm_directory.is_dir():
        algorithm_directory = (
            Path(__file__).resolve().parents[2]
            / "algorithms"
            / "Claude-sonnet55"
            / "USDJPY"
        )
    command = [
        sys.executable,
        str(algorithm_directory / "USDJPYStrategyV1.py"),
        "--api-candle-open-epoch",
        str(int(candle_open_time.timestamp())),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=SP500_PROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ClaudeUSDJPYStrategyError(
            "Nie udało się uruchomić strategii Claude USD/JPY."
        ) from error

    if completed.returncode == 3:
        logger.debug(
            "Claude USD/JPY nie utworzyła nowego wpisu dla świecy %s.",
            candle_open_time.isoformat(),
        )
        return None
    if completed.returncode not in {0, 4}:
        logger.error(
            "Strategia Claude USD/JPY zakończyła się błędem %s: %s",
            completed.returncode,
            completed.stderr.strip(),
        )
        raise ClaudeUSDJPYStrategyError(
            "Strategia Claude USD/JPY zakończyła się błędem."
        )
    try:
        result = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        logger.error(
            "Strategia Claude USD/JPY zwróciła błędny JSON dla świecy %s.",
            candle_open_time.isoformat(),
        )
        raise ClaudeUSDJPYStrategyError(
            "Strategia Claude USD/JPY zwróciła niepoprawny wynik."
        ) from error

    signal = result.get("signal") if isinstance(result, dict) else None
    if not isinstance(signal, str) or signal.upper() not in {
        "LONG",
        "SHORT",
        "NONE",
    }:
        logger.error(
            "Strategia Claude USD/JPY nie zwróciła poprawnego sygnału dla %s.",
            candle_open_time.isoformat(),
        )
        raise ClaudeUSDJPYStrategyError(
            "Strategia Claude USD/JPY nie zwróciła poprawnego sygnału."
        )
    return signal.upper(), completed.returncode == 0


def _run_claude_usdjpy_batch(
    candle_open_times: list[datetime],
) -> list[dict[str, object]]:
    """Uruchamia raz strategię Claude dla wszystkich zamkniętych świec paczki.

    Returns:
        Wyniki pojedynczych ewaluacji; błędy świec są logowane i oznaczane w wynikach.

    Raises:
        ClaudeUSDJPYStrategyError: Gdy proces lub jego zbiorczy wynik jest nieprawidłowy.
    """
    algorithm_directory = (
        Path(__file__).resolve().parent / "claude_usdjpy_algorithm"
    )
    if not algorithm_directory.is_dir():
        algorithm_directory = (
            Path(__file__).resolve().parents[2]
            / "algorithms"
            / "Claude-sonnet55"
            / "USDJPY"
        )
    command = [
        sys.executable,
        str(algorithm_directory / "USDJPYStrategyV1.py"),
        "--api-candle-open-epochs",
        *(
            str(int(candle_open_time.timestamp()))
            for candle_open_time in candle_open_times
        ),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=SP500_PROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ClaudeUSDJPYStrategyError(
            "Nie udało się uruchomić strategii Claude USD/JPY dla paczki."
        ) from error

    if completed.returncode not in {0, 3, 4, 5}:
        logger.error(
            "Strategia Claude USD/JPY dla paczki zakończyła się błędem %s: %s",
            completed.returncode,
            completed.stderr.strip(),
        )
        raise ClaudeUSDJPYStrategyError(
            "Strategia Claude USD/JPY nie przetworzyła paczki."
        )
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise ClaudeUSDJPYStrategyError(
            "Strategia Claude USD/JPY zwróciła niepoprawny wynik paczki."
        ) from error
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or len(results) != len(candle_open_times):
        raise ClaudeUSDJPYStrategyError(
            "Strategia Claude USD/JPY zwróciła niepełny wynik paczki."
        )

    expected_epochs = [
        int(candle_open_time.timestamp()) for candle_open_time in candle_open_times
    ]
    for expected_epoch, result in zip(expected_epochs, results):
        if not isinstance(result, dict):
            raise ClaudeUSDJPYStrategyError(
                "Wynik świecy Claude USD/JPY ma nieprawidłowy format."
            )
        if result.get("candle_open_epoch") != expected_epoch:
            raise ClaudeUSDJPYStrategyError(
                "Wynik Claude USD/JPY nie odpowiada kolejności świec paczki."
            )
        if result.get("error"):
            logger.error(
                "Strategia Claude USD/JPY pominęła świecę %s z błędem.",
                datetime.fromtimestamp(expected_epoch, timezone.utc).isoformat(),
            )
            continue
        signal = result.get("signal")
        if (
            not isinstance(signal, str)
            or signal not in {"LONG", "SHORT", "NONE"}
            or not isinstance(result.get("saved"), bool)
        ):
            raise ClaudeUSDJPYStrategyError(
                "Wynik Claude USD/JPY nie zawiera poprawnego sygnału."
            )
    return results


def _run_sp500_process(candle_open_time: datetime, container_directory: str,
                       author: str) -> bool:
    """Uruchamia strategię autora dla świecy UTC; błąd procesu zgłasza SP500StrategyError."""
    algorithm_directory = Path(__file__).resolve().parent / container_directory
    if not algorithm_directory.is_dir():
        algorithm_directory = (
            Path(__file__).resolve().parents[2]
            / "algorithms"
            / author
            / "S&P500"
        )
    command = [
        sys.executable,
        str(algorithm_directory / "S&P500StrategyV1.py"),
        "--api-candle-open-epoch",
        str(int(candle_open_time.timestamp())),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=SP500_PROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SP500StrategyError(
            f"Nie udało się uruchomić strategii S&P 500 ({author})."
        ) from error
    if completed.returncode == 0:
        return True
    if completed.returncode in (3, 4):
        return False
    logger.error(
        "Strategia S&P 500 (%s) zakończyła się błędem %s: %s",
        author,
        completed.returncode,
        completed.stderr.strip(),
    )
    raise SP500StrategyError(f"Strategia S&P 500 ({author}) zakończyła się błędem.")


def _run_sp500_strategy(candle_open_time: datetime) -> bool:
    """Uruchamia strategię Claude dla otwarcia UTC i zwraca żądanie dźwięku."""
    return _run_sp500_process(candle_open_time, "sp500_algorithm", "Claude-sonnet55")


class GrokSP500Worker:
    """Nadzoruje osobny proces pętli Grok S&P500."""

    def __init__(self) -> None:
        """Przygotowuje blokady i wątek; nie uruchamia procesu."""
        self._stop = Event()
        self._lock = Lock()
        self._process = None
        self._thread = Thread(target=self._run, daemon=True, name="grok-sp500")

    def start(self) -> None:
        """Uruchamia wątek nadzorujący; błędy uruchomienia wątku są propagowane."""
        self._thread.start()

    def _run(self) -> None:
        """Uruchamia proces i ponawia go po awarii."""
        directory = Path(__file__).resolve().parent / "grok_sp500_algorithm"
        if not directory.is_dir():
            directory = Path(__file__).resolve().parents[2] / "algorithms" / "Grok-45" / "S&P500"
        command = [sys.executable, "-u", str(directory / "S&P500StrategyV1.py"), "--loop"]
        while not self._stop.is_set():
            try:
                with self._lock:
                    if self._stop.is_set():
                        return
                    self._process = subprocess.Popen(
                        command, stdout=subprocess.DEVNULL
                    )
                    process = self._process
                process.wait()
                if not self._stop.is_set():
                    logger.error("Pętla Grok S&P500 zakończyła się kodem %s.", process.returncode)
            except OSError:
                logger.exception("Nie można uruchomić pętli Grok S&P500.")
            finally:
                with self._lock:
                    self._process = None
            self._stop.wait(5)

    def stop(self) -> None:
        """Kończy własny proces i czeka na wątek; nie dotyka innych procesów."""
        self._stop.set()
        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
        if process is not None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                process.wait()
        self._thread.join(timeout=10)


class CandleRecord(BaseModel):
    """Opis jednej świecy rynkowej przesyłanej przez aplikację desktopową."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9./_-]+$")
    datetime: datetime
    open: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=10)
    high: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=10)
    low: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=10)
    close: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=10)
    volume: Decimal | None = Field(
        default=None, ge=Decimal("0"), max_digits=30, decimal_places=10
    )

    @field_validator("datetime")
    @classmethod
    def require_timezone(cls, candle_datetime: datetime) -> datetime:
        """Wymaga strefy czasowej, aby zapisywać czas świecy jednoznacznie w UTC."""
        if candle_datetime.tzinfo is None or candle_datetime.utcoffset() is None:
            raise ValueError("datetime musi zawierać strefę czasową.")
        if candle_datetime.second != 0 or candle_datetime.microsecond != 0:
            raise ValueError("datetime świecy M1 musi zaczynać się równo w pełnej minucie.")
        return candle_datetime

    @model_validator(mode="after")
    def validate_ohlc_range(self) -> "CandleRecord":
        """Sprawdza, czy zakres świecy zawiera ceny otwarcia i zamknięcia."""
        if (
            self.high < self.open
            or self.high < self.close
            or self.low > self.open
            or self.low > self.close
            or self.low > self.high
        ):
            raise ValueError("Wartości OHLC nie tworzą prawidłowego zakresu świecy.")
        return self


class CandleBatch(BaseModel):
    """Opis paczki świec zapisywanych podczas uzupełniania historii."""

    candles: list[CandleRecord] = Field(min_length=1, max_length=MAX_BACKFILL_CANDLES)


class Grok45SignalRecord(BaseModel):
    """Opis sygnału LONG/SHORT wygenerowanego przez algorytm Grok-45."""

    model_config = ConfigDict(extra="forbid")

    instrument: Literal["EUR/USD"]
    price: Decimal = Field(gt=Decimal("0"), max_digits=20, decimal_places=10)
    timestamp: int = Field(gt=0)
    signal: Literal["LONG", "SHORT"]
    parameters: dict[str, int | float | str | None]

    @field_validator("price", mode="before")
    @classmethod
    def fit_price_to_database_precision(cls, value: object) -> object:
        """Zaokrągla cenę sygnału do precyzji DECIMAL zapisanej w bazie."""
        try:
            return Decimal(str(value)).quantize(
                Decimal("0.0000000001"), rounding=ROUND_HALF_UP
            )
        except InvalidOperation:
            return value


def _grok45_candles_from_history(candles: list[Candle]) -> list[Grok45Candle]:
    """Przekształca ostatnie zamknięte świece API na format Grok V2."""
    current_minute = datetime.now(timezone.utc).replace(
        second=0, microsecond=0
    )
    closed_candles = [
        candle
        for candle in candles
        if candle.timestamp.astimezone(timezone.utc) < current_minute
    ]
    return [
        Grok45Candle(
            timestamp=int(candle.timestamp.timestamp()),
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume or 0.0,
        )
        for candle in closed_candles[-GROK45_ANALYSIS_WINDOW:]
    ]


def _generate_grok45_v2_signal(
    candles: list[Grok45Candle],
) -> Grok45SignalRecord | None:
    """Zamienia wynik Grok V2 na rekord bazy, jeśli spełniono warunki sygnału."""
    response = json.loads(EURUSDStrategyV2().generate(candles))
    if response["signal"] not in ("LONG", "SHORT"):
        return None
    return Grok45SignalRecord.model_validate(response)


class MarketDataRepository:
    """Zarządza produktami i pobranymi świecami."""

    def __init__(self) -> None:
        """Pobiera ustawienia połączenia z bazy ze zmiennych środowiskowych."""
        self.database_settings = {
            "host": os.environ["MYSQL_HOST"],
            "port": int(os.environ.get("MYSQL_PORT", "3306")),
            "user": os.environ["MYSQL_USER"],
            "password": os.environ["MYSQL_PASSWORD"],
            "database": os.environ["MYSQL_DATABASE"],
            "charset": "utf8mb4",
        }
        self.signal_potential_calculator = SignalPotentialCalculator()

    def initialize(self) -> None:
        """Tworzy tabele, jeśli nie istnieją; błędy połączenia przerywają start API."""
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS products (
                        symbol VARCHAR(32) PRIMARY KEY,
                        created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                    ) ENGINE=InnoDB
                    """
                )
                TodoRepository.initialize_schema(cursor)
                create_daily_sentences_table(cursor)

                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS prompts (
                        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        prompt LONGTEXT NOT NULL,
                        used_context BOOLEAN NOT NULL DEFAULT FALSE,
                        used_parameters BOOLEAN NOT NULL DEFAULT FALSE,
                        prompt_in_use BOOLEAN NOT NULL DEFAULT FALSE,
                        used_candles BOOLEAN NOT NULL DEFAULT FALSE,
                        prompt_template_version SMALLINT UNSIGNED NOT NULL DEFAULT 0
                    ) ENGINE=InnoDB
                    DEFAULT CHARACTER SET utf8mb4
                    COLLATE utf8mb4_unicode_ci
                    """
                )
                self._migrate_prompt_used_candles(cursor)
                self._migrate_prompt_template_version(cursor)
                self._migrate_prompt_encoding(cursor)
                cursor.execute(
                    """
                    INSERT IGNORE INTO prompts
                        (id, prompt, used_context, used_parameters, prompt_in_use,
                         used_candles)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (1, INITIAL_SIGNAL_REVIEW_PROMPT, True, True, True, True),
                )
                self._migrate_prompt_template(cursor)
                cursor.execute("DROP TABLE IF EXISTS price_history")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS candles (
                        symbol VARCHAR(32) NOT NULL,
                        datetime DATETIME(6) NOT NULL,
                        open DECIMAL(20, 10) NOT NULL,
                        high DECIMAL(20, 10) NOT NULL,
                        low DECIMAL(20, 10) NOT NULL,
                        close DECIMAL(20, 10) NOT NULL,
                        volume DECIMAL(30, 10) NULL,
                        utc_offset_minutes SMALLINT NOT NULL DEFAULT 32767,
                        PRIMARY KEY (symbol, datetime, utc_offset_minutes),
                        CONSTRAINT fk_candles_product
                            FOREIGN KEY (symbol) REFERENCES products(symbol)
                    ) ENGINE=InnoDB
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS candle_gaps (
                        symbol VARCHAR(32) NOT NULL,
                        candle_datetime_utc DATETIME(6) NOT NULL,
                        status ENUM('missing', 'filled') NOT NULL DEFAULT 'missing',
                        detected_at_utc DATETIME(6) NOT NULL,
                        filled_at_utc DATETIME(6) NULL,
                        PRIMARY KEY (symbol, candle_datetime_utc),
                        INDEX idx_candle_gaps_status_time
                            (status, candle_datetime_utc),
                        CONSTRAINT fk_candle_gaps_product
                            FOREIGN KEY (symbol) REFERENCES products(symbol)
                    ) ENGINE=InnoDB
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS signals (
                        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        `datetime` DATETIME(6) NOT NULL,
                        product VARCHAR(32) NOT NULL,
                        price DECIMAL(20, 10) NOT NULL,
                        `signal` ENUM('short', 'long', 'none') NOT NULL,
                        algorithm_name VARCHAR(255) NOT NULL,
                        ai_decision JSON NULL,
                        transaction_id BIGINT UNSIGNED NULL,
                        candles_count INT UNSIGNED NOT NULL,
                        signal_parameters JSON NOT NULL,
                        source_timestamp BIGINT NULL,
                        INDEX idx_signals_product_datetime (product, `datetime`),
                        INDEX idx_signals_algorithm_datetime (algorithm_name, `datetime`),
                        UNIQUE KEY uq_signals_source_timestamp
                            (product, algorithm_name, source_timestamp),
                        CONSTRAINT fk_signals_product
                            FOREIGN KEY (product) REFERENCES products(symbol)
                    ) ENGINE=InnoDB
                    """
                )
                self._migrate_signal_enum(cursor)
                self._migrate_signal_source_timestamp(cursor)
                self._initialize_ai_verification_schema(cursor)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS algorithm_evaluations (
                        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        product VARCHAR(32) NOT NULL,
                        algorithm_name VARCHAR(255) NOT NULL,
                        source_timestamp BIGINT NOT NULL,
                        `signal` ENUM('short', 'long', 'none') NOT NULL,
                        notification_pending BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at_utc DATETIME(6) NOT NULL
                            DEFAULT CURRENT_TIMESTAMP(6),
                        UNIQUE KEY uq_algorithm_evaluations_source_timestamp
                            (product, algorithm_name, source_timestamp),
                        INDEX idx_algorithm_evaluations_pending
                            (notification_pending, id),
                        CONSTRAINT fk_algorithm_evaluations_product
                            FOREIGN KEY (product) REFERENCES products(symbol)
                    ) ENGINE=InnoDB
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS signal_potention (
                        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        signal_id BIGINT UNSIGNED NOT NULL,
                        saldo_1st_candle DECIMAL(20, 5) NULL DEFAULT NULL,
                        saldo_2nd_candle DECIMAL(20, 5) NULL DEFAULT NULL,
                        saldo_3rd_candle DECIMAL(20, 5) NULL DEFAULT NULL,
                        saldo_4th_candle DECIMAL(20, 5) NULL DEFAULT NULL,
                        saldo_5th_candle DECIMAL(20, 5) NULL DEFAULT NULL,
                        saldo_10aggr_candles DECIMAL(20, 5) NULL DEFAULT NULL,
                        saldo_20_aggr_candles DECIMAL(20, 5) NULL DEFAULT NULL,
                        best_candle_20_minute INT NULL DEFAULT NULL,
                        `100zl_deal_20` DECIMAL(20, 5) NULL DEFAULT NULL,
                        best_candle_60_datetime DATETIME NULL DEFAULT NULL,
                        saldo_60_aggr_candles DECIMAL(20, 5) NULL DEFAULT NULL,
                        `100zl_deal_60` DECIMAL(20, 5) NULL DEFAULT NULL,
                        average_spread DECIMAL(20, 5) NOT NULL,
                        UNIQUE KEY uq_signal_potention_signal_id (signal_id),
                        CONSTRAINT fk_signal_potention_signal
                            FOREIGN KEY (signal_id) REFERENCES signals(id)
                    ) ENGINE=InnoDB
                    """
                )
                self._migrate_signal_potention_schema(cursor)
                self._migrate_eurusd_symbol(cursor)
                self._migrate_algorithm_names(cursor)
                self._migrate_candle_timestamps(cursor)
                self._sync_signal_potention_spreads(cursor)
                self.signal_potential_calculator.update_pending(cursor)
                connection.commit()
            finally:
                cursor.close()
        finally:
            connection.close()

    @staticmethod
    def _migrate_eurusd_symbol(cursor: mysql.connector.cursor.MySQLCursor) -> None:
        """Przenosi dane EURUSD/USD do symbolu EUR/USD bez nadpisywania rekordow."""
        old_symbol = "EURUSD/USD"
        new_symbol = "EUR/USD"
        duplicate_queries = (
            (
                "candles",
                """SELECT COUNT(*) FROM candles old JOIN candles new
                   ON old.datetime = new.datetime
                   AND old.utc_offset_minutes = new.utc_offset_minutes
                   WHERE old.symbol = %s AND new.symbol = %s""",
            ),
            (
                "candle_gaps",
                """SELECT COUNT(*) FROM candle_gaps old JOIN candle_gaps new
                   ON old.candle_datetime_utc = new.candle_datetime_utc
                   WHERE old.symbol = %s AND new.symbol = %s""",
            ),
            (
                "signals",
                """SELECT COUNT(*) FROM signals old JOIN signals new
                   ON old.algorithm_name = new.algorithm_name
                   AND old.source_timestamp = new.source_timestamp
                   WHERE old.product = %s AND new.product = %s
                   AND old.source_timestamp IS NOT NULL""",
            ),
            (
                "algorithm_evaluations",
                """SELECT COUNT(*) FROM algorithm_evaluations old
                   JOIN algorithm_evaluations new
                   ON old.algorithm_name = new.algorithm_name
                   AND old.source_timestamp = new.source_timestamp
                   WHERE old.product = %s AND new.product = %s""",
            ),
        )
        for table_name, query in duplicate_queries:
            cursor.execute(query, (old_symbol, new_symbol))
            if int(cursor.fetchone()[0]) > 0:
                raise RuntimeError(
                    f"Nie mozna scalic symboli EUR/USD: tabela {table_name} "
                    "zawiera nakladajace sie rekordy."
                )

        cursor.execute(
            "INSERT IGNORE INTO products (symbol) "
            "SELECT %s FROM products WHERE symbol = %s",
            (new_symbol, old_symbol),
        )
        for table_name, column_name in (
            ("candles", "symbol"),
            ("candle_gaps", "symbol"),
            ("signals", "product"),
            ("algorithm_evaluations", "product"),
        ):
            cursor.execute(
                f"UPDATE `{table_name}` SET `{column_name}` = %s "
                f"WHERE `{column_name}` = %s",
                (new_symbol, old_symbol),
            )
        cursor.execute(
            "UPDATE signals SET signal_parameters = "
            "JSON_SET(signal_parameters, '$.instrument', %s) "
            "WHERE JSON_UNQUOTE(JSON_EXTRACT(signal_parameters, '$.instrument')) = %s",
            (new_symbol, old_symbol),
        )
        cursor.execute("DELETE FROM products WHERE symbol = %s", (old_symbol,))

    @staticmethod
    def _migrate_algorithm_names(
        cursor: mysql.connector.cursor.MySQLCursor,
    ) -> None:
        """Normalizuje historyczne nazwy strategii bez utraty rekordow."""
        name_mappings = (
            ("Grok-45", "Grok-45/EURUSDC++StrategyV1"),
            ("Grok-45/EURUSD/EURUSDStrategyV2", "Grok-45/EURUSDStrategyV2"),
            ("Grok-45/USDMXN/USDMXNStrategyV1", "Grok-45/USDMXNStrategyV1"),
            ("Chat-gpt6/EURUSD/EURUSDStrategyV1", "Chat-gpt6/EURUSDStrategyV1"),
            ("Chat-gpt6/DAX40/DAX40StrategyV1", "Chat-gpt6/DAX40StrategyV1"),
            ("Chat-gpt6/USDMXN/USDMXNStrategyV1", "Chat-gpt6/USDMXNStrategyV1"),
            ("Chat-gpt6/USDJPY/USDJPYStrategyV1", "Chat-gpt6/USDJPYStrategyV1"),
            ("Claude-sonnet55/EURUSD/MicroTransactions", "Claude-sonnet55/MicroTransactions"),
        )
        for old_name, new_name in name_mappings:
            for table_name in ("signals", "algorithm_evaluations"):
                if table_name == "signals":
                    collision_query = """SELECT COUNT(*) FROM signals old
                        JOIN signals new ON old.product = new.product
                        AND old.source_timestamp = new.source_timestamp
                        AND old.source_timestamp IS NOT NULL
                        WHERE old.algorithm_name = %s
                        AND new.algorithm_name = %s"""
                else:
                    collision_query = """SELECT COUNT(*) FROM algorithm_evaluations old
                        JOIN algorithm_evaluations new
                        ON old.product = new.product
                        AND old.source_timestamp = new.source_timestamp
                        WHERE old.algorithm_name = %s
                        AND new.algorithm_name = %s"""
                cursor.execute(collision_query, (old_name, new_name))
                if int(cursor.fetchone()[0]) > 0:
                    raise RuntimeError(
                        f"Nie mozna zmienic nazwy algorytmu {old_name!r}: "
                        f"tabela {table_name} zawiera nakladajace sie rekordy "
                        f"dla {new_name!r}."
                    )
                cursor.execute(
                    f"UPDATE `{table_name}` SET algorithm_name = %s "
                    "WHERE algorithm_name = %s",
                    (new_name, old_name),
                )

    def _migrate_prompt_used_candles(
        self, cursor: mysql.connector.cursor.MySQLCursor
    ) -> None:
        """Dodaje flagę użycia świec i ustawia ją dla istniejącego promptu startowego."""
        cursor.execute(
            "SHOW COLUMNS FROM prompts LIKE %s",
            ("used_candles",),
        )
        if cursor.fetchone() is None:
            cursor.execute(
                """
                ALTER TABLE prompts
                ADD COLUMN used_candles BOOLEAN NOT NULL DEFAULT FALSE
                """
            )
            cursor.execute(
                "UPDATE prompts SET used_candles = TRUE WHERE id = %s",
                (1,),
            )

    def _migrate_prompt_template_version(
        self, cursor: mysql.connector.cursor.MySQLCursor
    ) -> None:
        """Dodaje znacznik wersji, który pozwala jednorazowo odświeżyć prompt startowy."""
        cursor.execute(
            "SHOW COLUMNS FROM prompts LIKE %s",
            ("prompt_template_version",),
        )
        if cursor.fetchone() is None:
            cursor.execute(
                """
                ALTER TABLE prompts
                ADD COLUMN prompt_template_version
                    SMALLINT UNSIGNED NOT NULL DEFAULT 0
                """
            )

    def _migrate_prompt_encoding(
        self, cursor: mysql.connector.cursor.MySQLCursor
    ) -> None:
        """Ustawia kodowanie utf8mb4 dla kolumny prompt w istniejącej tabeli."""
        cursor.execute(
            "SHOW FULL COLUMNS FROM prompts LIKE %s",
            ("prompt",),
        )
        prompt_column = cursor.fetchone()
        if prompt_column is None:
            raise RuntimeError("Nie znaleziono kolumny prompts.prompt.")

        collation = prompt_column[2]
        if not collation or not collation.startswith("utf8mb4_"):
            cursor.execute(
                """
                ALTER TABLE prompts
                MODIFY COLUMN prompt LONGTEXT
                    CHARACTER SET utf8mb4
                    COLLATE utf8mb4_unicode_ci
                    NOT NULL
                """
            )

    def _migrate_prompt_template(
        self, cursor: mysql.connector.cursor.MySQLCursor
    ) -> None:
        """Zastępuje przestarzały prompt startowy tylko raz dla danej wersji szablonu."""
        cursor.execute(
            """
            UPDATE prompts
            SET prompt = %s, prompt_template_version = %s
            WHERE id = %s AND prompt_template_version < %s
            """,
            (
                INITIAL_SIGNAL_REVIEW_PROMPT,
                PROMPT_TEMPLATE_VERSION,
                1,
                PROMPT_TEMPLATE_VERSION,
            ),
        )

    def _migrate_signal_source_timestamp(
        self, cursor: mysql.connector.cursor.MySQLCursor
    ) -> None:
        """Dodaje znacznik świecy i unikalny klucz chroniący przed duplikatami."""
        cursor.execute("SHOW COLUMNS FROM signals LIKE 'source_timestamp'")
        if cursor.fetchone() is None:
            cursor.execute(
                "ALTER TABLE signals ADD COLUMN source_timestamp BIGINT NULL"
            )
        cursor.execute(
            "SHOW INDEX FROM signals WHERE Key_name = %s",
            ("uq_signals_source_timestamp",),
        )
        if not cursor.fetchall():
            cursor.execute(
                """
                ALTER TABLE signals
                ADD UNIQUE KEY uq_signals_source_timestamp
                    (product, algorithm_name, source_timestamp)
                """
            )

    @staticmethod
    def _initialize_ai_verification_schema(
        cursor: mysql.connector.cursor.MySQLCursor,
    ) -> None:
        """Tworzy tabele weryfikacji i uruchamia kolejkę dla nowych sygnałów."""
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS `transactions` (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                signal_id BIGINT UNSIGNED NOT NULL,
                verifier_id VARCHAR(64)
                    CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
                `mode` ENUM('aggressive', 'moderate') NOT NULL,
                signal_status ENUM('long', 'short') NOT NULL,
                ai_decision_status ENUM('long', 'short') NOT NULL,
                open_price DECIMAL(20, 10) NOT NULL,
                stop_loss_price DECIMAL(20, 10) NOT NULL,
                take_profit_price DECIMAL(20, 10) NOT NULL,
                true_spread DECIMAL(20, 10) NOT NULL,
                time_to_open INT UNSIGNED NULL DEFAULT 0,
                time_from_open_to_close INT UNSIGNED NULL,
                transaction_status
                    ENUM('awaiting', 'in progress', 'closed', 'canceled')
                    NOT NULL DEFAULT 'awaiting',
                transaction_saldo DECIMAL(20, 10) NULL,
                transaction_100zl_deal DECIMAL(20, 5) NULL,
                signal_close_utc DATETIME(6) NOT NULL,
                opened_at_utc DATETIME(6) NULL,
                closed_at_utc DATETIME(6) NULL,
                UNIQUE KEY uq_transactions_signal_verifier_mode
                    (signal_id, verifier_id, `mode`),
                INDEX idx_transactions_status (transaction_status, signal_id),
                CONSTRAINT fk_transactions_signal
                    FOREIGN KEY (signal_id) REFERENCES signals(id)
                    ON DELETE CASCADE
            ) ENGINE=InnoDB
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_decisions (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                transaction_id BIGINT UNSIGNED NOT NULL,
                `version` SMALLINT UNSIGNED NOT NULL DEFAULT 1,
                ai_name VARCHAR(255) NOT NULL,
                ai_pts TINYINT UNSIGNED NOT NULL,
                ai_decision ENUM('accept', 'decline') NOT NULL,
                ai_decision_reason_msg VARCHAR(250) NOT NULL,
                ai_after_tramsaction_msg TEXT NULL,
                `mode` ENUM('aggressive', 'moderate') NOT NULL,
                UNIQUE KEY uq_ai_decisions_transaction_version
                    (transaction_id, `version`),
                CONSTRAINT chk_ai_decisions_pts
                    CHECK (ai_pts BETWEEN 1 AND 100),
                CONSTRAINT fk_ai_decisions_transaction
                    FOREIGN KEY (transaction_id) REFERENCES `transactions`(id)
                    ON DELETE CASCADE
            ) ENGINE=InnoDB
                DEFAULT CHARACTER SET utf8mb4
                COLLATE utf8mb4_unicode_ci
                """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS prompts_revision (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                full_prompt LONGTEXT NOT NULL,
                ai_decision_id BIGINT UNSIGNED NULL,
                ai_response LONGTEXT NOT NULL,
                created_at_utc DATETIME(6) NOT NULL
                    DEFAULT CURRENT_TIMESTAMP(6),
                INDEX idx_prompts_revision_decision (ai_decision_id),
                CONSTRAINT fk_prompts_revision_decision
                    FOREIGN KEY (ai_decision_id) REFERENCES ai_decisions(id)
                    ON DELETE SET NULL
            ) ENGINE=InnoDB
            DEFAULT CHARACTER SET utf8mb4
            COLLATE utf8mb4_unicode_ci
            """
        )
        MarketDataRepository._migrate_transactions_verifier_schema(cursor)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_verification_jobs (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                signal_id BIGINT UNSIGNED NOT NULL,
                status ENUM(
                    'pending', 'processing', 'completed', 'failed', 'skipped'
                ) NOT NULL DEFAULT 'pending',
                last_error TEXT NULL,
                created_at_utc DATETIME(6) NOT NULL
                    DEFAULT CURRENT_TIMESTAMP(6),
                updated_at_utc DATETIME(6) NOT NULL
                    DEFAULT CURRENT_TIMESTAMP(6)
                    ON UPDATE CURRENT_TIMESTAMP(6),
                UNIQUE KEY uq_ai_verification_jobs_signal (signal_id),
                INDEX idx_ai_verification_jobs_status (status, created_at_utc),
                CONSTRAINT fk_ai_verification_jobs_signal
                    FOREIGN KEY (signal_id) REFERENCES signals(id)
                    ON DELETE CASCADE
            ) ENGINE=InnoDB
                DEFAULT CHARACTER SET utf8mb4
                COLLATE utf8mb4_unicode_ci
                """
        )
        cursor.execute(
            """
            UPDATE ai_verification_jobs
            SET status = 'pending',
                last_error = 'Odzyskano zadanie po nieoczekiwanym zatrzymaniu API.'
            WHERE status = 'processing'
                AND updated_at_utc <
                    UTC_TIMESTAMP(6) - INTERVAL 10 MINUTE
            """
        )
        cursor.execute(
            "DROP TRIGGER IF EXISTS trg_signals_ai_verification_after_insert"
        )
        cursor.execute(
            """
            CREATE TRIGGER trg_signals_ai_verification_after_insert
            AFTER INSERT ON signals
            FOR EACH ROW
            BEGIN
                IF NEW.`signal` IN ('long', 'short') THEN
                    INSERT IGNORE INTO ai_verification_jobs (signal_id)
                    VALUES (NEW.id);
                END IF;
            END
            """
        )

    @staticmethod
    def _migrate_transactions_verifier_schema(
        cursor: mysql.connector.cursor.MySQLCursor,
    ) -> None:
        """Dodaje identyfikator modelu i umożliwia osobne transakcje modeli."""
        cursor.execute("SHOW COLUMNS FROM `transactions` LIKE 'verifier_id'")
        if cursor.fetchone() is None:
            cursor.execute(
                """
                ALTER TABLE `transactions`
                ADD COLUMN verifier_id VARCHAR(64)
                    CHARACTER SET ascii COLLATE ascii_bin NULL
                    AFTER signal_id
                """
            )
        cursor.execute(
            """
            UPDATE `transactions`
            SET verifier_id = 'legacy'
            WHERE verifier_id IS NULL OR verifier_id = ''
            """
        )
        cursor.execute(
            """
            ALTER TABLE `transactions`
            MODIFY COLUMN verifier_id VARCHAR(64)
                CHARACTER SET ascii COLLATE ascii_bin NOT NULL
            """
        )

        cursor.execute(
            "SHOW INDEX FROM `transactions` "
            "WHERE Key_name = %s",
            ("uq_transactions_signal_verifier_mode",),
        )
        if not cursor.fetchall():
            cursor.execute(
                """
                ALTER TABLE `transactions`
                ADD UNIQUE KEY uq_transactions_signal_verifier_mode
                    (signal_id, verifier_id, `mode`)
                """
            )
        cursor.execute(
            "SHOW INDEX FROM `transactions` "
            "WHERE Key_name = %s",
            ("uq_transactions_signal_mode",),
        )
        if cursor.fetchall():
            cursor.execute(
                "ALTER TABLE `transactions` "
                "DROP INDEX uq_transactions_signal_mode"
            )

    def _migrate_signal_enum(
        self, cursor: mysql.connector.cursor.MySQLCursor
    ) -> None:
        """Rozszerza istniejący ENUM sygnałów o NONE, również po aktualizacji bazy."""
        cursor.execute("SHOW COLUMNS FROM signals LIKE 'signal'")
        signal_column = cursor.fetchone()
        if signal_column is None:
            raise RuntimeError("Tabela signals nie zawiera kolumny signal.")
        if "'none'" not in str(signal_column[1]).lower():
            cursor.execute(
                """
                ALTER TABLE signals
                MODIFY COLUMN `signal` ENUM('short', 'long', 'none') NOT NULL
                """
            )

    def _migrate_signal_potention_schema(
        self, cursor: mysql.connector.cursor.MySQLCursor
    ) -> None:
        """Aktualizuje kolumny tabeli wyników sygnałów bez utraty dotychczasowych sald."""
        cursor.execute("SHOW COLUMNS FROM signal_potention")
        column_names = {column[0] for column in cursor.fetchall()}

        old_saldo_column = "saldo_10th_candle"
        new_saldo_column = "saldo_10aggr_candles"
        if old_saldo_column in column_names and new_saldo_column not in column_names:
            cursor.execute(
                """
                UPDATE signal_potention
                SET saldo_10th_candle = TRUNCATE(saldo_10th_candle, 5)
                """
            )
            cursor.execute(
                """
                ALTER TABLE signal_potention
                CHANGE COLUMN saldo_10th_candle saldo_10aggr_candles
                DECIMAL(20, 5) NULL DEFAULT NULL
                """
            )
            column_names.remove(old_saldo_column)
            column_names.add(new_saldo_column)
        elif old_saldo_column in column_names and new_saldo_column in column_names:
            cursor.execute(
                """
                UPDATE signal_potention
                SET saldo_10aggr_candles =
                    COALESCE(
                        saldo_10aggr_candles,
                        TRUNCATE(saldo_10th_candle, 5)
                    )
                """
            )
            cursor.execute(
                "ALTER TABLE signal_potention DROP COLUMN saldo_10th_candle"
            )
            column_names.remove(old_saldo_column)

        column_renames = (
            (
                "max_candle",
                "best_candle_20_minute",
                "INT NULL DEFAULT NULL",
            ),
            (
                "100zl_deal",
                "100zl_deal_20",
                "DECIMAL(20, 5) NULL DEFAULT NULL",
            ),
            (
                "spread",
                "average_spread",
                "DECIMAL(20, 5) NOT NULL",
            ),
        )
        for old_name, new_name, column_definition in column_renames:
            if old_name in column_names and new_name not in column_names:
                cursor.execute(
                    f"""
                    ALTER TABLE signal_potention
                    CHANGE COLUMN `{old_name}` `{new_name}`
                    {column_definition}
                    """
                )
                column_names.remove(old_name)
                column_names.add(new_name)
            elif old_name in column_names and new_name in column_names:
                cursor.execute(
                    f"""
                    UPDATE signal_potention
                    SET `{new_name}` = COALESCE(`{new_name}`, `{old_name}`)
                    """
                )
                cursor.execute(
                    f"ALTER TABLE signal_potention DROP COLUMN `{old_name}`"
                )
                column_names.remove(old_name)

        if "saldo_20_aggr_candles" not in column_names:
            cursor.execute(
                """
                ALTER TABLE signal_potention
                ADD COLUMN saldo_20_aggr_candles
                DECIMAL(20, 5) NULL DEFAULT NULL
                    AFTER saldo_10aggr_candles
                """
            )
            column_names.add("saldo_20_aggr_candles")

        if "best_candle_20_minute" not in column_names:
            cursor.execute(
                """
                ALTER TABLE signal_potention
                ADD COLUMN best_candle_20_minute INT NULL DEFAULT NULL
                    AFTER saldo_20_aggr_candles
                """
            )
            column_names.add("best_candle_20_minute")

        if "100zl_deal_20" not in column_names:
            cursor.execute(
                """
                ALTER TABLE signal_potention
                ADD COLUMN `100zl_deal_20` DECIMAL(20, 5) NULL DEFAULT NULL
                    AFTER best_candle_20_minute
                """
            )
            column_names.add("100zl_deal_20")

        new_60_minute_columns = (
            (
                "best_candle_60_datetime",
                "DATETIME NULL DEFAULT NULL",
                "`100zl_deal_20`",
            ),
            (
                "saldo_60_aggr_candles",
                "DECIMAL(20, 5) NULL DEFAULT NULL",
                "best_candle_60_datetime",
            ),
            (
                "100zl_deal_60",
                "DECIMAL(20, 5) NULL DEFAULT NULL",
                "saldo_60_aggr_candles",
            ),
        )
        for column_name, column_definition, previous_column in (
            new_60_minute_columns
        ):
            if column_name not in column_names:
                cursor.execute(
                    f"""
                    ALTER TABLE signal_potention
                    ADD COLUMN `{column_name}` {column_definition}
                        AFTER {previous_column}
                    """
                )
                column_names.add(column_name)

        if "average_spread" not in column_names:
            cursor.execute(
                """
                ALTER TABLE signal_potention
                ADD COLUMN average_spread
                    DECIMAL(20, 5) NOT NULL DEFAULT 0.0003
                    AFTER `100zl_deal_60`
                """
            )
            column_names.add("average_spread")

        decimal_columns = {
            "saldo_1st_candle": "DECIMAL(20, 5) NULL DEFAULT NULL",
            "saldo_2nd_candle": "DECIMAL(20, 5) NULL DEFAULT NULL",
            "saldo_3rd_candle": "DECIMAL(20, 5) NULL DEFAULT NULL",
            "saldo_4th_candle": "DECIMAL(20, 5) NULL DEFAULT NULL",
            "saldo_5th_candle": "DECIMAL(20, 5) NULL DEFAULT NULL",
            "saldo_10aggr_candles": "DECIMAL(20, 5) NULL DEFAULT NULL",
            "saldo_20_aggr_candles": "DECIMAL(20, 5) NULL DEFAULT NULL",
            "saldo_60_aggr_candles": "DECIMAL(20, 5) NULL DEFAULT NULL",
            "100zl_deal_20": "DECIMAL(20, 5) NULL DEFAULT NULL",
            "100zl_deal_60": "DECIMAL(20, 5) NULL DEFAULT NULL",
            "average_spread": "DECIMAL(20, 5) NOT NULL",
        }
        cursor.execute("SHOW COLUMNS FROM signal_potention")
        column_types = {
            column[0]: str(column[1]).replace(" ", "").lower()
            for column in cursor.fetchall()
        }
        columns_to_update = [
            column_name
            for column_name in decimal_columns
            if column_types.get(column_name) != "decimal(20,5)"
        ]
        if columns_to_update:
            truncated_values = ", ".join(
                f"`{column_name}` = TRUNCATE(`{column_name}`, 5)"
                for column_name in columns_to_update
            )
            cursor.execute(
                f"UPDATE signal_potention SET {truncated_values}"
            )
            modified_columns = ", ".join(
                f"MODIFY COLUMN `{column_name}` {decimal_columns[column_name]}"
                for column_name in columns_to_update
            )
            cursor.execute(
                f"ALTER TABLE signal_potention {modified_columns}"
            )
        cursor.execute(
            "ALTER TABLE signal_potention ALTER COLUMN average_spread DROP DEFAULT"
        )

    def _sync_signal_potention_spreads(
        self, cursor: mysql.connector.cursor.MySQLCursor
    ) -> None:
        """Uzupełnia brakujące wiersze potencjału i aktualizuje spread instrumentu."""
        configured_spreads = (
            self.signal_potential_calculator.SPREADS_BY_PRODUCT
        )
        products = tuple(configured_spreads)
        case_expression = "CASE s.product " + " ".join(
            "WHEN %s THEN %s" for _ in products
        ) + " END"
        case_parameters = tuple(
            value
            for product, spread in configured_spreads.items()
            for value in (product, spread)
        )
        product_placeholders = ", ".join("%s" for _ in products)
        product_parameters = (*case_parameters, *products)

        cursor.execute(
            f"""
            INSERT IGNORE INTO signal_potention (signal_id, average_spread)
            SELECT s.id, {case_expression}
            FROM signals AS s
            WHERE s.`signal` IN ('short', 'long')
                AND s.product IN ({product_placeholders})
            """,
            product_parameters,
        )
        cursor.execute(
            f"""
            UPDATE signal_potention AS p
            JOIN signals AS s ON s.id = p.signal_id
            SET p.average_spread = {case_expression}
            WHERE s.`signal` IN ('short', 'long')
                AND s.product IN ({product_placeholders})
            """,
            product_parameters,
        )

    def _migrate_candle_timestamps(
        self, cursor: mysql.connector.cursor.MySQLCursor
    ) -> None:
        """Przekształca wcześniej zapisane znaczniki UTC na czas Europe/Warsaw."""
        cursor.execute("SHOW COLUMNS FROM candles LIKE %s", ("utc_offset_minutes",))
        offset_column = cursor.fetchone()
        if offset_column is None:
            cursor.execute(
                """
                ALTER TABLE candles
                ADD COLUMN utc_offset_minutes SMALLINT NOT NULL DEFAULT 32767
                """
            )

        cursor.execute("SHOW KEYS FROM candles WHERE Key_name = 'PRIMARY'")
        primary_key_columns = [key[4] for key in cursor.fetchall()]
        expected_primary_key = ["symbol", "datetime", "utc_offset_minutes"]
        if primary_key_columns != expected_primary_key:
            cursor.execute(
                """
                ALTER TABLE candles
                DROP PRIMARY KEY,
                ADD PRIMARY KEY (symbol, datetime, utc_offset_minutes)
                """
            )

        cursor.execute(
            """
            SELECT symbol, datetime
            FROM candles
            WHERE utc_offset_minutes = 32767
            """
        )
        for symbol, previous_utc_datetime in cursor.fetchall():
            utc_datetime = previous_utc_datetime.replace(tzinfo=timezone.utc)
            warsaw_datetime = utc_datetime.astimezone(WARSAW_TIME_ZONE)
            local_datetime = warsaw_datetime.replace(tzinfo=None)
            offset_minutes = int(warsaw_datetime.utcoffset().total_seconds() // 60)
            cursor.execute(
                """
                UPDATE candles
                SET datetime = %s, utc_offset_minutes = %s
                WHERE symbol = %s AND datetime = %s
                    AND utc_offset_minutes = 32767
                """,
                (local_datetime, offset_minutes, symbol, previous_utc_datetime),
            )

    def save_candle(
        self,
        candle: CandleRecord,
        signal: Signal | None = None,
        grok45_v2_signal: Grok45SignalRecord | None = None,
    ) -> tuple[int, int, str, int]:
        """Zapisuje świecę i zwraca liczbę nowo wykrytych luk."""
        return self.save_candles(
            [candle], signal, grok45_v2_signal, detect_gaps=True
        )

    def save_candles(
        self,
        candles: list[CandleRecord],
        signal: Signal | None = None,
        grok45_v2_signal: Grok45SignalRecord | None = None,
        detect_gaps: bool = False,
    ) -> tuple[int, int, str, int]:
        """Zapisuje tylko świece z godzin otwarcia rynku i zwraca ich najnowszy czas."""
        candle_values = []
        product_symbols = set()
        newly_detected_gap_count = 0
        open_market_candles = [
            candle
            for candle in candles
            if self._is_market_open(
                candle.symbol,
                candle.datetime.astimezone(timezone.utc)
            )
        ]
        if not open_market_candles:
            return 0, 0, "", False

        for candle in open_market_candles:
            candle_datetime_local = candle.datetime.astimezone(WARSAW_TIME_ZONE)
            local_datetime = candle_datetime_local.replace(tzinfo=None)
            offset_minutes = int(candle_datetime_local.utcoffset().total_seconds() // 60)
            product_symbols.add(candle.symbol)
            candle_values.append(
                (
                    candle.symbol,
                    local_datetime,
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                    offset_minutes,
                )
            )

        connection = mysql.connector.connect(**self.database_settings)
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.executemany(
                """
                INSERT INTO products (symbol) VALUES (%s)
                ON DUPLICATE KEY UPDATE symbol = VALUES(symbol)
                """,
                [(symbol,) for symbol in product_symbols],
            )
            if detect_gaps:
                for candle in open_market_candles:
                    newly_detected_gap_count += self._detect_gaps_before_candle(
                        cursor, candle
                    )
            cursor.executemany(
                """
                INSERT INTO candles
                    (symbol, datetime, open, high, low, close, volume,
                     utc_offset_minutes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    open = VALUES(open),
                    high = VALUES(high),
                    low = VALUES(low),
                    close = VALUES(close),
                    volume = VALUES(volume)
                """,
                candle_values,
            )
            current_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            cursor.executemany(
                """
                UPDATE candle_gaps
                SET status = 'filled', filled_at_utc = %s
                WHERE symbol = %s AND candle_datetime_utc = %s
                    AND status = 'missing'
                """,
                [
                    (
                        current_utc,
                        candle.symbol,
                        candle.datetime.astimezone(timezone.utc).replace(tzinfo=None),
                    )
                    for candle in open_market_candles
                ],
            )
            filled_gap_candles = []
            if cursor.rowcount > 0:
                for symbol in product_symbols:
                    symbol_candles = [
                        candle
                        for candle in open_market_candles
                        if candle.symbol == symbol
                    ]
                    candle_times_utc = [
                        candle.datetime.astimezone(timezone.utc).replace(
                            tzinfo=None
                        )
                        for candle in symbol_candles
                    ]
                    cursor.execute(
                        """
                        SELECT candle_datetime_utc
                        FROM candle_gaps
                        WHERE symbol = %s AND status = 'filled'
                            AND filled_at_utc = %s
                            AND candle_datetime_utc >= %s
                            AND candle_datetime_utc <= %s
                        """,
                        (
                            symbol,
                            current_utc,
                            min(candle_times_utc),
                            max(candle_times_utc),
                        ),
                    )
                    filled_times_utc = {
                        row[0] for row in cursor.fetchall()
                    }
                    filled_gap_candles.extend(
                        candle
                        for candle, candle_time_utc in zip(
                            symbol_candles, candle_times_utc
                        )
                        if candle_time_utc in filled_times_utc
                    )
            if signal is not None:
                self._insert_signal(cursor, signal)
            grok45_v2_signal_saved = False
            if grok45_v2_signal is not None:
                grok45_v2_signal_saved = self._insert_grok45_signal(
                    cursor, grok45_v2_signal
                )
            self.signal_potential_calculator.update_pending(
                cursor, product_symbols
            )
            transaction_tracker = TransactionTracker()
            transaction_tracker.process_candles(cursor, open_market_candles)
            if filled_gap_candles:
                transaction_tracker.recalculate_transactions(
                    cursor, filled_gap_candles
                )
            connection.commit()
        except (mysql.connector.Error, ValueError):
            connection.rollback()
            raise
        finally:
            if cursor is not None:
                cursor.close()
            connection.close()
        latest_saved_candle = max(
            open_market_candles,
            key=lambda item: item.datetime.astimezone(timezone.utc),
        )
        latest_saved_time = _format_api_timestamp(
            latest_saved_candle.datetime.astimezone(timezone.utc)
        )
        return (
            newly_detected_gap_count,
            len(open_market_candles),
            latest_saved_time,
            int(signal is not None) + int(grok45_v2_signal_saved),
        )

    def _detect_gaps_before_candle(
        self, cursor: mysql.connector.cursor.MySQLCursor, candle: CandleRecord
    ) -> int:
        """Rejestruje brakujące minuty handlowe między poprzednią świecą a nową."""
        current_utc = candle.datetime.astimezone(timezone.utc)
        cursor.execute(
            """
            SELECT datetime, utc_offset_minutes
            FROM candles
            WHERE symbol = %s
                AND DATE_SUB(datetime, INTERVAL utc_offset_minutes MINUTE) < %s
            ORDER BY DATE_SUB(datetime, INTERVAL utc_offset_minutes MINUTE) DESC
            LIMIT 1
            """,
            (candle.symbol, current_utc.replace(tzinfo=None)),
        )
        previous_row = cursor.fetchone()
        if previous_row is None:
            return 0

        previous_utc = previous_row[0].replace(
            tzinfo=timezone(timedelta(minutes=previous_row[1]))
        ).astimezone(timezone.utc)
        missing_times = []
        missing_time = previous_utc + timedelta(minutes=1)
        while missing_time < current_utc:
            if self._is_market_open(candle.symbol, missing_time):
                missing_times.append(missing_time)
            missing_time += timedelta(minutes=1)
        return self._upsert_candle_gaps(
            cursor, candle.symbol, missing_times, count_new_records=True
        )

    @staticmethod
    def _upsert_candle_gaps(
        cursor: mysql.connector.cursor.MySQLCursor,
        symbol: str,
        missing_times: list[datetime],
        count_new_records: bool = False,
    ) -> int:
        """Zapisuje lub ponownie otwiera wpisy luk, nie tworząc duplikatów."""
        if not missing_times:
            return 0

        detected_at_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        gap_values = [
            (
                symbol,
                missing_time.astimezone(timezone.utc).replace(tzinfo=None),
                detected_at_utc,
            )
            for missing_time in missing_times
        ]
        upsert_query = """
            INSERT INTO candle_gaps
                (symbol, candle_datetime_utc, status, detected_at_utc)
            VALUES (%s, %s, 'missing', %s)
            ON DUPLICATE KEY UPDATE
                detected_at_utc = IF(
                    status = 'filled', VALUES(detected_at_utc),
                    detected_at_utc
                ),
                filled_at_utc = IF(status = 'filled', NULL, filled_at_utc),
                status = 'missing'
            """
        if not count_new_records:
            cursor.executemany(upsert_query, gap_values)
            return 0

        newly_detected_count = 0
        for gap_value in gap_values:
            cursor.execute(upsert_query, gap_value)
            if cursor.rowcount > 0:
                newly_detected_count += 1
        return newly_detected_count

    def fetch_candle_history(self, symbol: str) -> list[Candle]:
        """Pobiera świece symbolu w kolejności UTC do odtworzenia stanu strategii."""
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT datetime, utc_offset_minutes, open, high, low, close, volume
                    FROM candles
                    WHERE symbol = %s
                    ORDER BY DATE_SUB(
                        datetime, INTERVAL utc_offset_minutes MINUTE
                    ) ASC
                    """,
                    (symbol,),
                )
                rows = cursor.fetchall()
                return [
                    Candle(
                        timestamp=candle_datetime.replace(
                            tzinfo=timezone(timedelta(minutes=offset_minutes))
                        ).astimezone(timezone.utc),
                        open=float(open_price),
                        high=float(high_price),
                        low=float(low_price),
                        close=float(close_price),
                        volume=float(volume) if volume is not None else None,
                    )
                    for (
                        candle_datetime,
                        offset_minutes,
                        open_price,
                        high_price,
                        low_price,
                        close_price,
                        volume,
                    ) in rows
                ]
            finally:
                cursor.close()
        finally:
            connection.close()

    def fetch_recent_candle_history(
        self, symbol: str, limit: int, cutoff: datetime
    ) -> list[dict[str, Any]]:
        """Pobiera ograniczoną historię do otwarcia cutoff, zachowując czas UTC."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Limit historii musi być dodatnią liczbą całkowitą.")
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("Granica historii musi zawierać strefę czasową.")
        cutoff_utc = cutoff.astimezone(timezone.utc).replace(tzinfo=None)
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT datetime, utc_offset_minutes, open, high, low, close, volume
                    FROM candles
                    WHERE symbol = %s
                        AND DATE_SUB(
                            datetime, INTERVAL utc_offset_minutes MINUTE
                        ) <= %s
                    ORDER BY DATE_SUB(
                        datetime, INTERVAL utc_offset_minutes MINUTE
                    ) DESC
                    LIMIT %s
                    """,
                    (symbol, cutoff_utc, limit),
                )
                rows = reversed(cursor.fetchall())
                candles = []
                for (
                    local_datetime,
                    offset_minutes,
                    open_price,
                    high,
                    low,
                    close,
                    volume,
                ) in rows:
                    opened_utc = local_datetime.replace(
                        tzinfo=timezone(timedelta(minutes=int(offset_minutes)))
                    ).astimezone(timezone.utc)
                    candles.append(
                        {
                            "timestamp_utc": opened_utc,
                            "open": float(open_price),
                            "high": float(high),
                            "low": float(low),
                            "close": float(close),
                            "volume": float(volume) if volume is not None else None,
                        }
                    )
                return candles
            finally:
                cursor.close()
        finally:
            connection.close()

    def fetch_prompt_candle_history(
        self,
        symbol: str,
        limit: int,
        cutoff: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Pobiera zamknięte świece M1 wraz z informacją o lukach w sesji."""
        if not is_monitored_product(symbol):
            raise ValueError(f"Instrument {symbol} nie jest skonfigurowany.")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1000
        ):
            raise ValueError("Limit historii musi należeć do zakresu 1-1000.")

        if cutoff is None:
            cutoff_utc = datetime.now(timezone.utc).replace(
                second=0, microsecond=0, tzinfo=None
            )
        else:
            if cutoff.tzinfo is None or cutoff.utcoffset() is None:
                raise ValueError("Granica historii musi zawierać strefę czasową.")
            cutoff_utc = cutoff.astimezone(timezone.utc).replace(
                second=0, microsecond=0, tzinfo=None
            )
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT datetime, utc_offset_minutes, open, high, low, close
                    FROM candles
                    WHERE symbol = %s
                        AND DATE_SUB(
                            datetime, INTERVAL utc_offset_minutes MINUTE
                        ) < %s
                    ORDER BY DATE_SUB(
                        datetime, INTERVAL utc_offset_minutes MINUTE
                    ) DESC
                    LIMIT %s
                    """,
                    (symbol, cutoff_utc, limit),
                )
                rows = list(reversed(cursor.fetchall()))
            finally:
                cursor.close()
        finally:
            connection.close()

        candles: list[dict[str, Any]] = []
        previous_timestamp: datetime | None = None
        for (
            local_datetime,
            offset_minutes,
            open_price,
            high,
            low,
            close,
        ) in rows:
            timestamp_utc = local_datetime.replace(
                tzinfo=timezone(timedelta(minutes=int(offset_minutes)))
            ).astimezone(timezone.utc)
            gap_before = False
            if previous_timestamp is not None:
                missing_minute = previous_timestamp + timedelta(minutes=1)
                while missing_minute < timestamp_utc:
                    if self._is_market_open(symbol, missing_minute):
                        gap_before = True
                        break
                    missing_minute += timedelta(minutes=1)
            candles.append(
                {
                    "timestamp_utc": int(timestamp_utc.timestamp()),
                    "open": format(Decimal(str(open_price)).normalize(), "f"),
                    "high": format(Decimal(str(high)).normalize(), "f"),
                    "low": format(Decimal(str(low)).normalize(), "f"),
                    "close": format(Decimal(str(close)).normalize(), "f"),
                    "gap_before": gap_before,
                }
            )
            previous_timestamp = timestamp_utc
        return candles

    def has_algorithm_evaluation(
        self, product: str, algorithm_name: str, source_timestamp: int
    ) -> bool:
        """Sprawdza, czy świeca została już oceniona.

        Args:
            product: Symbol produktu zapisany w bazie.
            algorithm_name: Nazwa strategii.
            source_timestamp: Epoka UTC otwarcia ocenianej świecy.

        Returns:
            ``True`` dla znacznika ewaluacji albo starszego zapisu sygnału.
        """
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT 1
                    FROM (
                        SELECT 1
                        FROM algorithm_evaluations
                        WHERE product = %s AND algorithm_name = %s
                            AND source_timestamp = %s
                        UNION ALL
                        SELECT 1
                        FROM signals
                        WHERE product = %s AND algorithm_name = %s
                            AND source_timestamp = %s
                    ) AS existing_evaluation
                    LIMIT 1
                    """,
                    (
                        product,
                        algorithm_name,
                        source_timestamp,
                        product,
                        algorithm_name,
                        source_timestamp,
                    ),
                )
                return cursor.fetchone() is not None
            finally:
                cursor.close()
        finally:
            connection.close()

    def fetch_recent_algorithm_signal_timestamp(
        self,
        product: str,
        algorithm_name: str,
        source_timestamp: int,
        lookback_seconds: int,
    ) -> int | None:
        """Pobiera czas ostatniego LONG/SHORT w podanym oknie cooldownu.

        Args:
            product: Symbol produktu zapisany w bazie.
            algorithm_name: Nazwa strategii, której sygnały są sprawdzane.
            source_timestamp: Epoka UTC otwarcia bieżącej świecy.
            lookback_seconds: Długość okna w sekundach.

        Returns:
            Epokę otwarcia ostatniej świecy z sygnałem LONG/SHORT albo None.

        Raises:
            ValueError: Gdy czas lub długość okna są nieprawidłowe.
            mysql.connector.Error: Gdy zapytanie do bazy się nie powiedzie.
        """
        if (
            isinstance(source_timestamp, bool)
            or not isinstance(source_timestamp, int)
            or source_timestamp < 0
        ):
            raise ValueError("Czas otwarcia świecy musi być nieujemną epoką.")
        if (
            isinstance(lookback_seconds, bool)
            or not isinstance(lookback_seconds, int)
            or lookback_seconds < 1
        ):
            raise ValueError("Okno cooldownu musi być dodatnią liczbą sekund.")

        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT MAX(source_timestamp)
                    FROM signals
                    WHERE product = %s
                        AND algorithm_name = %s
                        AND `signal` IN ('long', 'short')
                        AND source_timestamp >= %s
                        AND source_timestamp < %s
                    """,
                    (
                        product,
                        algorithm_name,
                        source_timestamp - lookback_seconds,
                        source_timestamp,
                    ),
                )
                row = cursor.fetchone()
                if row is None or row[0] is None:
                    return None
                return int(row[0])
            finally:
                cursor.close()
        finally:
            connection.close()

    @staticmethod
    def _insert_signal(cursor: mysql.connector.cursor.MySQLCursor, signal: Signal) -> None:
        """Zapisuje sygnał z czasem zamknięcia świecy i jego pakietem JSON."""
        signal_payload = signal.to_payload()
        local_signal_time = signal.timestamp.astimezone(
            WARSAW_TIME_ZONE
        ).replace(tzinfo=None)
        cursor.execute(
            """
            INSERT INTO signals
                (`datetime`, product, price, `signal`, algorithm_name,
                 ai_decision, transaction_id, candles_count, signal_parameters)
            VALUES (%s, %s, %s, %s, %s, NULL, NULL, %s, %s)
            """,
            (
                local_signal_time,
                "EUR/USD",
                Decimal(str(signal_payload["price"])),
                signal.direction.lower(),
                STRATEGY_NAME,
                signal.candles_count,
                json.dumps(
                    signal_payload, allow_nan=False, separators=(",", ":")
                ),
            ),
        )
        cursor.execute(
            """
            INSERT INTO signal_potention (signal_id, average_spread)
            VALUES (%s, %s)
            """,
            (
                cursor.lastrowid,
                SignalPotentialCalculator.spread_for_product("EUR/USD"),
            ),
        )

    def _insert_grok45_signal(
        self,
        cursor: mysql.connector.cursor.MySQLCursor,
        signal: Grok45SignalRecord,
        algorithm_name: str = GROK45_V2_STRATEGY_NAME,
    ) -> bool:
        """Dodaje nowy sygnał Grok do bieżącej transakcji, pomijając duplikaty."""
        signal_timestamp = datetime.fromtimestamp(
            signal.timestamp, timezone.utc
        )
        close_timestamp = signal_timestamp + timedelta(minutes=1)
        local_signal_time = close_timestamp.astimezone(
            WARSAW_TIME_ZONE
        ).replace(tzinfo=None)
        signal_parameters = json.dumps(
            {
                "instrument": signal.instrument,
                "price": float(signal.price),
                "timestamp": int(close_timestamp.timestamp()),
                "signal": signal.signal,
                "parameters": signal.parameters,
            },
            allow_nan=False,
            separators=(",", ":"),
        )
        cursor.execute(
            "INSERT IGNORE INTO products (symbol) VALUES (%s)",
            (signal.instrument,),
        )
        utc_signal_time = signal_timestamp.replace(tzinfo=None)
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT 1
                FROM candles
                WHERE symbol = %s
                    AND DATE_SUB(
                        datetime, INTERVAL utc_offset_minutes MINUTE
                    ) <= %s
                ORDER BY DATE_SUB(
                    datetime, INTERVAL utc_offset_minutes MINUTE
                ) DESC
                LIMIT %s
            ) AS recent_candles
            """,
            (signal.instrument, utc_signal_time, GROK45_ANALYSIS_WINDOW),
        )
        candles_count = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO signals
                (`datetime`, product, price, `signal`, algorithm_name,
                 ai_decision, transaction_id, candles_count,
                 signal_parameters, source_timestamp)
            VALUES (%s, %s, %s, %s, %s, NULL, NULL, %s, %s, %s)
            ON DUPLICATE KEY UPDATE id = id
            """,
            (
                local_signal_time,
                signal.instrument,
                signal.price,
                signal.signal.lower(),
                algorithm_name,
                candles_count,
                signal_parameters,
                signal.timestamp,
            ),
        )
        signal_was_inserted = cursor.rowcount == 1
        if signal_was_inserted:
            cursor.execute(
                """
                INSERT INTO signal_potention (signal_id, average_spread)
                VALUES (%s, %s)
                """,
                (
                    cursor.lastrowid,
                    SignalPotentialCalculator.spread_for_product(
                        signal.instrument
                    ),
                ),
            )
        return signal_was_inserted

    def save_algorithm_signal(
        self,
        product: str,
        algorithm_name: str,
        signal: dict[str, object],
        candles_count: int,
        *,
        record_evaluation: bool = False,
        save_signal: bool = True,
        save_none_signal: bool = False,
        save_signal_potential: bool = True,
        enqueue_notification: bool = True,
    ) -> bool:
        """Zapisuje wynik strategii w jednej transakcji MySQL.

        Args:
            product: Symbol produktu zapisany w bazie.
            algorithm_name: Nazwa strategii.
            signal: Wynik strategii z parametrami JSON i czasem świecy.
            candles_count: Liczba świec wykorzystanych do analizy.
            record_evaluation: Czy trwale oznaczyć świecę jako ocenioną.
            save_signal: Czy zapisać rekord również w tabeli ``signals``.
            save_none_signal: Czy zapisać ``NONE`` zamiast tylko ewaluacji.
            save_signal_potential: Czy liczyć potencjał z skonfigurowanym spreadem.
            enqueue_notification: Czy po nowym zapisie przygotować dźwięk.

        Returns:
            ``True`` tylko wtedy, gdy powstał nowy rekord w ``signals``.

        Raises:
            ValueError: Gdy wynik lub wymagany sposób zapisu jest nieprawidłowy.
            RuntimeError: Gdy zapis spójnego outboxa nie powiedzie się logicznie.
            mysql.connector.Error: Gdy zapis do bazy się nie powiedzie.
        """
        direction = str(signal.get("signal", "")).upper()
        if direction not in {"LONG", "SHORT", "NONE"}:
            raise ValueError("Sygnał musi mieć wartość LONG, SHORT albo NONE.")
        if not record_evaluation and not save_signal:
            raise ValueError("Nie można pominąć sygnału bez zapisu ewaluacji.")
        price = signal.get("price")
        candle_open_epoch = signal.get("candle_open_epoch")
        close_time_value = signal.get("timestamp")
        parameters = signal.get("signal_parameters")
        if (
            price is None
            or candle_open_epoch is None
            or not isinstance(close_time_value, str)
            or not isinstance(parameters, dict)
        ):
            raise ValueError("Sygnał nie zawiera wymaganych pól do zapisu.")

        close_time = datetime.fromisoformat(
            close_time_value.replace("Z", "+00:00")
        )
        if close_time.tzinfo is None or close_time.utcoffset() is None:
            raise ValueError("Czas sygnału musi zawierać strefę czasową.")
        local_close_time = close_time.astimezone(WARSAW_TIME_ZONE).replace(
            tzinfo=None
        )
        source_timestamp = int(candle_open_epoch)
        serialized_parameters = json.dumps(
            parameters,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        potential_spread = (
            SignalPotentialCalculator.spread_for_product(product)
            if save_signal and direction != "NONE" and save_signal_potential
            else None
        )

        connection = mysql.connector.connect(**self.database_settings)
        cursor = None
        try:
            cursor = connection.cursor()
            evaluation_id = None
            if record_evaluation:
                cursor.execute(
                    """
                    INSERT IGNORE INTO algorithm_evaluations
                        (product, algorithm_name, source_timestamp, `signal`)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        product,
                        algorithm_name,
                        source_timestamp,
                        direction.lower(),
                    ),
                )
                if cursor.rowcount != 1:
                    connection.commit()
                    return False
                evaluation_id = cursor.lastrowid
                if evaluation_id is None:
                    connection.rollback()
                    raise RuntimeError(
                        "Nie udało się odczytać identyfikatora ewaluacji."
                    )

            inserted = False
            if save_signal and (direction != "NONE" or save_none_signal):
                cursor.execute(
                    """
                    INSERT IGNORE INTO signals
                        (`datetime`, product, price, `signal`, algorithm_name,
                         ai_decision, transaction_id, candles_count,
                         signal_parameters, source_timestamp)
                    VALUES (%s, %s, %s, %s, %s, NULL, NULL, %s, %s, %s)
                    """,
                    (
                        local_close_time,
                        product,
                        Decimal(str(price)),
                        direction.lower(),
                        algorithm_name,
                        max(0, int(candles_count)),
                        serialized_parameters,
                        source_timestamp,
                    ),
                )
                inserted = cursor.rowcount == 1
                if inserted and potential_spread is not None:
                    cursor.execute(
                        """
                        INSERT INTO signal_potention (signal_id, average_spread)
                        VALUES (%s, %s)
                        """,
                        (
                            cursor.lastrowid,
                            potential_spread,
                        ),
                    )
                if (
                    inserted
                    and evaluation_id is not None
                    and enqueue_notification
                    and direction != "NONE"
                ):
                    cursor.execute(
                        """
                        UPDATE algorithm_evaluations
                        SET notification_pending = TRUE
                        WHERE id = %s
                        """,
                        (evaluation_id,),
                    )
                    if cursor.rowcount != 1:
                        connection.rollback()
                        raise RuntimeError(
                            "Nie udało się dodać powiadomienia do outboxa."
                        )
            connection.commit()
            return inserted
        except mysql.connector.Error:
            connection.rollback()
            raise
        finally:
            if cursor is not None:
                cursor.close()
            connection.close()

    def fetch_usdmxn_notification_ids(self, limit: int) -> list[int]:
        """Pobiera oczekujące dźwięki dla wszystkich strategii USD/MXN.

        Args:
            limit: Maksymalna liczba zwracanych identyfikatorów.

        Returns:
            Identyfikatory oczekujące na odtworzenie, w kolejności rosnącej.

        Raises:
            ValueError: Gdy limit nie jest dodatnią liczbą całkowitą.
            mysql.connector.Error: Gdy odczyt bazy się nie powiedzie.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Limit powiadomień musi być dodatnią liczbą całkowitą.")
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT id
                    FROM algorithm_evaluations
                    WHERE product = %s AND notification_pending = TRUE
                    ORDER BY id ASC
                    LIMIT %s
                    """,
                    (USDMXN_DATABASE_SYMBOL, limit),
                )
                return [int(row[0]) for row in cursor.fetchall()]
            finally:
                cursor.close()
        finally:
            connection.close()

    def acknowledge_usdmxn_notification(self, notification_id: int) -> bool:
        """Potwierdza dźwięk dowolnej strategii USD/MXN i obsługuje retry.

        Args:
            notification_id: Identyfikator odebranego zdarzenia outboxa.

        Returns:
            ``True`` dla istniejącego zdarzenia, także już potwierdzonego.

        Raises:
            ValueError: Gdy identyfikator nie jest dodatnią liczbą całkowitą.
            mysql.connector.Error: Gdy aktualizacja bazy się nie powiedzie.
        """
        if (
            isinstance(notification_id, bool)
            or not isinstance(notification_id, int)
            or notification_id < 1
        ):
            raise ValueError("Identyfikator powiadomienia musi być dodatni.")
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE algorithm_evaluations
                    SET notification_pending = FALSE
                    WHERE id = %s AND product = %s
                        AND notification_pending = TRUE
                    """,
                    (notification_id, USDMXN_DATABASE_SYMBOL),
                )
                acknowledged = cursor.rowcount == 1
                if not acknowledged:
                    cursor.execute(
                        """
                        SELECT 1
                        FROM algorithm_evaluations
                        WHERE id = %s AND product = %s
                        LIMIT 1
                        """,
                        (notification_id, USDMXN_DATABASE_SYMBOL),
                    )
                    acknowledged = cursor.fetchone() is not None
                connection.commit()
                return acknowledged
            finally:
                cursor.close()
        except mysql.connector.Error:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fetch_usdjpy_notification_ids(self, limit: int) -> list[int]:
        """Pobiera oczekujące powiadomienia dla wszystkich strategii USD/JPY."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Limit powiadomień musi być dodatnią liczbą całkowitą.")
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT id
                    FROM algorithm_evaluations
                    WHERE product = %s AND notification_pending = TRUE
                    ORDER BY id ASC
                    LIMIT %s
                    """,
                    (USDJPY_DATABASE_SYMBOL, limit),
                )
                return [int(row[0]) for row in cursor.fetchall()]
            finally:
                cursor.close()
        finally:
            connection.close()

    def acknowledge_usdjpy_notification(self, notification_id: int) -> bool:
        """Potwierdza odtworzenie i umożliwia ponowienie po błędzie dźwięku."""
        if (
            isinstance(notification_id, bool)
            or not isinstance(notification_id, int)
            or notification_id < 1
        ):
            raise ValueError("Identyfikator powiadomienia musi być dodatni.")
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE algorithm_evaluations
                    SET notification_pending = FALSE
                    WHERE id = %s AND product = %s
                        AND notification_pending = TRUE
                    """,
                    (notification_id, USDJPY_DATABASE_SYMBOL),
                )
                acknowledged = cursor.rowcount == 1
                if not acknowledged:
                    cursor.execute(
                        """
                        SELECT 1
                        FROM algorithm_evaluations
                        WHERE id = %s AND product = %s
                        LIMIT 1
                        """,
                        (notification_id, USDJPY_DATABASE_SYMBOL),
                    )
                    acknowledged = cursor.fetchone() is not None
                connection.commit()
                return acknowledged
            finally:
                cursor.close()
        except mysql.connector.Error:
            connection.rollback()
            raise
        finally:
            connection.close()

    def save_grok45_signal(self, signal: Grok45SignalRecord) -> bool:
        """Zapisuje sygnał C++ Grok-45 i pomija duplikaty tej samej świecy."""
        connection = mysql.connector.connect(**self.database_settings)
        cursor = None
        try:
            cursor = connection.cursor()
            signal_was_saved = self._insert_grok45_signal(
                cursor, signal, algorithm_name=GROK45_CPP_STRATEGY_NAME
            )
            if signal_was_saved:
                self.signal_potential_calculator.update_pending(
                    cursor, {signal.instrument}
                )
            connection.commit()
            return signal_was_saved
        except mysql.connector.Error:
            connection.rollback()
            raise
        finally:
            if cursor is not None:
                cursor.close()
            connection.close()

    def fetch_missing_candle_times(
        self, symbol: str, limit: int
    ) -> tuple[bool, list[str], int]:
        """Rejestruje wszystkie znalezione luki i zwraca najnowsze do uzupełnienia."""
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT datetime, utc_offset_minutes
                    FROM candles
                    WHERE symbol = %s
                    ORDER BY datetime DESC, utc_offset_minutes DESC
                    """,
                    (symbol,),
                )
                stored_times = {
                    candle_datetime.replace(
                        tzinfo=timezone(timedelta(minutes=offset_minutes))
                    ).astimezone(timezone.utc)
                    for candle_datetime, offset_minutes in cursor.fetchall()
                }
            finally:
                cursor.close()
        finally:
            connection.close()

        if not stored_times:
            return False, [], 0

        latest_closed_minute = (
            datetime.now(timezone.utc).replace(second=0, microsecond=0)
            - timedelta(minutes=1)
        )
        upper_bound = latest_closed_minute
        missing_times = []
        for stored_time in sorted(stored_times, reverse=True):
            if stored_time > latest_closed_minute:
                continue
            current_minute = upper_bound
            while current_minute > stored_time:
                if (
                    current_minute not in stored_times
                    and self._is_market_open(symbol, current_minute)
                ):
                    missing_times.append(current_minute)
                current_minute -= timedelta(minutes=1)
            upper_bound = stored_time - timedelta(minutes=1)

        if missing_times:
            connection = mysql.connector.connect(**self.database_settings)
            try:
                cursor = connection.cursor()
                try:
                    self._upsert_candle_gaps(cursor, symbol, missing_times)
                    connection.commit()
                finally:
                    cursor.close()
            except mysql.connector.Error:
                connection.rollback()
                raise
            finally:
                connection.close()

        total_missing_count = len(missing_times)
        latest_missing_times = missing_times[:limit]
        latest_missing_times.reverse()
        return (
            True,
            [
                _format_api_timestamp(missing_time)
                for missing_time in latest_missing_times
            ],
            total_missing_count,
        )

    @staticmethod
    def _is_market_open(symbol: str, utc_time: datetime) -> bool:
        """Stosuje harmonogram przypisany do produktu zamiast kalendarza Forex."""
        return is_market_open(symbol, utc_time)

    def retry_fetch_missing_candles(
        self,
        symbol: str,
        limit: int = 50,
        max_retries: int = 3,
        retry_delay_seconds: int = 2,
    ) -> tuple[bool, list[str], int]:
        """
        Backend
        Pobiera brakujące świece z automatycznym retry.
        Zwraca tuple (has_missing, missing_times, total_count) z próbami ponowienia.
        """
        last_error = None
        for attempt in range(max_retries):
            try:
                has_missing, missing_times, total_count = self.fetch_missing_candle_times(
                    symbol, limit
                )
                if has_missing and missing_times:
                    print(
                        f"[Retry] {symbol}: próba {attempt + 1}/{max_retries} - "
                        f"znaleziono {len(missing_times)} brakujących świec"
                    )
                    return has_missing, missing_times, total_count
                else:
                    return False, [], 0
            except mysql.connector.Error as error:
                last_error = error
                if attempt < max_retries - 1:
                    print(
                        f"[Retry] {symbol}: błąd połączenia na próbie {attempt + 1}/{max_retries}. "
                        f"Czekam {retry_delay_seconds}s przed ponowną próbą: {error}"
                    )
                    # Sygnał dźwiękowy dla błędu połączenia
                    Thread(
                        target=play_alert,
                        kwargs={"number_of_beeps": 4, "frequency": 1500},
                        daemon=True,
                    ).start()
                    time.sleep(retry_delay_seconds)
                else:
                    logger.error(
                        "[Retry] %s: nie udało się po %s próbach: %s",
                        symbol,
                        max_retries,
                        error,
                    )
        
        # Zwracamy ostatni błąd lub pusty wynik
        if last_error:
            raise last_error
        return False, [], 0

    def fetch_candle_page(
        self, page: int
    ) -> tuple[list[tuple[str, str, str, str, str, str, str | None]], bool]:
        """Pobiera 20 świec od najnowszych i sygnalizuje, czy istnieje kolejna strona."""
        page_size = 20
        offset = page * page_size
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT
                        symbol,
                        datetime,
                        CAST(open AS CHAR),
                        CAST(high AS CHAR),
                        CAST(low AS CHAR),
                        CAST(close AS CHAR),
                        CAST(volume AS CHAR)
                    FROM candles
                    ORDER BY datetime DESC, symbol ASC, utc_offset_minutes DESC
                    LIMIT %s OFFSET %s
                    """,
                    (page_size + 1, offset),
                )
                rows = cursor.fetchall()
                has_next_page = len(rows) > page_size
                page_rows = rows[:page_size]
                return [
                    (
                        symbol,
                        _format_api_timestamp(candle_datetime),
                        open_price,
                        high_price,
                        low_price,
                        close_price,
                        volume if volume is not None else "",
                    )
                    for (
                        symbol,
                        candle_datetime,
                        open_price,
                        high_price,
                        low_price,
                        close_price,
                        volume,
                    ) in page_rows
                ], has_next_page
            finally:
                cursor.close()
        finally:
            connection.close()

    def fetch_filtered_candle_page(
        self, start_date: date, end_date: date, page: int
    ) -> tuple[list[tuple[str, str, str, str, str, str, str | None]], bool]:
        """Pobiera do 10 świec z datami włącznie i zwraca informację o kolejnej stronie."""
        page_size = 10
        offset = page * page_size
        end_exclusive = end_date + timedelta(days=1)
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT
                        symbol,
                        datetime,
                        CAST(open AS CHAR),
                        CAST(high AS CHAR),
                        CAST(low AS CHAR),
                        CAST(close AS CHAR),
                        CAST(volume AS CHAR)
                    FROM candles
                    WHERE datetime >= %s AND datetime < %s
                    ORDER BY datetime DESC, symbol ASC, utc_offset_minutes DESC
                    LIMIT %s OFFSET %s
                    """,
                    (start_date, end_exclusive, page_size + 1, offset),
                )
                rows = cursor.fetchall()
                has_next_page = len(rows) > page_size
                return [
                    (
                        symbol,
                        _format_api_timestamp(candle_datetime),
                        open_price,
                        high_price,
                        low_price,
                        close_price,
                        volume if volume is not None else "",
                    )
                    for (
                        symbol,
                        candle_datetime,
                        open_price,
                        high_price,
                        low_price,
                        close_price,
                        volume,
                    ) in rows[:page_size]
                ], has_next_page
            finally:
                cursor.close()
        finally:
            connection.close()

    def fetch_latest_analysis_candles(
        self, limit: int
    ) -> list[tuple[str, str, str, str, str, str]]:
        """Pobiera do 100 ostatnich świec EUR/USD w kolejności od najnowszej."""
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT
                        datetime,
                        utc_offset_minutes,
                        CAST(open AS CHAR),
                        CAST(high AS CHAR),
                        CAST(low AS CHAR),
                        CAST(close AS CHAR),
                        CAST(volume AS CHAR)
                    FROM candles
                    WHERE symbol = 'EUR/USD'
                    ORDER BY DATE_SUB(
                        datetime, INTERVAL utc_offset_minutes MINUTE
                    ) DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cursor.fetchall()
                analysis_rows = []
                for (
                    candle_datetime,
                    offset_minutes,
                    open_price,
                    high_price,
                    low_price,
                    close_price,
                    volume,
                ) in rows:
                    utc_datetime = candle_datetime.replace(
                        tzinfo=timezone(timedelta(minutes=offset_minutes))
                    ).astimezone(timezone.utc)
                    analysis_rows.append(
                        (
                            str(int(utc_datetime.timestamp())),
                            open_price,
                            high_price,
                            low_price,
                            close_price,
                            volume if volume is not None else "",
                        )
                    )
                return analysis_rows
            finally:
                cursor.close()
        finally:
            connection.close()

    def fetch_candle_gap_page(
        self, page: int
    ) -> tuple[list[tuple[str, str, str, str, str | None]], bool]:
        """Pobiera stronę luk świecowych, od najnowszego oczekiwanego czasu."""
        page_size = 20
        offset = page * page_size
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT
                        symbol,
                        candle_datetime_utc,
                        status,
                        detected_at_utc,
                        filled_at_utc
                    FROM candle_gaps
                    ORDER BY candle_datetime_utc DESC, symbol ASC
                    LIMIT %s OFFSET %s
                    """,
                    (page_size + 1, offset),
                )
                rows = cursor.fetchall()
                has_next_page = len(rows) > page_size
                return [
                    (
                        symbol,
                        _format_api_timestamp(candle_datetime_utc),
                        status,
                        _format_api_timestamp(detected_at_utc),
                        (
                            _format_api_timestamp(filled_at_utc)
                            if filled_at_utc is not None
                            else None
                        ),
                    )
                    for (
                        symbol,
                        candle_datetime_utc,
                        status,
                        detected_at_utc,
                        filled_at_utc,
                    ) in rows[:page_size]
                ], has_next_page
            finally:
                cursor.close()
        finally:
            connection.close()

    def grok_sp500_notification_cursor(self) -> int:
        """Zwraca bieżące maksimum ID Groka ES albo 0; błędy MySQL są propagowane."""
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """SELECT COALESCE(MAX(id), 0) FROM signals
                       WHERE product = %s AND BINARY algorithm_name = %s""",
                    ("ES", "Grok-45/S&P500"),
                )
                return int(cursor.fetchone()[0])
            finally:
                cursor.close()
        finally:
            connection.close()

    def grok_sp500_notification_ids(self, after_id: int, limit: int) -> tuple[list[int], bool]:
        """Pobiera do limit ID powiadomień po after_id i flagę kolejnej strony.

        Nie zmienia danych; błędy połączenia i zapytania są propagowane.
        """
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """SELECT id FROM signals
                       WHERE product = %s AND BINARY algorithm_name = %s
                         AND id > %s
                         AND JSON_EXTRACT(signal_parameters, '$.audio_notification')
                             = CAST('true' AS JSON)
                       ORDER BY id ASC LIMIT %s""",
                    ("ES", "Grok-45/S&P500", after_id, limit + 1),
                )
                rows = cursor.fetchall()
                return [int(row[0]) for row in rows[:limit]], len(rows) > limit
            finally:
                cursor.close()
        finally:
            connection.close()

    def fetch_signal_page(
        self, page: int
    ) -> tuple[list[tuple[str, ...]], bool]:
        """Pobiera sygnały, decyzję AI i wynik potencjału po 20 świecach."""
        page_size = 15
        offset = page * page_size
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT
                        s.id,
                        s.`datetime`,
                        s.product,
                        s.`signal`,
                        s.algorithm_name,
                        CASE
                            WHEN COALESCE(ai_summary.accept_count, 0) > 0
                                THEN 'ZGODA'
                            WHEN verification_job.status = 'completed'
                                AND ai_summary.transaction_count > 0
                                AND ai_summary.complete_transaction_count =
                                    ai_summary.transaction_count
                                AND ai_summary.decision_count > 0
                                AND ai_summary.decline_count =
                                    ai_summary.decision_count
                                THEN 'ODRZUCENIE'
                            ELSE 'OCZEKUJE'
                        END,
                        COALESCE(CAST(s.transaction_id AS CHAR), '--'),
                        s.candles_count,
                        CAST(s.signal_parameters AS CHAR),
                        COALESCE(CAST(p.`100zl_deal_20` AS CHAR), '')
                    FROM signals AS s
                    LEFT JOIN signal_potention AS p ON p.signal_id = s.id
                    LEFT JOIN (
                        SELECT
                            t.signal_id,
                            COUNT(DISTINCT t.id) AS transaction_count,
                            COUNT(DISTINCT CASE
                                WHEN ad.ai_decision IN ('accept', 'decline')
                                    THEN t.id
                            END) AS complete_transaction_count,
                            COUNT(ad.id) AS decision_count,
                            SUM(CASE
                                WHEN ad.ai_decision = 'accept' THEN 1
                                ELSE 0
                            END) AS accept_count,
                            SUM(CASE
                                WHEN ad.ai_decision = 'decline' THEN 1
                                ELSE 0
                            END) AS decline_count
                        FROM transactions AS t
                        LEFT JOIN ai_decisions AS ad
                            ON ad.transaction_id = t.id
                        GROUP BY t.signal_id
                    ) AS ai_summary ON ai_summary.signal_id = s.id
                    LEFT JOIN ai_verification_jobs AS verification_job
                        ON verification_job.signal_id = s.id
                    ORDER BY s.`datetime` DESC, s.id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (page_size + 1, offset),
                )
                rows = cursor.fetchall()
                has_next_page = len(rows) > page_size
                return [
                    (
                        str(signal_id),
                        _format_api_timestamp(signal_datetime),
                        product,
                        signal_direction,
                        algorithm_name,
                        ai_decision,
                        transaction_id,
                        str(candles_count),
                        signal_parameters,
                        max_profit_after_20_minutes,
                    )
                    for (
                        signal_id,
                        signal_datetime,
                        product,
                        signal_direction,
                        algorithm_name,
                        ai_decision,
                        transaction_id,
                        candles_count,
                        signal_parameters,
                        max_profit_after_20_minutes,
                    ) in rows[:page_size]
                ], has_next_page
            finally:
                cursor.close()
        finally:
            connection.close()

    def fetch_signal_potential(
        self, signal_id: int
    ) -> tuple[str, ...] | None:
        """Pobiera wyniki dla okien 20 i 60 minut wskazanego sygnału."""
        connection = mysql.connector.connect(**self.database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT
                        CAST(saldo_20_aggr_candles AS CHAR),
                        CAST(best_candle_20_minute AS CHAR),
                        CAST(`100zl_deal_20` AS CHAR),
                        CAST(average_spread AS CHAR),
                        CAST(best_candle_60_datetime AS CHAR),
                        CAST(saldo_60_aggr_candles AS CHAR),
                        CAST(`100zl_deal_60` AS CHAR)
                    FROM signal_potention
                    WHERE signal_id = %s
                    """,
                    (signal_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                return tuple(
                    "" if value is None else str(value) for value in row
                )
            finally:
                cursor.close()
        finally:
            connection.close()

    # :)


repository = MarketDataRepository()
eurusd_strategy = EURUSDStrategyV1()
dax40_strategy = DAX40StrategyV1()
dax40_storage = DAX40Storage()
chat_dax40_strategy = ChatDAX40Strategy(market_is_open=is_market_open)
chat_dax40_storage = DAX40SignalStorage(repository)
usdmxn_strategy = USDMXNStrategyV1(market_is_open=is_market_open)
usdmxn_storage = USDMXNSignalStorage(repository)
usdjpy_strategy = USDJPYStrategyV1(market_is_open=is_market_open)
usdjpy_storage = USDJPYSignalStorage(repository)
grok45_usdmxn_strategy = Grok45USDMXNStrategy(market_is_open=is_market_open)
grok45_usdmxn_storage = Grok45USDMXNSignalStorage(repository)
btcusd_strategy = BTCUSDStrategyV1()
btcusd_storage = BTCUSDSignalStorage(repository)
strategy_lock = Lock()


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Przygotowuje tabele przed obsługą żądań i pobiera brakujące świece."""
    configured_log_directory = os.environ.get("GIEDA_LOG_DIRECTORY")
    if configured_log_directory:
        log_directory = Path(configured_log_directory)
    else:
        source_file = Path(__file__).resolve()
        project_root = next(
            (
                parent
                for parent in source_file.parents
                if (parent / ".git").exists()
            ),
            source_file.parent,
        )
        log_directory = project_root / "logs"
    configure_error_file_logging(log_directory)

    repository.initialize()
    application.state.todo_repository = TodoRepository(
        repository.database_settings
    )
    
    # Backend - Pobieranie brakujących świec dla wszystkich monitorowanych symboli z retry
    monitored_symbols = get_monitored_symbols()
    print(f"[Startup] Pobieranie brakujących świec dla {len(monitored_symbols)} symboli...")
    for symbol in monitored_symbols:
        try:
            has_missing, missing_times, total_count = repository.retry_fetch_missing_candles(
                symbol, limit=50, max_retries=3, retry_delay_seconds=2
            )
            if has_missing and missing_times:
                print(
                    f"[Startup] {symbol}: znaleziono {total_count} brakujących świec, "
                    f"ładuję {len(missing_times)} najnowszych..."
                )
            else:
                print(f"[Startup] {symbol}: brak brakujących świec")
        except Exception:
            logger.exception(
                "[Startup] Błąd przy pobieraniu brakujących świec dla %s.",
                symbol,
            )
    
    with strategy_lock:
        candle_history = repository.fetch_candle_history("EUR/USD")
        eurusd_strategy.warm_up(candle_history)
        app.state.grok45_v2_candles = _grok45_candles_from_history(
            candle_history
        )
        app.state.strategy_ready = True
    print("[Startup] Aplikacja gotowa do obsługi żądań.")
    worker: GrokSP500Worker | None = None
    if is_monitored_product("ES"):
        worker = GrokSP500Worker()
        worker.start()
    else:
        print("[Startup] Pętla Grok S&P 500 pominięta: ES nie jest monitorowany.")
    application.state.grok_sp500_worker = worker
    ai_verification_worker = AIVerificationWorker(repository)
    ai_verification_worker.start()
    application.state.ai_verification_worker = ai_verification_worker
    try:
        yield
    finally:
        if worker is not None:
            worker.stop()
        ai_verification_worker.stop()


app = FastAPI(title="gie-da market data API", lifespan=lifespan)


class TodoTitleRequest(BaseModel):
    """Waliduje nazwę zadania głównego lub podrzędnego."""

    title: str

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        """Odrzuca nazwę pustą lub złożoną wyłącznie z białych znaków."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Nazwa zadania nie może być pusta.")
        return normalized


class TodoCompletionRequest(BaseModel):
    """Przechowuje nowy stan ukończenia podzadania."""

    completed: bool


class TodoMoveRequest(BaseModel):
    """Wskazuje kierunek przesunięcia zadania na liście."""

    direction: Literal["up", "down"]


class TodoTextRequest(BaseModel):
    """Przechowuje prompt albo opis bez limitu długości aplikacji."""

    value: str


class TodoWeeklyReviewRequest(BaseModel):
    """Przekazuje nazwy widoków zarejestrowanych przez aplikację C++."""

    view_names: list[str] = Field(min_length=1)


def _seconds_until_next_todo_review(now: datetime) -> int:
    """Wylicza czas do następnego poniedziałku o 06:00 w Warszawie."""
    if now.weekday() == 0 and now.hour < 6:
        next_review = now.replace(
            hour=6, minute=0, second=0, microsecond=0
        )
    else:
        days_until_monday = (7 - now.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        next_monday = now.date() + timedelta(days=days_until_monday)
        next_review = now.replace(
            year=next_monday.year,
            month=next_monday.month,
            day=next_monday.day,
            hour=6,
            minute=0,
            second=0,
            microsecond=0,
        )
    elapsed_seconds = (
        next_review.astimezone(timezone.utc)
        - now.astimezone(timezone.utc)
    ).total_seconds()
    return max(1, math.ceil(elapsed_seconds))


@app.get("/health")
def health() -> dict[str, str]:
    """Zwraca prostą odpowiedź kontrolną dla lokalnego API."""
    return {"status": "ok"}


@app.get("/api/todos", response_class=Response)
def list_todo_tasks(request: Request) -> Response:
    """Udostępnia zadania w bezpiecznym dla dowolnego tekstu formacie TSV."""
    try:
        tasks = request.app.state.todo_repository.list_tasks()
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się pobrać zadań To-do z bazy danych.",
        ) from error

    lines: list[str] = []
    for task in tasks:
        lines.append(
            "\t".join(
                (
                    "M",
                    str(task["id"]),
                    quote(task["title"], safe="-_.~"),
                    task["created_at"],
                    task["updated_at"],
                )
            )
        )
        for subtask in task["subtasks"]:
            lines.append(
                "\t".join(
                    (
                        "S",
                        str(subtask["id"]),
                        str(task["id"]),
                        quote(subtask["title"], safe="-_.~"),
                        "1" if subtask["completed"] else "0",
                        "1" if subtask["hidden"] else "0",
                        "1" if subtask["prompt_exists"] else "0",
                        "1" if subtask["description_exists"] else "0",
                        subtask["created_at"],
                        subtask["updated_at"],
                    )
                )
            )
    return Response(
        content="\n".join(lines),
        media_type="text/plain; charset=utf-8",
    )


@app.post("/api/todos/main", status_code=201)
def create_todo_main_task(
    payload: TodoTitleRequest, request: Request
) -> dict[str, int]:
    """Zapisuje nowe zadanie główne."""
    try:
        task_id = request.app.state.todo_repository.create_main_task(
            payload.title
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się zapisać zadania głównego.",
        ) from error
    return {"id": task_id}


@app.put("/api/todos/main/{main_task_id}/title")
def update_todo_main_task_title(
    main_task_id: int, payload: TodoTitleRequest, request: Request
) -> dict[str, bool]:
    """Zmienia nazwę istniejącego zadania głównego."""
    if main_task_id < 1:
        raise HTTPException(status_code=422, detail="Nieprawidłowe ID zadania.")
    try:
        updated = request.app.state.todo_repository.update_main_task_title(
            main_task_id, payload.title
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się zmienić nazwy zadania głównego.",
        ) from error
    if not updated:
        raise HTTPException(
            status_code=404, detail="Nie znaleziono zadania głównego."
        )
    return {"updated": True}


@app.post("/api/todos/main/{main_task_id}/subtasks", status_code=201)
def create_todo_subtask(
    main_task_id: int, payload: TodoTitleRequest, request: Request
) -> dict[str, int]:
    """Dodaje podzadanie wskazanemu zadaniu głównemu."""
    if main_task_id < 1:
        raise HTTPException(status_code=422, detail="Nieprawidłowe ID zadania.")
    try:
        subtask_id = request.app.state.todo_repository.create_subtask(
            main_task_id, payload.title
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się zapisać zadania podrzędnego.",
        ) from error
    if subtask_id is None:
        raise HTTPException(
            status_code=404, detail="Nie znaleziono zadania głównego."
        )
    return {"id": subtask_id}


@app.put("/api/todos/subtasks/{subtask_id}/title")
def update_todo_subtask_title(
    subtask_id: int, payload: TodoTitleRequest, request: Request
) -> dict[str, bool]:
    """Zmienia nazwę istniejącego podzadania."""
    if subtask_id < 1:
        raise HTTPException(status_code=422, detail="Nieprawidłowe ID zadania.")
    try:
        updated = request.app.state.todo_repository.update_subtask_title(
            subtask_id, payload.title
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się zmienić nazwy zadania podrzędnego.",
        ) from error
    if not updated:
        raise HTTPException(
            status_code=404, detail="Nie znaleziono zadania podrzędnego."
        )
    return {"updated": True}


@app.patch("/api/todos/subtasks/{subtask_id}/completion")
def set_todo_subtask_completion(
    subtask_id: int, payload: TodoCompletionRequest, request: Request
) -> dict[str, bool]:
    """Zapisuje stan podzadania; stan rodzica pozostaje wyliczany."""
    if subtask_id < 1:
        raise HTTPException(status_code=422, detail="Nieprawidłowe ID zadania.")
    try:
        updated = request.app.state.todo_repository.set_subtask_completed(
            subtask_id, payload.completed
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się zmienić stanu zadania podrzędnego.",
        ) from error
    if not updated:
        raise HTTPException(
            status_code=404, detail="Nie znaleziono zadania podrzędnego."
        )
    return {"completed": payload.completed}


@app.post("/api/todos/main/{main_task_id}/hide")
def hide_todo_main_task(
    main_task_id: int, request: Request
) -> dict[str, bool]:
    """Ukrywa zadanie główne w widoku, zachowując je w bazie."""
    if main_task_id < 1:
        raise HTTPException(status_code=422, detail="Nieprawidłowe ID zadania.")
    try:
        hidden = request.app.state.todo_repository.hide_main_task(main_task_id)
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się ukryć zadania głównego.",
        ) from error
    if not hidden:
        raise HTTPException(
            status_code=404, detail="Nie znaleziono zadania głównego."
        )
    return {"hidden": True}


@app.post("/api/todos/subtasks/{subtask_id}/hide")
def hide_todo_subtask(subtask_id: int, request: Request) -> dict[str, bool]:
    """Ukrywa podzadanie w widoku, zachowując je dla statusu rodzica."""
    if subtask_id < 1:
        raise HTTPException(status_code=422, detail="Nieprawidłowe ID zadania.")
    try:
        hidden = request.app.state.todo_repository.hide_subtask(subtask_id)
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się ukryć zadania podrzędnego.",
        ) from error
    if not hidden:
        raise HTTPException(
            status_code=404, detail="Nie znaleziono zadania podrzędnego."
        )
    return {"hidden": True}


@app.post("/api/todos/main/{main_task_id}/move")
def move_todo_main_task(
    main_task_id: int, payload: TodoMoveRequest, request: Request
) -> dict[str, bool]:
    """Przesuwa zadanie główne względem innych widocznych zadań głównych."""
    if main_task_id < 1:
        raise HTTPException(status_code=422, detail="Nieprawidłowe ID zadania.")
    try:
        moved = request.app.state.todo_repository.move_main_task(
            main_task_id, payload.direction
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się zmienić kolejności zadań głównych.",
        ) from error
    if moved is None:
        raise HTTPException(
            status_code=404, detail="Nie znaleziono widocznego zadania głównego."
        )
    return {"moved": moved}


@app.post("/api/todos/subtasks/{subtask_id}/move")
def move_todo_subtask(
    subtask_id: int, payload: TodoMoveRequest, request: Request
) -> dict[str, bool]:
    """Przesuwa podzadanie wyłącznie w obrębie jego zadania głównego."""
    if subtask_id < 1:
        raise HTTPException(status_code=422, detail="Nieprawidłowe ID zadania.")
    try:
        moved = request.app.state.todo_repository.move_subtask(
            subtask_id, payload.direction
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się zmienić kolejności zadań podrzędnych.",
        ) from error
    if moved is None:
        raise HTTPException(
            status_code=404,
            detail="Nie znaleziono widocznego zadania podrzędnego.",
        )
    return {"moved": moved}


def _read_todo_subtask_text(
    request: Request, subtask_id: int, field: str
) -> Response:
    """Zwraca treść promptu lub opisu dopiero na żądanie użytkownika."""
    try:
        value = request.app.state.todo_repository.get_subtask_text(
            subtask_id, field
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się pobrać treści zadania podrzędnego.",
        ) from error
    if value is None:
        raise HTTPException(
            status_code=404, detail="Nie znaleziono zadania podrzędnego."
        )
    return Response(content=value, media_type="text/plain; charset=utf-8")


def _update_todo_subtask_text(
    request: Request, subtask_id: int, field: str, value: str
) -> dict[str, bool]:
    """Zapisuje prompt albo opis w rekordzie podzadania."""
    try:
        updated = request.app.state.todo_repository.update_subtask_text(
            subtask_id, field, value
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się zapisać treści zadania podrzędnego.",
        ) from error
    if not updated:
        raise HTTPException(
            status_code=404, detail="Nie znaleziono zadania podrzędnego."
        )
    return {"saved": True}


@app.get("/api/todos/subtasks/{subtask_id}/prompt", response_class=Response)
def get_todo_subtask_prompt(subtask_id: int, request: Request) -> Response:
    """Pobiera prompt wybranego podzadania."""
    if subtask_id < 1:
        raise HTTPException(status_code=422, detail="Nieprawidłowe ID zadania.")
    return _read_todo_subtask_text(request, subtask_id, "prompt")


@app.put("/api/todos/subtasks/{subtask_id}/prompt")
def update_todo_subtask_prompt(
    subtask_id: int, payload: TodoTextRequest, request: Request
) -> dict[str, bool]:
    """Tworzy lub aktualizuje prompt wybranego podzadania."""
    if subtask_id < 1:
        raise HTTPException(status_code=422, detail="Nieprawidłowe ID zadania.")
    return _update_todo_subtask_text(
        request, subtask_id, "prompt", payload.value
    )


@app.get(
    "/api/todos/subtasks/{subtask_id}/description", response_class=Response
)
def get_todo_subtask_description(
    subtask_id: int, request: Request
) -> Response:
    """Pobiera opis techniczny wybranego podzadania."""
    if subtask_id < 1:
        raise HTTPException(status_code=422, detail="Nieprawidłowe ID zadania.")
    return _read_todo_subtask_text(request, subtask_id, "description")


@app.put("/api/todos/subtasks/{subtask_id}/description")
def update_todo_subtask_description(
    subtask_id: int, payload: TodoTextRequest, request: Request
) -> dict[str, bool]:
    """Tworzy lub aktualizuje opis wybranego podzadania."""
    if subtask_id < 1:
        raise HTTPException(status_code=422, detail="Nieprawidłowe ID zadania.")
    return _update_todo_subtask_text(
        request, subtask_id, "description", payload.value
    )


@app.delete("/api/todos/main/{main_task_id}", status_code=204)
def delete_todo_main_task(main_task_id: int, request: Request) -> None:
    """Trwale usuwa zadanie główne oraz jego podzadania przez CASCADE."""
    if main_task_id < 1:
        raise HTTPException(status_code=422, detail="Nieprawidłowe ID zadania.")
    try:
        deleted = request.app.state.todo_repository.delete_main_task(
            main_task_id
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się usunąć zadania głównego.",
        ) from error
    if not deleted:
        raise HTTPException(
            status_code=404, detail="Nie znaleziono zadania głównego."
        )


@app.delete("/api/todos/subtasks/{subtask_id}", status_code=204)
def delete_todo_subtask(subtask_id: int, request: Request) -> None:
    """Trwale usuwa podzadanie oraz zapisany w nim prompt i opis."""
    if subtask_id < 1:
        raise HTTPException(status_code=422, detail="Nieprawidłowe ID zadania.")
    try:
        deleted = request.app.state.todo_repository.delete_subtask(subtask_id)
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się usunąć zadania podrzędnego.",
        ) from error
    if not deleted:
        raise HTTPException(
            status_code=404, detail="Nie znaleziono zadania podrzędnego."
        )


@app.post("/api/todos/weekly-review")
def create_weekly_todo_review(
    payload: TodoWeeklyReviewRequest, request: Request
) -> dict[str, bool | int | str | None]:
    """Tworzy zadanie dla bieżącego tygodnia w strefie Europe/Warsaw."""
    local_now = datetime.now(WARSAW_TIME_ZONE)
    if local_now.weekday() == 0 and local_now.hour < 6:
        return {
            "created": False,
            "week_key": None,
            "next_check_seconds": _seconds_until_next_todo_review(local_now),
        }

    iso_year, iso_week, _ = local_now.isocalendar()
    week_key = f"{iso_year:04d}-W{iso_week:02d}"
    try:
        created = request.app.state.todo_repository.create_weekly_review(
            week_key, payload.view_names
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się utworzyć cotygodniowego zadania.",
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się potwierdzić cotygodniowego zadania.",
        ) from error
    return {
        "created": created,
        "week_key": week_key,
        "next_check_seconds": _seconds_until_next_todo_review(
            datetime.now(WARSAW_TIME_ZONE)
        ),
    }


@app.get("/api/ai-verification/jobs")
def ai_verification_jobs(
    request: Request,
    status: Literal[
        "pending", "processing", "completed", "failed", "skipped"
    ] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Udostępnia statusy weryfikacji i szczegóły błędów asynchronicznych."""
    database_settings = request.app.state.repository.database_settings
    try:
        connection = mysql.connector.connect(**database_settings)
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się połączyć z bazą statusów weryfikacji AI.",
        ) from error
    try:
        cursor = connection.cursor()
        try:
            if status is None:
                cursor.execute(
                    """
                    SELECT signal_id, status, last_error,
                           created_at_utc, updated_at_utc
                    FROM ai_verification_jobs
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            else:
                cursor.execute(
                    """
                    SELECT signal_id, status, last_error,
                           created_at_utc, updated_at_utc
                    FROM ai_verification_jobs
                    WHERE status = %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (status, limit),
                )
            rows = cursor.fetchall()
        except mysql.connector.Error as error:
            raise HTTPException(
                status_code=503,
                detail="Nie udało się pobrać statusów weryfikacji AI.",
            ) from error
        finally:
            cursor.close()
    finally:
        connection.close()
    return {
        "jobs": [
            {
                "signal_id": row[0],
                "status": row[1],
                "last_error": row[2],
                "created_at_utc": row[3],
                "updated_at_utc": row[4],
            }
            for row in rows
        ]
    }


@app.post("/api/ai-verification/jobs/{signal_id}/retry")
def retry_ai_verification_job(
    signal_id: int,
    request: Request,
) -> dict[str, Any]:
    """Ponownie kolejkuje wyłącznie zadanie zakończone błędem lub pominięte."""
    database_settings = request.app.state.repository.database_settings
    try:
        connection = mysql.connector.connect(**database_settings)
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się połączyć z kolejką weryfikacji AI.",
        ) from error
    try:
        cursor = connection.cursor()
        try:
            connection.start_transaction()
            cursor.execute(
                """
                SELECT status
                FROM ai_verification_jobs
                WHERE signal_id = %s
                FOR UPDATE
                """,
                (signal_id,),
            )
            job = cursor.fetchone()
            if job is None:
                connection.rollback()
                raise HTTPException(
                    status_code=404,
                    detail="Nie znaleziono zadania dla podanego sygnału.",
                )
            if job[0] not in {"failed", "skipped"}:
                connection.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="Ponowić można tylko zadanie failed albo skipped.",
                )
            cursor.execute(
                """
                UPDATE ai_verification_jobs
                SET status = 'pending', last_error = NULL,
                    updated_at_utc = UTC_TIMESTAMP(6)
                WHERE signal_id = %s
                """,
                (signal_id,),
            )
            connection.commit()
        except mysql.connector.Error as error:
            connection.rollback()
            raise HTTPException(
                status_code=503,
                detail="Nie udało się ponownie zakolejkować weryfikacji AI.",
            ) from error
        finally:
            cursor.close()
    finally:
        connection.close()
    return {"signal_id": signal_id, "status": "pending"}


@app.get("/api/products/monitored", response_class=Response)
def monitored_products() -> Response:
    """Udostępnia monitorowi aktywne symbole bez powielania ich w C++."""
    return Response(
        content="\n".join(get_monitored_symbols()),
        media_type="text/plain; charset=utf-8",
    )


@app.get("/api/daily-sentences", response_class=Response)
def daily_sentence_pool(request: Request) -> Response:
    """Udostępnia pulę sentencji do losowania po stronie aplikacji C++."""
    try:
        sentences = fetch_daily_sentences(
            request.app.state.repository.database_settings
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się pobrać puli sentencji dnia.",
        ) from error
    return Response(
        content="\n".join(sentences),
        media_type="text/plain; charset=utf-8",
    )


@app.get("/api/products/open", response_class=Response)
def open_products() -> Response:
    """Udostępnia tylko instrumenty z otwartą sesją, oszczędzając zapytania."""
    return Response(
        content="\n".join(get_open_symbols(datetime.now(timezone.utc))),
        media_type="text/plain; charset=utf-8",
    )


def _process_closed_strategy_candle(
    request: Request,
    candle: CandleRecord,
    now_utc: datetime,
    *,
    enqueue_notifications: bool,
) -> None:
    """Ocenia zamkniętą świecę dla strategii USD/MXN, USD/JPY lub BTC/USD.

    Args:
        request: Żądanie API z repozytorium i strategiami w stanie aplikacji.
        candle: Zapisana świeca M1.
        now_utc: Bieżący czas UTC używany do sprawdzenia, czy świeca jest zamknięta.
        enqueue_notifications: Czy nowe sygnały mogą dodać powiadomienie do outboxa.

    Raises:
        mysql.connector.Error: Gdy odczyt lub zapis w bazie się nie powiedzie.
        RuntimeError: Gdy historia strategii nie zawiera ocenianej świecy.
        ValueError: Gdy strategia odrzuci dane świecy lub wynik.
    """
    repository = request.app.state.repository
    candle_timestamp = candle.datetime.astimezone(timezone.utc)
    if (
        candle.symbol
        not in {
            USDMXN_DATABASE_SYMBOL,
            USDJPY_DATABASE_SYMBOL,
            BTCUSD_DATABASE_SYMBOL,
        }
        or candle_timestamp + timedelta(minutes=1) > now_utc
        or not repository._is_market_open(candle.symbol, candle_timestamp)
    ):
        return

    if candle.symbol == BTCUSD_DATABASE_SYMBOL:
        state_prefix = "grok45_btcusd"
        source_timestamp = int(candle_timestamp.timestamp())
        last_processed = getattr(
            request.app.state,
            f"{state_prefix}_last_processed_timestamp",
            None,
        )
        if last_processed is not None and candle_timestamp <= last_processed:
            return
        if repository.has_algorithm_evaluation(
            BTCUSD_DATABASE_SYMBOL,
            BTCUSD_STRATEGY_NAME,
            source_timestamp,
        ):
            setattr(
                request.app.state,
                f"{state_prefix}_last_processed_timestamp",
                candle_timestamp,
            )
            return

        candles = repository.fetch_recent_candle_history(
            BTCUSD_DATABASE_SYMBOL,
            BTCUSD_HISTORY_LIMIT,
            candle_timestamp,
        )
        if not candles or candles[-1]["timestamp_utc"] != candle_timestamp:
            logger.error(
                "Historia BTC/USD nie zawiera właśnie zapisanej świecy %s.",
                candle_timestamp.isoformat(),
            )
            return

        strategy = getattr(
            request.app.state,
            f"{state_prefix}_strategy",
            btcusd_strategy,
        )
        storage = getattr(
            request.app.state,
            f"{state_prefix}_storage",
            btcusd_storage,
        )
        result = strategy.evaluate(candles, now=now_utc)
        if str(result.get("signal", "")).upper() in {"LONG", "SHORT"}:
            last_signal_timestamp = (
                repository.fetch_recent_algorithm_signal_timestamp(
                    BTCUSD_DATABASE_SYMBOL,
                    BTCUSD_STRATEGY_NAME,
                    source_timestamp,
                    BTCUSD_COOLDOWN_SECONDS,
                )
            )
            if last_signal_timestamp is not None:
                result = strategy.evaluate(
                    candles,
                    now=now_utc,
                    last_signal_timestamp=last_signal_timestamp,
                )
        storage.save(
            result,
            len(candles),
            enqueue_notification=(
                enqueue_notifications and BTCUSD_BEEP_ENABLED
            ),
        )
        setattr(
            request.app.state,
            f"{state_prefix}_last_processed_timestamp",
            candle_timestamp,
        )
        return

    if candle.symbol == USDMXN_DATABASE_SYMBOL:
        strategy_specs = (
            (
                "usdmxn",
                USDMXN_STRATEGY_NAME,
                USDMXN_HISTORY_LIMIT,
                usdmxn_strategy,
                usdmxn_storage,
                USDMXN_BEEP_ENABLED,
            ),
            (
                "grok45_usdmxn",
                GROK45_USDMXN_STRATEGY_NAME,
                GROK45_USDMXN_HISTORY_LIMIT,
                grok45_usdmxn_strategy,
                grok45_usdmxn_storage,
                GROK45_USDMXN_BEEP_ENABLED,
            ),
        )
        pending_strategies = []
        source_timestamp = int(candle_timestamp.timestamp())
        for (
            state_prefix,
            algorithm_name,
            history_limit,
            default_strategy,
            default_storage,
            beep_enabled,
        ) in strategy_specs:
            last_processed = getattr(
                request.app.state,
                f"{state_prefix}_last_processed_timestamp",
                None,
            )
            if last_processed is not None and candle_timestamp <= last_processed:
                continue
            if repository.has_algorithm_evaluation(
                USDMXN_DATABASE_SYMBOL,
                algorithm_name,
                source_timestamp,
            ):
                setattr(
                    request.app.state,
                    f"{state_prefix}_last_processed_timestamp",
                    candle_timestamp,
                )
                continue
            pending_strategies.append(
                (
                    state_prefix,
                    history_limit,
                    getattr(
                        request.app.state,
                        f"{state_prefix}_strategy",
                        default_strategy,
                    ),
                    getattr(
                        request.app.state,
                        f"{state_prefix}_storage",
                        default_storage,
                    ),
                    beep_enabled,
                )
            )

        if not pending_strategies:
            return
        history_limit = max(spec[1] for spec in pending_strategies)
        candles = repository.fetch_recent_candle_history(
            USDMXN_DATABASE_SYMBOL,
            history_limit,
            candle_timestamp,
        )
        if not candles or candles[-1]["timestamp_utc"] != candle_timestamp:
            logger.error(
                "Historia USD/MXN nie zawiera właśnie zapisanej świecy %s; "
                "pominięto obie ewaluacje.",
                candle_timestamp.isoformat(),
            )
            return

        strategy_error = None
        for (
            state_prefix,
            _history_limit,
            strategy,
            storage,
            beep_enabled,
        ) in pending_strategies:
            try:
                result = strategy.evaluate(candles, now=now_utc)
                storage.save(
                    result,
                    len(candles),
                    enqueue_notification=(
                        enqueue_notifications and beep_enabled
                    ),
                )
                setattr(
                    request.app.state,
                    f"{state_prefix}_last_processed_timestamp",
                    candle_timestamp,
                )
            except (mysql.connector.Error, ValueError) as error:
                logger.exception(
                    "Nie udało się wykonać strategii %s dla świecy USD/MXN %s.",
                    state_prefix,
                    candle_timestamp.isoformat(),
                )
                if strategy_error is None:
                    strategy_error = error
        if strategy_error is not None:
            raise strategy_error
        return

    state_prefix = "usdjpy"
    last_processed = getattr(
        request.app.state,
        f"{state_prefix}_last_processed_timestamp",
        None,
    )
    if last_processed is not None and candle_timestamp <= last_processed:
        return
    source_timestamp = int(candle_timestamp.timestamp())
    strategy = getattr(request.app.state, f"{state_prefix}_strategy", usdjpy_strategy)
    storage = getattr(request.app.state, f"{state_prefix}_storage", usdjpy_storage)
    if repository.has_algorithm_evaluation(
        USDJPY_DATABASE_SYMBOL,
        USDJPY_STRATEGY_NAME,
        source_timestamp,
    ):
        setattr(
            request.app.state,
            f"{state_prefix}_last_processed_timestamp",
            candle_timestamp,
        )
        return

    candles = repository.fetch_recent_candle_history(
        USDJPY_DATABASE_SYMBOL,
        USDJPY_HISTORY_LIMIT,
        candle_timestamp,
    )
    if not candles or candles[-1]["timestamp_utc"] != candle_timestamp:
        logger.error(
            "Historia USD/JPY nie zawiera właśnie zapisanej świecy %s.",
            candle_timestamp.isoformat(),
        )
        raise RuntimeError("Historia USD/JPY nie zawiera świecy do ewaluacji.")
    result = strategy.evaluate(candles, now=now_utc)
    storage.save(
        result,
        len(candles),
        enqueue_notification=enqueue_notifications and USDJPY_BEEP_ENABLED,
    )
    setattr(
        request.app.state,
        f"{state_prefix}_last_processed_timestamp",
        candle_timestamp,
    )


@app.post("/api/candles", response_class=Response)
def save_candle(candle: CandleRecord, request: Request) -> Response:
    """Zapisuje świecę i zwraca liczbę nowo wykrytych brakujących minut."""
    if not is_monitored_product(candle.symbol):
        raise HTTPException(
            status_code=422,
            detail=f"Instrument {candle.symbol} nie jest skonfigurowany.",
        )
    if not request.app.state.repository._is_market_open(
        candle.symbol,
        candle.datetime.astimezone(timezone.utc)
    ):
        return Response(content="0\n0", media_type="text/plain; charset=utf-8")

    xauusd_error = False
    claude_xauusd_error = False
    claude_usdjpy_error = False
    try:
        with strategy_lock:
            if not request.app.state.strategy_ready:
                recovered_strategy = copy.deepcopy(
                    request.app.state.eurusd_strategy
                )
                candle_history = (
                    request.app.state.repository.fetch_candle_history(
                        "EUR/USD"
                    )
                )
                recovered_strategy.warm_up(candle_history)
                request.app.state.eurusd_strategy = recovered_strategy
                request.app.state.grok45_v2_candles = (
                    _grok45_candles_from_history(candle_history)
                )
                request.app.state.strategy_ready = True
            current_strategy = request.app.state.eurusd_strategy
            updated_strategy = copy.deepcopy(current_strategy)
            signal = None
            updated_grok45_v2_candles = (
                request.app.state.grok45_v2_candles
            )
            grok45_v2_signal = None
            if candle.symbol == "EUR/USD":
                candle_timestamp = candle.datetime.astimezone(timezone.utc)
                signal = updated_strategy.process_candle(
                    Candle(
                        timestamp=candle_timestamp,
                        open=float(candle.open),
                        high=float(candle.high),
                        low=float(candle.low),
                        close=float(candle.close),
                        volume=(
                            float(candle.volume)
                            if candle.volume is not None
                            else None
                        ),
                    )
                )
                current_minute = datetime.now(timezone.utc).replace(
                    second=0, microsecond=0
                )
                if (
                    candle_timestamp < current_minute
                    and (
                        not updated_grok45_v2_candles
                        or candle_timestamp.timestamp()
                        > updated_grok45_v2_candles[-1].timestamp
                    )
                ):
                    updated_grok45_v2_candles = [
                        *updated_grok45_v2_candles,
                        Grok45Candle(
                            timestamp=int(candle_timestamp.timestamp()),
                            open=float(candle.open),
                            high=float(candle.high),
                            low=float(candle.low),
                            close=float(candle.close),
                            volume=(
                                float(candle.volume)
                                if candle.volume is not None
                                else 0.0
                            ),
                        ),
                    ][-GROK45_ANALYSIS_WINDOW:]
                    grok45_v2_signal = _generate_grok45_v2_signal(
                        updated_grok45_v2_candles
                    )
            new_gap_count, saved_candle_count, _, signal_save_count = (
                request.app.state.repository.save_candle(
                    candle, signal, grok45_v2_signal
                )
            )
            signal_save_count = int(signal_save_count)
            if (
                candle.symbol == USDJPY_DATABASE_SYMBOL
                and saved_candle_count
                and candle.datetime.astimezone(timezone.utc)
                    + timedelta(minutes=1)
                    <= datetime.now(timezone.utc)
            ):
                try:
                    claude_usdjpy_result = _run_claude_usdjpy_strategy(
                        candle.datetime.astimezone(timezone.utc)
                    )
                    if claude_usdjpy_result is not None:
                        claude_usdjpy_signal, sound_enabled = (
                            claude_usdjpy_result
                        )
                        if (
                            claude_usdjpy_signal in {"LONG", "SHORT"}
                            and sound_enabled
                        ):
                            signal_save_count += 1
                except ClaudeUSDJPYStrategyError:
                    logger.exception(
                        "Błąd strategii Claude USD/JPY po zapisaniu świecy."
                    )
                    claude_usdjpy_error = True
            if candle.symbol == "GER30":
                candle_timestamp = candle.datetime.astimezone(timezone.utc)
                current_minute = datetime.now(timezone.utc).replace(
                    second=0, microsecond=0
                )
                last_dax_candle = getattr(
                    request.app.state, "dax40_last_processed_timestamp", None
                )
                last_chat_dax_candle = getattr(
                    request.app.state,
                    "chat_dax40_last_processed_timestamp",
                    None,
                )
                chat_dax_strategy = getattr(
                    request.app.state, "chat_dax40_strategy", None
                )
                chat_dax_storage = getattr(
                    request.app.state, "chat_dax40_storage", None
                )
                process_claude_dax = (
                    last_dax_candle is None or candle_timestamp > last_dax_candle
                )
                process_chat_dax = (
                    chat_dax_strategy is not None
                    and chat_dax_storage is not None
                    and (
                        last_chat_dax_candle is None
                        or candle_timestamp > last_chat_dax_candle
                    )
                )
                if (
                    candle_timestamp < current_minute
                    and (process_claude_dax or process_chat_dax)
                ):
                    dax_candles, dax_signal_state = (
                        request.app.state.dax40_storage.load_context(
                            limit=CHAT_DAX40_HISTORY_LIMIT
                        )
                    )
                    if (
                        dax_candles
                        and dax_candles[-1]["timestamp_utc"] == candle_timestamp
                    ):
                        if process_claude_dax:
                            dax_signal = request.app.state.dax40_strategy.evaluate(
                                dax_candles,
                                now=datetime.now(timezone.utc),
                                last_signal_time=dax_signal_state["last_signal_time"],
                                signals_today=dax_signal_state["signals_today"],
                                orb_directions_today=dax_signal_state.get(
                                    "orb_directions_today", []
                                ),
                            )
                            dax_signal_saved = (
                                request.app.state.dax40_storage.save_signal(
                                    dax_signal, len(dax_candles)
                                )
                            )
                            if dax_signal_saved and DAX40_BEEP_ENABLED:
                                signal_save_count += 1
                            request.app.state.dax40_last_processed_timestamp = (
                                candle_timestamp
                            )
                        if process_chat_dax:
                            chat_dax_signal = chat_dax_strategy.evaluate(
                                dax_candles,
                                now=datetime.now(timezone.utc),
                            )
                            chat_dax_signal_saved = chat_dax_storage.save(
                                chat_dax_signal,
                                len(dax_candles),
                            )
                            if chat_dax_signal_saved and CHAT_DAX40_BEEP_ENABLED:
                                signal_save_count += 1
                            request.app.state.chat_dax40_last_processed_timestamp = (
                                candle_timestamp
                            )
            _process_closed_strategy_candle(
                request,
                candle,
                datetime.now(timezone.utc),
                enqueue_notifications=True,
            )
            if (
                candle.symbol == "ES"
                and saved_candle_count
                and candle.datetime.astimezone(timezone.utc)
                    + timedelta(minutes=1) <= datetime.now(timezone.utc)
            ):
                candle_timestamp = candle.datetime.astimezone(timezone.utc)
                if _run_sp500_strategy(candle_timestamp):
                    signal_save_count += 1
            if (
                candle.symbol == "XAU/USD"
                and saved_candle_count
                and candle.datetime.astimezone(timezone.utc)
                    + timedelta(minutes=1) <= datetime.now(timezone.utc)
            ):
                try:
                    xauusd_result = _run_xauusd_strategy(
                        candle.datetime.astimezone(timezone.utc)
                    )
                    if xauusd_result is not None:
                        xauusd_signal, sound_enabled = xauusd_result
                        if xauusd_signal in {"LONG", "SHORT"} and sound_enabled:
                            signal_save_count += 1
                            Thread(
                                target=play_alert,
                                kwargs={"number_of_beeps": 2, "duration_ms": 100},
                                daemon=True,
                            ).start()
                except Exception:
                    # Backend: także nieoczekiwany błąd procesu nie cofa zapisanej świecy.
                    logger.exception("Błąd strategii XAUUSD po zapisaniu świecy.")
                    xauusd_error = True
                try:
                    claude_xauusd_result = _run_claude_xauusd_strategy(
                        candle.datetime.astimezone(timezone.utc)
                    )
                    if claude_xauusd_result is not None:
                        claude_xauusd_signal, claude_sound_enabled = (
                            claude_xauusd_result
                        )
                        if (
                            claude_xauusd_signal in {"LONG", "SHORT"}
                            and claude_sound_enabled
                        ):
                            signal_save_count += 1
                except Exception:
                    logger.exception(
                        "Błąd strategii Claude XAU/USD po zapisaniu świecy."
                    )
                    claude_xauusd_error = True
            request.app.state.eurusd_strategy = updated_strategy
            request.app.state.grok45_v2_candles = (
                updated_grok45_v2_candles
            )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się zapisać świecy w bazie MySQL.",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail="Świeca jest nieprawidłowa lub nie zachowuje kolejności czasu.",
        ) from error
    except SP500StrategyError as error:
        logger.error("Błąd strategii S&P 500 po zapisaniu świecy: %s", error)
        return Response(
            status_code=503,
            content=f"SP500_PARTIAL\n{new_gap_count}\n{signal_save_count}",
            media_type="text/plain; charset=utf-8",
        )
    
    return Response(
        content=f"{new_gap_count}\n{signal_save_count}",
        media_type="text/plain; charset=utf-8",
        headers={
            name: "1"
            for name, enabled in (
                ("X-XAUUSD-Strategy-Error", xauusd_error),
                ("X-Claude-XAUUSD-Strategy-Error", claude_xauusd_error),
                ("X-Claude-USDJPY-Strategy-Error", claude_usdjpy_error),
            )
            if enabled
        } or None,
    )


@app.get("/api/candles/analysis", response_class=Response)
def analysis_candles(
    request: Request,
    limit: int = Query(ge=1, le=GROK45_ANALYSIS_WINDOW),
) -> Response:
    """Zwraca świece EUR/USD do analizy w formacie TSV z czasem Unix."""
    try:
        rows = request.app.state.repository.fetch_latest_analysis_candles(limit)
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się pobrać świec do analizy Grok-45.",
        ) from error
    return Response(
        content="\n".join("\t".join(row) for row in rows),
        media_type="text/tab-separated-values; charset=utf-8",
    )


@app.get("/api/candles/prompt-history", response_class=Response)
def prompt_candle_history(
    request: Request,
    symbol: str = Query(min_length=1, max_length=32),
    limit: int = Query(default=1000, ge=1, le=1000),
) -> Response:
    """Udostępnia zamknięte świece M1 do budowania kontekstu prompta C++."""
    try:
        candles = request.app.state.repository.fetch_prompt_candle_history(
            symbol, limit
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się pobrać świec do kontekstu prompta.",
        ) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    lines = ["timestamp_utc\topen\thigh\tlow\tclose\tgap_before"]
    lines.extend(
        "\t".join(
            (
                str(candle["timestamp_utc"]),
                candle["open"],
                candle["high"],
                candle["low"],
                candle["close"],
                "1" if candle["gap_before"] else "0",
            )
        )
        for candle in candles
    )
    return Response(
        content="\n".join(lines),
        media_type="text/tab-separated-values; charset=utf-8",
    )


@app.post("/api/signals/grok45", response_class=Response)
def save_grok45_signal(
    signal: Grok45SignalRecord, request: Request
) -> Response:
    """Zapisuje sygnał Grok-45 i zwraca 1 tylko dla nowego wpisu."""
    try:
        signal_was_saved = request.app.state.repository.save_grok45_signal(signal)
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się zapisać sygnału Grok-45 w bazie MySQL.",
        ) from error
    except (OverflowError, OSError, ValueError) as error:
        raise HTTPException(
            status_code=422,
            detail="Czas lub parametry sygnału Grok-45 są nieprawidłowe.",
        ) from error
    return Response(
        content="1" if signal_was_saved else "0",
        media_type="text/plain; charset=utf-8",
    )


@app.post("/api/candles/batch", response_class=Response)
def save_candle_batch(batch: CandleBatch, request: Request) -> Response:
    """Zapisuje paczkę świec i ocenia zamknięte świece obsługiwanych strategii."""
    unsupported_symbols = sorted(
        {
            candle.symbol
            for candle in batch.candles
            if not is_monitored_product(candle.symbol)
        }
    )
    if unsupported_symbols:
        raise HTTPException(
            status_code=422,
            detail=(
                "Nie skonfigurowano instrumentów: "
                + ", ".join(unsupported_symbols)
            ),
        )
    try:
        with strategy_lock:
            request.app.state.strategy_ready = False
            _, saved_candle_count, latest_saved_time, _ = (
                request.app.state.repository.save_candles(
                    batch.candles
                )
            )
            updated_strategy = copy.deepcopy(request.app.state.eurusd_strategy)
            candle_history = (
                request.app.state.repository.fetch_candle_history("EUR/USD")
            )
            updated_strategy.warm_up(candle_history)
            request.app.state.eurusd_strategy = updated_strategy
            request.app.state.grok45_v2_candles = (
                _grok45_candles_from_history(candle_history)
            )
            request.app.state.strategy_ready = True
            batch_claude_usdjpy_candles = sorted(
                (
                    candle
                    for candle in batch.candles
                    if candle.symbol == USDJPY_DATABASE_SYMBOL
                ),
                key=lambda candle: candle.datetime.astimezone(timezone.utc),
            )
            claude_usdjpy_open_times = []
            for candle in batch_claude_usdjpy_candles:
                candle_open_time = candle.datetime.astimezone(timezone.utc)
                if (
                    candle_open_time + timedelta(minutes=1)
                    > datetime.now(timezone.utc)
                    or not request.app.state.repository._is_market_open(
                        USDJPY_DATABASE_SYMBOL, candle_open_time
                    )
                ):
                    continue
                claude_usdjpy_open_times.append(candle_open_time)
            if claude_usdjpy_open_times:
                try:
                    _run_claude_usdjpy_batch(claude_usdjpy_open_times)
                except ClaudeUSDJPYStrategyError:
                    logger.exception(
                        "Nie udało się ocenić paczki świec USD/JPY przez Claude."
                    )
            batch_strategy_candles = sorted(
                (
                    candle
                    for candle in batch.candles
                    if candle.symbol
                    in {
                        USDMXN_DATABASE_SYMBOL,
                        USDJPY_DATABASE_SYMBOL,
                        BTCUSD_DATABASE_SYMBOL,
                    }
                ),
                key=lambda candle: (
                    candle.symbol,
                    candle.datetime.astimezone(timezone.utc),
                ),
            )
            for candle in batch_strategy_candles:
                _process_closed_strategy_candle(
                    request,
                    candle,
                    datetime.now(timezone.utc),
                    enqueue_notifications=False,
                )
            batch_xauusd_candles = sorted(
                (
                    candle
                    for candle in batch.candles
                    if candle.symbol == "XAU/USD"
                ),
                key=lambda candle: candle.datetime.astimezone(timezone.utc),
            )
            for candle in batch_xauusd_candles:
                candle_open_time = candle.datetime.astimezone(timezone.utc)
                if (
                    candle_open_time + timedelta(minutes=1)
                    > datetime.now(timezone.utc)
                    or not request.app.state.repository._is_market_open(
                        "XAU/USD", candle_open_time
                    )
                ):
                    continue
                for strategy_name, strategy_runner in (
                    ("Chat-gpt6/XAUUSDStrategyV1", _run_xauusd_strategy),
                    (
                        "Claude-sonnet55/XAUUSDStrategyV1",
                        _run_claude_xauusd_strategy,
                    ),
                ):
                    try:
                        strategy_runner(candle_open_time)
                    except XAUUSDStrategyError:
                        logger.exception(
                            "Nie udało się ocenić świecy XAU/USD %s przez %s.",
                            candle_open_time.isoformat(),
                            strategy_name,
                        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się zapisać świec w bazie MySQL.",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail="Świeca jest nieprawidłowa lub nie zachowuje kolejności czasu.",
        ) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się ocenić strategii dla świecy z paczki.",
        ) from error
    return Response(
        content=f"{saved_candle_count}\n{latest_saved_time}",
        media_type="text/plain; charset=utf-8",
    )


@app.get("/api/candles/missing", response_class=Response)
def missing_candles(
    request: Request,
    symbol: str = Query(min_length=1, max_length=32, pattern=r"^[A-Z0-9./_-]+$"),
) -> Response:
    """Zwraca status bazy i do 1000 najnowszych brakujących minut dla symbolu."""
    if not is_monitored_product(symbol):
        raise HTTPException(
            status_code=422,
            detail=f"Instrument {symbol} nie jest skonfigurowany.",
        )
    try:
        has_existing_candles, missing_times, total_missing_count = (
            request.app.state.repository.fetch_missing_candle_times(
                symbol, MAX_BACKFILL_CANDLES
            )
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się sprawdzić brakujących świec w bazie MySQL.",
        ) from error

    report_lines = [
        "1" if has_existing_candles else "0",
        str(total_missing_count),
    ]
    report_lines.extend(missing_times)
    return Response(
        content="\n".join(report_lines),
        media_type="text/plain; charset=utf-8",
    )


@app.get("/api/candles/report", response_class=Response)
def candle_report(
    request: Request,
    page: int = Query(ge=0, le=1000000),
) -> Response:
    """Zwraca stronę 20 najnowszych świec jako TSV z flagą kolejnej strony."""
    try:
        rows, has_next_page = request.app.state.repository.fetch_candle_page(page)
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się odczytać świec z bazy MySQL.",
        ) from error

    report_lines = ["1" if has_next_page else "0"]
    report_lines.extend("\t".join(row) for row in rows)
    return Response(
        content="\n".join(report_lines),
        media_type="text/tab-separated-values; charset=utf-8",
    )


@app.get("/api/candles/filtered-report", response_class=Response)
def filtered_candle_report(
    request: Request,
    start_date: date,
    end_date: date,
    page: int = Query(ge=0, le=1000000),
) -> Response:
    """Zwraca stronę 10 świec z wybranego, obustronnie włącznego zakresu dat."""
    if end_date < start_date:
        raise HTTPException(
            status_code=422,
            detail="Data końcowa nie może być wcześniejsza niż data początkowa.",
        )

    try:
        rows, has_next_page = request.app.state.repository.fetch_filtered_candle_page(
            start_date, end_date, page
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się odczytać świec z bazy MySQL.",
        ) from error

    report_lines = ["1" if has_next_page else "0"]
    report_lines.extend("\t".join(row) for row in rows)
    return Response(
        content="\n".join(report_lines),
        media_type="text/tab-separated-values; charset=utf-8",
    )


@app.get("/api/candles/gaps", response_class=Response)
def candle_gap_report(
    request: Request,
    page: int = Query(ge=0, le=1000000),
) -> Response:
    """Zwraca stronę 20 luk świecowych wraz ze statusem i czasami UTC."""
    try:
        rows, has_next_page = (
            request.app.state.repository.fetch_candle_gap_page(page)
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się odczytać tabeli brakujących świec.",
        ) from error

    report_lines = ["1" if has_next_page else "0"]
    report_lines.extend(
        "\t".join((symbol, candle_time, status, detected_at, filled_at or "--"))
        for symbol, candle_time, status, detected_at, filled_at in rows
    )
    return Response(
        content="\n".join(report_lines),
        media_type="text/tab-separated-values; charset=utf-8",
    )


# Backend: odrębny kursor każdego klienta nie usuwa ani nie przejmuje powiadomień.
@app.get("/api/signals/grok-sp500/notifications/cursor")
def grok_sp500_notification_cursor(request: Request) -> dict[str, int]:
    """Zwraca aktualny kursor startowy; 503 oznacza błąd odczytu bazy."""
    try:
        return {"cursor": request.app.state.repository.grok_sp500_notification_cursor()}
    except mysql.connector.Error as error:
        raise HTTPException(status_code=503, detail="Nie można odczytać kursora powiadomień Groka.") from error


@app.get("/api/signals/grok-sp500/notifications")
def grok_sp500_notifications(
    request: Request,
    after_id: int = Query(ge=0, le=18446744073709551615),
    limit: int = Query(default=100, ge=1, le=100),
) -> dict:
    """Zwraca stronę ID Groka ES po after_id; 422 oznacza błędne argumenty, 503 błąd bazy."""
    try:
        ids, has_more = request.app.state.repository.grok_sp500_notification_ids(after_id, limit)
    except mysql.connector.Error as error:
        raise HTTPException(status_code=503, detail="Nie można odczytać powiadomień Groka.") from error
    return {"ids": ids, "next_cursor": ids[-1] if ids else after_id, "has_more": has_more}


@app.get("/api/signals/usdmxn/notifications")
def usdmxn_notifications(
    request: Request,
    limit: int = Query(default=100, ge=1, le=100),
) -> dict[str, list[int]]:
    """Zwraca oczekujące powiadomienia USD/MXN.

    Args:
        request: Żądanie API z repozytorium bazy danych.
        limit: Maksymalna liczba identyfikatorów w odpowiedzi.

    Returns:
        Identyfikatory oczekujące na odtworzenie przez klienta C++.

    Raises:
        HTTPException: Z kodem 503, gdy odczyt bazy się nie powiedzie.
    """
    try:
        notification_ids = (
            request.app.state.repository.fetch_usdmxn_notification_ids(limit)
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie można odczytać powiadomień USD/MXN.",
        ) from error
    return {"ids": notification_ids}


@app.post("/api/signals/usdmxn/notifications/{notification_id}/ack")
def acknowledge_usdmxn_notification(
    notification_id: int,
    request: Request,
) -> dict[str, bool]:
    """Potwierdza odtworzenie dźwięku dla jednego powiadomienia USD/MXN.

    Args:
        notification_id: Identyfikator odebranego zdarzenia outboxa.
        request: Żądanie API z repozytorium bazy danych.

    Returns:
        Potwierdzenie, że zdarzenie istnieje i zostało obsłużone.

    Raises:
        HTTPException: Z kodem 422, 404 lub 503 przy błędnych danych,
            brakującym zdarzeniu albo błędzie bazy.
    """
    if notification_id < 1:
        raise HTTPException(
            status_code=422,
            detail="Identyfikator powiadomienia musi być dodatni.",
        )
    try:
        acknowledged = (
            request.app.state.repository.acknowledge_usdmxn_notification(
                notification_id
            )
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie można potwierdzić powiadomienia USD/MXN.",
        ) from error
    if not acknowledged:
        raise HTTPException(
            status_code=404,
            detail="Powiadomienie USD/MXN nie istnieje.",
        )
    return {"acknowledged": True}


@app.get("/api/signals/usdjpy/notifications")
def usdjpy_notifications(
    request: Request,
    limit: int = Query(default=100, ge=1, le=100),
) -> dict[str, list[int]]:
    """Zwraca oczekujące powiadomienia USD/JPY dla klienta C++."""
    try:
        notification_ids = (
            request.app.state.repository.fetch_usdjpy_notification_ids(limit)
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie można odczytać powiadomień USD/JPY.",
        ) from error
    return {"ids": notification_ids}


@app.post("/api/signals/usdjpy/notifications/{notification_id}/ack")
def acknowledge_usdjpy_notification(
    notification_id: int,
    request: Request,
) -> dict[str, bool]:
    """Potwierdza odtworzenie dźwięku dla jednego powiadomienia USD/JPY."""
    if notification_id < 1:
        raise HTTPException(
            status_code=422,
            detail="Identyfikator powiadomienia musi być dodatni.",
        )
    try:
        acknowledged = (
            request.app.state.repository.acknowledge_usdjpy_notification(
                notification_id
            )
        )
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie można potwierdzić powiadomienia USD/JPY.",
        ) from error
    if not acknowledged:
        raise HTTPException(
            status_code=404,
            detail="Powiadomienie USD/JPY nie istnieje.",
        )
    return {"acknowledged": True}


@app.get("/api/signals/report", response_class=Response)
def signal_report(
    request: Request,
    page: int = Query(ge=0, le=1000000),
) -> Response:
    """Zwraca sygnały, decyzję AI i wynik po 20 świecach jako dane TSV."""
    try:
        rows, has_next_page = request.app.state.repository.fetch_signal_page(page)
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się odczytać tabeli sygnałów.",
        ) from error

    report_lines = ["1" if has_next_page else "0"]
    report_lines.extend("\t".join(row) for row in rows)
    return Response(
        content="\n".join(report_lines),
        media_type="text/tab-separated-values; charset=utf-8",
    )


@app.get("/api/signals/potential", response_class=Response)
def signal_potential_details(
    request: Request,
    signal_id: int = Query(ge=1),
) -> Response:
    """Zwraca końcowe wyniki pojedynczego sygnału w formacie TSV."""
    try:
        details = request.app.state.repository.fetch_signal_potential(signal_id)
    except mysql.connector.Error as error:
        raise HTTPException(
            status_code=503,
            detail="Nie udało się odczytać wyników sygnału.",
        ) from error
    if details is None:
        raise HTTPException(
            status_code=404,
            detail="Nie znaleziono wyników dla wskazanego sygnału.",
        )
    return Response(
        content="\t".join(details),
        media_type="text/tab-separated-values; charset=utf-8",
    )


app.state.repository = repository
app.state.eurusd_strategy = eurusd_strategy
app.state.dax40_strategy = dax40_strategy
app.state.dax40_storage = dax40_storage
app.state.dax40_last_processed_timestamp = None
app.state.chat_dax40_strategy = chat_dax40_strategy
app.state.chat_dax40_storage = chat_dax40_storage
app.state.chat_dax40_last_processed_timestamp = None
app.state.usdmxn_strategy = usdmxn_strategy
app.state.usdmxn_storage = usdmxn_storage
app.state.usdmxn_last_processed_timestamp = None
app.state.grok45_usdmxn_strategy = grok45_usdmxn_strategy
app.state.grok45_usdmxn_storage = grok45_usdmxn_storage
app.state.grok45_usdmxn_last_processed_timestamp = None
app.state.grok45_btcusd_strategy = btcusd_strategy
app.state.grok45_btcusd_storage = btcusd_storage
app.state.grok45_btcusd_last_processed_timestamp = None
app.state.strategy_ready = False
app.state.grok45_v2_candles = []
