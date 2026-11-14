"""Pythonowa wersja algorytmu Grok-45 dla EURUSD, zgodna z generatorem C++."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Sequence


STRATEGY_NAME = "Grok-45/EURUSD/EURUSDStrategyV2"
BOLLINGER_DEVIATION_MULTIPLIER = 2.0
NEARBY_BAND_WIDTH_FRACTION = 0.3
WICK_TO_BODY_MULTIPLIER = 1.5
WICK_TO_ATR_FRACTION = 0.1

# tresc instrukcji jest w signalGenerator ale poprosilem chatagpt-lune o przetransponowanie go na pythona
@dataclass
class Candle:
    """Przechowuje OHLCV jednej zamkniętej świecy 1-minutowej."""

    timestamp: int = 0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0


@dataclass(frozen=True)
class BollingerBands:
    """Przechowuje górną, środkową i dolną wstęgę Bollingera."""

    upper: float
    middle: float
    lower: float


class IndicatorCalculator:
    """Oblicza wskaźniki techniczne używane przez generator Grok-45."""

    @staticmethod
    def calculate_ema_series(
        prices: Sequence[float], period: int
    ) -> list[float | None]:
        """Liczy serię EMA inicjalizowaną średnią SMA.

        Zwraca pustą wartość przed okresem rozgrzewki lub dla nieprawidłowych cen.
        """
        values: list[float | None] = [None] * len(prices)
        if period <= 0 or len(prices) < period or not _are_finite_prices(prices):
            return values

        initial_average = 0.0
        for index in range(period):
            initial_average += prices[index] / period
        values[period - 1] = initial_average

        smoothing_factor = 2.0 / (period + 1.0)
        for index in range(period, len(prices)):
            previous_value = values[index - 1]
            assert previous_value is not None
            values[index] = (
                (prices[index] - previous_value) * smoothing_factor
                + previous_value
            )
        return values

    @staticmethod
    def calculate_ema(
        prices: Sequence[float], period: int
    ) -> float | None:
        """Zwraca ostatnią wartość EMA albo `None`, gdy nie można jej policzyć."""
        values = IndicatorCalculator.calculate_ema_series(prices, period)
        return values[-1] if values else None

    @staticmethod
    def calculate_rsi_series(
        prices: Sequence[float], period: int
    ) -> list[float | None]:
        """Liczy serię RSI z wygładzaniem Wildera i takimi samymi brzegami jak C++."""
        values: list[float | None] = [None] * len(prices)
        if (
            period <= 0
            or len(prices) <= period
            or not _are_finite_prices(prices)
        ):
            return values

        average_gain = 0.0
        average_loss = 0.0
        for index in range(1, period + 1):
            change = prices[index] - prices[index - 1]
            average_gain += max(change, 0.0) / period
            average_loss += max(-change, 0.0) / period
        values[period] = _calculate_rsi_value(average_gain, average_loss)

        for index in range(period + 1, len(prices)):
            change = prices[index] - prices[index - 1]
            gain = max(change, 0.0)
            loss = max(-change, 0.0)
            average_gain = (
                average_gain * (period - 1) + gain
            ) / period
            average_loss = (
                average_loss * (period - 1) + loss
            ) / period
            values[index] = _calculate_rsi_value(average_gain, average_loss)
        return values

    @staticmethod
    def calculate_rsi(prices: Sequence[float], period: int) -> float | None:
        """Zwraca ostatni RSI albo `None`, jeśli okres rozgrzewki nie jest gotowy."""
        values = IndicatorCalculator.calculate_rsi_series(prices, period)
        return values[-1] if values else None

    @staticmethod
    def calculate_bollinger_bands(
        prices: Sequence[float],
        period: int,
        standard_deviation_multiplier: float,
    ) -> BollingerBands | None:
        """Liczy wstęgi Bollingera z populacyjnym odchyleniem standardowym."""
        if (
            period <= 0
            or len(prices) < period
            or not math.isfinite(standard_deviation_multiplier)
            or standard_deviation_multiplier < 0.0
            or not _are_finite_prices(prices)
        ):
            return None

        first_index = len(prices) - period
        average = 0.0
        for index in range(first_index, len(prices)):
            average += prices[index] / period

        variance = 0.0
        for index in range(first_index, len(prices)):
            difference = prices[index] - average
            variance += difference * difference / period

        distance = standard_deviation_multiplier * math.sqrt(variance)
        return BollingerBands(average + distance, average, average - distance)

    @staticmethod
    def calculate_atr(candles: Sequence[Candle], period: int) -> float | None:
        """Liczy ATR metodą Wildera po zweryfikowaniu wszystkich świec."""
        if period <= 0 or len(candles) < period:
            return None
        if not all(_is_valid_candle(candle) for candle in candles):
            return None

        average_true_range = 0.0
        for index in range(period):
            if index == 0:
                true_range = candles[index].high - candles[index].low
            else:
                true_range = max(
                    candles[index].high - candles[index].low,
                    abs(candles[index].high - candles[index - 1].close),
                    abs(candles[index].low - candles[index - 1].close),
                )
            if not math.isfinite(true_range) or true_range < 0.0:
                return None
            average_true_range += true_range / period

        for index in range(period, len(candles)):
            true_range = max(
                candles[index].high - candles[index].low,
                abs(candles[index].high - candles[index - 1].close),
                abs(candles[index].low - candles[index - 1].close),
            )
            if not math.isfinite(true_range) or true_range < 0.0:
                return None
            average_true_range = (
                average_true_range * (period - 1) + true_range
            ) / period
        return average_true_range


class EURUSDStrategyV2:
    """Generuje sygnały EURUSD według logiki generatora Grok-45 w C++."""

    minimum_candles = 50
    ema_short_period = 9
    ema_long_period = 21
    rsi_period = 14
    bollinger_period = 20
    atr_period = 14
    minimum_atr = 0.00015
    volatility_range_candles = 5
    analysis_window_candles = 100

    def generate(self, candles: Sequence[Candle]) -> str:
        """Analizuje zamknięte świece i zwraca odpowiedź JSON zgodną z C++.

        Do analizy używa maksymalnie ostatnich 100 świec. Nie wymaga równych
        odstępów czasu między świecami, ale wymaga ich rosnących znaczników czasu.
        """
        if not candles:
            return _create_json_response(
                [], "NONE", "Brak świec do analizy.",
                None, None, None, None, None,
            )

        analyzed_candles = list(candles[-self.analysis_window_candles :])
        if not _are_valid_candles(analyzed_candles):
            return _create_json_response(
                analyzed_candles,
                "NONE",
                "Dane świec są nieprawidłowe albo nie są ułożone chronologicznie.",
                None,
                None,
                None,
                None,
                None,
            )

        closing_prices = [candle.close for candle in analyzed_candles]
        ema9 = IndicatorCalculator.calculate_ema(
            closing_prices, self.ema_short_period
        )
        ema21 = IndicatorCalculator.calculate_ema(
            closing_prices, self.ema_long_period
        )
        rsi14 = IndicatorCalculator.calculate_rsi(
            closing_prices, self.rsi_period
        )
        bands = IndicatorCalculator.calculate_bollinger_bands(
            closing_prices,
            self.bollinger_period,
            BOLLINGER_DEVIATION_MULTIPLIER,
        )
        atr14 = IndicatorCalculator.calculate_atr(
            analyzed_candles, self.atr_period
        )

        if len(analyzed_candles) < self.minimum_candles:
            return _create_json_response(
                analyzed_candles,
                "NONE",
                "Za mało danych: wymagane są co najmniej 50 zamkniętych świec.",
                ema9,
                ema21,
                rsi14,
                bands,
                atr14,
            )
        if _have_all_closes_stayed_the_same(analyzed_candles):
            return _create_json_response(
                analyzed_candles,
                "NONE",
                "Ceny zamknięcia nie zmieniły się w analizowanym okresie.",
                ema9,
                ema21,
                rsi14,
                bands,
                atr14,
            )
        if ema9 is None or ema21 is None or rsi14 is None or bands is None or atr14 is None:
            return _create_json_response(
                analyzed_candles,
                "NONE",
                "Nie można obliczyć wszystkich wymaganych wskaźników.",
                ema9,
                ema21,
                rsi14,
                bands,
                atr14,
            )

        ema9_series = IndicatorCalculator.calculate_ema_series(
            closing_prices, self.ema_short_period
        )
        ema21_series = IndicatorCalculator.calculate_ema_series(
            closing_prices, self.ema_long_period
        )
        rsi_series = IndicatorCalculator.calculate_rsi_series(
            closing_prices, self.rsi_period
        )
        if len(rsi_series) < 2 or rsi_series[-2] is None:
            return _create_json_response(
                analyzed_candles,
                "NONE",
                "Brak poprzedniej wartości RSI do porównania.",
                ema9,
                ema21,
                rsi14,
                bands,
                atr14,
            )

        latest_candle = analyzed_candles[-1]
        band_width = bands.upper - bands.lower
        close_below_middle_or_near_lower = (
            latest_candle.close <= bands.middle
            or abs(latest_candle.close - bands.lower)
            <= NEARBY_BAND_WIDTH_FRACTION * band_width
        )
        close_above_middle_or_near_upper = (
            latest_candle.close >= bands.middle
            or abs(latest_candle.close - bands.upper)
            <= NEARBY_BAND_WIDTH_FRACTION * band_width
        )
        recent_range = _calculate_recent_range(
            analyzed_candles, self.volatility_range_candles
        )
        volatility_is_sufficient = atr14 > self.minimum_atr or (
            recent_range is not None and recent_range > self.minimum_atr
        )

        long_trend = ema9 > ema21 or _crossed_recently(
            ema9_series, ema21_series, upward=True
        )
        short_trend = ema9 < ema21 or _crossed_recently(
            ema9_series, ema21_series, upward=False
        )
        latest_rsi = rsi_series[-1]
        previous_rsi = rsi_series[-2]
        assert latest_rsi is not None and previous_rsi is not None
        rsi_is_rising = latest_rsi > previous_rsi
        rsi_is_falling = latest_rsi < previous_rsi
        long_rsi_condition = 40.0 < rsi14 <= 70.0 and rsi_is_rising
        short_rsi_condition = 30.0 <= rsi14 < 60.0 and rsi_is_falling
        bullish_candle = latest_candle.close > latest_candle.open or _has_clear_lower_wick(
            latest_candle, atr14
        )
        bearish_candle = latest_candle.close < latest_candle.open or _has_clear_upper_wick(
            latest_candle, atr14
        )

        if (
            volatility_is_sufficient
            and long_trend
            and long_rsi_condition
            and close_below_middle_or_near_lower
            and bullish_candle
        ):
            return _create_json_response(
                analyzed_candles,
                "LONG",
                "Warunki trendu EMA, rosnącego RSI, wstęg Bollingera, świecy i zmienności są spełnione.",
                ema9,
                ema21,
                rsi14,
                bands,
                atr14,
            )
        if (
            volatility_is_sufficient
            and short_trend
            and short_rsi_condition
            and close_above_middle_or_near_upper
            and bearish_candle
        ):
            return _create_json_response(
                analyzed_candles,
                "SHORT",
                "Warunki trendu EMA, spadającego RSI, wstęg Bollingera, świecy i zmienności są spełnione.",
                ema9,
                ema21,
                rsi14,
                bands,
                atr14,
            )

        reason = "Warunki LONG ani SHORT nie zostały spełnione."
        if not volatility_is_sufficient:
            reason = "Zmienność jest niższa od wymaganego progu."
        return _create_json_response(
            analyzed_candles, "NONE", reason, ema9, ema21, rsi14, bands, atr14
        )


def _are_finite_prices(prices: Sequence[float]) -> bool:
    """Sprawdza, czy wszystkie ceny są skończonymi liczbami."""
    return all(math.isfinite(price) for price in prices)


def _calculate_rsi_value(average_gain: float, average_loss: float) -> float:
    """Oblicza RSI z uśrednionych zmian i obsługuje przypadki skrajne."""
    if average_loss == 0.0:
        return 50.0 if average_gain == 0.0 else 100.0
    if average_gain == 0.0:
        return 0.0
    relative_strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def _is_valid_candle(candle: Candle) -> bool:
    """Sprawdza skończoność i spójność wartości OHLCV pojedynczej świecy."""
    return (
        all(
            math.isfinite(value)
            for value in (
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
            )
        )
        and candle.open > 0.0
        and candle.high > 0.0
        and candle.low > 0.0
        and candle.close > 0.0
        and candle.volume >= 0.0
        and candle.high >= candle.low
        and candle.high >= max(candle.open, candle.close)
        and candle.low <= min(candle.open, candle.close)
    )


def _are_valid_candles(candles: Sequence[Candle]) -> bool:
    """Weryfikuje świece oraz ich ściśle rosnącą kolejność czasową."""
    for index, candle in enumerate(candles):
        if not _is_valid_candle(candle):
            return False
        if index > 0 and candle.timestamp <= candles[index - 1].timestamp:
            return False
    return True


def _have_all_closes_stayed_the_same(candles: Sequence[Candle]) -> bool:
    """Sprawdza, czy wszystkie ceny zamknięcia są identyczne."""
    return bool(candles) and all(
        candle.close == candles[0].close for candle in candles
    )


def _calculate_recent_range(
    candles: Sequence[Candle], period: int
) -> float | None:
    """Zwraca rozpiętość high-low z ostatniego okna świec."""
    if period <= 0 or len(candles) < period:
        return None
    recent_candles = candles[-period:]
    highest_price = recent_candles[0].high
    lowest_price = recent_candles[0].low
    for candle in recent_candles[1:]:
        highest_price = max(highest_price, candle.high)
        lowest_price = min(lowest_price, candle.low)
    return highest_price - lowest_price


def _crossed_recently(
    fast_ema: Sequence[float | None],
    slow_ema: Sequence[float | None],
    upward: bool,
) -> bool:
    """Wykrywa przecięcie EMA w bieżącej albo poprzedniej świecy."""
    if len(fast_ema) != len(slow_ema) or len(fast_ema) < 2:
        return False

    last_index = len(fast_ema) - 1
    for candles_ago in range(2):
        if last_index <= candles_ago:
            continue
        current_index = last_index - candles_ago
        previous_index = current_index - 1
        fast_current = fast_ema[current_index]
        slow_current = slow_ema[current_index]
        fast_previous = fast_ema[previous_index]
        slow_previous = slow_ema[previous_index]
        if (
            fast_current is None
            or slow_current is None
            or fast_previous is None
            or slow_previous is None
        ):
            continue
        if (
            upward
            and fast_previous <= slow_previous
            and fast_current > slow_current
        ):
            return True
        if (
            not upward
            and fast_previous >= slow_previous
            and fast_current < slow_current
        ):
            return True
    return False


def _has_clear_lower_wick(candle: Candle, atr: float) -> bool:
    """Sprawdza, czy dolny cień jest duży względem korpusu lub ATR."""
    body_size = abs(candle.close - candle.open)
    lower_wick_size = min(candle.open, candle.close) - candle.low
    minimum_wick_size = max(
        body_size * WICK_TO_BODY_MULTIPLIER, atr * WICK_TO_ATR_FRACTION
    )
    return lower_wick_size >= minimum_wick_size


def _has_clear_upper_wick(candle: Candle, atr: float) -> bool:
    """Sprawdza, czy górny cień jest duży względem korpusu lub ATR."""
    body_size = abs(candle.close - candle.open)
    upper_wick_size = candle.high - max(candle.open, candle.close)
    minimum_wick_size = max(
        body_size * WICK_TO_BODY_MULTIPLIER, atr * WICK_TO_ATR_FRACTION
    )
    return upper_wick_size >= minimum_wick_size


def _format_number(value: float | None) -> str:
    """Formatuje liczbę do JSON z precyzją 12 cyfr znaczących jak w C++."""
    if value is None or not math.isfinite(value):
        return "null"
    return format(value, ".12g")


def _json_string(value: str) -> str:
    """Koduje tekst jako napis JSON bez zmiany znaków Unicode."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _create_json_response(
    candles: Sequence[Candle],
    signal: str,
    reason: str,
    ema9: float | None,
    ema21: float | None,
    rsi14: float | None,
    bands: BollingerBands | None,
    atr14: float | None,
) -> str:
    """Buduje uporządkowany dokument JSON zgodny z odpowiedzią generatora C++."""
    latest_candle = candles[-1] if candles else None
    upper_band = bands.upper if bands is not None else None
    middle_band = bands.middle if bands is not None else None
    lower_band = bands.lower if bands is not None else None
    price = _format_number(latest_candle.close if latest_candle else None)
    timestamp = str(latest_candle.timestamp) if latest_candle else "null"
    return (
        '{"instrument":"EURUSD/USD","price":'
        + price
        + ',"timestamp":'
        + timestamp
        + ',"signal":'
        + _json_string(signal)
        + ',"parameters":{"ema9":'
        + _format_number(ema9)
        + ',"ema21":'
        + _format_number(ema21)
        + ',"rsi14":'
        + _format_number(rsi14)
        + ',"bb_upper":'
        + _format_number(upper_band)
        + ',"bb_middle":'
        + _format_number(middle_band)
        + ',"bb_lower":'
        + _format_number(lower_band)
        + ',"atr14":'
        + _format_number(atr14)
        + ',"reason":'
        + _json_string(reason)
        + "}}"
    )
