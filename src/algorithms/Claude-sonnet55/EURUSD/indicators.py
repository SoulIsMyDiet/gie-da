"""
Backend
Wskaźniki techniczne dla strategii mikrotransakcji.
"""

from typing import Optional, List, Dict
import math


class SimpleMovingAverage:
    """Backend - Prosta średnia ruchoma (SMA)."""

    def __init__(self, period: int) -> None:
        self.period = period
        self.values: list[float] = []

    def add(self, value: float) -> Optional[float]:
        """Dodaje wartość i zwraca SMA jeśli mamy wystarczającą liczbę wartości."""
        self.values.append(value)
        if len(self.values) < self.period:
            return None
        return sum(self.values[-self.period:]) / self.period

    def value(self) -> Optional[float]:
        """Zwraca ostatnią wartość SMA bez dodawania nowych danych."""
        if len(self.values) < self.period:
            return None
        return sum(self.values[-self.period:]) / self.period


class ExponentialMovingAverage:
    """Backend - Wykładnicza średnia ruchoma (EMA)."""

    def __init__(self, period: int) -> None:
        self.period = period
        self.values: list[float] = []
        self.alpha = 2.0 / (period + 1)

    def add(self, value: float) -> Optional[float]:
        """Dodaje wartość i zwraca EMA."""
        self.values.append(value)
        if len(self.values) == 1:
            return value
        if len(self.values) < self.period:
            return None
        if len(self.values) == self.period:
            # Inicjalizacja: średnia arytmetyczna
            ema = sum(self.values[:self.period]) / self.period
        else:
            # Rekurencja EMA
            prev_ema = self.values[-2]  # Poprzednia EMA
            ema = prev_ema + self.alpha * (value - prev_ema)
        # Nadpisujemy ostatnią wartość
        self.values[-1] = ema
        return ema

    def value(self) -> Optional[float]:
        """Zwraca ostatnią wartość EMA."""
        if not self.values:
            return None
        return self.values[-1]

    def slope(self, period: int = 5) -> Optional[float]:
        """Backend - Zwraca nachylenie EMA z ostatnich N świec."""
        if len(self.values) < period:
            return None
        # Nachylenie = zmiana / okres
        recent_values = self.values[-period:]
        change = recent_values[-1] - recent_values[0]
        return change / period


class WilderRelativeStrengthIndex:
    """Backend - RSI (Wilder) z Smoothed EMA."""

    def __init__(self, period: int) -> None:
        self.period = period
        self.values: list[float] = []
        self.gains: list[float] = []
        self.losses: list[float] = []
        self.avg_gain: Optional[float] = None
        self.avg_loss: Optional[float] = None

    def add(self, value: float) -> Optional[float]:
        """Dodaje wartość i zwraca RSI."""
        self.values.append(value)

        if len(self.values) < 2:
            return None

        change = value - self.values[-2]
        gain = max(0, change)
        loss = max(0, -change)
        self.gains.append(gain)
        self.losses.append(loss)

        # Inicjalizacja: średnia arytmetyczna
        if len(self.values) == self.period + 1:
            self.avg_gain = sum(self.gains[:self.period]) / self.period
            self.avg_loss = sum(self.losses[:self.period]) / self.period
        elif len(self.values) > self.period + 1:
            # Wilder's Smoothing
            alpha = 1.0 / self.period
            self.avg_gain = self.avg_gain * (1 - alpha) + gain * alpha
            self.avg_loss = self.avg_loss * (1 - alpha) + loss * alpha

        if self.avg_loss is None or len(self.values) < self.period + 1:
            return None

        if self.avg_loss == 0:
            return 100.0 if self.avg_gain > 0 else 0.0

        rs = self.avg_gain / self.avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi

    def value(self) -> Optional[float]:
        """Zwraca ostatnią wartość RSI."""
        if len(self.values) < self.period + 1:
            return None
        if self.avg_loss is None:
            return None
        if self.avg_loss == 0:
            return 100.0 if self.avg_gain > 0 else 0.0
        rs = self.avg_gain / self.avg_loss
        return 100.0 - (100.0 / (1.0 + rs))


class WilderAverageTrueRange:
    """Backend - ATR (Wilder) dla zmienności."""

    def __init__(self, period: int) -> None:
        self.period = period
        self.closes: list[float] = []
        self.highs: list[float] = []
        self.lows: list[float] = []
        self.true_ranges: list[float] = []
        self.atr: Optional[float] = None

    def add(self, high: float, low: float, close: float) -> Optional[float]:
        """Dodaje OHLC i zwraca ATR (Wilder)."""
        self.highs.append(high)
        self.lows.append(low)
        self.closes.append(close)

        if len(self.closes) < 2:
            return None

        prev_close = self.closes[-2]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        self.true_ranges.append(tr)

        # Inicjalizacja: średnia arytmetyczna
        if len(self.true_ranges) == self.period:
            self.atr = sum(self.true_ranges[:self.period]) / self.period
        elif len(self.true_ranges) > self.period:
            # Wilder's Smoothing
            self.atr = (self.atr * (self.period - 1) + tr) / self.period

        return self.atr

    def value(self) -> Optional[float]:
        """Zwraca ostatnią wartość ATR."""
        return self.atr

    def value_pips(self, pip: float = 0.0001) -> Optional[float]:
        """Zwraca ATR w pipsach."""
        if self.atr is None:
            return None
        return self.atr / pip


class BollingerBands:
    """Backend - Bollinger Bands z z-score."""

    def __init__(self, period: int, std_dev: float) -> None:
        self.period = period
        self.std_dev = std_dev
        self.values: list[float] = []

    def add(self, value: float) -> Optional[dict]:
        """Dodaje wartość i zwraca słownik (middle, upper, lower, zscore)."""
        self.values.append(value)
        if len(self.values) < self.period:
            return None

        recent = self.values[-self.period:]
        middle = sum(recent) / self.period
        variance = sum((x - middle) ** 2 for x in recent) / self.period
        std = math.sqrt(variance)

        if std == 0:
            zscore = 0.0
        else:
            zscore = (value - middle) / std

        return {
            "middle": middle,
            "upper": middle + self.std_dev * std,
            "lower": middle - self.std_dev * std,
            "zscore": zscore,
            "std": std,
        }

    def value(self) -> Optional[dict]:
        """Zwraca ostatnie wartości BB bez dodawania nowych."""
        if not self.values or len(self.values) < self.period:
            return None
        recent = self.values[-self.period:]
        middle = sum(recent) / self.period
        variance = sum((x - middle) ** 2 for x in recent) / self.period
        std = math.sqrt(variance)
        current_zscore = (self.values[-1] - middle) / std if std > 0 else 0.0
        return {
            "middle": middle,
            "upper": middle + self.std_dev * std,
            "lower": middle - self.std_dev * std,
            "zscore": current_zscore,
            "std": std,
        }


class KaufmanEfficiencyRatio:
    """Backend - Kaufman Efficiency Ratio (trend vs noise)."""

    def __init__(self, period: int) -> None:
        self.period = period
        self.closes: list[float] = []

    def add(self, close: float) -> Optional[float]:
        """Dodaje close i zwraca ER."""
        self.closes.append(close)
        if len(self.closes) < self.period + 1:
            return None

        # Direction = zmiana netto
        direction = abs(self.closes[-1] - self.closes[-self.period - 1])

        # Noise = suma zmian absolutnych
        noise = sum(abs(self.closes[i] - self.closes[i - 1]) for i in range(-self.period, 0))

        if noise == 0:
            return 1.0 if direction > 0 else 0.0

        return direction / noise

    def value(self) -> Optional[float]:
        """Zwraca ostatnią wartość ER."""
        if len(self.closes) < self.period + 1:
            return None
        direction = abs(self.closes[-1] - self.closes[-self.period - 1])
        noise = sum(abs(self.closes[i] - self.closes[i - 1]) for i in range(-self.period, 0))
        if noise == 0:
            return 1.0 if direction > 0 else 0.0
        return direction / noise


class VolumeWeightedAveragePrice:
    """Backend - VWAP kotwiczony na sesję (UTC start)."""

    def __init__(self, anchor_time_utc=None) -> None:
        """anchor_time_utc - czas UTC startu sesji (lub None dla domyślnego dzisiaj)."""
        self.anchor_time_utc = anchor_time_utc
        self.candles_in_session: list[dict] = []

    def add(self, timestamp, high: float, low: float, close: float, volume: Optional[float]) -> Optional[float]:
        """
        Dodaje świecę i zwraca VWAP.
        timestamp: datetime UTC
        volume: wolumen (jeśli None, używamy 1.0)
        """
        if volume is None or volume <= 0:
            volume = 1.0

        # Średnia cena z OHLC
        typical_price = (high + low + close) / 3.0

        # Jeśli to nowa sesja, resetuj
        if not self.candles_in_session or self._is_new_session(timestamp):
            self.candles_in_session = []

        self.candles_in_session.append(
            {"typical_price": typical_price, "volume": volume}
        )

        # VWAP = suma(typical_price * volume) / suma(volume)
        cum_tp_vol = sum(c["typical_price"] * c["volume"] for c in self.candles_in_session)
        cum_vol = sum(c["volume"] for c in self.candles_in_session)

        if cum_vol == 0:
            return close

        return cum_tp_vol / cum_vol

    def value(self) -> Optional[float]:
        """Zwraca ostatnią wartość VWAP."""
        if not self.candles_in_session:
            return None
        cum_tp_vol = sum(c["typical_price"] * c["volume"] for c in self.candles_in_session)
        cum_vol = sum(c["volume"] for c in self.candles_in_session)
        if cum_vol == 0:
            return None
        return cum_tp_vol / cum_vol

    def _is_new_session(self, timestamp) -> bool:
        """Sprawdza czy to nowa sesja (nowy dzień UTC)."""
        if not self.candles_in_session:
            return False
        # Porównaj datę UTC
        last_candle_date = self.candles_in_session[-1].get("timestamp_date")
        current_date = timestamp.date()
        return last_candle_date != current_date if last_candle_date else False


class Candle5mAggregator:
    """
    Backend
    Agreguje świece 1-minutowe w świece 5-minutowe.
    Tworzy pełne 5-minutowe "kubełki" (00, 05, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55 minut).
    """

    def __init__(self) -> None:
        self.candles_5m: List[Dict] = []
        self.current_bucket: Optional[Dict] = None
        self.last_close_time: Optional[int] = None

    @staticmethod
    def _get_bucket_minute(timestamp_utc) -> int:
        """
        Zwraca minutę kubełka 5M dla danego timestamp.
        np. 00:03 → 0, 00:07 → 5, 00:12 → 10, itd.
        """
        minute = timestamp_utc.minute
        return (minute // 5) * 5

    def add_candle(self, candle: Dict) -> Optional[Dict]:
        """
        Dodaje świecę 1M i zwraca KOMPLETNĄ świecę 5M jeśli kubełek jest pełny.
        Zwraca None jeśli kubełek jeszcze się tworzy.
        """
        timestamp_utc = candle.get("datetime_utc")
        if not timestamp_utc:
            return None

        bucket_minute = self._get_bucket_minute(timestamp_utc)
        hour = timestamp_utc.hour

        # Jeśli to nowy kubełek, zwróć poprzedni (jeśli istnieje)
        completed_candle = None
        if self.current_bucket and self.current_bucket["bucket_minute"] != bucket_minute:
            # Poprzedni kubełek jest kompletny
            completed_candle = self._finalize_bucket()

        # Jeśli to nowy kubełek, inicjalizuj go
        if not self.current_bucket or self.current_bucket["bucket_minute"] != bucket_minute:
            self.current_bucket = {
                "bucket_minute": bucket_minute,
                "hour": hour,
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
                "volume": candle.get("volume", 0) or 0,
                "count": 1,
            }
        else:
            # Kontynuuj kubełek
            self.current_bucket["high"] = max(self.current_bucket["high"], candle["high"])
            self.current_bucket["low"] = min(self.current_bucket["low"], candle["low"])
            self.current_bucket["close"] = candle["close"]
            self.current_bucket["volume"] += candle.get("volume", 0) or 0
            self.current_bucket["count"] += 1

        return completed_candle

    def _finalize_bucket(self) -> Optional[dict]:
        """Finalizuje bieżący kubełek i zwraca świecę 5M."""
        if not self.current_bucket:
            return None

        bucket = self.current_bucket
        hour = bucket["hour"]
        minute = bucket["bucket_minute"]

        # Oblicz czas zamknięcia świecy 5M (5 minut po otwarciu kubełka)
        # Uwaga: timestamp z ostatniej świecy 1M w kubełku to już czas zamknięcia
        return {
            "open": bucket["open"],
            "high": bucket["high"],
            "low": bucket["low"],
            "close": bucket["close"],
            "volume": bucket["volume"],
            "timestamp_minute": minute,
            "timestamp_hour": hour,
        }

    def get_completed_5m_candles(self, candles_1m: List[Dict]) -> List[Dict]:
        """
        Agreguje listę świec 1M i zwraca TYLKO kompletne świece 5M.
        Pomiń ostatnią świecę 1M (może być niekompletna).
        """
        result = []
        for i, candle in enumerate(candles_1m[:-1]):  # Pomiń ostatnią
            completed = self.add_candle(candle)
            if completed:
                result.append(completed)

        return result
