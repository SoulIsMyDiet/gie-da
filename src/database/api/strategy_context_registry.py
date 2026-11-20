"""Odczytuje kontekst strategii bez uruchamiania kodu algorytmów."""

# Backend
from __future__ import annotations

import ast
from pathlib import Path


class StrategyContextError(ValueError):
    """Informuje, że dla sygnału nie znaleziono jednoznacznego kontekstu."""


def load_strategy_context_registry(api_directory: Path) -> dict[str, str]:
    """Buduje mapę nazw algorytmów na ich stałe STRATEGY_CONTEXT.

    Args:
        api_directory: Katalog, w którym działa lokalne API.

    Returns:
        Mapa nazwy algorytmu zapisanej w bazie na opis strategii.

    Raises:
        StrategyContextError: Gdy różne źródła deklarują sprzeczne konteksty.
    """
    algorithm_directories = set(api_directory.glob("*_algorithm"))
    algorithm_directory = api_directory / "algorithm"
    if algorithm_directory.is_dir():
        algorithm_directories.add(algorithm_directory)
    source_root = api_directory.resolve().parent.parent / "algorithms"
    if source_root.is_dir():
        algorithm_directories.update(
            path
            for provider_directory in source_root.iterdir()
            if provider_directory.is_dir()
            for path in provider_directory.iterdir()
            if path.is_dir()
        )

    registry: dict[str, str] = {}
    for algorithm_directory in sorted(algorithm_directories):
        names, contexts = _read_algorithm_constants(algorithm_directory)
        if len(names) != 1 or len(contexts) != 1:
            continue
        algorithm_name = next(iter(names))
        strategy_context = next(iter(contexts))
        existing_context = registry.get(algorithm_name)
        if existing_context is not None and existing_context != strategy_context:
            raise StrategyContextError(
                f"Algorytm {algorithm_name!r} ma sprzeczne konteksty."
            )
        registry[algorithm_name] = strategy_context

    compatible_cpp_context = registry.get(
        "Grok-45/EURUSD/EURUSDStrategyV2"
    )
    if compatible_cpp_context is not None:
        registry["Grok-45"] = compatible_cpp_context
    return registry


def _read_algorithm_constants(
    algorithm_directory: Path,
) -> tuple[set[str], set[str]]:
    """Odczytuje literały nazw i kontekstów przez bezpieczne parsowanie AST."""
    names: set[str] = set()
    contexts: set[str] = set()
    for source_file in algorithm_directory.rglob("*.py"):
        try:
            syntax_tree = ast.parse(source_file.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, SyntaxError):
            continue
        for node in syntax_tree.body:
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id not in {
                    "STRATEGY_NAME",
                    "ALGORITHM_NAME",
                    "STRATEGY_CONTEXT",
                }:
                    continue
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    continue
                if not isinstance(value, str) or not value.strip():
                    continue
                if target.id in {"STRATEGY_NAME", "ALGORITHM_NAME"}:
                    names.add(value)
                else:
                    contexts.add(value)
    return names, contexts
