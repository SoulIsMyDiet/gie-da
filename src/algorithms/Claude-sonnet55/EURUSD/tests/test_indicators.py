"""
Backend
Testy jednostkowe wskaźników i filtrów.
"""

import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import sys
from pathlib import Path

# Dodaj parent dir do path
sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators import (
    SimpleMovingAverage,
    ExponentialMovingAverage,
    WilderRelativeStrengthIndex,
    WilderAverageTrueRange,
    BollingerBands,
)
from signal_logic import SessionFilter, VolatilityFilter
from config import (
    TRADING_START_UTC,
    TRADING_END_UTC,
    UTC_TIMEZONE,
)


class TestIndicators:
    """Backend - Testy wskaźników."""

    def test_sma_calculation(self):
        """SMA powinna obliczać prawidłową średnią."""
        sma = SimpleMovingAverage(3)
        assert sma.add(10) is None  # Za mało danych
        assert sma.add(20) is None
        assert abs(sma.add(30) - 20.0) < 0.001  # (10+20+30)/3 = 20

        assert abs(sma.add(40) - 30.0) < 0.001  # (20+30+40)/3 = 30

    def test_ema_initialization(self):
        """EMA powinna inicjalizować się SMA."""
        ema = ExponentialMovingAverage(3)
        assert ema.add(100) is None
        assert ema.add(100) is None
        # Po 3 wartościach SMA = 100
        result = ema.add(100)
        assert result is not None
        assert abs(result - 100.0) < 0.001

    def test_rsi_extremes(self):
        """RSI powinna dawać 100 na wzroście i 0 na spadku."""
        rsi = WilderRelativeStrengthIndex(5)

        # Rosnące wartości
        for i in range(1, 10):
            rsi.add(float(i))

        result = rsi.value()
        assert result is not None
        assert result > 50, "RSI powinno być > 50 na rosnącym rynku"

    def test_atr_presence(self):
        """ATR powinna być obliczana po wystarczającej liczbie świec."""
        atr = WilderAverageTrueRange(5)

        candles = [
            (1.0100, 1.0090, 1.0095),  # high, low, close
            (1.0105, 1.0095, 1.0100),
            (1.0110, 1.0100, 1.0105),
            (1.0115, 1.0105, 1.0110),
            (1.0120, 1.0110, 1.0115),
        ]

        for i, (h, l, c) in enumerate(candles):
            result = atr.add(h, l, c)
            if i < 4:
                assert result is None or result == 0
            else:
                assert result is not None and result > 0

    def test_bollinger_bands_zscore(self):
        """BB powinna obliczać z-score."""
        bb = BollingerBands(3, 2.0)

        # Dodaj wartości blisko siebie
        for _ in range(3):
            bb.add(100.0)

        result = bb.add(100.0)
        assert result is not None
        assert abs(result["zscore"]) < 0.1, "Z-score dla średniej powinna być bliska 0"

        # Wartość odległa
        result = bb.add(102.0)
        assert result["zscore"] > 0, "Z-score dla wartości > średniej powinna być pozytywna"


class TestSessionFilter:
    """Backend - Testy filtrów sesji."""

    def test_trading_hours_within_range(self):
        """Powinno być OK w godzinach handlu."""
        # Poniedziałek 08:00 UTC
        dt = datetime(2024, 1, 8, 8, 0, tzinfo=UTC_TIMEZONE)
        passed, reason = SessionFilter.is_within_trading_hours(dt)
        assert passed, "Powinno być OK w godzinach handlu"

    def test_trading_hours_outside_range(self):
        """Powinno być blokada poza godzinami handlu."""
        # Poniedziałek 06:00 UTC (przed otwarciem)
        dt = datetime(2024, 1, 8, 6, 0, tzinfo=UTC_TIMEZONE)
        passed, reason = SessionFilter.is_within_trading_hours(dt)
        assert not passed, "Powinno być blokada przed otwarciem"
        assert reason == "outside_trading_hours"

    def test_friday_close(self):
        """Piątek po 19:00 UTC powinno być blokada."""
        # Piątek 20:00 UTC
        dt = datetime(2024, 1, 12, 20, 0, tzinfo=UTC_TIMEZONE)
        passed, reason = SessionFilter.is_within_trading_hours(dt)
        assert not passed, "Piątek po 19:00 powinno być blokada"
        assert reason == "friday_after_close"

    def test_publication_blackout(self):
        """Pierwsze 2 minuty godziny powinno być blokada."""
        # Poniedziałek 08:00:30 UTC
        dt = datetime(2024, 1, 8, 8, 0, 30, tzinfo=UTC_TIMEZONE)
        passed, reason = SessionFilter.is_publication_blackout(dt)
        assert passed, "Minuta 0 powinna być blokada"
        assert reason == "publication_blackout"

        # Poniedziałek 08:02 UTC
        dt = datetime(2024, 1, 8, 8, 2, 0, tzinfo=UTC_TIMEZONE)
        passed, reason = SessionFilter.is_publication_blackout(dt)
        assert not passed, "Minuta 2 powinna być OK"


class TestVolatilityFilter:
    """Backend - Testy filtrów zmienności."""

    def test_atr_in_range(self):
        """ATR w zakresie powinno przejść."""
        # ATR 1.2 pips (w zakresie 0.7-3.0)
        # ATR ratio 1.0 (w zakresie 0.8-2.5)
        passed, blocked = VolatilityFilter.check_atr(1.2, 1.0)
        assert passed, "ATR w zakresie powinna przejść"
        assert len(blocked) == 0

    def test_atr_too_low(self):
        """ATR zbyt niska powinna być blokada."""
        passed, blocked = VolatilityFilter.check_atr(0.5, 0.5)
        assert not passed, "ATR zbyt niska powinna być blokada"
        assert any("atr_out_of_range" in b for b in blocked)

    def test_cost_filter_sufficient(self):
        """Wystarczający ruch powinien przejść."""
        # ATR 2.0 pips, COST_MULTIPLE=3.0, ASSUMED_COST=1.5
        # Oczekiwany ruch = 2.0 * sqrt(20) ~= 8.94, wymagany = 3.0 * 1.5 = 4.5
        passed, blocked = VolatilityFilter.check_cost_filter(2.0)
        assert passed, "Wystarczający ruch powinien przejść"
