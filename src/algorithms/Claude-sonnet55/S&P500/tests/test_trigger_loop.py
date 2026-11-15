"""Backend: trwały stan i ponawianie odczytu zamkniętej minuty."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import trigger
from storage import SP500Storage


def test_checkpoint_round_trip_and_corruption(tmp_path):
    """Stan odtwarza minutę UTC i nie ukrywa błędnego pliku."""
    path = tmp_path / "stan.json"
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    assert trigger.load_checkpoint(path) is None
    trigger.save_checkpoint(path, stamp)
    assert trigger.load_checkpoint(path) == stamp
    path.write_text('{"last_handled_open":"błędny czas"}', encoding="utf-8")
    with pytest.raises(ValueError):
        trigger.load_checkpoint(path)


def test_single_run_uses_latest_closed_candle():
    """Jednorazowa analiza korzysta z ostatniej świecy bazy, a nie bieżącej minuty."""
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    now = stamp + timedelta(minutes=5)
    storage = Mock()
    storage.latest_closed_open.return_value = stamp
    with patch("trigger.analyze_candle", return_value=({"signal": "NONE"}, True)) as analyze:
        assert trigger.run_latest(storage, now)[1]
    analyze.assert_called_once_with(storage, stamp, now, allow_outside=True)


def test_previously_saved_candle_is_not_analyzed():
    """Ponowny POST po restarcie nie przelicza już zapisanego wyniku."""
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    storage = Mock()
    storage.get_processed_signal.return_value = {"signal": "LONG", "score": 80}
    with patch("trigger.generate_signal") as generate:
        result, saved = trigger.analyze_candle(storage, stamp, stamp + timedelta(minutes=1))
    assert not saved
    assert result == {"signal": "LONG", "score": 80}
    storage.load_context.assert_not_called()
    generate.assert_not_called()


def test_stored_result_is_loaded_with_exact_epoch():
    """Wcześniejszy wynik jest wyszukiwany po jednoznacznym czasie otwarcia UTC."""
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    cursor, connection = Mock(), Mock()
    connection.cursor.return_value = cursor
    cursor.fetchone.return_value = ('{"signal":"NONE","params":{"rsi5":50}}',)
    with patch.object(SP500Storage, "_connect", return_value=connection):
        result = SP500Storage({}).get_processed_signal(stamp)
    assert result == {"signal": "NONE", "params": {"rsi5": 50}}
    assert cursor.execute.call_args.args[1][-1] == int(stamp.timestamp())
    cursor.close.assert_called_once()
    connection.close.assert_called_once()


def test_loop_waits_until_candle_exists_and_checkpoints_once(tmp_path):
    """Świeca opóźniona o kilka sekund jest analizowana tylko raz."""
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    now = stamp + timedelta(minutes=1, seconds=5)
    clock = Mock()
    clock.now.return_value = now
    storage = Mock()
    storage.latest_processed_open.return_value = stamp - timedelta(minutes=1)
    path = tmp_path / "stan.json"
    with patch("trigger.datetime", clock), patch("trigger.is_trading_minute", return_value=True), \
            patch("trigger.is_signal_window", return_value=True), \
            patch("trigger.analyze_candle", side_effect=[
                ({"signal": "NONE", "blocked_by": ["missing_candle"]}, False),
                ({"signal": "NONE", "blocked_by": []}, True),
            ]) as analyze, \
            patch("trigger.play_signal_confirmation"), \
            patch("trigger.time.sleep", side_effect=[None, KeyboardInterrupt]):
        with pytest.raises(KeyboardInterrupt):
            trigger.run_loop(storage, path)
    assert analyze.call_count == 2
    assert json.loads(path.read_text(encoding="utf-8"))["last_handled_open"] == stamp.isoformat()


def test_loop_skips_expired_candle_without_analysis(tmp_path):
    """Po 20 sekundach brakująca minuta zostaje pominięta i zapisana w stanie."""
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    clock = Mock()
    clock.now.return_value = stamp + timedelta(minutes=1, seconds=21)
    storage = Mock()
    storage.latest_processed_open.return_value = stamp - timedelta(minutes=1)
    path = tmp_path / "stan.json"
    with patch("trigger.datetime", clock), patch("trigger.is_trading_minute", return_value=True), \
            patch("trigger.is_signal_window", return_value=True), \
            patch("trigger.analyze_candle") as analyze, \
            patch("trigger.time.sleep", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            trigger.run_loop(storage, path)
    analyze.assert_not_called()
    assert trigger.load_checkpoint(path) == stamp
