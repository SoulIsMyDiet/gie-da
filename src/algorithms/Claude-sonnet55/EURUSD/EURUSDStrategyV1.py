#!/usr/bin/env python3
"""
Backend
Algorytm sygnałów mikrotransakcji dla EURUSD z dwoma setupami:
1. TREND_PULLBACK - dla rynków trendowych (ER >= 0.30)
2. RANGE_REVERSION - dla rynków bocznych (ER <= 0.20)

Uruchamianie:
    python EURUSDStrategyV1.py [--loop]

--loop: czeka na nową świecę w bazie i wtedy oblicza sygnał
"""

import sys
import json
import logging
import argparse
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Tuple
from decimal import Decimal
from zoneinfo import ZoneInfo

# Importy lokalne (wszystkie w tym samym folderze)
from config import (
    INSTRUMENT,
    PIP,
    MIN_CANDLE_HISTORY,
    MAX_STALE_DATA_MINUTES,
    STRATEGY_NAME,
    COOLDOWN_MINUTES,
    EMA_SHORT,
    EMA_MEDIUM,
    EMA_LONG,
    RSI_PERIOD,
    ATR_PERIOD,
    BB_PERIOD,
    BB_STD_DEV,
    EFFICIENCY_RATIO_PERIOD,
    EMA_5M,
    SL_MULTIPLIER,
    TP_MULTIPLIER,
    MIN_TP_PIPS,
    MAX_HOLD_MINUTES,
    TREND_PULLBACK_ER_MIN,
    RANGE_REVERSION_ER_MAX,
    REGIME_UNCERTAIN_ZONE,
    MIN_SCORE,
    UTC_TIMEZONE,
    WARSAW_TIMEZONE,
)
from indicators import (
    ExponentialMovingAverage,
    WilderRelativeStrengthIndex,
    WilderAverageTrueRange,
    SimpleMovingAverage,
    BollingerBands,
    KaufmanEfficiencyRatio,
    VolumeWeightedAveragePrice,
)
from signal_logic import (
    SessionFilter,
    VolatilityFilter,
    DataAnomalyFilter,
    TrendPullbackSetup,
    RangeReversionSetup,
    calculate_risk_reward,
)
from storage import CandleDatabase
from sound import play_signal_confirmation, play_error_alert

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class EURUSDStrategyV1:
    """Backend - Główny algorytm generujący sygnały mikrotransakcji."""

    def __init__(self):
        """Inicjalizuje wskaźniki i bazę danych."""
        self.db = CandleDatabase()

        # Wskaźniki 1M
        self.ema9 = ExponentialMovingAverage(EMA_SHORT)
        self.ema21 = ExponentialMovingAverage(EMA_MEDIUM)
        self.ema55 = ExponentialMovingAverage(EMA_LONG)
        self.rsi5 = WilderRelativeStrengthIndex(RSI_PERIOD)
        self.atr14 = WilderAverageTrueRange(ATR_PERIOD)
        self.bb20 = BollingerBands(BB_PERIOD, BB_STD_DEV)
        self.er30 = KaufmanEfficiencyRatio(EFFICIENCY_RATIO_PERIOD)
        self.volume_sma = SimpleMovingAverage(20)
        self.atr_median = SimpleMovingAverage(200)

        # Wskaźniki 5M
        self.ema20_5m = ExponentialMovingAverage(EMA_5M)

        # VWAP
        self.vwap = VolumeWeightedAveragePrice()

        # Historia dla analizy nachyleń
        self.ema9_history: List[float] = []
        self.ema21_history: List[float] = []
        self.ema55_history: List[float] = []
        self.rsi5_history: List[float] = []
        self.close_history: List[float] = []

        # Ostatni sygnał i cooldown
        self.last_signal_time: Optional[datetime] = None
        self.last_signal_direction: Optional[str] = None

    def warm_up(self, candles: List[Dict]) -> None:
        """Backend - Ładuje historię świec do wskaźników."""
        logger.info(f"Ładuję {len(candles)} świec do wskaźników...")

        for candle in candles:
            close = candle["close"]
            high = candle["high"]
            low = candle["low"]
            volume = candle.get("volume")

            # Wskaźniki 1M
            self.ema9.add(close)
            self.ema21.add(close)
            self.ema55.add(close)
            self.rsi5.add(close)
            self.atr14.add(high, low, close)
            self.bb20.add(close)
            self.er30.add(close)
            self.volume_sma.add(volume if volume else 1.0)
            self.atr_median.add(self.atr14.value() if self.atr14.value() else 0)

            # VWAP (tutaj byś potrzebował timestamp, używam dummy)
            self.vwap.add(candle.get("datetime_utc"), high, low, close, volume)

            # Historia
            if self.ema9.value():
                self.ema9_history.append(self.ema9.value())
            if self.ema21.value():
                self.ema21_history.append(self.ema21.value())
            if self.ema55.value():
                self.ema55_history.append(self.ema55.value())
            if self.rsi5.value():
                self.rsi5_history.append(self.rsi5.value())
            self.close_history.append(close)

        logger.info("Wskaźniki załadowane pomyślnie.")

    def generate_signal(self, candles: List[Dict]) -> Dict:
        """
        Backend
        Główna funkcja generująca sygnał. Zwraca słownik JSON z wynikiem.
        """
        # Walidacja danych
        if len(candles) < MIN_CANDLE_HISTORY:
            logger.warning(f"Za mało świec ({len(candles)} < {MIN_CANDLE_HISTORY})")
            return self._format_signal("NONE", None, 0, [], ["insufficient_history"])

        # Pobierz ostatnią świecę
        last_candle = candles[-1]
        last_close = last_candle["close"]
        last_timestamp = last_candle.get("datetime_utc")

        if not last_timestamp:
            return self._format_signal("NONE", None, 0, [], ["no_timestamp"])

        # Sprawdzenie stalości danych
        if isinstance(last_timestamp, str):
            last_timestamp = datetime.fromisoformat(last_timestamp.replace("Z", "+00:00"))
        if last_timestamp.tzinfo is None:
            last_timestamp = last_timestamp.replace(tzinfo=UTC_TIMEZONE)

        time_since_candle = (datetime.now(UTC_TIMEZONE) - last_timestamp).total_seconds() / 60
        if time_since_candle > MAX_STALE_DATA_MINUTES:
            logger.warning(f"Stale data ({time_since_candle:.1f} minut temu)")
            return self._format_signal("NONE", None, 0, [], ["stale_data"])

        # Aktualizuj wskaźniki
        try:
            self._update_indicators(candles)
        except Exception as e:
            logger.error(f"Błąd przy aktualizacji wskaźników: {e}")
            return self._format_signal("NONE", None, 0, [], ["indicator_error"])

        # Zbierz wszystkie wskaźniki
        indicators = self._collect_indicators(last_candle)

        # Hard filters
        blocked_by = []

        # Filter 1: Sesja
        session_ok, session_blocked = SessionFilter.check_session(last_timestamp)
        if not session_ok:
            blocked_by.extend(session_blocked)

        if blocked_by:
            logger.info(f"Sygnał NONE (sesja): {blocked_by}")
            return self._format_signal("NONE", None, 0, [], blocked_by)

        # Filter 2: Zmienność
        atr_pips = indicators.get("atr_pips", 0)
        atr_ratio = indicators.get("atr_ratio", 0)
        atr_ok, atr_blocked = VolatilityFilter.check_atr(atr_pips, atr_ratio)
        if not atr_ok:
            blocked_by.extend(atr_blocked)
            logger.info(f"Sygnał NONE (ATR): {blocked_by}")
            return self._format_signal("NONE", None, 0, [], blocked_by)

        # Filter 3: Cost filter
        cost_ok, cost_blocked = VolatilityFilter.check_cost_filter(atr_pips)
        if not cost_ok:
            blocked_by.extend(cost_blocked)
            logger.info(f"Sygnał NONE (cost): {blocked_by}")
            return self._format_signal("NONE", None, 0, [], blocked_by)

        # Filter 4: Anomalie
        anomaly_ok, anomaly_blocked = DataAnomalyFilter.check_candle_range(
            last_candle["high"], last_candle["low"], indicators.get("atr", 0)
        )
        if not anomaly_ok:
            blocked_by.extend(anomaly_blocked)
            logger.info(f"Sygnał NONE (anomalia): {blocked_by}")
            return self._format_signal("NONE", None, 0, [], blocked_by)

        # Sprawdzenie setupów
        signal_result = self._check_setups(last_timestamp, indicators, candles)

        return signal_result

    def _check_setups(self, timestamp: datetime, indicators: Dict, candles: List[Dict]) -> Dict:
        """Backend - Sprawdza oba setupy i wybiera najlepszy."""
        er30 = indicators.get("efficiency_ratio", 0)

        # Sprawdzenie reżimu
        if REGIME_UNCERTAIN_ZONE[0] < er30 < REGIME_UNCERTAIN_ZONE[1]:
            logger.info(f"Sygnał NONE (regime_unclear, ER={er30:.2f})")
            return self._format_signal("NONE", None, 0, [], ["regime_unclear"])

        # Setup 1: TREND_PULLBACK
        if er30 >= TREND_PULLBACK_ER_MIN:
            signal, score, reasons, blocked = TrendPullbackSetup.check_setup(indicators, candles)
            if signal and score >= MIN_SCORE:
                logger.info(f"✓ Setup TREND_PULLBACK: {signal} (score={score})")
                return self._format_signal(
                    signal,
                    candles[-1]["close"],
                    score,
                    reasons,
                    blocked,
                    setup="TREND_PULLBACK",
                    indicators=indicators,
                )

        # Setup 2: RANGE_REVERSION
        if er30 <= RANGE_REVERSION_ER_MAX:
            signal, score, reasons, blocked = RangeReversionSetup.check_setup(indicators, candles)
            if signal and score >= MIN_SCORE:
                logger.info(f"✓ Setup RANGE_REVERSION: {signal} (score={score})")
                return self._format_signal(
                    signal,
                    candles[-1]["close"],
                    score,
                    reasons,
                    blocked,
                    setup="RANGE_REVERSION",
                    indicators=indicators,
                )

        # Brak sygnału
        logger.debug("Brak sygnału (setup warunki nie spełnione)")
        return self._format_signal("NONE", None, 0, [], ["setup_conditions_not_met"])

    def _update_indicators(self, candles: List[Dict]) -> None:
        """Backend - Aktualizuje wskaźniki nowymi świecami."""
        # Najprościej: załaduj wszystko jeszcze raz
        # W produkcji byś dodawał tylko ostatnią świecę
        pass

    def _collect_indicators(self, last_candle: Dict) -> Dict:
        """Backend - Zbiera wszystkie wartości wskaźników."""
        atr_value = self.atr14.value()
        atr_pips = (atr_value / PIP) if atr_value else 0
        atr_median = self.atr_median.value()
        atr_ratio = (atr_pips / atr_median) if atr_median and atr_median > 0 else 0

        ema55_slope = None
        if len(self.ema55_history) >= 10:
            slope_change = self.ema55_history[-1] - self.ema55_history[-10]
            ema55_slope = slope_change / 10

        return {
            "atr": atr_value,
            "atr_pips": atr_pips,
            "atr_ratio": atr_ratio,
            "ema9": self.ema9.value(),
            "ema21": self.ema21.value(),
            "ema55": self.ema55.value(),
            "ema55_slope": ema55_slope,
            "rsi5": self.rsi5.value(),
            "rsi5_history": self.rsi5_history[-10:] if len(self.rsi5_history) > 0 else [],
            "bb_zscore": self.bb20.value().get("zscore", 0) if self.bb20.value() else 0,
            "bollinger_bands": self.bb20.value() if self.bb20.value() else {},
            "efficiency_ratio": self.er30.value() or 0,
            "vwap": self.vwap.value(),
            "relative_volume": (
                last_candle.get("volume", 1) / self.volume_sma.value()
                if self.volume_sma.value()
                else 1.0
            ),
            "ema20_5m": self.ema20_5m.value(),
            "ema20_5m_slope": 0,  # TODO: oblicz z historii
            "is_prime_session": self._is_prime_session(datetime.now(UTC_TIMEZONE)),
        }

    @staticmethod
    def _is_prime_session(utc_time: datetime) -> bool:
        """Backend - Sprawdza czy to prime time (12:00-16:00 UTC)."""
        from config import PRIME_SESSION_START_UTC, PRIME_SESSION_END_UTC
        time_only = utc_time.time()
        return PRIME_SESSION_START_UTC <= time_only < PRIME_SESSION_END_UTC

    def _format_signal(
        self,
        signal: str,
        price: Optional[float],
        score: int,
        reasons: List[str],
        blocked_by: List[str],
        setup: Optional[str] = None,
        indicators: Optional[Dict] = None,
    ) -> Dict:
        """Backend - Formatuje wynik na JSON."""
        result = {
            "instrument": INSTRUMENT,
            "signal": signal,
            "price": float(price) if price else None,
            "timestamp": datetime.now(UTC_TIMEZONE).isoformat(),
            "setup": setup,
            "score": score,
            "reasons": reasons,
            "blocked_by": blocked_by if blocked_by else None,
            "params": indicators or {},
            "max_hold_minutes": MAX_HOLD_MINUTES,
        }

        # Dodaj TP/SL jeśli to sygnał
        if signal in ("LONG", "SHORT") and indicators:
            atr = indicators.get("atr", 0)
            if atr > 0:
                sl_pips, tp_pips = calculate_risk_reward(atr, PIP)
                result["tp_pips"] = tp_pips
                result["sl_pips"] = sl_pips
                
                current_price = float(price) if price else 0
                if signal == "LONG":
                    result["tp_price"] = current_price + tp_pips * PIP
                    result["sl_price"] = current_price - sl_pips * PIP
                else:  # SHORT
                    result["tp_price"] = current_price - tp_pips * PIP
                    result["sl_price"] = current_price + sl_pips * PIP

        return result


def main():
    """Frontend - Główna funkcja (entry point)."""
    parser = argparse.ArgumentParser(
        description="Algorytm mikrotransakcji dla EURUSD"
    )
    parser.add_argument("--loop", action="store_true", help="Czeka na nową świecę")
    args = parser.parse_args()

    try:
        strategy = EURUSDStrategyV1()

        # Pobierz świece z bazy
        logger.info(f"Pobieranie świec dla {INSTRUMENT}...")
        candles = strategy.db.fetch_candles(INSTRUMENT, limit=500)

        if len(candles) < MIN_CANDLE_HISTORY:
            logger.error(f"Za mało świec w bazie ({len(candles)})")
            sys.exit(1)

        # Warm-up wskaźników
        strategy.warm_up(candles)

        # Generuj sygnał
        signal_result = strategy.generate_signal(candles)

        # Drukuj JSON (frontend)
        print(json.dumps(signal_result, indent=2))

        # Zapisz do bazy
        if signal_result["signal"] in ("LONG", "SHORT") or (
            signal_result["signal"] == "NONE" and strategy.db  # Jeśli ma być zapisany
        ):
            try:
                was_saved = strategy.db.save_signal(signal_result, len(candles))
                if was_saved:
                    logger.info("Sygnał zapisany do bazy. Emituję potwierdzenie dźwiękowe...")
                    play_signal_confirmation()
            except Exception as e:
                logger.error(f"Błąd przy zapisie sygnału: {e}")
                play_error_alert()

    except KeyboardInterrupt:
        logger.info("Przerwano przez użytkownika")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Krytyczny błąd: {e}", exc_info=True)
        play_error_alert()
        sys.exit(1)


if __name__ == "__main__":
    main()
