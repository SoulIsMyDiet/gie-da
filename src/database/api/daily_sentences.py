"""Przechowywanie i jednorazowe zasilanie puli sentencji dnia."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import mysql.connector

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "qwen/qwen3.8-27b"
TARGET_SENTENCE_COUNT = 2000
SENTENCES_PER_REQUEST = 100
MAX_API_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_RATE_LIMIT_RETRIES = 5
MIN_SECONDS_BETWEEN_BATCHES = 65


class GroqRateLimitError(RuntimeError):
    """Przechowuje czas oczekiwania wymagany po odpowiedzi HTTP 429."""

    def __init__(self, retry_after_seconds: float) -> None:
        """Tworzy błąd z opóźnieniem ponowienia odczytanym z odpowiedzi API."""
        super().__init__(
            f"Groq ograniczył liczbę żądań; ponowienie za "
            f"{retry_after_seconds:.1f} s."
        )
        self.retry_after_seconds = retry_after_seconds


def _retry_delay_seconds(error: HTTPError) -> float:
    """Odczytuje czas ponowienia z nagłówków lub komunikatu Groq."""
    retry_delays: list[float] = []
    for header_name in (
        "Retry-After",
        "x-ratelimit-reset-tokens",
        "x-ratelimit-reset-requests",
    ):
        header_value = error.headers.get(header_name)
        if not header_value:
            continue
        try:
            retry_delays.append(float(header_value))
            continue
        except ValueError:
            pass
        duration_match = re.fullmatch(
            r"\s*(\d+(?:\.\d+)?)\s*(ms|s|m)\s*",
            header_value,
            flags=re.IGNORECASE,
        )
        if duration_match is None:
            continue
        duration = float(duration_match.group(1))
        unit = duration_match.group(2).lower()
        retry_delays.append(
            duration / 1000 if unit == "ms" else duration * (60 if unit == "m" else 1)
        )

    response_body = error.read()
    try:
        response_data = json.loads(response_body)
        message = response_data.get("error", {}).get("message", "")
    except (AttributeError, json.JSONDecodeError, TypeError):
        message = ""
    if isinstance(message, str):
        message_match = re.search(
            r"try again in\s+(\d+(?:\.\d+)?)\s*(ms|s|m)",
            message,
            flags=re.IGNORECASE,
        )
        if message_match is not None:
            duration = float(message_match.group(1))
            unit = message_match.group(2).lower()
            retry_delays.append(
                duration / 1000
                if unit == "ms"
                else duration * (60 if unit == "m" else 1)
            )
    positive_delays = [delay for delay in retry_delays if delay > 0]
    return max(positive_delays, default=60.0)


def create_daily_sentences_table(cursor: Any) -> None:
    """Tworzy tabelę sentencji z unikalną treścią, jeśli jeszcze nie istnieje."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_sentences (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            sentence VARCHAR(512) NOT NULL,
            created_at_utc TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            UNIQUE KEY uq_daily_sentences_sentence (sentence)
        ) ENGINE=InnoDB
        DEFAULT CHARACTER SET utf8mb4
        COLLATE utf8mb4_unicode_ci
        """
    )


def fetch_daily_sentences(database_settings: dict[str, Any]) -> list[str]:
    """Pobiera wszystkie zapisane sentencje w stabilnej kolejności identyfikatorów."""
    connection = mysql.connector.connect(**database_settings)
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT sentence FROM daily_sentences ORDER BY id"
            )
            return [str(row[0]) for row in cursor.fetchall()]
        finally:
            cursor.close()
    finally:
        connection.close()


def parse_generated_sentences(
    content: str, maximum_count: int
) -> list[str]:
    """Odczytuje unikalne, kompletne sentencje z odpowiedzi wierszowej."""
    if maximum_count < 1:
        raise ValueError("Liczba sentencji do odczytania musi być dodatnia.")

    sentences: list[str] = []
    seen_sentences: set[str] = set()
    for generated_line in content.splitlines():
        sentence = re.sub(
            r"^\s*(?:[-*]\s+|\d+[.)]\s*)",
            "",
            generated_line,
        )
        sentence = " ".join(sentence.split()).strip(" \"'“”‘’")
        if (
            not sentence
            or len(sentence) > 512
            or sentence[-1] not in ".!?…"
        ):
            continue
        normalized_sentence = unicodedata.normalize(
            "NFKC", sentence
        ).casefold()
        if normalized_sentence in seen_sentences:
            continue
        seen_sentences.add(normalized_sentence)
        sentences.append(sentence)
        if len(sentences) == maximum_count:
            break
    if not sentences:
        raise RuntimeError("Odpowiedź Groq nie zawiera poprawnych sentencji.")
    return sentences


def _request_sentence_batch(api_key: str, count: int) -> list[str]:
    """Prosi Groq o jedną małą paczkę oryginalnych sentencji po polsku."""
    request_data = {
        "model": GROQ_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Twórz wyłącznie krótkie, oryginalne sentencje po polsku. "
                    "Nie cytuj istniejących utworów ani znanych autorów. "
                    "Nie dodawaj autorów, numeracji ani komentarzy."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Przygotuj {count} różnych sentencji. Zwróć dokładnie "
                    f"{count} wierszy tekstu, po jednej krótkiej sentencji "
                    "zakończonej kropką, wykrzyknikiem lub znakiem zapytania. "
                    "Bez numeracji, cudzysłowów, list punktowanych i komentarzy."
                ),
            },
        ],
        "max_completion_tokens": 1024,
        "reasoning_effort": "none",
        "temperature": 0.9,
    }
    request = Request(
        GROQ_API_URL,
        data=json.dumps(request_data, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "gie-da/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=90) as response:
            response_body = response.read(MAX_API_RESPONSE_BYTES + 1)
    except HTTPError as error:
        if error.code == 429:
            raise GroqRateLimitError(
                _retry_delay_seconds(error)
            ) from error
        raise RuntimeError(
            f"Groq zwrócił błąd HTTP {error.code} podczas generowania sentencji."
        ) from error
    except (TimeoutError, URLError) as error:
        raise RuntimeError(
            "Nie udało się połączyć z Groq podczas generowania sentencji."
        ) from error

    if len(response_body) > MAX_API_RESPONSE_BYTES:
        raise RuntimeError("Odpowiedź Groq przekroczyła dozwolony rozmiar.")
    try:
        response_data = json.loads(response_body)
        content = response_data["choices"][0]["message"]["content"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "Groq zwrócił odpowiedź bez treści sentencji."
        ) from error
    if not isinstance(content, str):
        raise RuntimeError("Treść odpowiedzi Groq nie jest tekstem.")
    return parse_generated_sentences(content, count)


def _insert_daily_sentences(
    database_settings: dict[str, Any], sentences: list[str]
) -> None:
    """Dopisuje paczkę sentencji bez tworzenia duplikatów."""
    connection = mysql.connector.connect(**database_settings)
    try:
        cursor = connection.cursor()
        try:
            cursor.executemany(
                """
                INSERT IGNORE INTO daily_sentences (sentence)
                VALUES (%s)
                """,
                [(sentence,) for sentence in sentences],
            )
            connection.commit()
        except mysql.connector.Error:
            connection.rollback()
            raise
        finally:
            cursor.close()
    finally:
        connection.close()


def seed_daily_sentences(
    database_settings: dict[str, Any],
    api_key: str,
    target_count: int = TARGET_SENTENCE_COUNT,
    batch_size: int = SENTENCES_PER_REQUEST,
) -> int:
    """Uzupełnia pulę do wskazanej liczby, wznawiając od już zapisanych danych."""
    if target_count < 1 or batch_size < 1:
        raise ValueError("Liczba docelowa i rozmiar paczki muszą być dodatnie.")

    connection = mysql.connector.connect(**database_settings)
    try:
        cursor = connection.cursor()
        try:
            create_daily_sentences_table(cursor)
            connection.commit()
        finally:
            cursor.close()
    finally:
        connection.close()

    existing_sentences = fetch_daily_sentences(database_settings)
    if len(existing_sentences) >= target_count:
        print(f"Pula zawiera już {len(existing_sentences)} sentencji.")
        return len(existing_sentences)
    if not api_key.strip():
        raise RuntimeError("Brak klucza GROK_API_KEY wymaganego przez Groq.")

    normalized_existing = {
        unicodedata.normalize("NFKC", sentence).casefold()
        for sentence in existing_sentences
    }
    attempts_without_progress = 0
    rate_limit_retries = 0
    while len(existing_sentences) < target_count:
        requested_count = min(
            batch_size, target_count - len(existing_sentences)
        )
        try:
            generated_sentences = _request_sentence_batch(
                api_key, requested_count
            )
        except GroqRateLimitError as error:
            rate_limit_retries += 1
            if rate_limit_retries > MAX_RATE_LIMIT_RETRIES:
                raise RuntimeError(
                    "Groq nadal ogranicza żądania po pięciu ponowieniach; "
                    "uruchom skrypt ponownie później."
                ) from error
            retry_delay = max(
                MIN_SECONDS_BETWEEN_BATCHES,
                error.retry_after_seconds,
            )
            if retry_delay > 300:
                raise RuntimeError(
                    "Limit Groq wymaga dłuższej przerwy; uruchom skrypt "
                    f"ponownie za około {retry_delay:.0f} sekund."
                ) from error
            print(
                f"Groq ograniczył żądania; ponawiam za "
                f"{retry_delay:.0f} sekund."
            )
            time.sleep(retry_delay + 1.0)
            continue
        rate_limit_retries = 0
        new_sentences: list[str] = []
        for sentence in generated_sentences:
            normalized_sentence = unicodedata.normalize(
                "NFKC", sentence
            ).casefold()
            if normalized_sentence in normalized_existing:
                continue
            normalized_existing.add(normalized_sentence)
            new_sentences.append(sentence)

        if new_sentences:
            _insert_daily_sentences(database_settings, new_sentences)
            previous_count = len(existing_sentences)
            existing_sentences = fetch_daily_sentences(database_settings)
            if len(existing_sentences) > previous_count:
                attempts_without_progress = 0
                print(
                    f"Zapisano {len(existing_sentences)} z "
                    f"{target_count} sentencji."
                )
                if len(existing_sentences) < target_count:
                    time.sleep(MIN_SECONDS_BETWEEN_BATCHES)
                continue

        attempts_without_progress += 1
        if attempts_without_progress >= 3:
            raise RuntimeError(
                "Trzy kolejne paczki nie dodały nowych sentencji. "
                "Zasilanie przerwano bez usuwania zapisanych danych."
            )

    return len(existing_sentences)


def _database_settings_from_environment() -> dict[str, Any]:
    """Odczytuje wymagane ustawienia MySQL bez wypisywania haseł."""
    required_names = ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE")
    missing_names = [
        name for name in required_names if not os.environ.get(name)
    ]
    if missing_names:
        raise RuntimeError(
            "Brak wymaganych zmiennych MySQL: " + ", ".join(missing_names)
        )
    return {
        "host": os.environ["MYSQL_HOST"],
        "port": int(os.environ.get("MYSQL_PORT", "3306")),
        "user": os.environ["MYSQL_USER"],
        "password": os.environ["MYSQL_PASSWORD"],
        "database": os.environ["MYSQL_DATABASE"],
        "charset": "utf8mb4",
    }


def main() -> None:
    """Uruchamia jednorazowe zasilenie puli z poziomu kontenera API."""
    try:
        sentence_count = seed_daily_sentences(
            _database_settings_from_environment(),
            os.environ.get("GROK_API_KEY", ""),
        )
    except (mysql.connector.Error, RuntimeError, ValueError) as error:
        print(f"Nie udało się zasilić puli sentencji: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print(f"Pula sentencji zawiera {sentence_count} rekordów.")


if __name__ == "__main__":
    main()
