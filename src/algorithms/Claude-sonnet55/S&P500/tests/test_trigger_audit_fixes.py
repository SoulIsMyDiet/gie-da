"""Backend: regresje ponownej obsługi zapisanych i przeterminowanych świec."""

import json
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from mysql.connector import Error as MySQLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import trigger


OPENED = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
CLOSED = OPENED + timedelta(minutes=1)


def _saved_result(direction="LONG"):
    """Tworzy pełny zapis JSON historycznej świecy i wybranego kierunku."""
    result = trigger.generate_signal([], now=CLOSED)
    result.update(
        price=6000.0, candle_open_epoch=int(OPENED.timestamp()),
        candle_open_time=OPENED.astimezone(trigger.config.WARSAW).isoformat(),
        timestamp_epoch=int(CLOSED.timestamp()),
        timestamp=CLOSED.astimezone(trigger.config.WARSAW).isoformat(),
        signal=direction, setup="TREND_PULLBACK", score=90,
        tp_price=6005.0, sl_price=5998.0, tp_points=5.0, sl_points=2.0,
        reasons=["historyczne uzasadnienie otwarcia"],
        blocked_by=["historyczna_blokada"],
        params={"data_age_minutes": 0.1, "atr": 2.5, "nested": {"value": 7}},
    )
    return result


def _storage(previous):
    """Przygotowuje atrapę bazy z podanym wynikiem i zmienioną świecą."""
    storage = Mock()
    storage.get_processed_signal.return_value = previous
    storage.load_context.return_value = ([{
        "timestamp_utc": OPENED, "open": 6100.0, "high": 6101.0,
        "low": 6099.0, "close": 6100.0, "volume": 10.0,
    }], {})
    return storage


@pytest.mark.parametrize("direction", ["LONG", "SHORT", "NONE"])
@pytest.mark.parametrize("allow_outside", [False, True])
def test_stale_saved_result_is_inactive_copy(direction, allow_outside):
    """Stary zapis zachowuje cenę i diagnostykę bez ponownego odczytu historii."""
    previous = _saved_result(direction)
    original = deepcopy(previous)
    storage = _storage(previous)
    now = CLOSED + timedelta(minutes=4, seconds=30)
    result, saved = trigger.analyze_candle(storage, OPENED, now, allow_outside)
    assert set(result) >= set(trigger.generate_signal([], now=now))
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    assert result["signal"] == "NONE"
    assert result["setup"] is None and result["score"] == 0
    assert all(result[field] is None for field in ("tp_price", "sl_price", "tp_points", "sl_points"))
    assert result["reasons"] == []
    assert result["blocked_by"] == ["historyczna_blokada", "stale_data"]
    assert result["params"] == {"data_age_minutes": 4.5, "atr": 2.5, "nested": {"value": 7}}
    assert result["price"] == 6000.0
    assert result["timestamp"] == previous["timestamp"]
    assert result["candle_open_epoch"] == int(OPENED.timestamp())
    assert not saved
    storage.load_context.assert_not_called()
    storage.save_signal.assert_not_called()
    result["params"]["nested"]["value"] = 99
    assert previous == original


@pytest.mark.parametrize(("age", "stale"), [
    (timedelta(minutes=3) - timedelta(microseconds=1), False),
    (timedelta(minutes=3), False),
    (timedelta(minutes=3, microseconds=1), True),
])
def test_saved_result_three_minute_boundary(age, stale):
    """Limit trzech minut jest włącznie; dopiero późniejszy wynik jest nieaktywny."""
    previous = _saved_result()
    storage = _storage(previous)
    result, saved = trigger.analyze_candle(storage, OPENED, CLOSED + age)
    assert not saved
    assert (result["signal"] == "NONE") is stale
    assert ("stale_data" in result["blocked_by"]) is stale
    if not stale:
        assert result == previous
    storage.load_context.assert_not_called()
    storage.save_signal.assert_not_called()


@pytest.mark.parametrize("allow_outside", [False, True])
def test_stale_saved_outside_window_preserves_original_diagnostics(allow_outside):
    """Po oknie usuwa blokadę luki, lecz zachowuje pozostałe dane diagnostyczne."""
    previous = _saved_result()
    previous["blocked_by"] = ["data_gap", "stale_data"]
    storage = _storage(previous)
    now = datetime(2026, 11, 16, 21, 51, tzinfo=timezone.utc)
    result, saved = trigger.analyze_candle(storage, OPENED, now, allow_outside)
    assert result["blocked_by"] == ["stale_data", "outside_session"]
    assert result["params"]["data_age_minutes"] == (now - CLOSED).total_seconds() / 60
    assert not saved
    storage.load_context.assert_not_called()
    storage.save_signal.assert_not_called()


def test_saved_none_with_data_gap_outside_session_keeps_gap_counts():
    """W weekend stary NONE nie zgłasza blokady luki, ale zachowuje jej liczniki."""
    previous = _saved_result("NONE")
    previous["blocked_by"] = ["data_gap", "warmup"]
    previous["params"].update(missing_candles_60m=4, max_consecutive_missing=2)
    original = deepcopy(previous)
    storage = _storage(previous)
    storage.latest_closed_open.return_value = OPENED
    now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
    result, saved = trigger.run_latest(storage, now)
    assert result["signal"] == "NONE"
    assert result["blocked_by"] == ["warmup", "stale_data", "outside_session"]
    assert result["params"]["missing_candles_60m"] == 4
    assert result["params"]["max_consecutive_missing"] == 2
    assert result["params"]["data_age_minutes"] == (now - CLOSED).total_seconds() / 60
    assert previous == original
    assert not saved
    storage.load_context.assert_not_called()
    storage.save_signal.assert_not_called()


def test_saved_stale_at_end_of_window_does_not_add_outside_session():
    """Cała ostatnia minuta okna pozostaje w oknie również dla starego zapisu."""
    now = datetime(2026, 11, 16, 20, 50, 59, tzinfo=timezone.utc)
    result, saved = trigger.analyze_candle(_storage(_saved_result()), OPENED, now)
    assert "stale_data" in result["blocked_by"]
    assert "outside_session" not in result["blocked_by"]
    assert not saved


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_fresh_unprocessed_candle_still_saves_signal(direction):
    """Nowa świeża świeca nadal korzysta z historii i zapisuje prawidłowy sygnał."""
    storage = _storage(None)
    storage.save_signal.return_value = True
    expected = _saved_result(direction)
    with patch("trigger.generate_signal", return_value=expected) as generate:
        result, saved = trigger.analyze_candle(storage, OPENED, CLOSED)
    assert result is expected and saved
    generate.assert_called_once()
    storage.save_signal.assert_called_once_with(expected, 1)


def test_latest_outside_window_uses_saved_price_even_if_history_changed():
    """Jednorazowy odczyt poza oknem nie przelicza zapisanego wyniku ze zmienionej historii."""
    storage = _storage(_saved_result())
    storage.latest_closed_open.return_value = OPENED
    result, saved = trigger.run_latest(storage, datetime(2026, 11, 16, 21, 51, tzinfo=timezone.utc))
    assert result["signal"] == "NONE"
    assert result["price"] == 6000.0
    assert {"stale_data", "outside_session"} <= set(result["blocked_by"])
    assert not saved
    storage.load_context.assert_not_called()
    storage.save_signal.assert_not_called()


def test_unprocessed_stale_candle_is_evaluated_normally():
    """Brak zapisu nadal powoduje ocenę faktycznej świecy i jej aktualnej ceny."""
    storage = _storage(None)
    storage.save_signal.return_value = False
    result, saved = trigger.analyze_candle(storage, OPENED, CLOSED + timedelta(minutes=4))
    assert result["signal"] == "NONE"
    assert result["price"] == 6100.0
    assert "stale_data" in result["blocked_by"]
    assert not saved
    storage.load_context.assert_called_once_with(OPENED)


def test_unprocessed_outside_window_stays_unsaved():
    """Jednorazowy odczyt poza oknem bez zapisu zachowuje dotychczasową blokadę."""
    storage = _storage(None)
    storage.latest_closed_open.return_value = OPENED
    now = datetime(2026, 11, 16, 21, 51, tzinfo=timezone.utc)
    result, saved = trigger.run_latest(storage, now)
    assert result["signal"] == "NONE"
    assert {"stale_data", "outside_session"} <= set(result["blocked_by"])
    assert result["price"] == 6100.0
    assert not saved
    storage.save_signal.assert_not_called()


def test_latest_analyzes_historical_candle_outside_signal_window():
    """Jednorazowy odczyt poza sesją nadal analizuje historyczną świecę spoza okna."""
    opened = datetime(2026, 11, 16, 13, 0, tzinfo=timezone.utc)
    now = datetime(2026, 11, 16, 22, 30, tzinfo=timezone.utc)
    storage = Mock()
    storage.latest_closed_open.return_value = opened
    storage.load_context.return_value = ([{
        "timestamp_utc": opened, "open": 6100.0, "high": 6101.0,
        "low": 6099.0, "close": 6100.0, "volume": 10.0,
    }], {})
    result, saved = trigger.run_latest(storage, now)
    assert result["price"] == 6100.0
    assert result["candle_open_epoch"] == int(opened.timestamp())
    assert result["params"]["data_age_minutes"] == (now - opened - timedelta(minutes=1)).total_seconds() / 60
    assert {"stale_data", "outside_session"} <= set(result["blocked_by"])
    assert result["signal"] == "NONE" and not saved
    storage.get_processed_signal.assert_not_called()
    storage.load_context.assert_called_once_with(opened)
    storage.save_signal.assert_not_called()


def test_forming_and_off_schedule_candles_keep_existing_guards():
    """Niezamknięta świeca i świeca poza oknem nie odpytują ani nie zapisują bazy."""
    storage = _storage(_saved_result())
    forming, saved = trigger.analyze_candle(storage, OPENED, OPENED + timedelta(seconds=30))
    assert not saved and forming["blocked_by"] == ["forming_candle"]
    weekend = datetime(2026, 11, 15, 15, 0, tzinfo=timezone.utc)
    outside, saved = trigger.analyze_candle(storage, weekend, weekend + timedelta(minutes=10))
    assert not saved and outside["blocked_by"] == ["outside_session"]
    assert not storage.mock_calls


def test_incomplete_saved_json_keeps_requested_candle_timestamps():
    """Niepełny dawny zapis nadal otrzymuje pełny JSON ze znacznikami żądanej świecy."""
    storage = _storage({"signal": "LONG", "blocked_by": []})
    result, saved = trigger.analyze_candle(storage, OPENED, CLOSED + timedelta(minutes=4))
    assert result["candle_open_epoch"] == int(OPENED.timestamp())
    assert result["timestamp_epoch"] == int(CLOSED.timestamp())
    assert result["params"]["data_age_minutes"] == 4
    assert not saved
    storage.save_signal.assert_not_called()


def test_existing_validation_and_database_errors_propagate():
    """Niepoprawne czasy i błędy bazy nie są maskowane przez nową ścieżkę."""
    storage = _storage(None)
    with pytest.raises(ValueError):
        trigger.analyze_candle(storage, OPENED.replace(second=1), CLOSED)
    storage.get_processed_signal.side_effect = MySQLError("błąd bazy")
    with pytest.raises(MySQLError):
        trigger.analyze_candle(storage, OPENED, CLOSED + timedelta(minutes=4))


def test_api_stale_saved_emits_none_without_beep(capsys):
    """Ścieżka API zwraca pełny JSON bez ponownego zapisu i bez dźwięku."""
    storage = _storage(_saved_result())
    clock = Mock(wraps=datetime)
    clock.now.return_value = CLOSED + timedelta(minutes=4)
    with patch.object(sys, "argv", ["trigger.py", "--api-candle-open-epoch", str(int(OPENED.timestamp()))]), \
            patch("trigger.datetime", clock), patch("trigger.SP500Storage", return_value=storage), \
            patch("trigger.play_signal_confirmation") as beep:
        assert trigger.main() == 3
    result = json.loads(capsys.readouterr().out)
    assert result["signal"] == "NONE" and "stale_data" in result["blocked_by"]
    assert result["price"] == 6000.0
    storage.load_context.assert_not_called()
    storage.save_signal.assert_not_called()
    beep.assert_not_called()


def test_loop_restart_stale_saved_does_not_resave_or_beep():
    """Restart po zapisie sygnału i przed zapisem stanu zwraca nieaktywną kopię."""
    path = Path.cwd() / ("test-stanu-audyt-" + uuid4().hex + ".json")
    storage = _storage(_saved_result("SHORT"))
    clock = Mock(wraps=datetime)
    clock.now.return_value = CLOSED + timedelta(minutes=4)
    try:
        trigger.save_checkpoint(path, OPENED - timedelta(minutes=1))
        with patch("trigger.datetime", clock), patch.object(trigger.config, "BEEP_ENABLED", True), \
                patch("trigger.play_signal_confirmation") as beep, \
                patch("trigger._emit", side_effect=KeyboardInterrupt) as emit:
            with pytest.raises(KeyboardInterrupt):
                trigger.run_loop(storage, path)
        result = emit.call_args.args[0]
        assert result["signal"] == "NONE" and "stale_data" in result["blocked_by"]
        assert result["price"] == 6000.0
        assert trigger.load_checkpoint(path) == OPENED
        storage.load_context.assert_not_called()
        storage.save_signal.assert_not_called()
        beep.assert_not_called()
    finally:
        path.unlink(missing_ok=True)
