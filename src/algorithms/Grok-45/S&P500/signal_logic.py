"""Backend: generowanie sygnału M1 trend/pullback bez dostępu do bazy ani API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite

import pandas as pd

import config
from indicators import calculate_indicators

UTC = timezone.utc


def generate_signal(candles_df: pd.DataFrame, now: datetime | None = None) -> dict:
    """Ocenia ostatni zamknięty segment świec i zwraca słownik zgodny z JSON.

    Argumenty:
        candles_df: Ramka z OHLCV i timestamp_utc lub datetime. Czasy naiwne
            wymagają utc_offset_minutes, aby jawnie rozstrzygnąć zmianę DST.
        now: Opcjonalny świadomy czas analizy; domyślnie aktualny czas UTC.
    Zwraca:
        Słownik zawierający instrument, cenę, czasy świecy, sygnał i
        signal_parameters z wartościami wskaźników, punktacją oraz powodami.
        Sygnał ma wartość LONG, SHORT albo NONE; wszystkie wartości są JSON-safe.
    Wyjątki:
        TypeError: Gdy wejście nie jest pandas.DataFrame albo czas jest naiwny.
        ValueError: Gdy kolumny, znaczniki czasu lub zamknięte OHLCV są błędne.

    Scoring ma 100 punktów: trend EMA 20, pullback 15, RSI 15, ATR 10,
    relative volume 15, świeca 10, ADX 10 i DI 5. Wszystkie osiem warunków
    musi być spełnionych, w tym ADX > próg oraz osobno DI zgodne z kierunkiem.
    Trend wymaga ceny po właściwej stronie EMA50 oraz właściwego układu
    EMA9 i EMA21. Pullback wymaga dotknięcia EMA21 i powrotu ceny za tę EMA;
    osobny filtr świecy dopuszcza kierunkowy korpus albo odpowiedni knot.
    Priorytet diagnostyczny: poprawność danych/czasu, wolumen ostatniej świecy,
    świeżość, rozgrzewka, następnie pozostałe filtry konfluencji.
    """
    if not isinstance(candles_df, pd.DataFrame):
        raise TypeError("candles_df musi być obiektem pandas.DataFrame.")
    reference = _aware_utc(now if now is not None else datetime.now(UTC), "now")
    result = _empty_result()
    if candles_df.empty:
        result["signal_parameters"]["reason"] = "Brak danych wejściowych."
        result["signal_parameters"]["reasons"] = ["no_data"]
        return result

    required = {"open", "high", "low", "close", "volume"}
    missing = required.difference(candles_df.columns)
    if missing:
        raise ValueError(f"Brak wymaganych kolumn: {', '.join(sorted(missing))}.")
    timestamp_column = "timestamp_utc" if "timestamp_utc" in candles_df.columns else "datetime"
    if timestamp_column not in candles_df.columns:
        raise ValueError("Wymagana jest kolumna timestamp_utc albo datetime.")

    rows = []
    for index, source in candles_df.iterrows():
        opened = _row_timestamp(source, timestamp_column, candles_df.columns)
        values = {key: source[key] for key in ("open", "high", "low", "close", "volume")}
        if pd.isna(values["volume"]):
            values["volume"] = None
        if opened + timedelta(minutes=1) > reference:
            continue
        _validate_candle(values)
        rows.append((opened, values, index))
    rows.sort(key=lambda item: item[0])
    if len({item[0] for item in rows}) != len(rows):
        raise ValueError("Historia zawiera powtórzone otwarcia świec.")
    if not rows:
        result["signal_parameters"]["reason"] = "Brak zamkniętych świec."
        result["signal_parameters"]["reasons"] = ["no_closed_data"]
        return result

    rows = rows[-config.HISTORY_LIMIT:]
    last_open, last_candle, _ = rows[-1]
    closed_at = last_open + timedelta(minutes=1)
    result.update(
        price=float(last_candle["close"]),
        timestamp=closed_at.astimezone(config.WARSAW).isoformat(),
        candle_open_time=last_open.astimezone(config.WARSAW).isoformat(),
        candle_open_epoch=int(last_open.timestamp()),
    )
    parameters = result["signal_parameters"]
    parameters["volume"] = last_candle["volume"]
    parameters["segment_candles"] = 0
    if last_candle["volume"] is None or float(last_candle["volume"]) <= 0:
        parameters["reason"] = "Brak dodatniego wolumenu na ostatniej świecy."
        parameters["reasons"] = ["invalid_or_zero_volume"]
        return result
    age_minutes = (reference - closed_at).total_seconds() / 60.0
    parameters["data_age_minutes"] = age_minutes
    if age_minutes > config.MAX_STALE_MINUTES:
        parameters["reason"] = (
            f"Ostatnia świeca jest nieaktualna (limit {config.MAX_STALE_MINUTES} min)."
        )
        parameters["reasons"] = ["stale_data"]
        return result

    segment_start = len(rows) - 1
    for index in range(len(rows) - 1, 0, -1):
        gap = (rows[index][0] - rows[index - 1][0]).total_seconds() / 60.0
        if gap > 2:
            break
        segment_start = index - 1
    segment = rows[segment_start:]
    parameters["segment_candles"] = len(segment)
    if len(segment) < config.MIN_SEGMENT_CANDLES:
        parameters["reason"] = (
            f"Za mało świec w ostatnim segmencie: wymagane {config.MIN_SEGMENT_CANDLES}."
        )
        parameters["reasons"] = ["insufficient_segment"]
        return result

    candles = [item[1] for item in segment]
    indicators = calculate_indicators(candles, config)
    parameters.update(indicators)
    parameters.update(
        ema_9=indicators["ema9"],
        ema_21=indicators["ema21"],
        ema_50=indicators["ema50"],
        rsi_14=indicators["rsi14"],
        atr_14=indicators["atr14"],
        volume_sma_20=indicators["volume_sma20"],
        adx_14=indicators["adx14"],
        plus_di=indicators["plus_di14"],
        minus_di=indicators["minus_di14"],
    )
    parameters.update(
        score=0,
        score_max=100,
        score_components={key: 0 for key in config.SCORE_WEIGHTS},
        conditions={},
    )
    checks_by_direction = {
        "LONG": _conditions("LONG", candles, indicators),
        "SHORT": _conditions("SHORT", candles, indicators),
    }
    scores = {
        direction: sum(
            config.SCORE_WEIGHTS[name]
            for name, passed in checks.items()
            if passed
        )
        for direction, checks in checks_by_direction.items()
    }
    best_direction = max(scores, key=scores.get)
    best_checks = checks_by_direction[best_direction]
    parameters["score"] = scores[best_direction]
    parameters["score_components"] = {
        name: config.SCORE_WEIGHTS[name] if passed else 0
        for name, passed in best_checks.items()
    }
    parameters["conditions"] = dict(best_checks)
    reasons = [name for name, passed in best_checks.items() if not passed]
    if not reasons:
        reasons = [f"all_filters_passed_{best_direction.lower()}"]
        result["signal"] = best_direction
    parameters["reasons"] = reasons
    parameters["reason"] = (
        f"Spełniono wszystkie warunki dla {best_direction}."
        if result["signal"] != "NONE"
        else "Nie spełniono wszystkich wymaganych filtrów."
    )
    return result


def _empty_result() -> dict:
    """Tworzy podstawowy, bezpieczny dla JSON kształt wyniku NONE.

    Argumenty:
        Brak.
    Zwraca:
        Słownik z pustymi ceną i czasami oraz polami sygnału.
    Wyjątki:
        Nie zgłasza wyjątków.
    """
    return {
        "instrument": config.INSTRUMENT,
        "algorithm_name": config.STRATEGY_NAME,
        "price": None,
        "timestamp": None,
        "candle_open_time": None,
        "candle_open_epoch": None,
        "signal": "NONE",
        "max_hold_minutes": config.MAX_HOLD_MINUTES,
        "signal_parameters": {
            "ema_9": None,
            "ema_21": None,
            "ema_50": None,
            "rsi_14": None,
            "atr_14": None,
            "volume": None,
            "volume_sma_20": None,
            "relative_volume": None,
            "adx_14": None,
            "plus_di": None,
            "minus_di": None,
            "score": 0,
            "score_max": 100,
            "reason": "",
            "reasons": [],
            "conditions": {},
            "score_components": {},
        },
    }


def _aware_utc(value: datetime, name: str) -> datetime:
    """Wymaga świadomego datetime i normalizuje go do UTC.

    Argumenty:
        value: Wartość czasu ze znaną strefą.
        name: Nazwa pola używana w komunikacie błędu.
    Zwraca:
        Ten sam moment jako świadomy datetime UTC.
    Wyjątki:
        TypeError: Gdy argument nie jest świadomym obiektem datetime.
    """
    if not isinstance(value, datetime):
        raise TypeError(f"{name} musi być obiektem datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise TypeError(f"{name} musi mieć jawną strefę czasową.")
    return value.astimezone(UTC)


def _conditions(direction: str, candles: list[dict], values: dict) -> dict[str, bool]:
    """Ocenia osiem osobnych warunków konfluencji wybranego kierunku.

    Argumenty:
        direction: Kierunek LONG albo SHORT.
        candles: Poprawny segment zakończony ostatnią zamkniętą świecą.
        values: Ostatnie wartości wskaźników.
    Zwraca:
        Mapę nazw filtrów na wyniki logiczne.
    Wyjątki:
        KeyError: Gdy brakuje wymaganej wartości wskaźnika lub OHLC.
    """
    long_side = direction == "LONG"
    ema9, ema21, ema50 = values["ema9"], values["ema21"], values["ema50"]
    atr, rsi, adx = values["atr14"], values["rsi14"], values["adx14"]
    plus_di, minus_di = values["plus_di14"], values["minus_di14"]
    volume = values["relative_volume"]
    last = candles[-1]
    trend = (
        ema9 is not None and ema21 is not None and ema50 is not None
        and (ema9 > ema21 if long_side else ema9 < ema21)
        and (float(last["close"]) > ema50 if long_side else float(last["close"]) < ema50)
    )
    pullback = _has_recent_pullback(candles, values, direction)
    rsi_ok = (
        rsi is not None
        and (config.LONG_RSI_MIN <= rsi <= config.LONG_RSI_MAX if long_side
             else config.SHORT_RSI_MIN <= rsi <= config.SHORT_RSI_MAX)
    )
    atr_ok = atr is not None and atr > config.MIN_ATR
    volume_ok = volume is not None and volume > config.MIN_RELATIVE_VOLUME
    candle_ok = (
        values["bullish_candle"] or values["lower_wick_ratio"] > config.MIN_REJECTION_WICK_RATIO
        if long_side else
        values["bearish_candle"] or values["upper_wick_ratio"] > config.MIN_REJECTION_WICK_RATIO
    )
    adx_ok = adx is not None and adx > config.MIN_ADX
    di_ok = (
        plus_di is not None and minus_di is not None
        and (plus_di > minus_di if long_side else minus_di > plus_di)
    )
    return {
        "ema_trend": bool(trend),
        "pullback": bool(pullback),
        "rsi": bool(rsi_ok),
        "atr": bool(atr_ok),
        "relative_volume": bool(volume_ok),
        "candle": bool(candle_ok),
        "adx": bool(adx_ok),
        "directional_indicator": bool(di_ok),
    }


def _has_recent_pullback(candles: list[dict], values: dict, direction: str) -> bool:
    """Sprawdza dotknięcie właściwej EMA21 w ostatnich 3–5 świecach.

    Argumenty:
        candles: Chronologiczny segment cen.
        values: Wskaźniki wraz z szeregiem EMA21.
        direction: Kierunek LONG albo SHORT.
    Zwraca:
        True, gdy w oknie był pullback i ostatnie zamknięcie jest po właściwej
        stronie bieżącej EMA21, niezależnie od kierunku korpusu świecy.
    Wyjątki:
        KeyError: Gdy brak wymaganych cen lub wskaźników.
    """
    atr, ema21 = values["atr14"], values["ema21"]
    if atr is None or atr <= 0 or ema21 is None:
        return False
    lookback = min(config.PULLBACK_LOOKBACK_MAX, len(candles))
    lookback = max(config.PULLBACK_LOOKBACK_MIN, lookback)
    distance = atr * config.PULLBACK_MAX_DISTANCE_ATR
    recent = candles[-lookback:]
    ema21_values = values["ema21_series"][-lookback:]
    last = candles[-1]
    if direction == "LONG":
        touched = any(
            average is not None and float(row["low"]) <= average + distance
            for row, average in zip(recent, ema21_values)
        )
        return touched and float(last["close"]) > ema21
    touched = any(
        average is not None and float(row["high"]) >= average - distance
        for row, average in zip(recent, ema21_values)
    )
    return touched and float(last["close"]) < ema21


def _row_timestamp(source, timestamp_column: str, columns) -> datetime:
    """Konwertuje znacznik czasu do UTC bez zgadywania offsetu przy zmianie DST.

    Argumenty:
        source: Wiersz pandas z wartością czasu i ewentualnym offsetem.
        timestamp_column: Nazwa wybranej kolumny czasu.
        columns: Nazwy kolumn ramki wejściowej.
    Zwraca:
        Świadomy czas otwarcia świecy w UTC.
    Wyjątki:
        ValueError: Gdy znacznik lub jego jawny offset jest niepoprawny.
    """
    stamp = source[timestamp_column]
    if pd.isna(stamp):
        raise ValueError("Znacznik czasu nie może być pusty.")
    if not isinstance(stamp, (datetime, pd.Timestamp, str)):
        raise ValueError("Znacznik czasu musi być datą albo tekstem ISO.")
    if isinstance(stamp, pd.Timestamp):
        stamp = stamp.to_pydatetime()
    elif not isinstance(stamp, datetime):
        stamp = pd.Timestamp(stamp).to_pydatetime()
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        if timestamp_column != "datetime" or "utc_offset_minutes" not in columns:
            raise ValueError("Naiwny czas wymaga utc_offset_minutes i kolumny datetime.")
        offset = source["utc_offset_minutes"]
        if pd.isna(offset) or not isfinite(float(offset)) or float(offset) % 1:
            raise ValueError("utc_offset_minutes musi być całkowitym offsetem minutowym.")
        minutes = int(offset)
        candidate = stamp.replace(tzinfo=timezone(timedelta(minutes=minutes)))
        local = candidate.astimezone(config.WARSAW)
        if (local.replace(tzinfo=None) != stamp or local.utcoffset() != candidate.utcoffset()):
            raise ValueError("Podany offset nie jest poprawny dla czasu warszawskiego.")
        stamp = candidate
    elif timestamp_column == "timestamp_utc" and stamp.utcoffset() != timedelta(0):
        raise ValueError("timestamp_utc musi mieć jawny offset UTC.")
    elif timestamp_column == "datetime":
        local = stamp.astimezone(UTC).astimezone(config.WARSAW)
        if (local.replace(tzinfo=None) != stamp.replace(tzinfo=None)
                or local.utcoffset() != stamp.utcoffset()):
            raise ValueError("Świadomy datetime musi mieć poprawny offset Warszawy.")
    if stamp.second or stamp.microsecond:
        raise ValueError("Świeca M1 musi zaczynać się na pełnej minucie.")
    return stamp.astimezone(UTC)


def _validate_candle(candle: dict) -> None:
    """Waliduje OHLCV świecy zamkniętej, zachowując brak wolumenu jako None.

    Argumenty:
        candle: Słownik świecy, którego wolumen może być None.
    Zwraca:
        None po poprawnej walidacji.
    Wyjątki:
        ValueError: Gdy OHLC jest sprzeczne albo wolumen jest ujemny/niefinity.
    """
    try:
        opening, high, low, close = (
            float(candle[key]) for key in ("open", "high", "low", "close")
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Świeca zawiera niepoprawną wartość OHLC.") from error
    if (not all(isfinite(value) and value > 0 for value in (opening, high, low, close))
            or low > min(opening, close) or high < max(opening, close)):
        raise ValueError("Świeca zawiera niepoprawny zakres OHLC.")
    volume = candle["volume"]
    if volume is not None:
        try:
            numeric_volume = float(volume)
        except (TypeError, ValueError) as error:
            raise ValueError("Wolumen musi być liczbą nieujemną albo None.") from error
        if not isfinite(numeric_volume) or numeric_volume < 0:
            raise ValueError("Wolumen musi być liczbą nieujemną albo None.")
        candle["volume"] = numeric_volume
