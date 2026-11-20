"""Kieruje sygnały do skonfigurowanych modeli i zapisuje wynik w MySQL."""

# Backend
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import mysql.connector

from candle_prompt_context import (
    CandlePromptContextBuilder,
)
from strategy_context_registry import (
    StrategyContextError,
    load_strategy_context_registry,
)
from signal_potential_calculator import SignalPotentialCalculator
from transaction_tracker import TransactionTracker

LOGGER = logging.getLogger(__name__)
GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "qwen/qwen3.8-27b"
MODEL_DISPLAY_NAME = "Qwen 3.8 27B (Groq)"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
ANTHROPIC_EFFORT = "medium"
MAX_COMPLETION_TOKENS = 900
MAXIMUM_HISTORY_SIZE = 1000
REQUEST_TIMEOUT_SECONDS = 30
POLL_INTERVAL_SECONDS = 1
STALE_JOB_MINUTES = 10
WARSAW_TIME_ZONE = ZoneInfo("Europe/Warsaw")
MODE_NAMES = ("aggressive", "moderate")
RESPONSE_FIELDS = {
    "transaction",
    "spread",
    "spread_confidence",
    "open_price",
    "take_profit",
    "stop_loss",
    "pts",
    "decision",
    "reason_msg",
}


class AIVerificationError(ValueError):
    """Opisuje błąd komunikacji z modelem lub niepoprawną odpowiedź."""


class GroqChatClient:
    """Wysyła pojedyncze żądanie czatu do zgodnego z OpenAI API Groq."""

    display_name = MODEL_DISPLAY_NAME

    def __init__(
        self,
        api_key: str | None = None,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        """Pobiera klucz API albo przyjmuje go jawnie w testach.

        Args:
            api_key: Klucz Groq; domyślnie odczytywany ze zmiennej GROK_API_KEY.
            opener: Funkcja otwierająca żądanie HTTP.
        """
        self._api_key = (
            os.environ.get("GROK_API_KEY", "").strip()
            if api_key is None
            else api_key.strip()
        )
        self._opener = opener

    def complete(self, prompt: str) -> str:
        """Wysyła prompt i zwraca tekst odpowiedzi modelu.

        Args:
            prompt: Pełny prompt z kontekstem sygnału i historią świec.

        Returns:
            Treść odpowiedzi wygenerowanej przez model.

        Raises:
            AIVerificationError: Gdy brakuje klucza, API odpowie błędem
                albo zwróci nieoczekiwany format.
        """
        if not self._api_key:
            raise AIVerificationError(
                "Brak klucza API Groq w zmiennej GROK_API_KEY."
            )
        request_body = json.dumps(
            {
                "model": GROQ_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Zwróć wyłącznie poprawny obiekt JSON z polami "
                            "aggressive i moderate. Każdy wariant "
                            "musi zawierać dokładnie pola wymagane przez prompt."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": MAX_COMPLETION_TOKENS,
            },
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            GROQ_CHAT_COMPLETIONS_URL,
            data=request_body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": "gie-da/1.0",
            },
            method="POST",
        )
        try:
            with self._opener(
                request, timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                response_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")[:1000]
            raise AIVerificationError(
                f"Groq zwrócił HTTP {error.code}: {details}"
            ) from error
        except urllib.error.URLError as error:
            raise AIVerificationError(
                f"Nie udało się połączyć z API Groq: {error.reason}"
            ) from error
        except (TimeoutError, OSError) as error:
            raise AIVerificationError(
                f"Połączenie z API Groq nie powiodło się: {error}"
            ) from error

        try:
            provider_response = json.loads(response_body)
            content = provider_response["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
            raise AIVerificationError(
                "API Groq zwróciło nieprawidłową strukturę odpowiedzi."
            ) from error
        if not isinstance(content, str) or not content.strip():
            raise AIVerificationError(
                "API Groq zwróciło pustą treść odpowiedzi."
            )
        return content.strip()


class OpenAICompatibleChatClient:
    """Wysyła żądanie do wybranego API obsługującego format OpenAI Chat."""

    def __init__(
        self,
        *,
        chat_completions_url: str,
        model: str,
        display_name: str,
        api_key_env: str,
        api_key: str | None = None,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        """Przygotowuje klienta bez wysyłania żądania do dostawcy.

        Args:
            chat_completions_url: Pełny adres endpointu Chat Completions.
            model: Identyfikator modelu wymagany przez dostawcę.
            display_name: Nazwa zapisywana w odpowiedzi modelu i bazie.
            api_key_env: Nazwa zmiennej środowiskowej z kluczem API.
            api_key: Opcjonalny klucz podany bezpośrednio, głównie w testach.
            opener: Funkcja otwierająca żądanie HTTP.
        """
        try:
            parsed_url = urlsplit(chat_completions_url)
        except ValueError as error:
            raise AIVerificationError(
                "Adres dodatkowego dostawcy nie jest prawidłowym URL-em."
            ) from error
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise AIVerificationError(
                "Adres dodatkowego dostawcy musi być adresem HTTP lub HTTPS."
            )
        self._chat_completions_url = chat_completions_url
        self._model = model
        self.display_name = display_name
        self._api_key_env = api_key_env
        self._api_key = (
            os.environ.get(api_key_env, "").strip()
            if api_key is None
            else api_key.strip()
        )
        self._opener = opener

    def complete(self, prompt: str) -> str:
        """Wysyła prompt do dodatkowego modelu i zwraca jego odpowiedź.

        Args:
            prompt: Pełny prompt z kontekstem sygnału i historią świec.

        Returns:
            Treść odpowiedzi wygenerowanej przez model.

        Raises:
            AIVerificationError: Gdy brakuje klucza, API odpowie błędem
                albo zwróci nieoczekiwany format.
        """
        if not self._api_key:
            raise AIVerificationError(
                f"Brak klucza API w zmiennej {self._api_key_env}."
            )
        request_body = json.dumps(
            {
                "model": self._model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Zwróć wyłącznie poprawny obiekt JSON z polami "
                            "aggressive i moderate. Każdy wariant "
                            "musi zawierać dokładnie pola wymagane przez prompt."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": MAX_COMPLETION_TOKENS,
            },
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self._chat_completions_url,
            data=request_body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": "gie-da/1.0",
            },
            method="POST",
        )
        try:
            with self._opener(
                request, timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                response_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")[:1000]
            raise AIVerificationError(
                f"Dostawca {self.display_name} zwrócił HTTP "
                f"{error.code}: {details}"
            ) from error
        except urllib.error.URLError as error:
            raise AIVerificationError(
                f"Nie udało się połączyć z dostawcą "
                f"{self.display_name}: {error.reason}"
            ) from error
        except (TimeoutError, OSError) as error:
            raise AIVerificationError(
                f"Połączenie z dostawcą {self.display_name} nie powiodło się: "
                f"{error}"
            ) from error

        try:
            provider_response = json.loads(response_body)
            content = provider_response["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
            raise AIVerificationError(
                f"Dostawca {self.display_name} zwrócił "
                "nieprawidłową strukturę odpowiedzi."
            ) from error
        if not isinstance(content, str) or not content.strip():
            raise AIVerificationError(
                f"Dostawca {self.display_name} zwrócił pustą treść odpowiedzi."
            )
        return content.strip()


class AnthropicMessagesClient:
    """Wysyła żądanie do natywnego interfejsu Messages API Anthropic."""

    def __init__(
        self,
        *,
        model: str,
        display_name: str,
        api_key_env: str,
        api_key: str | None = None,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        """Przygotowuje klienta Claude bez wysyłania żądania.

        Args:
            model: Identyfikator modelu Anthropic.
            display_name: Nazwa zapisywana w kolumnie ai_decisions.ai_name.
            api_key_env: Nazwa zmiennej środowiskowej z kluczem Anthropic.
            api_key: Opcjonalny klucz podany bezpośrednio, głównie w testach.
            opener: Funkcja otwierająca żądanie HTTP.
        """
        self._model = model
        self.display_name = display_name
        self._api_key_env = api_key_env
        self._api_key = (
            os.environ.get(api_key_env, "").strip()
            if api_key is None
            else api_key.strip()
        )
        self._opener = opener

    def complete(self, prompt: str) -> str:
        """Wysyła prompt do Claude i zwraca tekst odpowiedzi.

        Args:
            prompt: Pełny prompt z kontekstem sygnału i historią świec.

        Returns:
            Połączoną treść bloków tekstowych z odpowiedzi Anthropic.

        Raises:
            AIVerificationError: Gdy brakuje klucza, API odpowie błędem
                albo zwróci nieoczekiwany format.
        """
        if not self._api_key:
            raise AIVerificationError(
                f"Brak klucza API w zmiennej {self._api_key_env}."
            )
        request_body = json.dumps(
            {
                "model": self._model,
                "max_tokens": MAX_COMPLETION_TOKENS,
                "system": (
                    "Zwróć wyłącznie poprawny obiekt JSON z polami "
                    "aggressive i moderate. Każdy wariant musi zawierać "
                    "dokładnie pola wymagane przez prompt."
                ),
                "messages": [{"role": "user", "content": prompt}],
                "output_config": {"effort": ANTHROPIC_EFFORT},
            },
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            ANTHROPIC_MESSAGES_URL,
            data=request_body,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_API_VERSION,
                "Content-Type": "application/json",
                "User-Agent": "gie-da/1.0",
            },
            method="POST",
        )
        try:
            with self._opener(
                request, timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                response_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")[:1000]
            raise AIVerificationError(
                f"Anthropic zwrócił HTTP {error.code}: {details}"
            ) from error
        except urllib.error.URLError as error:
            raise AIVerificationError(
                f"Nie udało się połączyć z Anthropic: {error.reason}"
            ) from error
        except (TimeoutError, OSError) as error:
            raise AIVerificationError(
                f"Połączenie z Anthropic nie powiodło się: {error}"
            ) from error

        try:
            provider_response = json.loads(response_body)
            content_blocks = provider_response["content"]
            if not isinstance(content_blocks, list):
                raise TypeError("Pole content nie jest listą.")
            content = "".join(
                block["text"]
                for block in content_blocks
                if isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            )
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise AIVerificationError(
                "Anthropic zwrócił nieprawidłową strukturę odpowiedzi."
            ) from error
        if not content.strip():
            raise AIVerificationError(
                "Anthropic zwrócił pustą treść odpowiedzi."
            )
        return content.strip()


@dataclass(frozen=True)
class VerifierProviderSettings:
    """Przechowuje bezpieczne, niebędące sekretami ustawienia dostawcy."""

    provider_type: str
    display_name: str
    model: str | None = None
    chat_completions_url: str | None = None
    api_key_env: str | None = None


@dataclass(frozen=True)
class VerifierResult:
    """Przechowuje wynik jednego modelu dla pojedynczego sygnału."""

    provider_id: str
    ai_name: str
    response: dict[str, Any]
    response_text: str


def load_verifier_routing_config(
    config_path: Path | None = None,
) -> tuple[
    str,
    dict[str, VerifierProviderSettings],
    dict[str, tuple[str, ...]],
]:
    """Wczytuje dostawców i przypisania instrumentów do modeli.

    Args:
        config_path: Opcjonalna ścieżka pliku JSON; domyślnie plik obok modułu.

    Returns:
        Domyślny dostawca, definicje dostawców i przypisania instrumentów.

    Raises:
        AIVerificationError: Gdy plik lub jego konfiguracja są nieprawidłowe.
    """
    path = config_path or Path(__file__).with_name(
        "ai_verification_config.json"
    )
    try:
        configuration = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AIVerificationError(
            f"Nie można wczytać konfiguracji weryfikatora {path}: {error}"
        ) from error
    if not isinstance(configuration, dict) or set(configuration) != {
        "default_provider",
        "instrument_routes",
        "providers",
    }:
        raise AIVerificationError(
            "Konfiguracja weryfikatora musi zawierać dokładnie "
            "default_provider, instrument_routes i providers."
        )

    raw_providers = configuration["providers"]
    if not isinstance(raw_providers, dict) or not raw_providers:
        raise AIVerificationError(
            "Konfiguracja weryfikatora musi zawierać dostawców."
        )
    providers: dict[str, VerifierProviderSettings] = {}
    for provider_id, provider_value in raw_providers.items():
        if (
            not isinstance(provider_id, str)
            or not provider_id.strip()
            or len(provider_id) > 64
            or not provider_id.isascii()
            or not provider_id.replace("_", "").replace("-", "").isalnum()
            or not isinstance(provider_value, dict)
        ):
            raise AIVerificationError(
                "Każdy dostawca musi mieć unikalny identyfikator ASCII "
                "do 64 znaków i obiekt ustawień."
            )
        provider_type = provider_value.get("type")
        if provider_type == "groq":
            if set(provider_value) != {"type"}:
                raise AIVerificationError(
                    f"Dostawca {provider_id!r} typu groq nie przyjmuje "
                    "dodatkowych pól."
                )
            providers[provider_id] = VerifierProviderSettings(
                provider_type="groq",
                display_name=MODEL_DISPLAY_NAME,
            )
            continue
        if provider_type not in {"anthropic", "openai_compatible"}:
            raise AIVerificationError(
                f"Dostawca {provider_id!r} ma nieobsługiwany typ."
            )

        required_fields = {
            "type",
            "model",
            "display_name",
            "api_key_env",
        }
        if provider_type == "openai_compatible":
            required_fields.add("chat_completions_url")
        if set(provider_value) != required_fields:
            raise AIVerificationError(
                f"Dostawca {provider_id!r} musi zawierać dokładnie "
                "pola wymagane przez wybrany typ."
            )
        model = provider_value["model"]
        display_name = provider_value["display_name"]
        api_key_env = provider_value["api_key_env"]
        endpoint = provider_value.get("chat_completions_url")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (model, display_name, api_key_env)
        ) or len(display_name.strip()) > 255:
            raise AIVerificationError(
                f"Dostawca {provider_id!r} zawiera puste ustawienie "
                "lub nazwę dłuższą niż 255 znaków."
            )
        if provider_type == "openai_compatible":
            if not isinstance(endpoint, str) or not endpoint.strip():
                raise AIVerificationError(
                    f"Dostawca {provider_id!r} nie ma adresu API."
                )
            try:
                parsed_url = urlsplit(endpoint)
            except ValueError as error:
                raise AIVerificationError(
                    f"Dostawca {provider_id!r} ma nieprawidłowy adres API."
                ) from error
            if (
                parsed_url.scheme not in {"http", "https"}
                or not parsed_url.netloc
            ):
                raise AIVerificationError(
                    f"Dostawca {provider_id!r} ma nieprawidłowy adres API."
                )
        if (
            not api_key_env.isascii()
            or not api_key_env.replace("_", "").isalnum()
            or not api_key_env[0].isalpha()
        ):
            raise AIVerificationError(
                f"Dostawca {provider_id!r} ma nieprawidłową nazwę "
                "zmiennej środowiskowej klucza."
            )
        providers[provider_id] = VerifierProviderSettings(
            provider_type=provider_type,
            display_name=display_name.strip(),
            model=model.strip(),
            chat_completions_url=(
                endpoint.strip() if isinstance(endpoint, str) else None
            ),
            api_key_env=api_key_env,
        )

    default_provider = configuration["default_provider"]
    if not isinstance(default_provider, str) or default_provider not in providers:
        raise AIVerificationError(
            "default_provider musi wskazywać skonfigurowanego dostawcę."
        )
    raw_routes = configuration["instrument_routes"]
    if not isinstance(raw_routes, dict):
        raise AIVerificationError(
            "instrument_routes musi być obiektem przypisań instrumentów."
        )
    routes: dict[str, tuple[str, ...]] = {}
    for instrument, raw_provider_ids in raw_routes.items():
        provider_ids = (
            (raw_provider_ids,)
            if isinstance(raw_provider_ids, str)
            else raw_provider_ids
        )
        if (
            not isinstance(instrument, str)
            or not instrument.strip()
            or not isinstance(provider_ids, (list, tuple))
            or not provider_ids
            or any(
                not isinstance(provider_id, str)
                or provider_id not in providers
                for provider_id in provider_ids
            )
            or len(set(provider_ids)) != len(provider_ids)
        ):
            raise AIVerificationError(
                "Każde przypisanie instrumentu musi wskazywać jednego lub "
                "więcej różnych, skonfigurowanych dostawców."
            )
        routes[instrument] = tuple(provider_ids)
    return default_provider, providers, routes


class AIVerificationRouter:
    """Wybiera jednego lub więcej dostawców na podstawie instrumentu."""

    def __init__(self, config_path: Path | None = None) -> None:
        """Wczytuje routing raz przy starcie workera.

        Args:
            config_path: Opcjonalna ścieżka pliku konfiguracyjnego.
        """
        (
            self._default_provider,
            self._providers,
            self._instrument_routes,
        ) = load_verifier_routing_config(config_path)
        self._clients: dict[
            str,
            GroqChatClient
            | OpenAICompatibleChatClient
            | AnthropicMessagesClient,
        ] = {}

    def provider_ids_for(self, instrument: str) -> tuple[str, ...]:
        """Zwraca wszystkich dostawców przypisanych do instrumentu."""
        return self._instrument_routes.get(
            instrument, (self._default_provider,)
        )

    def provider_id_for(self, instrument: str) -> str:
        """Zwraca pierwszego dostawcę dla zgodności ze starszym użyciem."""
        return self.provider_ids_for(instrument)[0]

    def clients_for(
        self, instrument: str
    ) -> tuple[
        tuple[
            str,
            GroqChatClient
            | OpenAICompatibleChatClient
            | AnthropicMessagesClient,
        ],
        ...,
    ]:
        """Zwraca klientów dla wszystkich modeli przypisanych do instrumentu."""
        return tuple(
            (provider_id, self._client_for_provider(provider_id))
            for provider_id in self.provider_ids_for(instrument)
        )

    def client_for(
        self, instrument: str
    ) -> (
        GroqChatClient
        | OpenAICompatibleChatClient
        | AnthropicMessagesClient
    ):
        """Zwraca pierwszego klienta dla zgodności ze starszym użyciem."""
        provider_id = self.provider_id_for(instrument)
        return self._client_for_provider(provider_id)

    def _client_for_provider(
        self, provider_id: str
    ) -> (
        GroqChatClient
        | OpenAICompatibleChatClient
        | AnthropicMessagesClient
    ):
        """Tworzy raz i ponownie wykorzystuje klienta danego dostawcy."""
        if provider_id not in self._clients:
            settings = self._providers[provider_id]
            if settings.provider_type == "groq":
                client = GroqChatClient()
            elif settings.provider_type == "anthropic":
                client = AnthropicMessagesClient(
                    model=settings.model or "",
                    display_name=settings.display_name,
                    api_key_env=settings.api_key_env or "",
                )
            else:
                client = OpenAICompatibleChatClient(
                    chat_completions_url=settings.chat_completions_url or "",
                    model=settings.model or "",
                    display_name=settings.display_name,
                    api_key_env=settings.api_key_env or "",
                )
            self._clients[provider_id] = client
        return self._clients[provider_id]


def validate_verifier_response(
    response_text: str,
) -> dict[str, Any]:
    """Sprawdza strukturę i wartości odpowiedzi weryfikatora.

    Args:
        response_text: Tekst JSON zwrócony przez model.

    Returns:
        Dwa warianty odpowiedzi z cenami i spreadem zamienionymi na Decimal.

    Raises:
        AIVerificationError: Gdy odpowiedź nie spełnia ścisłego kontraktu.
    """
    try:
        response = json.loads(
            response_text,
            parse_constant=_reject_non_finite_json_number,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise AIVerificationError(
            "Odpowiedź weryfikatora nie jest poprawnym JSON-em."
        ) from error

    if not isinstance(response, dict) or set(response) != {
        "aggressive",
        "moderate",
    }:
        raise AIVerificationError(
            "Odpowiedź musi zawierać wyłącznie aggressive i moderate."
        )

    normalized: dict[str, Any] = {}
    for mode_name in MODE_NAMES:
        mode_response = response[mode_name]
        if (
            not isinstance(mode_response, dict)
            or set(mode_response) != RESPONSE_FIELDS
        ):
            raise AIVerificationError(
                f"Wariant {mode_name} ma nieprawidłowy zestaw pól."
            )
        normalized[mode_name] = _validate_mode_response(
            mode_name, mode_response
        )
    return normalized


def assemble_verifier_prompt(
    prompt_template: str,
    signal_parameters: Any,
    algorithm_context: str,
    candle_context: str,
) -> str:
    """Podstawia dane sygnału wyłącznie w zmiennych obecnych w szablonie."""
    prompt = prompt_template
    replacements = {
        "<parametry_algorytmu>": json.dumps(
            signal_parameters,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        ),
        "<kontekst_algorytmu>": algorithm_context,
        "<historia_świec>": candle_context,
    }
    for placeholder, value in replacements.items():
        prompt = prompt.replace(placeholder, value)
    return prompt


class AIVerificationWorker:
    """Przetwarza nowe sygnały asynchronicznie, poza żądaniem zapisu świecy."""

    def __init__(
        self,
        repository: Any,
        *,
        client: Any | None = None,
        router: AIVerificationRouter | None = None,
        transaction_tracker: TransactionTracker | None = None,
    ) -> None:
        """Przygotowuje połączenia i zależności wymagane przez kolejkę.

        Args:
            repository: Repozytorium API udostępniające bazę i historię świec.
            client: Opcjonalny klient zastępujący routing, głównie w testach.
            router: Konfiguracja przypisująca instrumenty do dostawców.
            transaction_tracker: Komponent aktualizujący transakcje po świecach.
        """
        self._repository = repository
        self._database_settings = repository.database_settings
        self._client = client
        self._router = router or AIVerificationRouter()
        self._transaction_tracker = (
            transaction_tracker or TransactionTracker()
        )
        self._api_directory = Path(__file__).resolve().parent
        self._strategy_contexts: dict[str, str] | None = None
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="ai-verification-worker",
            daemon=True,
        )

    def start(self) -> None:
        """Uruchamia pojedynczy wątek pobierający zadania z MySQL."""
        self._thread.start()

    def stop(self) -> None:
        """Zatrzymuje wątek i czeka na zakończenie bieżącego żądania modelu."""
        self._stop_event.set()
        self._thread.join(timeout=REQUEST_TIMEOUT_SECONDS + 5)
        if self._thread.is_alive():
            LOGGER.error(
                "Wątek weryfikacji AI nie zakończył pracy w wyznaczonym czasie."
            )

    def process_next_job(self) -> bool:
        """Pobiera i obsługuje jedno zadanie; zwraca, czy kolejka miała wpis."""
        job = self._claim_next_job()
        if job is None:
            return False
        self._process_job(job)
        return True

    def _run(self) -> None:
        """Odpytuje kolejkę i jawnie loguje błędy infrastruktury bazy."""
        while not self._stop_event.is_set():
            try:
                processed_job = self.process_next_job()
            except mysql.connector.Error:
                LOGGER.exception("Błąd bazy danych w kolejce weryfikacji AI.")
                processed_job = False
            self._stop_event.wait(
                0 if processed_job else POLL_INTERVAL_SECONDS
            )

    def _claim_next_job(self) -> tuple[Any, ...] | None:
        """Atomowo oznacza najstarsze oczekujące zadanie jako przetwarzane."""
        connection = mysql.connector.connect(**self._database_settings)
        try:
            cursor = connection.cursor()
            try:
                connection.start_transaction()
                cursor.execute(
                    """
                    SELECT j.id, j.signal_id, s.product, s.datetime, s.price,
                           s.`signal`, s.algorithm_name, s.signal_parameters
                    FROM ai_verification_jobs AS j
                    JOIN signals AS s ON s.id = j.signal_id
                    WHERE j.status = 'pending'
                    ORDER BY j.created_at_utc, j.signal_id
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                    """
                )
                job = cursor.fetchone()
                if job is None:
                    connection.rollback()
                    return None
                cursor.execute(
                    """
                    UPDATE ai_verification_jobs
                    SET status = 'processing', updated_at_utc = UTC_TIMESTAMP(6)
                    WHERE id = %s AND status = 'pending'
                    """,
                    (job[0],),
                )
                connection.commit()
                return job
            finally:
                cursor.close()
        finally:
            connection.close()

    def _process_job(self, job: tuple[Any, ...]) -> None:
        """Buduje prompt, odpytuje model i zapisuje sukces albo czytelny błąd."""
        (
            job_id,
            signal_id,
            product,
            signal_datetime,
            signal_price,
            signal_direction,
            algorithm_name,
            signal_parameters_value,
        ) = job
        full_prompt: str | None = None
        response_text: str | None = None
        try:
            prompt_template = self._get_active_prompt()
            if prompt_template is None:
                self._mark_skipped(
                    job_id,
                    "Brak promptu z prompts.prompt_in_use = TRUE.",
                )
                return

            signal_parameters = _parse_signal_parameters(
                signal_parameters_value
            )
            signal_close_utc = _signal_datetime_to_utc(
                signal_datetime, signal_parameters
            )
            algorithm_context = ""
            candle_context = ""
            if "<kontekst_algorytmu>" in prompt_template:
                if self._strategy_contexts is None:
                    self._strategy_contexts = load_strategy_context_registry(
                        self._api_directory
                    )
                try:
                    algorithm_context = self._strategy_contexts[algorithm_name]
                except KeyError as error:
                    raise StrategyContextError(
                        f"Brak kontekstu dla algorytmu {algorithm_name!r}."
                    ) from error
            if "<historia_świec>" in prompt_template:
                candle_history = self._repository.fetch_prompt_candle_history(
                    product, MAXIMUM_HISTORY_SIZE, signal_close_utc
                )
                candle_context = CandlePromptContextBuilder.build_json(
                    product, candle_history
                )
            full_prompt = assemble_verifier_prompt(
                prompt_template,
                signal_parameters,
                algorithm_context,
                candle_context,
            )
            if self._client is not None:
                configured_clients = (("injected", self._client),)
            else:
                configured_clients = self._router.clients_for(product)
            verifier_results = self._request_verifier_results(
                configured_clients, full_prompt
            )
            response_text = json.dumps(
                {
                    result.ai_name: result.response_text
                    for result in verifier_results
                },
                ensure_ascii=False,
            )
            self._save_success(
                job_id,
                signal_id,
                product,
                signal_close_utc,
                signal_price,
                signal_direction,
                verifier_results,
                full_prompt,
            )
        except (
            AIVerificationError,
            StrategyContextError,
            ValueError,
            mysql.connector.Error,
        ) as error:
            LOGGER.error(
                "Weryfikacja sygnału %s nie powiodła się: %s",
                signal_id,
                error,
            )
            self._mark_failed(
                job_id, str(error), full_prompt, response_text
            )

    @staticmethod
    def _request_verifier_results(
        configured_clients: tuple[tuple[str, Any], ...],
        full_prompt: str,
    ) -> tuple[VerifierResult, ...]:
        """Odpytuje przypisane modele równolegle i sprawdza ich odpowiedzi."""
        if not configured_clients:
            raise AIVerificationError(
                "Dla instrumentu nie skonfigurowano żadnego weryfikatora."
            )

        results_by_provider: dict[str, VerifierResult] = {}
        with ThreadPoolExecutor(
            max_workers=len(configured_clients),
            thread_name_prefix="ai-verifier",
        ) as executor:
            futures = {
                executor.submit(client.complete, full_prompt): (
                    provider_id,
                    client,
                )
                for provider_id, client in configured_clients
            }
            for future in as_completed(futures):
                provider_id, client = futures[future]
                response_text = future.result()
                ai_name = getattr(client, "display_name", None)
                if (
                    not isinstance(ai_name, str)
                    or not ai_name.strip()
                    or len(ai_name) > 255
                ):
                    raise AIVerificationError(
                        f"Dostawca {provider_id!r} nie ma prawidłowej "
                        "nazwy modelu."
                    )
                results_by_provider[provider_id] = VerifierResult(
                    provider_id=provider_id,
                    ai_name=ai_name.strip(),
                    response=validate_verifier_response(response_text),
                    response_text=response_text,
                )
        return tuple(
            results_by_provider[provider_id]
            for provider_id, _client in configured_clients
        )

    def _get_active_prompt(self) -> str | None:
        """Zwraca jedyny aktywny prompt lub zgłasza niejednoznaczną konfigurację."""
        connection = mysql.connector.connect(**self._database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    SELECT prompt
                    FROM prompts
                    WHERE prompt_in_use = TRUE
                    ORDER BY id
                    LIMIT 2
                    """
                )
                prompts = cursor.fetchall()
            finally:
                cursor.close()
        finally:
            connection.close()
        if not prompts:
            return None
        if len(prompts) != 1:
            raise AIVerificationError(
                "Aktywny może być tylko jeden prompt w tabeli prompts."
            )
        return prompts[0][0]

    def _save_success(
        self,
        job_id: int,
        signal_id: int,
        product: str,
        signal_close_utc: datetime,
        signal_price_value: Any,
        signal_direction: str,
        verifier_results: tuple[VerifierResult, ...],
        full_prompt: str,
    ) -> None:
        """Zapisuje wyniki wszystkich modeli i oba profile atomowo.

        Args:
            job_id: Identyfikator przetwarzanego zadania.
            signal_id: Identyfikator sygnału, który uruchomił weryfikację.
            product: Symbol instrumentu używany do pobrania świec.
            signal_close_utc: Czas zamknięcia świecy sygnałowej w UTC.
            signal_price_value: Cena zamknięcia świecy sygnałowej.
            signal_direction: Kierunek sygnału zapisany w tabeli signals.
            verifier_results: Odpowiedzi modeli wraz z nazwami i identyfikatorami.
            full_prompt: Pełny prompt wysłany do dostawcy.

        Raises:
            mysql.connector.Error: Gdy transakcja zapisu w MySQL nie powiedzie się.
            ValueError: Gdy wartości odpowiedzi nie mogą zostać zapisane.
        """
        if not verifier_results:
            raise ValueError("Brak wyników weryfikatora do zapisania.")
        connection = mysql.connector.connect(**self._database_settings)
        try:
            cursor = connection.cursor()
            try:
                connection.start_transaction()
                signal_close_db = signal_close_utc.replace(tzinfo=None)
                signal_price = Decimal(str(signal_price_value))
                for verifier_result in verifier_results:
                    if (
                        not verifier_result.provider_id
                        or not verifier_result.ai_name.strip()
                        or len(verifier_result.ai_name) > 255
                    ):
                        raise ValueError(
                            "Wynik weryfikatora ma nieprawidłową "
                            "identyfikację dostawcy."
                        )
                    for mode_name in MODE_NAMES:
                        mode_response = verifier_result.response[mode_name]
                        open_price = mode_response["open_price"]
                        opened_immediately = open_price == signal_price
                        cursor.execute(
                            """
                            INSERT INTO `transactions`
                                (signal_id, verifier_id, `mode`,
                                 signal_status, ai_decision_status, open_price,
                                 stop_loss_price, take_profit_price,
                                 true_spread, time_to_open,
                                 transaction_status, transaction_saldo,
                                 transaction_100zl_deal, signal_close_utc,
                                 opened_at_utc)
                            VALUES
                                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                 %s, NULL, NULL, %s, %s)
                            """,
                            (
                                signal_id,
                                verifier_result.provider_id,
                                mode_name,
                                signal_direction,
                                mode_response["transaction"].lower(),
                                open_price,
                                mode_response["stop_loss"],
                                mode_response["take_profit"],
                                mode_response["spread"],
                                0 if opened_immediately else None,
                                (
                                    "in progress"
                                    if opened_immediately
                                    else "awaiting"
                                ),
                                signal_close_db,
                                signal_close_db
                                if opened_immediately
                                else None,
                            ),
                        )
                        transaction_id = cursor.lastrowid
                        cursor.execute(
                            """
                            INSERT INTO ai_decisions
                                (transaction_id, ai_name, ai_pts,
                                 ai_decision, ai_decision_reason_msg,
                                 ai_after_tramsaction_msg, `mode`)
                            VALUES (%s, %s, %s, %s, %s, NULL, %s)
                            """,
                            (
                                transaction_id,
                                verifier_result.ai_name,
                                mode_response["pts"],
                                (
                                    "accept"
                                    if mode_response["decision"] == "YES"
                                    else "decline"
                                ),
                                mode_response["reason_msg"],
                                mode_name,
                            ),
                        )
                        cursor.execute(
                            """
                            INSERT INTO prompts_revision
                                (full_prompt, ai_decision_id, ai_response)
                            VALUES (%s, %s, %s)
                            """,
                            (
                                full_prompt,
                                cursor.lastrowid,
                                verifier_result.response_text,
                            ),
                        )
                self._transaction_tracker.process_historical_candles(
                    cursor, product, signal_close_utc
                )
                cursor.execute(
                    """
                    UPDATE ai_verification_jobs
                    SET status = 'completed', last_error = NULL,
                        updated_at_utc = UTC_TIMESTAMP(6)
                    WHERE id = %s AND status = 'processing'
                    """,
                    (job_id,),
                )
                connection.commit()
            except (mysql.connector.Error, ValueError):
                connection.rollback()
                raise
            finally:
                cursor.close()
        finally:
            connection.close()

    def _mark_skipped(self, job_id: int, reason: str) -> None:
        """Kończy zadanie bez zapytania do modelu, gdy prompt nie jest aktywny."""
        self._update_job_status(job_id, "skipped", reason)

    def _mark_failed(
        self,
        job_id: int,
        reason: str,
        full_prompt: str | None,
        response_text: str | None,
    ) -> None:
        """Zapisuje błąd, a otrzymaną błędną odpowiedź zachowuje do diagnozy."""
        connection = mysql.connector.connect(**self._database_settings)
        try:
            cursor = connection.cursor()
            try:
                connection.start_transaction()
                if full_prompt is not None and response_text is not None:
                    cursor.execute(
                        """
                        INSERT INTO prompts_revision
                            (full_prompt, ai_decision_id, ai_response)
                        VALUES (%s, NULL, %s)
                        """,
                        (full_prompt, response_text),
                    )
                cursor.execute(
                    """
                    UPDATE ai_verification_jobs
                    SET status = 'failed', last_error = %s,
                        updated_at_utc = UTC_TIMESTAMP(6)
                    WHERE id = %s
                    """,
                    (reason[:4000], job_id),
                )
                connection.commit()
            except mysql.connector.Error:
                connection.rollback()
                raise
            finally:
                cursor.close()
        finally:
            connection.close()

    def _update_job_status(
        self, job_id: int, status: str, reason: str
    ) -> None:
        """Zapisuje końcowy status zadania i wyjaśnienie jego pominięcia."""
        connection = mysql.connector.connect(**self._database_settings)
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE ai_verification_jobs
                    SET status = %s, last_error = %s,
                        updated_at_utc = UTC_TIMESTAMP(6)
                    WHERE id = %s
                    """,
                    (status, reason, job_id),
                )
                connection.commit()
            finally:
                cursor.close()
        finally:
            connection.close()


def _validate_mode_response(
    mode_name: str, response: dict[str, Any]
) -> dict[str, Any]:
    """Sprawdza kierunek, ceny, punktację i opis jednego trybu."""
    direction = response["transaction"]
    if not isinstance(direction, str) or direction not in {"LONG", "SHORT"}:
        raise AIVerificationError(
            f"Wariant {mode_name} ma nieprawidłowy transaction."
        )
    spread = _parse_decimal(response["spread"], "spread", mode_name)
    open_price = _parse_decimal(response["open_price"], "open_price", mode_name)
    take_profit = _parse_decimal(
        response["take_profit"], "take_profit", mode_name
    )
    stop_loss = _parse_decimal(response["stop_loss"], "stop_loss", mode_name)
    if spread < 0 or min(open_price, take_profit, stop_loss) <= 0:
        raise AIVerificationError(
            f"Wariant {mode_name} zawiera niedodatnią cenę lub spread."
        )
    if direction == "LONG" and not stop_loss < open_price < take_profit:
        raise AIVerificationError(
            f"Wariant {mode_name} ma niespójne ceny dla LONG."
        )
    if direction == "SHORT" and not take_profit < open_price < stop_loss:
        raise AIVerificationError(
            f"Wariant {mode_name} ma niespójne ceny dla SHORT."
        )

    confidence = response["spread_confidence"]
    if not isinstance(confidence, str) or confidence not in {
        "LOW",
        "MEDIUM",
        "HIGH",
    }:
        raise AIVerificationError(
            f"Wariant {mode_name} ma nieprawidłowy spread_confidence."
        )
    points = response["pts"]
    if (
        isinstance(points, bool)
        or not isinstance(points, int)
        or not 1 <= points <= 100
    ):
        raise AIVerificationError(
            f"Wariant {mode_name} ma nieprawidłowe pts; wymagany zakres to 1-100."
        )
    decision = response["decision"]
    if not isinstance(decision, str) or decision not in {"YES", "NO"}:
        raise AIVerificationError(
            f"Wariant {mode_name} ma nieprawidłowe decision."
        )
    reason = response["reason_msg"]
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 250:
        raise AIVerificationError(
            f"Wariant {mode_name} ma nieprawidłowy reason_msg."
        )
    return {
        "transaction": direction,
        "spread": spread,
        "spread_confidence": confidence,
        "open_price": open_price,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "pts": points,
        "decision": decision,
        "reason_msg": reason,
    }


def _parse_decimal(value: Any, field_name: str, mode_name: str) -> Decimal:
    """Odczytuje skończoną liczbę dziesiętną z odpowiedzi modelu."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise AIVerificationError(
            f"Wariant {mode_name} ma nieprawidłowe pole {field_name}."
        )
    try:
        parsed_value = Decimal(str(value))
    except InvalidOperation as error:
        raise AIVerificationError(
            f"Wariant {mode_name} ma nieprawidłowe pole {field_name}."
        ) from error
    if not parsed_value.is_finite():
        raise AIVerificationError(
            f"Wariant {mode_name} ma nieskończone pole {field_name}."
        )
    normalized_value = parsed_value.normalize()
    decimal_exponent = normalized_value.as_tuple().exponent
    decimal_places = max(0, -decimal_exponent)
    integer_places = max(
        0, len(normalized_value.as_tuple().digits) + decimal_exponent
    )
    if decimal_places > 10 or integer_places > 10:
        raise AIVerificationError(
            f"Wariant {mode_name} przekracza precyzję pola {field_name}."
        )
    return parsed_value


def _parse_signal_parameters(value: Any) -> Any:
    """Dekoduje zapisany JSON sygnału i odrzuca błędne lub niedozwolone liczby."""
    if isinstance(value, dict):
        parameters = value
    elif isinstance(value, (str, bytes, bytearray)):
        try:
            parameters = json.loads(
                value,
                parse_constant=_reject_non_finite_json_number,
            )
        except (json.JSONDecodeError, ValueError) as error:
            raise AIVerificationError(
                "Parametry sygnału nie zawierają poprawnego JSON-u."
            ) from error
    else:
        raise AIVerificationError(
            "Parametry sygnału mają nieobsługiwany format."
        )
    if not isinstance(parameters, dict):
        raise AIVerificationError(
            "Parametry sygnału muszą być obiektem JSON."
        )
    return parameters


def _signal_datetime_to_utc(
    signal_datetime: datetime,
    signal_parameters: dict[str, Any] | None = None,
) -> datetime:
    """Odczytuje UTC z pakietu sygnału lub konwertuje czas lokalny MySQL."""
    if signal_parameters is not None and "timestamp" in signal_parameters:
        signal_timestamp = SignalPotentialCalculator._signal_timestamp(
            signal_parameters
        )
        return signal_timestamp.replace(tzinfo=timezone.utc)
    if signal_datetime.tzinfo is not None:
        return signal_datetime.astimezone(timezone.utc)
    return signal_datetime.replace(
        tzinfo=WARSAW_TIME_ZONE
    ).astimezone(timezone.utc)


def _reject_non_finite_json_number(value: str) -> None:
    """Uniemożliwia dekoderowi zaakceptowanie NaN i nieskończoności."""
    raise ValueError(f"Niedozwolona liczba JSON: {value}.")
