"""Backend: deterministyczne wskaźniki techniczne liczone bez danych przyszłych."""

from __future__ import annotations

from collections.abc import Sequence
from math import isfinite


def ema_series(values: Sequence[float], period: int) -> list[float | None]:
    """Oblicza EMA z inicjalizacją pierwszą średnią SMA.

    Argumenty:
        values: Chronologiczna lista wartości.
        period: Dodatni okres średniej.
    Zwraca:
        Wartość EMA dla każdej pozycji lub None przed rozgrzewką.
    Wyjątki:
        ValueError: Gdy okres nie jest dodatni lub dane nie są skończone.
    """
    if period < 1:
        raise ValueError("Okres EMA musi być dodatni.")
    if not all(isfinite(float(value)) for value in values):
        raise ValueError("Wartości EMA muszą być skończonymi liczbami.")
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    result[period - 1] = sum(float(value) for value in values[:period]) / period
    multiplier = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        previous = result[index - 1]
        assert previous is not None
        result[index] = (float(values[index]) - previous) * multiplier + previous
    return result


def rsi_series(values: Sequence[float], period: int) -> list[float | None]:
    """Oblicza RSI metodą Wildera z pierwszą średnią z ``period`` zmian.

    Argumenty:
        values: Chronologiczne ceny zamknięcia.
        period: Dodatni okres RSI.
    Zwraca:
        Serię RSI; pierwsza wartość jest dostępna po ``period`` zmianach.
    Wyjątki:
        ValueError: Gdy okres nie jest dodatni lub ceny nie są skończone.
    """
    if period < 1:
        raise ValueError("Okres RSI musi być dodatni.")
    if not all(isfinite(float(value)) for value in values):
        raise ValueError("Ceny RSI muszą być skończonymi liczbami.")
    result: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return result

    changes = [float(values[i]) - float(values[i - 1]) for i in range(1, len(values))]
    average_gain = sum(max(change, 0.0) for change in changes[:period]) / period
    average_loss = sum(max(-change, 0.0) for change in changes[:period]) / period
    result[period] = _rsi_value(average_gain, average_loss)
    for index in range(period + 1, len(values)):
        change = changes[index - 1]
        average_gain = (average_gain * (period - 1) + max(change, 0.0)) / period
        average_loss = (average_loss * (period - 1) + max(-change, 0.0)) / period
        result[index] = _rsi_value(average_gain, average_loss)
    return result


def atr_series(candles: Sequence[dict], period: int) -> list[float | None]:
    """Oblicza ATR Wildera, uwzględniając poprzednie zamknięcie w TR.

    Argumenty:
        candles: Chronologiczne świece OHLC.
        period: Dodatni okres ATR.
    Zwraca:
        Serię ATR lub None przed uzyskaniem pełnego okresu.
    Wyjątki:
        ValueError: Gdy okres jest niedodatni albo OHLC jest niepoprawne.
    """
    if period < 1:
        raise ValueError("Okres ATR musi być dodatni.")
    _validate_ohlc(candles)
    result: list[float | None] = [None] * len(candles)
    if len(candles) < period:
        return result
    ranges = _true_ranges(candles)
    average = sum(ranges[:period]) / period
    result[period - 1] = average
    for index in range(period, len(candles)):
        average = (average * (period - 1) + ranges[index]) / period
        result[index] = average
    return result


def adx_series(
    candles: Sequence[dict], period: int
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Oblicza ADX oraz kierunkowe DI metodą Wildera.

    Pierwsza para TR/DM wykorzystuje poprzednią świecę. ADX pojawia się dopiero
    po wygładzeniu ``period`` wartości DX, aby nie pomijać okresu inicjalizacji.

    Argumenty:
        candles: Chronologiczne świece OHLC.
        period: Dodatni okres wygładzania.
    Zwraca:
        Trzy listy: ADX, +DI i -DI; nierozgrzane pozycje mają wartość None.
    Wyjątki:
        ValueError: Gdy okres jest niedodatni albo OHLC jest niepoprawne.
    """
    if period < 1:
        raise ValueError("Okres ADX musi być dodatni.")
    _validate_ohlc(candles)
    length = len(candles)
    adx = [None] * length
    plus_di = [None] * length
    minus_di = [None] * length
    if length <= period:
        return adx, plus_di, minus_di

    true_ranges = []
    positive_moves = []
    negative_moves = []
    for index in range(1, length):
        current, previous = candles[index], candles[index - 1]
        up_move = float(current["high"]) - float(previous["high"])
        down_move = float(previous["low"]) - float(current["low"])
        positive_moves.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        negative_moves.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        true_ranges.append(max(
            float(current["high"]) - float(current["low"]),
            abs(float(current["high"]) - float(previous["close"])),
            abs(float(current["low"]) - float(previous["close"])),
        ))

    first_index = period
    smoothed_tr = sum(true_ranges[:period])
    smoothed_plus = sum(positive_moves[:period])
    smoothed_minus = sum(negative_moves[:period])
    dx_values: list[tuple[int, float]] = []
    for index in range(first_index, length):
        if index > first_index:
            offset = index - 1
            smoothed_tr = smoothed_tr - smoothed_tr / period + true_ranges[offset]
            smoothed_plus = smoothed_plus - smoothed_plus / period + positive_moves[offset]
            smoothed_minus = smoothed_minus - smoothed_minus / period + negative_moves[offset]
        if smoothed_tr == 0:
            current_plus = current_minus = 0.0
        else:
            current_plus = 100.0 * smoothed_plus / smoothed_tr
            current_minus = 100.0 * smoothed_minus / smoothed_tr
        plus_di[index], minus_di[index] = current_plus, current_minus
        denominator = current_plus + current_minus
        dx_values.append((index, 100.0 * abs(current_plus - current_minus) / denominator if denominator else 0.0))

    if len(dx_values) >= period:
        adx_index = dx_values[period - 1][0]
        current_adx = sum(value for _, value in dx_values[:period]) / period
        adx[adx_index] = current_adx
        for index, dx in dx_values[period:]:
            current_adx = (current_adx * (period - 1) + dx) / period
            adx[index] = current_adx
    return adx, plus_di, minus_di


def calculate_indicators(candles: Sequence[dict], config_module) -> dict:
    """Zwraca ostatnie wskaźniki obliczone wyłącznie na przekazanym segmencie.

    Argumenty:
        candles: Chronologiczny, ciągły segment poprawnych świec.
        config_module: Moduł konfiguracji z okresami strategii.
    Zwraca:
        Słownik wskaźników z None dla wartości jeszcze nierozgrzanych.
    Wyjątki:
        ValueError: Gdy przekazane ceny lub świece nie są poprawne.
    """
    closes = [float(candle["close"]) for candle in candles]
    fast = ema_series(closes, config_module.EMA_FAST_PERIOD)
    middle = ema_series(closes, config_module.EMA_MIDDLE_PERIOD)
    slow = ema_series(closes, config_module.EMA_SLOW_PERIOD)
    rsi = rsi_series(closes, config_module.RSI_PERIOD)
    atr = atr_series(candles, config_module.ATR_PERIOD)
    adx, plus_di, minus_di = adx_series(candles, config_module.ADX_PERIOD)

    period = config_module.VOLUME_SMA_PERIOD
    recent_volumes = [candle.get("volume") for candle in candles[-period:]]
    volume_sma = None
    relative_volume = None
    if len(recent_volumes) == period and all(value is not None for value in recent_volumes):
        numeric_volumes = [float(value) for value in recent_volumes]
        volume_sma = sum(numeric_volumes) / period
        if volume_sma > 0:
            relative_volume = numeric_volumes[-1] / volume_sma
    last_candle = candles[-1] if candles else None
    candle_range = (
        float(last_candle["high"]) - float(last_candle["low"])
        if last_candle else 0.0
    )
    lower_wick_ratio = upper_wick_ratio = 0.0
    bullish_candle = bearish_candle = False
    if last_candle and candle_range > 0:
        opening, closing = float(last_candle["open"]), float(last_candle["close"])
        lower_wick_ratio = (min(opening, closing) - float(last_candle["low"])) / candle_range
        upper_wick_ratio = (float(last_candle["high"]) - max(opening, closing)) / candle_range
        bullish_candle = closing > opening
        bearish_candle = closing < opening

    return {
        "ema9": fast[-1] if fast else None,
        "ema21": middle[-1] if middle else None,
        "ema21_series": middle[-5:],
        "ema50": slow[-1] if slow else None,
        "rsi14": rsi[-1] if rsi else None,
        "atr14": atr[-1] if atr else None,
        "volume_sma20": volume_sma,
        "relative_volume": relative_volume,
        "adx14": adx[-1] if adx else None,
        "plus_di14": plus_di[-1] if plus_di else None,
        "minus_di14": minus_di[-1] if minus_di else None,
        "lower_wick_ratio": lower_wick_ratio,
        "upper_wick_ratio": upper_wick_ratio,
        "bullish_candle": bullish_candle,
        "bearish_candle": bearish_candle,
    }


def _rsi_value(average_gain: float, average_loss: float) -> float:
    """Przelicza uśrednione zyski i straty na RSI.

    Argumenty:
        average_gain: Średnia dodatnich zmian ceny.
        average_loss: Średnia bezwzględnych ujemnych zmian ceny.
    Zwraca:
        RSI z zakresu 0–100; brak ruchu zwraca 50.
    Wyjątki:
        Nie zgłasza wyjątków dla poprawnych średnich nieujemnych.
    """
    if average_loss == 0:
        return 50.0 if average_gain == 0 else 100.0
    if average_gain == 0:
        return 0.0
    relative_strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def _validate_ohlc(candles: Sequence[dict]) -> None:
    """Sprawdza zakresy świec przed obliczeniem wskaźników.

    Argumenty:
        candles: Świece zawierające dodatnie i skończone OHLC.
    Zwraca:
        None, jeśli wszystkie świece są poprawne.
    Wyjątki:
        ValueError: Gdy brakuje wartości OHLC albo ich zakres jest sprzeczny.
    """
    for candle in candles:
        try:
            opening, high, low, close = (
                float(candle[key]) for key in ("open", "high", "low", "close")
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Świeca nie zawiera poprawnych wartości OHLC.") from error
        if (not all(isfinite(value) for value in (opening, high, low, close))
                or min(opening, high, low, close) <= 0
                or low > min(opening, close)
                or high < max(opening, close)):
            raise ValueError("Zakres OHLC świecy jest niepoprawny.")


def _true_ranges(candles: Sequence[dict]) -> list[float]:
    """Liczy TR świec, porównując zakres ze zmianą względem poprzedniego close.

    Argumenty:
        candles: Chronologiczne, wcześniej zweryfikowane świece OHLC.
    Zwraca:
        Listę nieujemnych zakresów rzeczywistych o długości wejścia.
    Wyjątki:
        KeyError: Gdy świecy brakuje pola OHLC wymaganego do obliczeń.
    """
    result = []
    for index, candle in enumerate(candles):
        high, low = float(candle["high"]), float(candle["low"])
        if index == 0:
            result.append(high - low)
        else:
            previous_close = float(candles[index - 1]["close"])
            result.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return result
