"""Backend: terminy sesji kasowej przy rozbieżnych zmianach czasu USA i Polski."""

import sys
from datetime import date, time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from schedule import cash_close, cash_open


@pytest.mark.parametrize(
    ("day", "start", "end"),
    [
        (date(2026, 3, 6), time(15, 30), time(22)),
        (date(2026, 3, 9), time(14, 30), time(21)),
        (date(2026, 3, 23), time(14, 30), time(21)),
        (date(2026, 3, 30), time(15, 30), time(22)),
        (date(2026, 10, 23), time(15, 30), time(22)),
        (date(2026, 10, 26), time(14, 30), time(21)),
        (date(2026, 11, 2), time(15, 30), time(22)),
    ],
)
def test_cash_hours_convert_both_timezones(day, start, end):
    """Sesja 09:30–16 ET jest prawidłowo przeliczana w obu okresach DST."""
    assert cash_open(day).astimezone(config.WARSAW).time() == start
    assert cash_close(day).astimezone(config.WARSAW).time() == end
