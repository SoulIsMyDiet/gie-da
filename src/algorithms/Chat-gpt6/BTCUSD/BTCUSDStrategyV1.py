"""Strategia trendowa BTC/USD na zamkniętych świecach M1 i M5."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

_SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if _SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, _SCRIPT_DIRECTORY)

if __package__:
    from .config import (
        ATR_PERIOD,
        CANDLE_DURATION,
        COOLDOWN_MINUTES,
        DATABASE_SYMBOL,
        EMA_FAST_PERIOD,
        EMA_SLOW_PERIOD,
        HISTORY_LIMIT,
        INSTRUMENT,
        MACD_FAST_PERIOD,
        MACD_SIGNAL_PERIOD,
        MACD_SLOW_PERIOD,
        MAX_MISSING_MINUTES_WITHOUT_RESET,
        MAX_SIGNAL_HOLDING_MINUTES,
        M5_EMA_FAST_PERIOD,
        M5_EMA_SLOW_PERIOD,
        M5_MAXIMUM_AGE_MINUTES,
        MINIMUM_M1_CANDLES,
        MINIMUM_M5_CANDLES,
        RSI_PERIOD,
        STRATEGY_NAME,
        VOLUME_SMA_PERIOD,
    )
    from .indicators import (
        aggregate_complete_m5,
        atr_wilder_series,
        ema_series,
        macd_series,
        rsi_wilder_series,
        volume_average,
    )
else:
    from config import (  # type: ignore[no-redef]
        ATR_PERIOD,
        CANDLE_DURATION,
        COOLDOWN_MINUTES,
        DATABASE_SYMBOL,
        EMA_FAST_PERIOD,
        EMA_SLOW_PERIOD,
        HISTORY_LIMIT,
        INSTRUMENT,
        MACD_FAST_PERIOD,
        MACD_SIGNAL_PERIOD,
        MACD_SLOW_PERIOD,
        MAX_MISSING_MINUTES_WITHOUT_RESET,
        MAX_SIGNAL_HOLDING_MINUTES,
        M5_EMA_FAST_PERIOD,
        M5_EMA_SLOW_PERIOD,
        M5_MAXIMUM_AGE_MINUTES,
        MINIMUM_M1_CANDLES,
        MINIMUM_M5_CANDLES,
        RSI_PERIOD,
        STRATEGY_NAME,
        VOLUME_SMA_PERIOD,
    )
    from indicators import (  # type: ignore[no-redef]
        aggregate_complete_m5,
        atr_wilder_series,
        ema_series,
        macd_series,
        rsi_wilder_series,
        volume_average,
    )

logger = logging.getLogger(__name__)
WARSAW_TIMEZONE = ZoneInfo("Europe/Warsaw")
UTC = timezone.utc
Candle = dict[str, Any]
CandleInput = Sequence[Mapping[str, object]]

STRATEGY_CONTEXT = (
    "Strategia BTCUSD M1 dla mikrotransakcji o horyzoncie do 20 minut. "
    "Analizuje wyłącznie zamknięte świece M1, a trend potwierdza ostatnią "
    "kompletną i nieprzyszłą świecą M5. LONG wymaga trendu M5 EMA20>EMA50, "
    "M1 EMA9>EMA21, korekty do EMA9 lub EMA21, zamknięcia nad EMA9, "
    "rosnącego RSI14 powyżej 50, dodatniego i rosnącego histogramu MACD "
    "oraz zakresu świecy nie większego niż 2*ATR14. SHORT stosuje warunki "
    "lustrzane. RSI i MACD są liczone na M1 metodą Wildera/EMA, a wolumen "
    "jest tylko informacyjny. Strategia nie ma dostępu do spreadu ani BID/ASK; "
    "Close nie jest gwarantowaną ceną wykonania. Sygnał jest kandydatem do "
    "oceny, nie gwarancją zysku ani zleceniem transakcyjnym. Dwie kolejne "
    "brakujące minuty resetują wskaźniki i rearm; pojedyncza luka nie tworzy "
    "sztucznej świecy ani nie resetuje wskaźników."
)


class BTCUSDStrategyV1:
    """Ocenia nową zamkniętą świecę BTC/USD bez wykonywania transakcji."""

    def evaluate(
        self,
        candles: CandleInput,
        now: datetime | None = None,
        last_signal_timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Liczy wskaźniki, warunki kierunku i zwraca wynik dla API.

        Args:
            candles: Świece OHLCV zwrócone przez istniejące repozytorium.
            now: Bieżący czas ze strefą; domyślnie aktualny czas UTC.
            last_signal_timestamp: Opcjonalny czas otwarcia poprzedniego sygnału
                kierunkowego, podany jako sekundy Unix.

        Returns:
            Pełny wynik analizy i krótki JSON do kolumny signal_parameters.

        Raises:
            ValueError: Gdy czas, historia lub wartości OHLC są nieprawidłowe.
        """
        current_time = now or datetime.now(UTC)
        if current_time.tzinfo is None or current_time.utcoffset() is None:
            raise ValueError("Bieżący czas musi zawierać strefę czasową.")
        current_time = current_time.astimezone(UTC)
        ordered, duplicate_count = self._normalize_and_order(candles)
        closed = [
            candle
            for candle in ordered
            if candle["timestamp_utc"] + CANDLE_DURATION <= current_time
        ]
        if not closed:
            raise ValueError("Brak zamkniętych świec M1 do analizy.")

        significant_gap_seen = False
        segment_start = 0
        for index in range(1, len(closed)):
            missing_count = self._missing_minutes(
                closed[index - 1]["timestamp_utc"],
                closed[index]["timestamp_utc"],
            )
            if missing_count > MAX_MISSING_MINUTES_WITHOUT_RESET:
                segment_start = index
                significant_gap_seen = True
        segment = closed[segment_start:][-HISTORY_LIMIT:]
        current_index = len(segment) - 1
        current = segment[current_index]
        logger.info(
            "%s analizuje BTC/USD %s: %d świec w segmencie, %d duplikatów.",
            STRATEGY_NAME,
            current["timestamp_utc"].isoformat(),
            len(segment),
            duplicate_count,
        )

        closes = [float(candle["close"]) for candle in segment]
        ema9 = ema_series(closes, EMA_FAST_PERIOD)
        ema21 = ema_series(closes, EMA_SLOW_PERIOD)
        rsi14 = rsi_wilder_series(closes, RSI_PERIOD)
        atr14 = atr_wilder_series(segment, ATR_PERIOD)
        macd_line, macd_signal, macd_histogram = macd_series(
            closes,
            MACD_FAST_PERIOD,
            MACD_SLOW_PERIOD,
            MACD_SIGNAL_PERIOD,
        )
        m5_candles = aggregate_complete_m5(segment)
        m5_closes = [float(candle["close"]) for candle in m5_candles]
        ema20_m5 = ema_series(m5_closes, M5_EMA_FAST_PERIOD)
        ema50_m5 = ema_series(m5_closes, M5_EMA_SLOW_PERIOD)
        m5_by_m1 = self._latest_m5_indices(segment, m5_candles)
        volume_sma20, volume_ratio = volume_average(
            segment, current_index, VOLUME_SMA_PERIOD
        )

        current_close_time = current["timestamp_utc"] + CANDLE_DURATION
        current_m5_index = m5_by_m1[current_index]
        m5_age_minutes: float | None = None
        if current_m5_index is not None:
            m5_close_time = m5_candles[current_m5_index]["close_time_utc"]
            m5_age_minutes = max(
                0.0,
                (current_close_time - m5_close_time).total_seconds() / 60.0,
            )

        current_indicators = {
            "ema9": ema9[current_index],
            "ema21": ema21[current_index],
            "rsi14": rsi14[current_index],
            "atr14": atr14[current_index],
            "macd_line": macd_line[current_index],
            "macd_signal": macd_signal[current_index],
            "macd_histogram": macd_histogram[current_index],
            "ema20_m5": (
                ema20_m5[current_m5_index]
                if current_m5_index is not None
                else None
            ),
            "ema50_m5": (
                ema50_m5[current_m5_index]
                if current_m5_index is not None
                else None
            ),
            "volume_sma20": volume_sma20,
            "volume_ratio": volume_ratio,
        }
        warmup_ready = (
            len(segment) >= MINIMUM_M1_CANDLES
            and len(m5_candles) >= MINIMUM_M5_CANDLES
            and all(
                current_indicators[name] is not None
                for name in (
                    "ema9",
                    "ema21",
                    "rsi14",
                    "atr14",
                    "macd_line",
                    "macd_signal",
                    "macd_histogram",
                    "ema20_m5",
                    "ema50_m5",
                )
            )
        )
        m5_is_stale = (
            m5_age_minutes is None
            or m5_age_minutes > M5_MAXIMUM_AGE_MINUTES
        )

        long_current = warmup_ready and self._direction_conditions(
            segment,
            current_index,
            "LONG",
            ema9,
            ema21,
            rsi14,
            atr14,
            macd_histogram,
            m5_by_m1,
            ema20_m5,
            ema50_m5,
        )
        short_current = warmup_ready and self._direction_conditions(
            segment,
            current_index,
            "SHORT",
            ema9,
            ema21,
            rsi14,
            atr14,
            macd_histogram,
            m5_by_m1,
            ema20_m5,
            ema50_m5,
        )
        previous_index = current_index - 1
        long_previous = previous_index >= 0 and self._direction_conditions(
            segment,
            previous_index,
            "LONG",
            ema9,
            ema21,
            rsi14,
            atr14,
            macd_histogram,
            m5_by_m1,
            ema20_m5,
            ema50_m5,
        )
        short_previous = previous_index >= 0 and self._direction_conditions(
            segment,
            previous_index,
            "SHORT",
            ema9,
            ema21,
            rsi14,
            atr14,
            macd_histogram,
            m5_by_m1,
            ema20_m5,
            ema50_m5,
        )

        if not warmup_ready:
            signal = "NONE"
            reason = (
                f"Niewystarczający warm-up: {len(segment)} świec M1 i "
                f"{len(m5_candles)} kompletnych świec M5; wymagane po "
                "100 każdego interwału."
            )
        elif m5_is_stale:
            signal = "NONE"
            reason = (
                "Ostatnia kompletna świeca M5 jest starsza niż 5 minut "
                "względem ocenianej świecy M1."
            )
        elif long_current and not long_previous:
            signal = "LONG"
            reason = "Trend M5 wzrostowy i powrót momentum po korekcie M1."
        elif short_current and not short_previous:
            signal = "SHORT"
            reason = "Trend M5 spadkowy i powrót momentum po korekcie M1."
        elif long_current or short_current:
            signal = "NONE"
            reason = "Warunki pozostały aktywne; rearm blokuje powtórny sygnał."
        else:
            signal = "NONE"
            reason = (
                "Istotna luka wymaga ponownego warm-up."
                if significant_gap_seen and len(segment) < MINIMUM_M1_CANDLES
                else "Warunki LONG ani SHORT nie zostały spełnione."
            )

        if signal in {"LONG", "SHORT"} and last_signal_timestamp is not None:
            seconds_since_last_signal = (
                current["timestamp_utc"].timestamp() - last_signal_timestamp
            )
            if 0 <= seconds_since_last_signal < COOLDOWN_MINUTES * 60:
                signal = "NONE"
                reason = (
                    f"Sygnał wstrzymano na {COOLDOWN_MINUTES} min po "
                    "poprzednim sygnale kierunkowym."
                )

        open_time = current["timestamp_utc"]
        close_time = open_time + CANDLE_DURATION
        close_epoch = int(close_time.timestamp())
        payload = {
            "price": float(current["close"]),
            "signal": signal,
            "timestamp": close_epoch,
            "instrument": INSTRUMENT,
            "reason": reason,
            "ema9_1m": self._finite_number(ema9[current_index]),
            "ema21_1m": self._finite_number(ema21[current_index]),
            "ema20_5m": self._finite_number(current_indicators["ema20_m5"]),
            "ema50_5m": self._finite_number(current_indicators["ema50_m5"]),
            "rsi14_1m": self._finite_number(rsi14[current_index]),
            "atr14_1m": self._finite_number(atr14[current_index]),
            "macd_line_1m": self._finite_number(macd_line[current_index]),
            "macd_signal_1m": self._finite_number(macd_signal[current_index]),
            "macd_histogram_1m": self._finite_number(
                macd_histogram[current_index]
            ),
            "volume_sma20_1m": self._finite_number(volume_sma20),
            "volume_ratio_1m": self._finite_number(volume_ratio),
        }
        logger.info("%s BTC/USD: %s — %s", STRATEGY_NAME, signal, reason)
        return {
            "instrument": INSTRUMENT,
            "database_symbol": DATABASE_SYMBOL,
            "price": float(current["close"]),
            "signal": signal,
            "reason": reason,
            "timestamp": close_time.astimezone(WARSAW_TIMEZONE).isoformat(),
            "candle_open_time": open_time.astimezone(WARSAW_TIMEZONE).isoformat(),
            "candle_open_epoch": int(open_time.timestamp()),
            "candle_close_time": close_time.astimezone(
                WARSAW_TIMEZONE
            ).isoformat(),
            "signal_parameters": payload,
            "indicators": {
                key: value
                for key, value in payload.items()
                if key.endswith("_1m") or key.endswith("_5m")
            },
            "data_quality": {
                "m1_segment_candles": len(segment),
                "complete_m5_candles": len(m5_candles),
                "m5_age_minutes": self._finite_number(m5_age_minutes),
                "duplicate_candles_discarded": duplicate_count,
                "significant_gap_before_segment": significant_gap_seen,
                "warmup_ready": warmup_ready,
            },
            "max_holding_minutes": MAX_SIGNAL_HOLDING_MINUTES,
        }

    def generate_signal(
        self,
        candles: CandleInput,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Zwraca wyłącznie kompaktowy JSON wyniku dla signal_parameters."""
        return self.evaluate(candles, now=now)["signal_parameters"]

    @classmethod
    def _normalize_and_order(
        cls, candles: CandleInput
    ) -> tuple[list[Candle], int]:
        """Waliduje OHLC, normalizuje czas i usuwa powtórzone timestampy."""
        if not candles:
            raise ValueError("Brak historii świec BTC/USD.")
        normalized: dict[datetime, Candle] = {}
        duplicate_count = 0
        for source in candles:
            candle = dict(source)
            opened = cls._parse_timestamp(candle)
            if opened.second != 0 or opened.microsecond != 0:
                raise ValueError("Świeca M1 musi zaczynać się w pełnej minucie.")
            prices = tuple(
                cls._required_finite_number(candle, name)
                for name in ("open", "high", "low", "close")
            )
            open_price, high, low, close = prices
            if (
                min(prices) <= 0
                or high < max(open_price, close)
                or low > min(open_price, close)
                or low > high
            ):
                raise ValueError("Świeca zawiera nieprawidłowe wartości OHLC.")
            volume = cls._finite_number(candle.get("volume"))
            if volume is not None and volume < 0:
                volume = None
            if opened in normalized:
                duplicate_count += 1
                continue
            normalized[opened] = {
                "timestamp_utc": opened,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        ordered = [normalized[key] for key in sorted(normalized)]
        if not ordered:
            raise ValueError("Brak prawidłowych świec BTC/USD.")
        if duplicate_count:
            logger.warning("Pominięto %d zduplikowanych świec M1.", duplicate_count)
        return ordered[-HISTORY_LIMIT:], duplicate_count

    @staticmethod
    def _parse_timestamp(candle: Mapping[str, object]) -> datetime:
        """Przelicza timestamp na UTC bez zgadywania niejednoznacznego czasu."""
        raw: object = next(
            (
                candle[key]
                for key in ("timestamp_utc", "datetime", "open_time")
                if candle.get(key) is not None
            ),
            None,
        )
        if isinstance(raw, bool) or raw is None:
            raise ValueError("Świeca nie zawiera czasu otwarcia.")
        if isinstance(raw, (int, float)):
            if not isfinite(float(raw)):
                raise ValueError("Timestamp musi być liczbą skończoną.")
            return datetime.fromtimestamp(float(raw), UTC)
        if isinstance(raw, str):
            try:
                raw = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("Nie można odczytać czasu świecy.") from error
        if not isinstance(raw, datetime):
            raise ValueError("Nieobsługiwany typ czasu świecy.")
        if raw.tzinfo is not None and raw.utcoffset() is not None:
            return raw.astimezone(UTC)

        offset_minutes = candle.get("utc_offset_minutes")
        if offset_minutes is not None:
            try:
                offset = int(offset_minutes)
            except (TypeError, ValueError, OverflowError) as error:
                raise ValueError("Nieprawidłowy utc_offset_minutes.") from error
            if not -840 <= offset <= 840:
                raise ValueError("Nieprawidłowy utc_offset_minutes.")
            return raw.replace(
                tzinfo=timezone(timedelta(minutes=offset))
            ).astimezone(UTC)

        first = raw.replace(tzinfo=WARSAW_TIMEZONE, fold=0)
        second = raw.replace(tzinfo=WARSAW_TIMEZONE, fold=1)
        if first.utcoffset() != second.utcoffset():
            raise ValueError(
                "Niejednoznaczny lub nieistniejący czas Europe/Warsaw "
                "bez utc_offset_minutes."
            )
        if first.astimezone(UTC).astimezone(WARSAW_TIMEZONE).replace(
            tzinfo=None
        ) != raw:
            raise ValueError("Lokalny czas świecy nie istnieje w Europe/Warsaw.")
        return first.astimezone(UTC)

    @staticmethod
    def _required_finite_number(candle: Mapping[str, object], key: str) -> float:
        """Odczytuje wymaganą, skończoną wartość liczbową."""
        try:
            value = float(candle[key])
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"Świeca nie zawiera poprawnego pola {key}.") from error
        if not isfinite(value):
            raise ValueError(f"Pole {key} musi być liczbą skończoną.")
        return value

    @staticmethod
    def _finite_number(value: object) -> float | None:
        """Zwraca liczbę skończoną albo None dla brakujących danych."""
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if isfinite(number) else None

    @staticmethod
    def _missing_minutes(previous: datetime, current: datetime) -> int:
        """Zwraca liczbę nieobecnych minut między otwarciami świec."""
        elapsed_seconds = (current - previous).total_seconds()
        if elapsed_seconds <= 0 or elapsed_seconds % 60 != 0:
            raise ValueError("Timestampy świec muszą rosnąć co pełną minutę.")
        return max(0, int(elapsed_seconds // 60) - 1)

    @staticmethod
    def _latest_m5_indices(
        m1_candles: Sequence[Candle],
        m5_candles: Sequence[dict[str, object]],
    ) -> list[int | None]:
        """Wskazuje ostatnie M5 zamknięte nie później niż każda świeca M1."""
        result: list[int | None] = []
        latest_index: int | None = None
        m5_index = 0
        for candle in m1_candles:
            m1_close = candle["timestamp_utc"] + CANDLE_DURATION
            while (
                m5_index < len(m5_candles)
                and m5_candles[m5_index]["close_time_utc"] <= m1_close
            ):
                latest_index = m5_index
                m5_index += 1
            result.append(latest_index)
        return result

    @staticmethod
    def _direction_conditions(
        candles: Sequence[Candle],
        index: int,
        direction: str,
        ema9: Sequence[float],
        ema21: Sequence[float],
        rsi14: Sequence[float | None],
        atr14: Sequence[float | None],
        macd_histogram: Sequence[float],
        m5_indices: Sequence[int | None],
        ema20_m5: Sequence[float],
        ema50_m5: Sequence[float],
    ) -> bool:
        """Sprawdza wszystkie warunki kierunkowe dla wskazanej świecy M1."""
        previous_index = index - 1
        m5_index = m5_indices[index]
        if previous_index < 0 or m5_index is None:
            return False
        current = candles[index]
        rsi = rsi14[index]
        previous_rsi = rsi14[previous_index]
        atr = atr14[index]
        if rsi is None or previous_rsi is None or atr is None:
            return False
        if float(current["high"]) - float(current["low"]) > 2.0 * atr:
            return False

        pullback_found = False
        for pullback_index in range(max(0, index - 2), index + 1):
            pullback_candle = candles[pullback_index]
            if direction == "LONG":
                pullback_found = pullback_found or (
                    float(pullback_candle["low"]) <= ema9[pullback_index]
                    or float(pullback_candle["low"]) <= ema21[pullback_index]
                )
            else:
                pullback_found = pullback_found or (
                    float(pullback_candle["high"]) >= ema9[pullback_index]
                    or float(pullback_candle["high"]) >= ema21[pullback_index]
                )

        close = float(current["close"])
        if direction == "LONG":
            return (
                ema20_m5[m5_index] > ema50_m5[m5_index]
                and ema9[index] > ema21[index]
                and pullback_found
                and close > ema9[index]
                and rsi > 50.0
                and rsi > previous_rsi
                and macd_histogram[index] > 0.0
                and macd_histogram[index] > macd_histogram[previous_index]
            )
        if direction == "SHORT":
            return (
                ema20_m5[m5_index] < ema50_m5[m5_index]
                and ema9[index] < ema21[index]
                and pullback_found
                and close < ema9[index]
                and rsi < 50.0
                and rsi < previous_rsi
                and macd_histogram[index] < 0.0
                and macd_histogram[index] < macd_histogram[previous_index]
            )
        raise ValueError(f"Nieznany kierunek sygnału: {direction}.")
