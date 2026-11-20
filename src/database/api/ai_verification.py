"""Kolejkuje sygnały do oceny przez Groq i zapisuje wynik w MySQL."""

# Backend
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
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
                            "llm_name, aggressive i moderate. Każdy wariant "
                            "musi zawierać dokładnie pola wymagane przez prompt. "
                            f"llm_name ustaw dokładnie na {MODEL_DISPLAY_NAME!r}."
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


def validate_verifier_response(response_text: str) -> dict[str, Any]:
    """Sprawdza strukturę i wartości odpowiedzi weryfikatora.

    Args:
        response_text: Tekst JSON zwrócony przez model.

    Returns:
        Odpowiedź z cenami i spreadem zamienionymi na Decimal.

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
        "llm_name",
        "aggressive",
        "moderate",
    }:
        raise AIVerificationError(
            "Odpowiedź musi zawierać wyłącznie llm_name, aggressive i moderate."
        )
    llm_name = response["llm_name"]
    if (
        not isinstance(llm_name, str)
        or not llm_name.strip()
        or len(llm_name.strip()) > 255
    ):
        raise AIVerificationError("Pole llm_name musi zawierać nazwę modelu.")
    if llm_name.strip() != MODEL_DISPLAY_NAME:
        raise AIVerificationError(
            f"Pole llm_name musi mieć wartość {MODEL_DISPLAY_NAME!r}."
        )

    normalized: dict[str, Any] = {"llm_name": llm_name.strip()}
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
        client: GroqChatClient | None = None,
        transaction_tracker: TransactionTracker | None = None,
    ) -> None:
        """Przygotowuje połączenia i zależności wymagane przez kolejkę.

        Args:
            repository: Repozytorium API udostępniające bazę i historię świec.
            client: Klient Groq; domyślnie używa klucza ze środowiska.
            transaction_tracker: Komponent aktualizujący transakcje po świecach.
        """
        self._repository = repository
        self._database_settings = repository.database_settings
        self._client = client or GroqChatClient()
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
            response_text = self._client.complete(full_prompt)
            verifier_response = validate_verifier_response(response_text)
            self._save_success(
                job_id,
                signal_id,
                product,
                signal_close_utc,
                signal_price,
                signal_direction,
                verifier_response,
                full_prompt,
                response_text,
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
        response: dict[str, Any],
        full_prompt: str,
        response_text: str,
    ) -> None:
        """Zapisuje oba warianty, ich decyzje oraz historię prompta atomowo."""
        connection = mysql.connector.connect(**self._database_settings)
        try:
            cursor = connection.cursor()
            try:
                connection.start_transaction()
                signal_close_db = signal_close_utc.replace(tzinfo=None)
                signal_price = Decimal(str(signal_price_value))
                for mode_name in MODE_NAMES:
                    mode_response = response[mode_name]
                    open_price = mode_response["open_price"]
                    opened_immediately = open_price == signal_price
                    cursor.execute(
                        """
                        INSERT INTO `transactions`
                            (signal_id, `mode`, signal_status,
                             ai_decision_status, open_price, stop_loss_price,
                             take_profit_price, true_spread, time_to_open,
                             transaction_status, transaction_saldo,
                             transaction_100zl_deal, signal_close_utc,
                             opened_at_utc)
                        VALUES
                            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                             NULL, NULL, %s, %s)
                        """,
                        (
                            signal_id,
                            mode_name,
                            signal_direction,
                            mode_response["transaction"].lower(),
                            open_price,
                            mode_response["stop_loss"],
                            mode_response["take_profit"],
                            mode_response["spread"],
                            0 if opened_immediately else None,
                            "in progress" if opened_immediately else "awaiting",
                            signal_close_db,
                            signal_close_db if opened_immediately else None,
                        ),
                    )
                    transaction_id = cursor.lastrowid
                    cursor.execute(
                        """
                        INSERT INTO ai_decisions
                            (transaction_id, ai_name, ai_pts, ai_decision,
                             ai_decision_reason_msg, ai_after_tramsaction_msg,
                             `mode`)
                        VALUES (%s, %s, %s, %s, %s, NULL, %s)
                        """,
                        (
                            transaction_id,
                            response["llm_name"],
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
                        (full_prompt, cursor.lastrowid, response_text),
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
