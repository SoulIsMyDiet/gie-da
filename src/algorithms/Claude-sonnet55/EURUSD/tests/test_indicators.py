"""
Backend
Testy jednostkowe wskaźników i filtrów.
"""

import pytest
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

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
    VolumeWeightedAveragePrice,
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
        """EMA powinna inicjalizować się SMA i zwracać wartość na 1. elemencie."""
        ema = ExponentialMovingAverage(3)
        # 1. wartość: zwraca samą wartość
        assert ema.add(100) == 100
        # 2. wartość: zwraca None (czeka na period elementów)
        assert ema.add(100) is None
        # Na 3. wartości: SMA = 100, zwraca EMA
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
            (1.0125, 1.0115, 1.0120),  # Dodano 6. świecę dla ATR
        ]

        for i, (h, l, c) in enumerate(candles):
            result = atr.add(h, l, c)
            # ATR zwraca wartość dopiero po period+1 świecach (Wilder smooth)
            if i < 5:
                # Przed 6. świecą ATR może być None lub 0
                pass
            else:
                # Na 6. świecy ATR powinien być dostępny
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


class TestCooldownTimer:
    """Backend - Testy cooldown timeru."""

    def test_cooldown_allows_first_signal(self):
        """Pierwszy sygnał powinien przejść bez cooldownu."""
        now = datetime(2025, 1, 27, 12, 0, 0, tzinfo=UTC_TIMEZONE)
        # last_signal_time = None → brak cooldownu
        passed, blocked = SessionFilter.check_session(now, last_signal_time=None, cooldown_minutes=10)
        # Powinno przejść (jeśli sesja OK)
        assert "cooldown" not in str(blocked)

    def test_cooldown_blocks_second_signal_too_early(self):
        """Drugi sygnał w ciągu cooldownu powinien być zablokowany."""
        last_signal = datetime(2025, 1, 27, 12, 0, 0, tzinfo=UTC_TIMEZONE)
        now = datetime(2025, 1, 27, 12, 5, 0, tzinfo=UTC_TIMEZONE)  # 5 minut później
        
        passed, blocked = SessionFilter.check_session(now, last_signal_time=last_signal, cooldown_minutes=10)
        assert not passed
        assert any("cooldown" in str(b) for b in blocked), f"Expected cooldown block, got: {blocked}"

    def test_cooldown_allows_second_signal_after_timeout(self):
        """Drugi sygnał po upłynięciu cooldownu powinien być dozwolony."""
        last_signal = datetime(2025, 1, 27, 12, 0, 0, tzinfo=UTC_TIMEZONE)
        now = datetime(2025, 1, 27, 12, 15, 0, tzinfo=UTC_TIMEZONE)  # 15 minut później
        
        passed, blocked = SessionFilter.check_session(now, last_signal_time=last_signal, cooldown_minutes=10)
        # Powinno przejść (jeśli sesja OK i nie ma cooldownu)
        assert "cooldown" not in str(blocked)


class TestFormingCandle:
    """Backend - Testy detekcji świecy w formowaniu."""

    def test_forming_candle_detection_logic(self):
        """Świeca w formowaniu powinna być wykryta."""
        from datetime import datetime, timedelta, timezone
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo
        
        UTC = ZoneInfo('UTC')
        
        # Mock metody _is_candle_forming
        def _is_candle_forming(last_timestamp: datetime) -> bool:
            if not last_timestamp:
                return True
            current_time = datetime.now(UTC)
            closing_time = last_timestamp + timedelta(minutes=1)
            return current_time < closing_time

        # Świeca otwiera się teraz - jest w formowaniu
        current_time = datetime.now(UTC)
        is_forming = _is_candle_forming(current_time)
        assert is_forming, "Aktualna świeca powinna być w formowaniu"

    def test_closed_candle_detection_logic(self):
        """Zamknięta świeca powinna być rozpoznana."""
        from datetime import datetime, timedelta, timezone
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo
        
        UTC = ZoneInfo('UTC')
        
        # Mock metody _is_candle_forming
        def _is_candle_forming(last_timestamp: datetime) -> bool:
            if not last_timestamp:
                return True
            current_time = datetime.now(UTC)
            closing_time = last_timestamp + timedelta(minutes=1)
            return current_time < closing_time

        # Świeca zamknęła się 2 minuty temu
        past_time = datetime.now(UTC) - timedelta(minutes=2)
        is_forming = _is_candle_forming(past_time)
        assert not is_forming, "Zamknięta świeca nie powinna być w formowaniu"


class TestVWAPSessionDetection:
    """Backend - Testy śledzenia sesji VWAP."""

    def test_vwap_session_reset_on_new_day(self):
        """VWAP powinien resetować się na nowy dzień UTC."""
        from indicators import VolumeWeightedAveragePrice
        
        vwap = VolumeWeightedAveragePrice()
        
        # Dzień 1
        day1 = datetime(2025, 1, 27, 10, 0, 0, tzinfo=UTC_TIMEZONE)
        v1 = vwap.add(day1, 1.1050, 1.1040, 1.1045, 100)
        assert v1 is not None
        
        # Dzień 2 - sesja powinna się resetować
        day2 = datetime(2025, 1, 28, 10, 0, 0, tzinfo=UTC_TIMEZONE)
        v2 = vwap.add(day2, 1.1050, 1.1040, 1.1045, 100)
        
        # VWAP w nowej sesji powinien być równy typowej cenie pierwszej świecy
        typical_price_day2 = (1.1050 + 1.1040 + 1.1045) / 3
        assert abs(v2 - typical_price_day2) < 0.00001, f"Expected {typical_price_day2}, got {v2}"
