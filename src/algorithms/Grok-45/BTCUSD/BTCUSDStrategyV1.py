"""Strategia mikrotransakcji BTC/USD na zamkniętych świecach jednominutowych."""

from __future__ import annotations

STRATEGY_CONTEXT = """
Strategia mikrotransakcji BTC/USD M1 V1
- Interwał: 1 minuta
- Horyzont oceny potencjału: około 20 minut
- LONG: EMA8 > EMA21 + zamknięcie > EMA8 + RSI9 52-72 + wybicie najwyższego zamknięcia z 6 świec + wzrostowy korpus > 0,35*ATR14
- SHORT: warunki odwrotne (EMA8 < EMA21, RSI9 28-48, wybicie najniższego zamknięcia, spadkowy korpus)
- Filtry: momentum i krótkoterminowe wybicie struktury ceny
- Wolumen jest ignorowany, ponieważ źródło Twelve Data zwraca null
"""

import logging
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable
from zoneinfo import ZoneInfo

from .config import (
    ATR_PERIOD,
    BREAKOUT_LOOKBACK,
    CANDLE_DURATION,
    COOLDOWN_SECONDS,
    DATABASE_SYMBOL,
    EMA_FAST_PERIOD,
    EMA_SLOW_PERIOD,
    HISTORY_LIMIT,
    INSTRUMENT,
    LONG_RSI_MAXIMUM,
    LONG_RSI_MINIMUM,
    MINIMUM_BODY_ATR_MULTIPLIER,
    MINIMUM_CANDLES,
    RSI_PERIOD,
    SHORT_RSI_MAXIMUM,
    SHORT_RSI_MINIMUM,
)
from .indicators import atr_wilder, ema_series, rsi_wilder

WARSAW_TIMEZONE = ZoneInfo("Europe/Warsaw")
UTC = timezone.utc
Candle = dict[str, Any]


@runtime_checkable
class CandleDataFrame(Protocol):
    """Opisuje minimalny interfejs DataFrame używany do odczytu świec."""

    def to_dict(self, orient: str = "dict") -> object:
        """Zwraca wiersze jako rekordy słownikowe dla wskazanego formatu."""
        ...


CandleInput = Sequence[Mapping[str, object]] | CandleDataFrame


# Backend
class BTCUSDStrategyV1:
    """Ocenia zamknięte świece BTC/USD i zwraca krótki sygnał transakcyjny."""

    def evaluate(
        self,
        candles: CandleInput,
        now: datetime | None = None,
        last_signal_timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Ocenia ostatnią zamkniętą świecę i buduje wynik dla lokalnego API.

        Args:
            candles: Świece OHLCV jako lista słowników albo obiekt DataFrame.
            now: Bieżący czas ze strefą; domyślnie aktualny czas UTC.
            last_signal_timestamp: Czas otwarcia ostatniej świecy LONG/SHORT
                tej strategii, zapisany jako Unix timestamp.

        Returns:
            Wynik API oraz dokładny JSON przeznaczony do signal_parameters.

        Raises:
            ValueError: Gdy brakuje świec, dane są niepoprawne albo ostatnia
                świeca nie została jeszcze zamknięta.
        """
        current_time = now or datetime.now(UTC)
        if current_time.tzinfo is None or current_time.utcoffset() is None:
            raise ValueError("Bieżący czas musi zawierać strefę czasową.")
        current_time = current_time.astimezone(UTC)

        ordered_candles = self._validate_and_order(candles)[-HISTORY_LIMIT:]
        if not ordered_candles:
            raise ValueError("Brak świec wejściowych.")

        current = ordered_candles[-1]
        candle_open_time = current["timestamp_utc"]
        candle_close_time = candle_open_time + CANDLE_DURATION
        if candle_close_time > current_time:
            raise ValueError("Nie można oceniać niezakończonej świecy M1.")

        if last_signal_timestamp is not None:
            if (
                isinstance(last_signal_timestamp, bool)
                or not isinstance(last_signal_timestamp, int)
                or last_signal_timestamp < 0
                or last_signal_timestamp >= int(candle_open_time.timestamp())
            ):
                raise ValueError("Czas poprzedniego sygnału jest nieprawidłowy.")

        signal, reason = self._generate_direction(
            ordered_candles,
            last_signal_timestamp=last_signal_timestamp,
        )
        price = float(current["close"])
        candle_open_epoch = int(candle_open_time.timestamp())
        candle_close_epoch = int(candle_close_time.timestamp())
        payload = {
            "price": price,
            "signal": signal,
            "timestamp": candle_close_epoch,
            "instrument": INSTRUMENT,
            "reason": reason,
        }
        close_time_local = candle_close_time.astimezone(WARSAW_TIMEZONE).isoformat()

        return {
            "instrument": INSTRUMENT,
            "database_symbol": DATABASE_SYMBOL,
            "price": price,
            "signal": signal,
            "reason": reason,
            "timestamp": close_time_local,
            "candle_open_epoch": candle_open_epoch,
            "candle_close_time": close_time_local,
            "signal_parameters": payload,
        }

    def generate_signal(
        self,
        candles: CandleInput,
        now: datetime | None = None,
        last_signal_timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Zwraca wyłącznie wymagany, pięcioelementowy obiekt JSON sygnału.

        Args:
            candles: Świece OHLCV jako lista słowników albo obiekt DataFrame.
            now: Bieżący czas ze strefą; domyślnie aktualny czas UTC.
            last_signal_timestamp: Czas otwarcia ostatniej świecy LONG/SHORT
                tej strategii, zapisany jako Unix timestamp.

        Returns:
            Obiekt zawierający wyłącznie price, signal, timestamp, instrument
            i reason.

        Raises:
            ValueError: Gdy dane wejściowe lub czas ostatniego sygnału są
                nieprawidłowe.
        """
        return self.evaluate(
            candles,
            now=now,
            last_signal_timestamp=last_signal_timestamp,
        )["signal_parameters"]

    def _generate_direction(
        self,
        candles: Sequence[Candle],
        *,
        last_signal_timestamp: int | None,
    ) -> tuple[str, str]:
        """Sprawdza wskaźniki i stosuje reguły LONG, SHORT oraz cooldownu.

        Args:
            candles: Posortowane i zweryfikowane świece OHLCV.
            last_signal_timestamp: Czas otwarcia ostatniego sygnału kierunkowego.

        Returns:
            Kierunek LONG/SHORT/NONE oraz krótki powód wyniku.
        """
        if len(candles) < MINIMUM_CANDLES:
            return (
                "NONE",
                f"Za mało danych do rozgrzania wskaźników ({len(candles)}/"
                f"{MINIMUM_CANDLES} świec).",
            )

        closes = [float(candle["close"]) for candle in candles]
        indicators = {
            "ema_fast": ema_series(closes, EMA_FAST_PERIOD)[-1],
            "ema_slow": ema_series(closes, EMA_SLOW_PERIOD)[-1],
            "rsi": rsi_wilder(closes, RSI_PERIOD),
            "atr": atr_wilder(candles, ATR_PERIOD),
        }
        if any(value is None for value in indicators.values()):
            return "NONE", "Brak wystarczających danych do obliczenia wskaźników."

        current = candles[-1]
        previous_closes = closes[-(BREAKOUT_LOOKBACK + 1) : -1]
        if len(previous_closes) < BREAKOUT_LOOKBACK:
            return "NONE", "Brak sześciu świec do potwierdzenia wybicia."

        conditions = self._conditions(
            current,
            previous_closes,
            indicators,
        )
        long_ready = all(conditions["long"].values())
        short_ready = all(conditions["short"].values())

        if long_ready and short_ready:
            return "NONE", "Konflikt kierunków: jednocześnie spełniono LONG i SHORT."
        if not long_ready and not short_ready:
            return "NONE", self._none_reason(conditions)

        candle_open_epoch = int(current["timestamp_utc"].timestamp())
        cooldown_active = (
            last_signal_timestamp is not None
            and 0
            < candle_open_epoch - last_signal_timestamp
            <= COOLDOWN_SECONDS
        )
        if cooldown_active:
            return (
                "NONE",
                "Cooldown: w ostatnich 4 minutach wystąpił sygnał LONG lub SHORT.",
            )
        if long_ready:
            return "LONG", "Spełniono warunki wybicia wzrostowego BTC/USD."
        return "SHORT", "Spełniono warunki wybicia spadkowego BTC/USD."

    @staticmethod
    def _conditions(
        current: Candle,
        previous_closes: Sequence[float],
        indicators: dict[str, float | None],
    ) -> dict[str, dict[str, bool]]:
        """Oblicza wszystkie obowiązkowe warunki dla obu kierunków.

        Args:
            current: Najnowsza zweryfikowana świeca.
            previous_closes: Zamknięcia sześciu świec przed bieżącą.
            indicators: Ostatnie wartości EMA, RSI i ATR.

        Returns:
            Nazwane warunki LONG i SHORT, gotowe do połączenia operatorem AND.
        """
        close = float(current["close"])
        open_price = float(current["open"])
        body = abs(close - open_price)
        ema_fast = indicators["ema_fast"]
        ema_slow = indicators["ema_slow"]
        rsi = indicators["rsi"]
        atr = indicators["atr"]
        body_large_enough = (
            atr is not None and body > MINIMUM_BODY_ATR_MULTIPLIER * atr
        )

        return {
            "long": {
                "ema_trend": (
                    ema_fast is not None
                    and ema_slow is not None
                    and ema_fast > ema_slow
                ),
                "close_above_ema": ema_fast is not None and close > ema_fast,
                "rsi_range": (
                    rsi is not None
                    and LONG_RSI_MINIMUM <= rsi <= LONG_RSI_MAXIMUM
                ),
                "breakout": close > max(previous_closes),
                "candle_direction": close > open_price,
                "body_large_enough": body_large_enough,
            },
            "short": {
                "ema_trend": (
                    ema_fast is not None
                    and ema_slow is not None
                    and ema_fast < ema_slow
                ),
                "close_above_ema": ema_fast is not None and close < ema_fast,
                "rsi_range": (
                    rsi is not None
                    and SHORT_RSI_MINIMUM <= rsi <= SHORT_RSI_MAXIMUM
                ),
                "breakout": close < min(previous_closes),
                "candle_direction": close < open_price,
                "body_large_enough": body_large_enough,
            },
        }

    @staticmethod
    def _none_reason(conditions: dict[str, dict[str, bool]]) -> str:
        """Buduje krótki powód braku sygnału na podstawie blokujących filtrów.

        Args:
            conditions: Warunki obliczone dla LONG i SHORT.

        Returns:
            Powód NONE bez dodatkowych wskaźników w JSON.
        """
        failed_filters = []
        if not any(
            conditions[direction]["ema_trend"]
            and conditions[direction]["close_above_ema"]
            for direction in ("long", "short")
        ):
            failed_filters.append("trend EMA lub położenie ceny")
        if not any(
            conditions[direction]["rsi_range"]
            for direction in ("long", "short")
        ):
            failed_filters.append("RSI poza wymaganym zakresem")
        if not any(
            conditions[direction]["breakout"]
            for direction in ("long", "short")
        ):
            failed_filters.append("brak wybicia z ostatnich 6 świec")
        if not any(
            conditions[direction]["candle_direction"]
            and conditions[direction]["body_large_enough"]
            for direction in ("long", "short")
        ):
            failed_filters.append("korpus świecy jest zbyt mały lub bez kierunku")
        if not failed_filters:
            return "Warunki LONG ani SHORT nie zostały spełnione."
        return "Brak sygnału: " + "; ".join(failed_filters) + "."

    @classmethod
    def _validate_and_order(
        cls,
        candles: CandleInput,
    ) -> list[Candle]:
        """Waliduje i sortuje świece, nie uzupełniając przerw w danych.

        Args:
            candles: Lista słowników albo obiekt DataFrame zawierający OHLCV.

        Returns:
            Zweryfikowane świece w kolejności UTC od najstarszej.

        Raises:
            ValueError: Gdy świece, czasy albo ceny są niepoprawne.
        """
        records = cls._to_records(candles)
        normalized: list[Candle] = []
        for source in records:
            candle = dict(source)
            instrument = candle.get("instrument", candle.get("symbol"))
            if (
                instrument is not None
                and str(instrument).replace("/", "").upper() != INSTRUMENT
            ):
                raise ValueError(f"Strategia przyjmuje wyłącznie {INSTRUMENT}.")
            candle["timestamp_utc"] = cls._normalize_timestamp(candle)

            try:
                prices = tuple(
                    float(candle[key]) for key in ("open", "high", "low", "close")
                )
            except (KeyError, TypeError, ValueError, OverflowError) as error:
                raise ValueError(
                    "Świeca musi zawierać poprawne wartości OHLC."
                ) from error

            if (
                not all(isfinite(value) and value > 0 for value in prices)
                or prices[1] < max(prices[0], prices[3])
                or prices[2] > min(prices[0], prices[3])
                or prices[2] > prices[1]
            ):
                raise ValueError("Świeca zawiera nieprawidłowe wartości OHLC.")

            # Wolumen z Twelve Data jest niedostępny, więc nie wpływa na sygnał.
            candle.pop("volume", None)
            candle.update(
                {
                    "open": prices[0],
                    "high": prices[1],
                    "low": prices[2],
                    "close": prices[3],
                }
            )
            normalized.append(candle)

        ordered = sorted(normalized, key=lambda candle: candle["timestamp_utc"])
        previous_timestamp: datetime | None = None
        for candle in ordered:
            timestamp = candle["timestamp_utc"]
            if timestamp.second != 0 or timestamp.microsecond != 0:
                raise ValueError("Świeca M1 musi zaczynać się równo w pełnej minucie.")
            if timestamp == previous_timestamp:
                raise ValueError("Historia zawiera powtórzony timestamp świecy.")
            previous_timestamp = timestamp
        return ordered

    @staticmethod
    def _to_records(
        candles: CandleInput,
    ) -> list[Mapping[str, object]]:
        """Zamienia listę lub DataFrame na listę rekordów słownikowych.

        Args:
            candles: Dane wejściowe jako sekwencja albo obiekt z metodą to_dict.

        Returns:
            Lista rekordów OHLCV.

        Raises:
            ValueError: Gdy wejście jest puste lub nie ma oczekiwanej postaci.
        """
        if isinstance(candles, CandleDataFrame):
            records = candles.to_dict(orient="records")
        else:
            records = candles
        if (
            not isinstance(records, Sequence)
            or isinstance(records, (str, bytes))
            or not records
            or any(not isinstance(record, Mapping) for record in records)
        ):
            raise ValueError("Świece muszą być niepustą listą rekordów OHLCV.")
        return list(records)

    @staticmethod
    def _normalize_timestamp(candle: Candle) -> datetime:
        """Przelicza czas świecy na UTC z uwzględnieniem czasu Europe/Warsaw.

        Args:
            candle: Rekord z polem czasu oraz opcjonalnym przesunięciem UTC.

        Returns:
            Czas otwarcia świecy jako obiekt datetime w UTC.

        Raises:
            ValueError: Gdy brakuje czasu albo jest on niejednoznaczny lub błędny.
        """
        timestamp_key = next(
            (
                key
                for key in ("timestamp_utc", "datetime", "open_time", "timestamp")
                if candle.get(key) is not None
            ),
            None,
        )
        timestamp = candle.get(timestamp_key) if timestamp_key is not None else None
        if isinstance(timestamp, bool):
            raise ValueError("Timestamp świecy jest nieprawidłowy.")
        if isinstance(timestamp, (int, float)):
            try:
                numeric_timestamp = float(timestamp)
                if not isfinite(numeric_timestamp):
                    raise ValueError("Timestamp musi być liczbą skończoną.")
                return datetime.fromtimestamp(numeric_timestamp, UTC)
            except (OverflowError, OSError, ValueError) as error:
                raise ValueError(
                    "Timestamp jest poza obsługiwanym zakresem."
                ) from error
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(
                    timestamp.replace("Z", "+00:00")
                )
            except ValueError as error:
                raise ValueError("Nie można odczytać czasu świecy.") from error
        if not isinstance(timestamp, datetime):
            raise ValueError(
                "Świeca musi zawierać timestamp_utc, datetime, open_time lub timestamp."
            )
        if timestamp.tzinfo is not None and timestamp.utcoffset() is not None:
            return timestamp.astimezone(UTC)

        offset_minutes = candle.get("utc_offset_minutes")
        if offset_minutes is not None:
            try:
                offset_value = float(offset_minutes)
            except (TypeError, ValueError, OverflowError) as error:
                raise ValueError("Nieprawidłowy utc_offset_minutes.") from error
            if (
                not isfinite(offset_value)
                or not offset_value.is_integer()
                or not -840 <= offset_value <= 840
            ):
                raise ValueError("Nieprawidłowy utc_offset_minutes.")
            local = timestamp.replace(
                tzinfo=timezone(timedelta(minutes=int(offset_value)))
            )
            return local.astimezone(UTC)

        if timestamp_key == "timestamp_utc":
            raise ValueError("timestamp_utc musi zawierać informację o strefie.")

        first_occurrence = timestamp.replace(tzinfo=WARSAW_TIMEZONE, fold=0)
        second_occurrence = timestamp.replace(tzinfo=WARSAW_TIMEZONE, fold=1)
        first_round_trip = (
            first_occurrence.astimezone(UTC)
            .astimezone(WARSAW_TIMEZONE)
            .replace(tzinfo=None)
        )
        second_round_trip = (
            second_occurrence.astimezone(UTC)
            .astimezone(WARSAW_TIMEZONE)
            .replace(tzinfo=None)
        )
        if first_round_trip != timestamp and second_round_trip != timestamp:
            raise ValueError("Lokalny czas świecy nie istnieje w Europe/Warsaw.")
        if first_occurrence.utcoffset() != second_occurrence.utcoffset():
            raise ValueError(
                "Lokalny czas jest niejednoznaczny; wymagany jest utc_offset_minutes."
            )
        return first_occurrence.astimezone(UTC)
