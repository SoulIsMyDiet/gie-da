"""Testy kontraktu odpowiedzi i kontekstu wysyłanego do weryfikatora AI."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DATABASE", "test")

import app as market_data_api  # noqa: E402
from ai_verification import (  # noqa: E402
    AIVerificationWorker,
    AIVerificationError,
    AIVerificationRouter,
    AnthropicMessagesClient,
    GroqChatClient,
    MODEL_DISPLAY_NAME,
    OpenAICompatibleChatClient,
    VerifierResult,
    _signal_datetime_to_utc,
    assemble_verifier_prompt,
    validate_verifier_response,
)
from candle_prompt_context import (  # noqa: E402
    CandlePromptContextBuilder,
    PromptContextError,
)
from strategy_context_registry import load_strategy_context_registry  # noqa: E402
from prompt_templates import INITIAL_SIGNAL_REVIEW_PROMPT  # noqa: E402


def _valid_response() -> dict[str, object]:
    """Tworzy małą odpowiedź przykładową zgodną ze schematem weryfikatora."""
    variant = {
        "transaction": "LONG",
        "spread": 0.003,
        "spread_confidence": "MEDIUM",
        "open_price": 17.102,
        "take_profit": 17.125,
        "stop_loss": 17.085,
        "pts": 72,
        "decision": "YES",
        "reason_msg": "M1 i M5 potwierdzają kierunek.",
    }
    return {
        "aggressive": variant.copy(),
        "moderate": variant.copy(),
    }


def _history(count: int = 200) -> list[dict[str, object]]:
    """Buduje uporządkowany zestaw świec M1 bez użycia floatów."""
    start = 1_800_000_000 - (1_800_000_000 % 60)
    history = []
    for index in range(count):
        price = Decimal("100") + Decimal(index) / Decimal("1000")
        history.append(
            {
                "timestamp_utc": start + index * 60,
                "open": str(price),
                "high": str(price + Decimal("0.01")),
                "low": str(price - Decimal("0.01")),
                "close": str(price + Decimal("0.005")),
                "gap_before": False,
            }
        )
    return history


class _MemoryCursor:
    """Imituje identyfikatory INSERT bez połączenia z bazą MySQL."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.lastrowid = 0

    def execute(
        self, query: str, parameters: tuple[object, ...] = ()
    ) -> None:
        self.calls.append((query, parameters))
        if query.lstrip().startswith("INSERT"):
            self.lastrowid += 1

    def fetchall(self) -> list[tuple[object, ...]]:
        return []

    def close(self) -> None:
        return None


def test_validator_accepts_both_modes_and_preserves_decimal_values() -> None:
    """Parsuje warianty agresywny i umiarkowany bez utraty precyzji cen."""
    validated = validate_verifier_response(
        json.dumps(_valid_response(), ensure_ascii=False)
    )

    assert set(validated) == {"aggressive", "moderate"}
    assert validated["aggressive"]["open_price"] == Decimal("17.102")
    assert validated["moderate"]["decision"] == "YES"


def test_validator_rejects_a_model_name_returned_in_the_json() -> None:
    """Nie przyjmuje llm_name w JSON-ie, bo nazwę ustala konfiguracja kodu."""
    response = _valid_response()
    response["llm_name"] = "Dodatkowy model"

    with pytest.raises(AIVerificationError, match="wyłącznie aggressive i moderate"):
        validate_verifier_response(json.dumps(response, ensure_ascii=False))


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.pop("moderate"),
        lambda value: value["aggressive"].update(pts=101),
        lambda value: value["aggressive"].update(take_profit=17.100),
        lambda value: value["aggressive"].update(reason_msg="x" * 251),
    ],
)
def test_validator_rejects_invalid_response_shapes(change) -> None:
    """Odrzuca niekompletną odpowiedź i błędne wartości."""
    response = _valid_response()
    change(response)

    with pytest.raises(AIVerificationError):
        validate_verifier_response(json.dumps(response))


def test_validator_rejects_non_finite_json_values() -> None:
    """Nie akceptuje NaN, mimo że standardowy dekoder Pythona go dopuszcza."""
    response = json.dumps(_valid_response()).replace('"spread": 0.003', '"spread": NaN')

    with pytest.raises(AIVerificationError):
        validate_verifier_response(response)


def test_prompt_replaces_only_the_named_variables() -> None:
    """Podstawia dane w szablonie i pozostawia pozostały tekst bez zmian."""
    prompt = assemble_verifier_prompt(
        "P=<parametry_algorytmu>; C=<kontekst_algorytmu>; H=<historia_świec>",
        {"ema": 12},
        "Opis strategii",
        '{"M1":[]}',
    )

    assert '"ema": 12' in prompt
    assert "Opis strategii" in prompt
    assert '{"M1":[]}' in prompt


def test_seed_prompt_does_not_spend_tokens_on_the_model_name() -> None:
    """Pozostawia identyfikację modelu kodowi i zachowuje zmienne promptu."""
    assert "llm_name" not in INITIAL_SIGNAL_REVIEW_PROMPT
    assert "<parametry_algorytmu>" in INITIAL_SIGNAL_REVIEW_PROMPT
    assert "<kontekst_algorytmu>" in INITIAL_SIGNAL_REVIEW_PROMPT
    assert "<historia_świec>" in INITIAL_SIGNAL_REVIEW_PROMPT


def test_groq_client_reports_missing_key_without_sending_a_request() -> None:
    """Brak klucza API kończy się czytelnym błędem bez połączenia sieciowego."""
    client = GroqChatClient(api_key="", opener=lambda *_args, **_kwargs: None)

    with pytest.raises(AIVerificationError, match="GROK_API_KEY"):
        client.complete("prompt")


def test_groq_client_uses_the_desktop_user_agent() -> None:
    """Identyfikuje klienta tak samo jak działający klient sentencji dnia."""
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = (
        b'{"choices":[{"message":{"content":"Odpowiedz"}}]}'
    )
    opener = Mock(return_value=response)
    client = GroqChatClient(api_key="test-key", opener=opener)

    assert client.complete("prompt") == "Odpowiedz"
    request = opener.call_args.args[0]
    assert request.get_header("User-agent") == "gie-da/1.0"


def test_router_supports_multiple_models_for_one_instrument(
    tmp_path: Path,
) -> None:
    """Zwraca wszystkie modele przypisane do instrumentu w kolejności z pliku."""
    config_path = tmp_path / "ai_verification_config.json"
    config_path.write_text(
        json.dumps(
            {
                "default_provider": "groq",
                "instrument_routes": {
                    "USD/MXN": ["secondary", "groq"]
                },
                "providers": {
                    "groq": {"type": "groq"},
                    "secondary": {
                        "type": "openai_compatible",
                        "chat_completions_url": (
                            "https://provider.example/v1/chat/completions"
                        ),
                        "model": "small-model",
                        "display_name": "Dodatkowy model",
                        "api_key_env": "SECONDARY_LLM_API_KEY",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    router = AIVerificationRouter(config_path)

    assert router.provider_ids_for("USD/MXN") == ("secondary", "groq")
    assert router.provider_ids_for("USD/JPY") == ("groq",)
    clients = router.clients_for("USD/MXN")
    assert isinstance(clients[0][1], OpenAICompatibleChatClient)
    assert isinstance(clients[1][1], GroqChatClient)
    assert router.clients_for("USD/MXN") == clients


def test_router_assigns_both_models_only_to_usdmxn() -> None:
    """Uruchamia Claude i Groq dla USD/MXN, a pozostałym zostawia Groq."""
    router = AIVerificationRouter(
        Path(__file__).resolve().parents[1]
        / "ai_verification_config.json"
    )

    assert router.provider_ids_for("USD/MXN") == (
        "claude_sonnet55",
        "groq",
    )
    assert router.provider_ids_for("USD/JPY") == ("groq",)
    claude_client = router.clients_for("USD/MXN")[0][1]
    assert isinstance(claude_client, AnthropicMessagesClient)
    assert claude_client.display_name == "Claude Sonnet 5.5 (Anthropic)"


def test_openai_compatible_client_sends_model_without_requesting_its_name() -> None:
    """Wysyła model z konfiguracji bez proszenia o nazwę w odpowiedzi."""
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = (
        b'{"choices":[{"message":{"content":"Odpowiedz"}}]}'
    )
    opener = Mock(return_value=response)
    client = OpenAICompatibleChatClient(
        chat_completions_url=(
            "https://provider.example/v1/chat/completions"
        ),
        model="small-model",
        display_name="Dodatkowy model",
        api_key_env="SECONDARY_LLM_API_KEY",
        api_key="test-key",
        opener=opener,
    )

    assert client.complete("prompt") == "Odpowiedz"
    request = opener.call_args.args[0]
    request_body = json.loads(request.data.decode("utf-8"))
    assert request.get_header("Authorization") == "Bearer test-key"
    assert request_body["model"] == "small-model"
    assert "llm_name" not in request_body["messages"][0]["content"]


def test_anthropic_client_uses_messages_api_and_claude_key() -> None:
    """Buduje żądanie Messages API bez wysyłania go do zewnętrznej usługi."""
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = (
        b'{"content":[{"type":"text","text":"{\\"aggressive\\":{},\\"moderate\\":{}}"}]}'
    )
    opener = Mock(return_value=response)
    client = AnthropicMessagesClient(
        model="claude-sonnet-5-5",
        display_name="Claude Sonnet 5.5 (Anthropic)",
        api_key_env="CLAUDE_API_KEY",
        api_key="test-key",
        opener=opener,
    )

    result = client.complete("prompt")

    assert result == '{"aggressive":{},"moderate":{}}'
    request = opener.call_args.args[0]
    request_body = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "https://api.anthropic.com/v1/messages"
    assert request.get_header("X-api-key") == "test-key"
    assert request.get_header("Anthropic-version") == "2023-06-01"
    assert request_body["model"] == "claude-sonnet-5-5"
    assert request_body["max_tokens"] == 900
    assert request_body["output_config"] == {"effort": "medium"}
    assert request_body["messages"] == [{"role": "user", "content": "prompt"}]
    assert "llm_name" not in request_body["system"]


def test_anthropic_client_rejects_missing_key_without_sending_a_request() -> None:
    """Nie próbuje użyć Claude, jeśli nie ma klucza API."""
    opener = Mock()
    client = AnthropicMessagesClient(
        model="claude-sonnet-5-5",
        display_name="Claude Sonnet 5.5 (Anthropic)",
        api_key_env="CLAUDE_API_KEY",
        api_key="",
        opener=opener,
    )

    with pytest.raises(AIVerificationError, match="CLAUDE_API_KEY"):
        client.complete("prompt")

    opener.assert_not_called()


def test_worker_requests_all_models_assigned_to_the_signal_product() -> None:
    """Odpytuje Claude i Groq dla sygnału USD/MXN i zapisuje oba wyniki."""
    claude_client = Mock()
    claude_client.display_name = "Claude Sonnet 5.5 (Anthropic)"
    groq_client = Mock()
    groq_client.display_name = MODEL_DISPLAY_NAME
    response = _valid_response()
    simultaneous_requests = Barrier(2, timeout=2)
    response_text = json.dumps(response, ensure_ascii=False)

    def complete_in_parallel(prompt: str) -> str:
        assert prompt == "Sprawdź sygnał."
        simultaneous_requests.wait()
        return response_text

    claude_client.complete.side_effect = complete_in_parallel
    groq_client.complete.side_effect = complete_in_parallel
    router = Mock()
    router.clients_for.return_value = (
        ("claude_sonnet55", claude_client),
        ("groq", groq_client),
    )
    worker = AIVerificationWorker(
        SimpleNamespace(database_settings={}),
        router=router,
        transaction_tracker=Mock(),
    )
    worker._get_active_prompt = Mock(return_value="Sprawdź sygnał.")
    worker._save_success = Mock()
    signal_parameters = json.dumps(
        {"timestamp": "2026-11-17T12:00:00Z"}
    )

    worker._process_job(
        (
            1,
            2,
            "USD/MXN",
            datetime(2026, 11, 17, 13, 0),
            Decimal("17.102"),
            "long",
            "test-strategy",
            signal_parameters,
        )
    )

    router.clients_for.assert_called_once_with("USD/MXN")
    claude_client.complete.assert_called_once_with("Sprawdź sygnał.")
    groq_client.complete.assert_called_once_with("Sprawdź sygnał.")
    results = worker._save_success.call_args.args[6]
    assert [result.provider_id for result in results] == [
        "claude_sonnet55",
        "groq",
    ]
    assert [result.ai_name for result in results] == [
        "Claude Sonnet 5.5 (Anthropic)",
        MODEL_DISPLAY_NAME,
    ]


def test_strategy_registry_maps_known_signal_names() -> None:
    """Łączy nazwy z konfiguracji z kontekstem strategii bez jej uruchamiania."""
    api_directory = Path(__file__).resolve().parents[1]
    registry = load_strategy_context_registry(api_directory)

    expected_names = {
        "Chat-gpt6/EURUSDStrategyV1",
        "Chat-gpt6/DAX40StrategyV1",
        "Chat-gpt6/USDJPYStrategyV1",
        "Chat-gpt6/USDMXNStrategyV1",
        "Chat-gpt6/XAUUSDStrategyV1",
        "Claude-sonnet55/DAX40",
        "Claude-sonnet55/MicroTransactions",
        "Claude-sonnet55/S&P500",
        "Claude-sonnet55/USDJPYStrategyV1",
        "Claude-sonnet55/XAUUSDStrategyV1",
        "Grok-45/EURUSDC++StrategyV1",
        "Grok-45/EURUSDStrategyV2",
        "Grok-45/S&P500",
        "Grok-45/USDMXNStrategyV1",
    }

    assert expected_names <= registry.keys()


def test_candle_context_contains_50_m1_and_30_complete_m5_bars() -> None:
    """Tworzy zgodny z klasą C++ JSON bez ekspozycji świec po czasie sygnału."""
    context = json.loads(
        CandlePromptContextBuilder.build_json("TEST", _history())
    )

    assert len(context["M1"]["ohlc_ticks"]) == 50
    assert len(context["M5"]["ohlc_ticks"]) == 30
    assert context["M1"]["step_minutes"] == 1
    assert context["M5"]["step_minutes"] == 5
    assert context["synthetic"] is False


def test_candle_context_rejects_duplicate_timestamps() -> None:
    """Nie pozwala, by nieuporządkowana historia trafiła do modelu."""
    history = _history()
    history[1]["timestamp_utc"] = history[0]["timestamp_utc"]

    with pytest.raises(PromptContextError, match="uporządkowane"):
        CandlePromptContextBuilder.build_json("TEST", history)


def test_signal_cutoff_is_timezone_aware() -> None:
    """Konwertuje lokalny czas zapisany w MySQL na UTC zgodnie ze strefą."""
    local_signal_time = datetime(2026, 1, 15, 13, 0)

    assert _signal_datetime_to_utc(local_signal_time) == datetime(
        2026, 1, 15, 12, 0, tzinfo=timezone.utc
    )


def test_signal_payload_timestamp_takes_precedence_over_local_database_time() -> None:
    """Używa dokładnego UTC z sygnału, także w powtarzającej się godzinie DST."""
    local_signal_time = datetime(2026, 10, 25, 2, 30)

    assert _signal_datetime_to_utc(
        local_signal_time,
        {"timestamp": "2026-10-25T01:30:00Z"},
    ) == datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc)


def test_retry_endpoint_requeues_a_failed_job() -> None:
    """Ponawia zadanie failed po ręcznym żądaniu bez bezpośredniego wywołania AI."""
    cursor = Mock()
    cursor.fetchone.return_value = ("failed",)
    connection = Mock()
    connection.cursor.return_value = cursor
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                repository=SimpleNamespace(database_settings={})
            )
        )
    )

    with patch.object(
        market_data_api.mysql.connector,
        "connect",
        return_value=connection,
    ):
        result = market_data_api.retry_ai_verification_job(123, request)

    assert result == {"signal_id": 123, "status": "pending"}
    assert connection.commit.called
    assert cursor.execute.call_args_list[-1].args[1] == (123,)


def test_retry_endpoint_rejects_a_job_already_in_progress() -> None:
    """Nie pozwala na ręczne ponowienie aktywnego żądania modelu."""
    cursor = Mock()
    cursor.fetchone.return_value = ("processing",)
    connection = Mock()
    connection.cursor.return_value = cursor
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                repository=SimpleNamespace(database_settings={})
            )
        )
    )

    with patch.object(
        market_data_api.mysql.connector,
        "connect",
        return_value=connection,
    ), pytest.raises(HTTPException) as error:
        market_data_api.retry_ai_verification_job(123, request)

    assert error.value.status_code == 409
    assert connection.rollback.called
    assert not connection.commit.called


def test_success_persists_each_model_and_both_modes_even_when_decision_is_no() -> None:
    """Zapisuje osobne transakcje obu modeli i nie pomija decyzji NO."""
    cursor = _MemoryCursor()
    connection = Mock()
    connection.cursor.return_value = cursor
    transaction_tracker = Mock()
    repository = SimpleNamespace(database_settings={})
    worker = AIVerificationWorker(
        repository,
        client=Mock(),
        transaction_tracker=transaction_tracker,
    )
    raw_model_response = _valid_response()
    raw_model_response["moderate"]["open_price"] = 17.101
    raw_model_response["moderate"]["decision"] = "NO"
    response_text = json.dumps(raw_model_response, ensure_ascii=False)
    response = validate_verifier_response(response_text)
    signal_close_utc = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)

    with patch.object(
        market_data_api.mysql.connector,
        "connect",
        return_value=connection,
    ):
        verifier_results = (
            VerifierResult(
                provider_id="claude_sonnet55",
                ai_name="Claude Sonnet 5.5 (Anthropic)",
                response=response,
                response_text=response_text,
            ),
            VerifierResult(
                provider_id="groq",
                ai_name=MODEL_DISPLAY_NAME,
                response=response,
                response_text=response_text,
            ),
        )
        worker._save_success(
            10,
            20,
            "USD/MXN",
            signal_close_utc,
            Decimal("17.102"),
            "long",
            verifier_results,
            "pełny prompt",
        )

    transactions = [
        parameters
        for query, parameters in cursor.calls
        if "INSERT INTO `transactions`" in query
    ]
    decisions = [
        parameters
        for query, parameters in cursor.calls
        if "INSERT INTO ai_decisions" in query
    ]
    revisions = [
        parameters
        for query, parameters in cursor.calls
        if "INSERT INTO prompts_revision" in query
    ]
    assert len(transactions) == len(decisions) == len(revisions) == 4
    assert transactions[0][1:3] == ("claude_sonnet55", "aggressive")
    assert transactions[1][1:3] == ("claude_sonnet55", "moderate")
    assert transactions[0][9:11] == (0, "in progress")
    assert transactions[1][9:11] == (None, "awaiting")
    assert transactions[2][1:3] == ("groq", "aggressive")
    assert transactions[3][1:3] == ("groq", "moderate")
    assert [decision[1] for decision in decisions] == [
        "Claude Sonnet 5.5 (Anthropic)",
        "Claude Sonnet 5.5 (Anthropic)",
        MODEL_DISPLAY_NAME,
        MODEL_DISPLAY_NAME,
    ]
    assert decisions[1][3] == "decline"
    assert [revision[1] for revision in revisions] == [2, 5, 8, 11]
    assert connection.commit.called
    transaction_tracker.process_historical_candles.assert_called_once()
