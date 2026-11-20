"""Buduje zwięzły, bezstratny kontekst świec dla prompta weryfikującego."""

# Backend
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from typing import Any, Sequence

logger = logging.getLogger(__name__)

MAXIMUM_HISTORY_SIZE = 1000
MINUTE_CANDLES_TO_RETURN = 50
FIVE_MINUTE_CANDLES_TO_RETURN = 30
SECONDS_PER_MINUTE = 60
SECONDS_PER_FIVE_MINUTES = 5 * SECONDS_PER_MINUTE
MAXIMUM_PRICE_PRECISION = 10
OHLC_FIELDS = ("open", "high", "low", "close")


class PromptContextError(ValueError):
    """Informuje, że nie można bezpiecznie zbudować historii świec do prompta."""


class CandlePromptContextBuilder:
    """Udostępnia API-owy odpowiednik budowniczego kontekstu świec."""

    @staticmethod
    def build_json(
        symbol: str, history: Sequence[dict[str, Any]]
    ) -> str:
        """Buduje JSON świec; walidację i agregację wykonuje wspólna funkcja."""
        return build_candle_prompt_context(symbol, history)


def build_candle_prompt_context(
    symbol: str, history: Sequence[dict[str, Any]]
) -> str:
    """Buduje JSON z ostatnimi 50 świecami M1 i 30 pełnymi świecami M5.

    Args:
        symbol: Symbol instrumentu zapisany w bazie.
        history: Świece M1 uporządkowane rosnąco; ceny są tekstem dziesiętnym.

    Returns:
        JSON z cenami OHLC zakodowanymi jako całkowite ticki.

    Raises:
        PromptContextError: Gdy historia jest niekompletna, nieuporządkowana
            lub zawiera nieprawidłowe ceny.
    """
    if not symbol or len(symbol) > 32 or len(history) > MAXIMUM_HISTORY_SIZE:
        raise PromptContextError(
            "Symbol lub liczba świec do kontekstu prompta jest nieprawidłowa."
        )

    validated_history = []
    previous_timestamp = -1
    for index, candle in enumerate(history):
        validated_candle = _validate_candle(candle, index, previous_timestamp)
        previous_timestamp = validated_candle["timestamp_utc"]
        validated_history.append(validated_candle)
    if len(validated_history) < MINUTE_CANDLES_TO_RETURN:
        raise PromptContextError(
            "Do kontekstu prompta potrzeba co najmniej 50 zamkniętych świec M1."
        )

    minute_candles = validated_history[-MINUTE_CANDLES_TO_RETURN:]
    five_minute_candles = _aggregate_five_minute_candles(validated_history)
    if len(five_minute_candles) < FIVE_MINUTE_CANDLES_TO_RETURN:
        raise PromptContextError(
            "Do kontekstu prompta potrzeba 30 pełnych świec M5."
        )
    five_minute_candles = five_minute_candles[-FIVE_MINUTE_CANDLES_TO_RETURN:]

    gap_search_start = (
        five_minute_candles[0]["timestamp_utc"]
        if len(five_minute_candles) >= FIVE_MINUTE_CANDLES_TO_RETURN
        else minute_candles[0]["timestamp_utc"]
    )
    gap_search_end = minute_candles[-1]["timestamp_utc"]
    if any(
        candle["gap_before"]
        and gap_search_start <= candle["timestamp_utc"] <= gap_search_end
        for candle in validated_history
    ):
        logger.warning(
            "W historii prompta dla %s wykryto lukę w świecach.", symbol
        )

    selected_prices = [
        price
        for candle in (*minute_candles, *five_minute_candles)
        for price in candle["prices"].values()
    ]
    price_precision = max(_decimal_precision(price) for price in selected_prices)
    price_base = min(selected_prices).to_integral_value(rounding=ROUND_FLOOR)
    tick_scale = Decimal(10) ** price_precision
    tick_precision = max(1, price_precision)

    payload = {
        "synthetic": False,
        "symbol": symbol,
        "price_base": _format_decimal(price_base, 1),
        "tick_size": _format_decimal(Decimal(1) / tick_scale, tick_precision),
        "row_format": "[open,high,low,close]",
        "M1": {
            "start_utc": _format_utc_timestamp(minute_candles[0]["timestamp_utc"]),
            "step_minutes": 1,
            "ohlc_ticks": [
                _prices_to_ticks(candle["prices"], price_base, tick_scale)
                for candle in minute_candles
            ],
        },
        "M5": {
            "start_utc": _format_utc_timestamp(
                five_minute_candles[0]["timestamp_utc"]
            ),
            "step_minutes": 5,
            "ohlc_ticks": [
                _prices_to_ticks(candle["prices"], price_base, tick_scale)
                for candle in five_minute_candles
            ],
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _validate_candle(
    candle: dict[str, Any], index: int, previous_timestamp: int
) -> dict[str, Any]:
    """Sprawdza czas, ceny i zakres OHLC pojedynczej świecy M1."""
    if not isinstance(candle, dict):
        raise PromptContextError(
            f"Rekord świecy przy indeksie {index} nie jest obiektem."
        )
    timestamp = candle.get("timestamp_utc")
    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, int)
        or timestamp < 0
        or timestamp % SECONDS_PER_MINUTE != 0
    ):
        raise PromptContextError("Historia zawiera nieprawidłowy czas świecy M1.")

    if timestamp <= previous_timestamp:
        raise PromptContextError(
            "Świece w historii prompta nie są uporządkowane rosnąco."
        )

    prices = {
        field: _parse_price(candle.get(field))
        for field in OHLC_FIELDS
    }
    if (
        prices["high"] < max(prices["open"], prices["close"])
        or prices["low"] > min(prices["open"], prices["close"])
        or prices["low"] > prices["high"]
    ):
        raise PromptContextError("Historia zawiera nieprawidłowy zakres OHLC.")

    return {
        "timestamp_utc": timestamp,
        "prices": prices,
        "gap_before": candle.get("gap_before") is True,
    }


def _parse_price(value: Any) -> Decimal:
    """Odczytuje nieujemną cenę z najwyżej dziesięcioma miejscami po przecinku."""
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise PromptContextError("Cena świecy ma nieprawidłowy format.") from error
    if not price.is_finite() or price < 0 or _decimal_precision(price) > MAXIMUM_PRICE_PRECISION:
        raise PromptContextError("Cena świecy ma nieprawidłową precyzję.")
    return price


def _decimal_precision(value: Decimal) -> int:
    """Zwraca liczbę znaczących cyfr po separatorze dziesiętnym."""
    return max(0, -value.normalize().as_tuple().exponent)


def _aggregate_five_minute_candles(
    history: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Łączy wyłącznie kompletne grupy pięciu kolejnych świec M1 w UTC."""
    buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for candle in history:
        bucket_start = (
            candle["timestamp_utc"]
            - candle["timestamp_utc"] % SECONDS_PER_FIVE_MINUTES
        )
        buckets[bucket_start].append(candle)

    aggregated = []
    for bucket_start, rows in sorted(buckets.items()):
        expected_timestamps = [
            bucket_start + index * SECONDS_PER_MINUTE for index in range(5)
        ]
        if [row["timestamp_utc"] for row in rows] != expected_timestamps:
            continue
        aggregated.append(
            {
                "timestamp_utc": bucket_start,
                "prices": {
                    "open": rows[0]["prices"]["open"],
                    "high": max(row["prices"]["high"] for row in rows),
                    "low": min(row["prices"]["low"] for row in rows),
                    "close": rows[-1]["prices"]["close"],
                },
            }
        )
    return aggregated


def _prices_to_ticks(
    prices: dict[str, Decimal], price_base: Decimal, tick_scale: Decimal
) -> list[int]:
    """Przelicza OHLC na ticki bez użycia liczb zmiennoprzecinkowych."""
    return [
        int((prices[field] - price_base) * tick_scale)
        for field in OHLC_FIELDS
    ]


def _format_decimal(value: Decimal, precision: int) -> str:
    """Formatuje liczbę bez notacji wykładniczej i zbędnych zer."""
    formatted = format(value, f".{precision}f")
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    if "." not in formatted:
        formatted += ".0"
    return formatted


def _format_utc_timestamp(timestamp: int) -> str:
    """Formatuje sekundowy czas Unix jako ISO 8601 UTC."""
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (OverflowError, OSError, ValueError) as error:
        raise PromptContextError(
            "Nie można sformatować czasu świecy jako UTC."
        ) from error
