"""Testy walidacji odpowiedzi modelu używanej do zasilania puli sentencji."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daily_sentences import parse_generated_sentences  # noqa: E402


def test_parse_generated_sentences_normalizes_text_and_removes_duplicates() -> None:
    """Odrzuca puste i powtórzone pozycje, zachowując poprawne sentencje."""
    content = (
        "  - Mały krok prowadzi do celu. \n"
        "1. mały krok prowadzi do celu.\n"
        "\n"
        "Każdy dzień niesie nową możliwość."
    )

    assert parse_generated_sentences(content, 10) == [
        "Mały krok prowadzi do celu.",
        "Każdy dzień niesie nową możliwość.",
    ]


def test_parse_generated_sentences_respects_requested_limit() -> None:
    """Nie zwraca większej liczby sentencji niż można zapisać z paczki."""
    content = "Pierwsza sentencja.\nDruga sentencja."

    assert parse_generated_sentences(content, 1) == ["Pierwsza sentencja."]


def test_parse_generated_sentences_rejects_incomplete_lines() -> None:
    """Nie zapisuje pustej ani uciętej sentencji bez końcowego znaku."""
    with pytest.raises(RuntimeError, match="poprawnych sentencji"):
        parse_generated_sentences("Niepełna sentencja", 10)
