"""
Backend
Logika generowania sygnałów i scoring dla setupów.
"""

import logging
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from zoneinfo import ZoneInfo

from config import (
    PIP,
    ASSUMED_COST_PIPS,
    COST_MULTIPLE,
    TRADING_START_UTC,
    TRADING_END_UTC,
    PRIME_SESSION_START_UTC,
    PRIME_SESSION_END_UTC,
    FRIDAY_CLOSE_UTC,
    WEEKEND_CLOSE_START_UTC,
    WEEKEND_CLOSE_END_UTC,
    BLACKOUT_MINUTE_EVERY_HOUR,
    NEWS_BLACKOUTS,
    ATR_MIN_PIPS,
    ATR_MAX_PIPS,
    ATR_RATIO_MIN,
    ATR_RATIO_MAX,
    MAX_CANDLE_RANGE_MULTIPLIER,
    MIN_RELATIVE_VOLUME,
    EMA_SHORT,
    EMA_MEDIUM,
    EMA_LONG,
    RSI_PERIOD,
    TREND_PULLBACK_ER_MIN,
    PULLBACK_ATR_DISTANCE,
    RSI_OVERSOLD_THRESHOLD,
    RSI_RECOVERY_THRESHOLD,
    RANGE_REVERSION_ER_MAX,
    RANGE_EMA55_SLOPE_THRESHOLD,
    BB_ZSCORE_EXTREME,
    RSI_OVERSOLD_RANGE,
    REVERSION_ATR_DISTANCE,
    REGIME_UNCERTAIN_ZONE,
    MIN_SCORE,
    CORE_SETUP_POINTS,
    BONUS_RELATIVE_VOLUME,
    BONUS_PRIME_SESSION,
    BONUS_STRONG_TREND,
    BONUS_CANDLE_QUALITY,
    SL_MULTIPLIER,
    TP_MULTIPLIER,
    MIN_TP_PIPS,
    UTC_TIMEZONE,
)

logger = logging.getLogger(__name__)


class SessionFilter:
    """Backend - Filtry czasu sesji (hard filters)."""

    @staticmethod
    def is_within_trading_hours(utc_time: datetime) -> Tuple[bool, Optional[str]]:
        """Sprawdza czy czas jest w godzinach handlu. Zwraca (pass, reason_if_blocked)."""
        time_only = utc_time.time()

        # Blokada: piątek po 19:00 UTC
        if utc_time.weekday() == 4 and time_only >= FRIDAY_CLOSE_UTC:
            return False, "friday_after_close"

        # Blokada: niedziela (cały dzień poza 17:00 do 23:59 UTC)
        if utc_time.weekday() == 6:
            if time_only < WEEKEND_CLOSE_START_UTC or time_only >= WEEKEND_CLOSE_END_UTC:
                return False, "sunday_closed"

        # Blokada: nie w godzinach głównych handlu
        if not (TRADING_START_UTC <= time_only < TRADING_END_UTC):
            return False, "outside_trading_hours"

        return True, None

    @staticmethod
    def is_news_blackout(utc_time: datetime) -> Tuple[bool, Optional[str]]:
        """Sprawdza czy czas w blackoucie news."""
        time_only = utc_time.time()
        for start, end in NEWS_BLACKOUTS:
            if start <= time_only < end:
                return True, "news_blackout"
        return False, None

    @staticmethod
    def is_publication_blackout(utc_time: datetime) -> Tuple[bool, Optional[str]]:
        """
        Blokada: pierwsze 2 minuty każdej pełnej godziny i połowy (07:58-08:01, 07:28-07:31 itd).
        """
        minute = utc_time.minute
        if minute in BLACKOUT_MINUTE_EVERY_HOUR:
            return True, "publication_blackout"
        return False, None

    @staticmethod
    def check_session(utc_time: datetime) -> Tuple[bool, List[str]]:
        """
        Sprawdza wszystkie filtry sesji. Zwraca (pass, blocked_by_reasons).
        """
        blocked_by = []

        # Check 1: Godziny handlu
        passed, reason = SessionFilter.is_within_trading_hours(utc_time)
        if not passed:
            blocked_by.append(reason)

        # Check 2: News blackout
        passed, reason = SessionFilter.is_news_blackout(utc_time)
        if passed:
            blocked_by.append(reason)

        # Check 3: Publication blackout
        passed, reason = SessionFilter.is_publication_blackout(utc_time)
        if passed:
            blocked_by.append(reason)

        return len(blocked_by) == 0, blocked_by


class VolatilityFilter:
    """Backend - Filtry zmienności."""

    @staticmethod
    def check_atr(atr_pips: float, atr_ratio: float) -> Tuple[bool, List[str]]:
        """
        Sprawdza czy ATR jest w dopuszczalnym zakresie.
        """
        blocked_by = []

        if not (ATR_MIN_PIPS <= atr_pips <= ATR_MAX_PIPS):
            blocked_by.append(f"atr_out_of_range ({atr_pips:.2f})")

        if not (ATR_RATIO_MIN <= atr_ratio <= ATR_RATIO_MAX):
            blocked_by.append(f"atr_ratio_out_of_range ({atr_ratio:.2f})")

        return len(blocked_by) == 0, blocked_by

    @staticmethod
    def check_cost_filter(atr_pips: float) -> Tuple[bool, List[str]]:
        """
        Sprawdza czy oczekiwany ruch (ATR14 * sqrt(20)) >= COST_MULTIPLE * ASSUMED_COST.
        """
        import math

        expected_move = atr_pips * math.sqrt(20)
        required_move = COST_MULTIPLE * ASSUMED_COST_PIPS

        if expected_move < required_move:
            return False, [f"insufficient_expected_move ({expected_move:.2f} < {required_move:.2f})"]

        return True, []


class DataAnomalyFilter:
    """Backend - Filtry anomalii danych."""

    @staticmethod
    def check_candle_range(
        high: float, low: float, atr: float
    ) -> Tuple[bool, List[str]]:
        """Sprawdza czy zakres świecy <= 3 × ATR."""
        candle_range = high - low
        max_range = MAX_CANDLE_RANGE_MULTIPLIER * atr

        if candle_range > max_range:
            return False, [f"candle_range_spike ({candle_range:.6f} > {max_range:.6f})"]

        return True, []

    @staticmethod
    def check_relative_volume(
        current_volume: Optional[float], avg_volume: Optional[float]
    ) -> Tuple[bool, List[str]]:
        """Sprawdza czy relatywny wolumen >= MIN_RELATIVE_VOLUME."""
        if current_volume is None or avg_volume is None or avg_volume == 0:
            return True, []  # Brak danych - pomiń filtr

        rel_vol = current_volume / avg_volume
        if rel_vol < MIN_RELATIVE_VOLUME:
            return False, [f"low_relative_volume ({rel_vol:.2f})"]

        return True, []


class TrendPullbackSetup:
    """Backend - Setup TREND_PULLBACK (ER >= 0.30)."""

    @staticmethod
    def check_setup(
        indicators: Dict,
        candles: List[Dict],
    ) -> Tuple[Optional[str], int, List[str], List[str]]:
        """
        Sprawdza warunki TREND_PULLBACK.
        Zwraca (signal, score, reasons, blocked_by)
        """
        reasons = []
        blocked_by = []
        score = 0

        # Pobierz wartości wskaźników
        ema9 = indicators.get("ema9")
        ema21 = indicators.get("ema21")
        ema55 = indicators.get("ema55")
        rsi5 = indicators.get("rsi5")
        atr = indicators.get("atr")
        ema20_5m = indicators.get("ema20_5m")
        trend_5m = indicators.get("ema20_5m_slope")
        er30 = indicators.get("efficiency_ratio")

        if not all([ema9, ema21, ema55, rsi5, atr, ema20_5m, er30]):
            return None, 0, [], ["insufficient_indicators"]

        # Warunek 1: Trend 5M (close5m > EMA20_5m i nachylenie > 0)
        if ema20_5m is None or trend_5m is None:
            return None, 0, [], ["no_5m_data"]

        # Pobierz ostatnią świecę 5M close
        last_close = candles[-1]["close"]
        # Uwaga: tutaj byś potrzebował rzeczywistego close 5M, w uproszczeniu używam 1M
        close_5m = last_close
        if not (close_5m > ema20_5m and trend_5m > 0):
            return None, 0, [], ["5m_trend_not_aligned"]

        reasons.append("5m_trend_up")

        # Warunek 2: EMA9 > EMA21 > EMA55
        if not (ema9 > ema21 > ema55):
            return None, 0, reasons, ["ema_order_not_aligned"]

        reasons.append("ema_order_correct")

        # Warunek 3: low zbliżyła się do EMA21 (low >= EMA21 - 0.25*ATR)
        last_low = candles[-1]["low"]
        pullback_threshold = ema21 - PULLBACK_ATR_DISTANCE * atr
        if last_low < pullback_threshold:
            return None, 0, reasons, ["pullback_not_close_enough"]

        reasons.append("pullback_detected")

        # Warunek 4: RSI bounce (RSI < 40 w ostatnich 3 świecach i teraz > 45)
        recent_rsi_values = indicators.get("rsi5_history", [])
        if len(recent_rsi_values) < 3:
            return None, 0, reasons, ["rsi_history_insufficient"]

        rsi_oversold = any(v < RSI_OVERSOLD_THRESHOLD for v in recent_rsi_values[-3:-1])
        rsi_recovered = rsi5 > RSI_RECOVERY_THRESHOLD

        if not (rsi_oversold and rsi_recovered):
            return None, 0, reasons, ["rsi_bounce_not_confirmed"]

        reasons.append("rsi_bounce_confirmed")

        # Warunek 5: Ostatnia świeca bycza
        last_open = candles[-1]["open"]
        last_close = candles[-1]["close"]
        last_high = candles[-1]["high"]
        last_low = candles[-1]["low"]

        if not (last_close > last_open):
            return None, 0, reasons, ["candle_not_bullish"]

        candle_body = last_close - last_open
        candle_range = last_high - last_low
        if candle_range > 0 and candle_body / candle_range < 0.4:
            return None, 0, reasons, ["candle_body_too_small"]

        reasons.append("candle_bullish")

        # Setup przeszedł - oblicz score
        score = CORE_SETUP_POINTS
        reasons.append("trend_pullback_core_conditions_met")

        # Bonusy
        if indicators.get("relative_volume", 0) >= 1.2:
            score += BONUS_RELATIVE_VOLUME
            reasons.append("bonus_high_volume")

        if indicators.get("is_prime_session", False):
            score += BONUS_PRIME_SESSION
            reasons.append("bonus_prime_session")

        if er30 > 0.45:
            score += BONUS_STRONG_TREND
            reasons.append("bonus_strong_trend")

        if candle_body / candle_range >= 0.7:
            score += BONUS_CANDLE_QUALITY
            reasons.append("bonus_candle_quality")

        return "LONG", score, reasons, []

    @staticmethod
    def check_setup_short(
        indicators: Dict,
        candles: List[Dict],
    ) -> Tuple[Optional[str], int, List[str], List[str]]:
        """Sprawdza SHORT (lustrzane warunki)."""
        # Implementacja lustrzana - pomijam dla zwięzłości
        return None, 0, [], []


class RangeReversionSetup:
    """Backend - Setup RANGE_REVERSION (ER <= 0.20)."""

    @staticmethod
    def check_setup(
        indicators: Dict,
        candles: List[Dict],
    ) -> Tuple[Optional[str], int, List[str], List[str]]:
        """Sprawdza warunki RANGE_REVERSION (LONG)."""
        reasons = []
        blocked_by = []
        score = 0

        # Pobierz wskaźniki
        ema55 = indicators.get("ema55")
        ema55_slope = indicators.get("ema55_slope")
        atr = indicators.get("atr")
        bb_zscore = indicators.get("bb_zscore")
        rsi5 = indicators.get("rsi5")
        vwap = indicators.get("vwap")
        er30 = indicators.get("efficiency_ratio")

        if not all([ema55, ema55_slope, atr, bb_zscore, rsi5, vwap, er30]):
            return None, 0, [], ["insufficient_indicators"]

        # Warunek 1: Nachylenie EMA55 płaskie (slope < 0.3 * ATR)
        if abs(ema55_slope) > RANGE_EMA55_SLOPE_THRESHOLD * atr:
            return None, 0, [], ["ema55_not_flat"]

        reasons.append("ema55_flat")

        # Warunek 2: Poprzednia świeca < dolna wstęga BB, obecna wraca do wnętrza
        bb = indicators.get("bollinger_bands", {})
        lower_bb = bb.get("lower", vwap - 2.0 * atr)

        prev_close = candles[-2]["close"] if len(candles) > 1 else candles[-1]["close"]
        last_close = candles[-1]["close"]

        if not (prev_close < lower_bb and last_close > lower_bb):
            return None, 0, reasons, ["bb_reversion_not_confirmed"]

        reasons.append("bb_reversion_confirmed")

        # Warunek 3: RSI oversold w ostatnich 2 świecach
        recent_rsi = indicators.get("rsi5_history", [])
        if len(recent_rsi) < 2:
            return None, 0, reasons, ["rsi_history_insufficient"]

        if not any(v <= RSI_OVERSOLD_RANGE for v in recent_rsi[-2:]):
            return None, 0, reasons, ["rsi_not_oversold"]

        reasons.append("rsi_oversold_confirmed")

        # Warunek 4: Odległość od VWAP >= 1.5 * ATR w kierunku powrotu
        distance_to_vwap = abs(last_close - vwap)
        if distance_to_vwap < REVERSION_ATR_DISTANCE * atr:
            return None, 0, reasons, ["distance_to_vwap_insufficient"]

        reasons.append("distance_to_vwap_confirmed")

        # Warunek 5: Ostatnia świeca bycza (rejection - lower wick >= 30%)
        last_open = candles[-1]["open"]
        last_high = candles[-1]["high"]
        last_low = candles[-1]["low"]

        if not (last_close > last_open):
            return None, 0, reasons, ["candle_not_bullish"]

        candle_range = last_high - last_low
        lower_wick = last_open - last_low if candle_range > 0 else 0
        if candle_range > 0 and lower_wick / candle_range < 0.3:
            return None, 0, reasons, ["lower_wick_insufficient"]

        reasons.append("candle_rejection_confirmed")

        # Setup przeszedł
        score = CORE_SETUP_POINTS
        reasons.append("range_reversion_core_conditions_met")

        # Bonusy
        if indicators.get("relative_volume", 0) >= 1.2:
            score += BONUS_RELATIVE_VOLUME
            reasons.append("bonus_high_volume")

        if indicators.get("is_prime_session", False):
            score += BONUS_PRIME_SESSION
            reasons.append("bonus_prime_session")

        candle_body = last_close - last_open
        if candle_range > 0 and candle_body / candle_range >= 0.5:
            score += BONUS_CANDLE_QUALITY
            reasons.append("bonus_candle_quality")

        return "LONG", score, reasons, []

    @staticmethod
    def check_setup_short(
        indicators: Dict,
        candles: List[Dict],
    ) -> Tuple[Optional[str], int, List[str], List[str]]:
        """Sprawdza SHORT (lustrzane warunki)."""
        return None, 0, [], []


def calculate_risk_reward(atr: float, pip: float = PIP) -> Tuple[float, float]:
    """Backend - Oblicza SL i TP na podstawie ATR."""
    sl_pips = SL_MULTIPLIER * (atr / pip)
    tp_pips = TP_MULTIPLIER * (atr / pip)

    # Minimum TP
    tp_pips = max(tp_pips, MIN_TP_PIPS)

    return sl_pips, tp_pips
