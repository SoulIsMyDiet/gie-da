"""Testy zapisu, deduplikacji i stanu lokalnej pętli."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from claude_btcusd_test import config
from claude_btcusd_test.storage import BTCUSDStorage
from claude_btcusd_test.trigger import (
    analyze_candle,
    load_checkpoint,
    save_checkpoint,
)

UTC = timezone.utc


def result_payload() -> dict:
    """Tworzy poprawny wynik minimalny dla adaptera zapisu."""
    opened = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
    closed = opened + timedelta(minutes=1)
    return {
        "signal": "NONE",
        "price": 100.0,
        "timestamp": int(closed.timestamp()),
        "timestamp_epoch": int(closed.timestamp()),
        "candle_open_epoch": int(opened.timestamp()),
        "candle_close_time": closed.isoformat(),
        "signal_parameters": {
            "price": 100.0,
            "signal": "NONE",
            "timestamp": int(closed.timestamp()),
            "instrument": "BTC/USD",
            "reason": "Brak setupu.",
        },
    }


def test_none_is_recorded_as_evaluation_but_not_signal_by_default(
    monkeypatch,
) -> None:
    """Ustawienie domyślne pomija NONE w signals, lecz zapisuje ewaluację."""
    monkeypatch.setattr(config, "SAVE_NONE_SIGNALS", False)
    repository = Mock()
    storage = BTCUSDStorage(repository)

    storage.save(result_payload(), 100)

    options = repository.save_algorithm_signal.call_args.kwargs
    assert options["record_evaluation"] is True
    assert options["save_signal"] is False
    assert options["save_none_signal"] is False
    assert options["save_signal_potential"] is False


def test_save_none_flag_can_store_none(monkeypatch) -> None:
    """Jedna flaga w konfiguracji włącza zapis wyników NONE."""
    monkeypatch.setattr(config, "SAVE_NONE_SIGNALS", True)
    repository = Mock()

    BTCUSDStorage(repository).save(result_payload(), 100)

    options = repository.save_algorithm_signal.call_args.kwargs
    assert options["save_signal"] is True
    assert options["save_none_signal"] is True


def test_duplicate_evaluation_is_not_loaded_or_saved() -> None:
    """Trwały marker zapobiega ponownej analizie tej samej minuty."""
    opened = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
    storage = Mock()
    storage.has_processed.return_value = True

    result, saved = analyze_candle(
        storage, opened, opened + timedelta(minutes=1)
    )

    assert not saved
    assert result["blocked_by"] == ["already_processed"]
    storage.load_context.assert_not_called()
    storage.save.assert_not_called()


def test_checkpoint_survives_restart(tmp_path: Path) -> None:
    """Stan pętli można odtworzyć po restarcie procesu."""
    checkpoint = tmp_path / "btc_state.json"
    opened = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)

    save_checkpoint(checkpoint, opened)

    assert load_checkpoint(checkpoint) == opened


def test_missing_checkpoint_returns_none(tmp_path: Path) -> None:
    """Brak pliku stanu oznacza pierwsze uruchomienie pętli."""
    assert load_checkpoint(tmp_path / "absent.json") is None


def test_storage_rejects_oversized_signal_parameters() -> None:
    """Adapter nie zapisze JSON-u przekraczającego limit 512 bajtów."""
    repository = Mock()
    payload = result_payload()
    payload["signal_parameters"]["reason"] = "ż" * 300

    try:
        BTCUSDStorage(repository).save(payload, 100)
    except ValueError as error:
        assert "512" in str(error)
    else:
        raise AssertionError("Oczekiwano odrzucenia zbyt dużego JSON-u.")

    repository.save_algorithm_signal.assert_not_called()
