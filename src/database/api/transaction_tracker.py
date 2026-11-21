"""Aktualizuje stan hipotetycznych transakcji na podstawie zamkniętych świec M1."""

# Backend
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any, Protocol, Sequence

from signal_potential_calculator import SignalPotentialCalculator

BALANCE_QUANTUM = Decimal("0.0000000001")


class CandleLike(Protocol):
    """Minimalne dane świecy wymagane do śledzenia pozycji."""

    symbol: str
    datetime: datetime
    high: Decimal
    low: Decimal


@dataclass(frozen=True)
class TransactionCandle:
    """Przechowuje zakres cen i czas otwarcia jednej świecy M1."""

    symbol: str
    datetime: datetime
    high: Decimal
    low: Decimal


class TransactionTracker:
    """Otwiera oczekujące pozycje i zamyka je po osiągnięciu SL albo TP."""

    def process_candles(
        self, cursor: Any, candles: Sequence[CandleLike]
    ) -> None:
        """Aktualizuje transakcje dla zapisanych świec w kolejności chronologicznej.

        Args:
            cursor: Kursor MySQL należący do bieżącej transakcji bazy.
            candles: Świece M1 zapisane przez bieżące żądanie.

        Raises:
            ValueError: Gdy świeca lub zapisane parametry transakcji są błędne.
            mysql.connector.Error: Gdy zapytanie lub aktualizacja bazy nie powiedzie się.
        """
        candles_by_symbol: dict[str, list[TransactionCandle]] = {}
        for candle in candles:
            normalized = TransactionCandle(
                symbol=candle.symbol,
                datetime=candle.datetime.astimezone(timezone.utc).replace(
                    tzinfo=None
                ),
                high=Decimal(str(candle.high)),
                low=Decimal(str(candle.low)),
            )
            candles_by_symbol.setdefault(normalized.symbol, []).append(normalized)
        self._process_candles_by_symbol(cursor, candles_by_symbol)

    def process_historical_candles(
        self,
        cursor: Any,
        symbol: str,
        signal_close_utc: datetime,
    ) -> None:
        """Odtwarza świece zapisane po sygnale, zanim powstały rekordy transakcji.

        Args:
            cursor: Kursor MySQL należący do bieżącej transakcji bazy.
            symbol: Symbol instrumentu z sygnału.
            signal_close_utc: Czas zamknięcia świecy sygnałowej ze strefą UTC.

        Raises:
            ValueError: Gdy czas sygnału nie zawiera strefy czasowej.
            mysql.connector.Error: Gdy odczyt świec z bazy nie powiedzie się.
        """
        if (
            signal_close_utc.tzinfo is None
            or signal_close_utc.utcoffset() is None
        ):
            raise ValueError("Czas sygnału musi zawierać strefę czasową.")
        cutoff = signal_close_utc.astimezone(timezone.utc).replace(tzinfo=None)
        candles = self._fetch_historical_candles(cursor, symbol, cutoff)
        self._process_candles_by_symbol(cursor, {symbol: candles})

    @staticmethod
    def _fetch_historical_candles(
        cursor: Any,
        symbol: str,
        cutoff_utc: datetime,
    ) -> list[TransactionCandle]:
        """Pobiera świece instrumentu od wskazanego czasu UTC w kolejności."""
        cursor.execute(
            """
            SELECT datetime, utc_offset_minutes, high, low
            FROM candles
            WHERE symbol = %s
                AND DATE_SUB(datetime, INTERVAL utc_offset_minutes MINUTE) >= %s
            ORDER BY DATE_SUB(datetime, INTERVAL utc_offset_minutes MINUTE)
            """,
            (symbol, cutoff_utc),
        )
        return [
            TransactionCandle(
                symbol=symbol,
                datetime=local_datetime.replace(
                    tzinfo=timezone(timedelta(minutes=int(offset_minutes)))
                ).astimezone(timezone.utc),
                high=Decimal(str(high)),
                low=Decimal(str(low)),
            )
            for local_datetime, offset_minutes, high, low in cursor.fetchall()
        ]

    def recalculate_transactions(
        self,
        cursor: Any,
        repaired_candles: Sequence[CandleLike],
    ) -> None:
        """Ponownie przetwarza transakcje, na które wpłynęła uzupełniona luka.

        Args:
            cursor: Kursor MySQL należący do bieżącej transakcji bazy.
            repaired_candles: Świece, które wcześniej były oznaczone jako brakujące.

        Raises:
            ValueError: Gdy zapisane parametry transakcji są błędne.
            mysql.connector.Error: Gdy odczyt lub aktualizacja bazy nie powiedzie się.
        """
        repaired_by_symbol: dict[str, set[datetime]] = {}
        for candle in repaired_candles:
            candle_open = candle.datetime.astimezone(timezone.utc).replace(
                tzinfo=None
            )
            repaired_by_symbol.setdefault(candle.symbol, set()).add(candle_open)

        for symbol, repaired_times in repaired_by_symbol.items():
            if not repaired_times:
                continue
            first_repaired_time = min(repaired_times)
            last_repaired_time = max(repaired_times)
            cursor.execute(
                """
                SELECT t.id, s.product, t.signal_close_utc,
                       t.ai_decision_status, t.open_price,
                       t.stop_loss_price, t.take_profit_price, t.true_spread,
                       t.transaction_status, t.closed_at_utc, s.price
                FROM `transactions` AS t
                JOIN signals AS s ON s.id = t.signal_id
                WHERE s.product = %s
                    AND t.transaction_status IN (
                        'awaiting', 'in progress', 'closed'
                    )
                    AND t.signal_close_utc <= %s
                    AND (
                        t.transaction_status != 'closed'
                        OR t.closed_at_utc > %s
                    )
                ORDER BY t.id
                FOR UPDATE
                """,
                (symbol, last_repaired_time, first_repaired_time),
            )
            affected_transactions = []
            for transaction in cursor.fetchall():
                signal_close = _as_utc_naive(transaction[2])
                transaction_status = transaction[8]
                closed_at = (
                    _as_utc_naive(transaction[9])
                    if transaction[9] is not None
                    else None
                )
                if any(
                    signal_close <= repaired_time
                    and (
                        transaction_status != "closed"
                        or (closed_at is not None and repaired_time < closed_at)
                    )
                    for repaired_time in repaired_times
                ):
                    affected_transactions.append(transaction)

            if not affected_transactions:
                continue

            earliest_signal_close = min(
                _as_utc_naive(transaction[2])
                for transaction in affected_transactions
            )
            historical_candles = self._fetch_historical_candles(
                cursor, symbol, earliest_signal_close
            )
            for transaction in affected_transactions:
                (
                    transaction_id,
                    transaction_symbol,
                    signal_close_value,
                    direction,
                    open_price_value,
                    stop_loss_value,
                    take_profit_value,
                    spread_value,
                    previous_status,
                    _closed_at,
                    signal_price_value,
                ) = transaction
                signal_close = _as_utc_naive(signal_close_value)
                opened_immediately = (
                    Decimal(str(open_price_value))
                    == Decimal(str(signal_price_value))
                )
                initial_status = (
                    "in progress" if opened_immediately else "awaiting"
                )
                initial_time_to_open = 0 if opened_immediately else None
                initial_opened_at = signal_close if opened_immediately else None
                cursor.execute(
                    """
                    UPDATE `transactions`
                    SET transaction_status = %s,
                        time_to_open = %s,
                        time_from_open_to_close = NULL,
                        transaction_saldo = NULL,
                        transaction_100zl_deal = NULL,
                        opened_at_utc = %s,
                        closed_at_utc = NULL
                    WHERE id = %s AND transaction_status = %s
                    """,
                    (
                        initial_status,
                        initial_time_to_open,
                        initial_opened_at,
                        transaction_id,
                        previous_status,
                    ),
                )
                self._process_transaction(
                    cursor,
                    (
                        transaction_id,
                        transaction_symbol,
                        signal_close_value,
                        direction,
                        open_price_value,
                        stop_loss_value,
                        take_profit_value,
                        spread_value,
                        initial_status,
                        initial_opened_at,
                        signal_price_value,
                    ),
                    historical_candles,
                )

    def _process_candles_by_symbol(
        self,
        cursor: Any,
        candles_by_symbol: dict[str, list[TransactionCandle]],
    ) -> None:
        """Przetwarza uporządkowane świece dla aktualnie aktywnych transakcji."""
        if not candles_by_symbol:
            return
        symbols = sorted(candles_by_symbol)
        placeholders = ", ".join(["%s"] * len(symbols))
        cursor.execute(
            f"""
            SELECT t.id, s.product, t.signal_close_utc, t.ai_decision_status,
                   t.open_price, t.stop_loss_price, t.take_profit_price,
                   t.true_spread, t.transaction_status, t.opened_at_utc,
                   s.price
            FROM `transactions` AS t
            JOIN signals AS s ON s.id = t.signal_id
            WHERE s.product IN ({placeholders})
                AND t.transaction_status IN ('awaiting', 'in progress')
            ORDER BY t.id
            """,
            tuple(symbols),
        )
        transactions = cursor.fetchall()
        for transaction in transactions:
            self._process_transaction(
                cursor,
                transaction,
                sorted(
                    candles_by_symbol.get(transaction[1], []),
                    key=lambda candle: candle.datetime,
                ),
            )

    def _process_transaction(
        self,
        cursor: Any,
        transaction: tuple[Any, ...],
        candles: Sequence[TransactionCandle],
    ) -> None:
        """Przetwarza transakcję od pierwszej świecy po sygnale."""
        (
            transaction_id,
            symbol,
            signal_close_utc,
            direction,
            open_price_value,
            stop_loss_value,
            take_profit_value,
            spread_value,
            transaction_status,
            opened_at_utc,
            signal_price_value,
        ) = transaction
        signal_close = _as_utc_naive(signal_close_utc)
        open_price = Decimal(str(open_price_value))
        signal_price = Decimal(str(signal_price_value))
        stop_loss = Decimal(str(stop_loss_value))
        take_profit = Decimal(str(take_profit_value))
        spread = Decimal(str(spread_value))
        opened_at = (
            _as_utc_naive(opened_at_utc) if opened_at_utc is not None else None
        )
        status = transaction_status

        for candle in candles:
            candle_open = _as_utc_naive(candle.datetime)
            if candle_open < signal_close:
                continue
            candle_close = candle_open + timedelta(minutes=1)
            opened_on_current_candle = False

            if status == "awaiting":
                if open_price == signal_price:
                    cursor.execute(
                        """
                        UPDATE `transactions`
                        SET transaction_status = 'in progress',
                            time_to_open = 0,
                            opened_at_utc = %s
                        WHERE id = %s AND transaction_status = 'awaiting'
                        """,
                        (signal_close, transaction_id),
                    )
                    status = "in progress"
                    opened_at = signal_close
                else:
                    if not _entry_level_reached(
                        direction, candle, open_price, signal_price
                    ):
                        continue
                    time_to_open = self._count_open_delay_minutes(
                        cursor,
                        symbol,
                        signal_close,
                        candle_open,
                    )
                    cursor.execute(
                        """
                        UPDATE `transactions`
                        SET transaction_status = 'in progress',
                            time_to_open = %s,
                            opened_at_utc = %s
                        WHERE id = %s AND transaction_status = 'awaiting'
                        """,
                        (time_to_open, candle_close, transaction_id),
                    )
                    status = "in progress"
                    opened_at = candle_close
                    opened_on_current_candle = True

            if status != "in progress":
                continue
            if opened_at is None:
                raise ValueError(
                    f"Transakcja {transaction_id} nie ma czasu otwarcia."
                )
            if not opened_on_current_candle and candle_open < opened_at:
                continue

            stop_hit, take_profit_hit = _exit_levels_touched(
                direction, candle, stop_loss, take_profit
            )
            if not stop_hit and not take_profit_hit:
                continue

            exit_price = stop_loss if stop_hit else take_profit
            saldo = _calculate_balance(
                direction, open_price, exit_price, spread
            )
            deal_value = (
                SignalPotentialCalculator.DEPOSIT_PLN
                * SignalPotentialCalculator.LEVERAGE
                * saldo
                / open_price
            ).quantize(
                SignalPotentialCalculator.BALANCE_DECIMAL_PLACES,
                rounding=ROUND_DOWN,
            )
            elapsed_candles = (
                0
                if opened_on_current_candle
                else self._count_transaction_candles(
                    cursor,
                    symbol,
                    opened_at,
                    candle_open,
                )
            )
            cursor.execute(
                """
                UPDATE `transactions`
                SET transaction_status = 'closed',
                    time_from_open_to_close = %s,
                    transaction_saldo = %s,
                    transaction_100zl_deal = %s,
                    closed_at_utc = %s
                WHERE id = %s AND transaction_status = 'in progress'
                """,
                (
                    elapsed_candles,
                    saldo,
                    deal_value,
                    candle_close,
                    transaction_id,
                ),
            )
            break

    @staticmethod
    def _count_open_delay_minutes(
        cursor: Any,
        symbol: str,
        signal_close_utc: datetime,
        entry_candle_open_utc: datetime,
    ) -> int:
        """Zwraca numer świecy M1, która osiągnęła cenę wejścia."""
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM candles
            WHERE symbol = %s
                AND DATE_SUB(datetime, INTERVAL utc_offset_minutes MINUTE)
                    >= %s
                AND DATE_SUB(datetime, INTERVAL utc_offset_minutes MINUTE)
                    <= %s
            """,
            (symbol, signal_close_utc, entry_candle_open_utc),
        )
        row = cursor.fetchone()
        if row is None or row[0] < 1:
            raise ValueError(
                "Nie znaleziono świecy wejścia w zapisanej historii M1."
            )
        return int(row[0])

    @staticmethod
    def _count_transaction_candles(
        cursor: Any,
        symbol: str,
        opened_at_utc: datetime,
        exit_candle_open_utc: datetime,
    ) -> int:
        """Liczy świece od otwarcia do świecy wyjścia, niezależnie od przerw."""
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM candles
            WHERE symbol = %s
                AND DATE_SUB(datetime, INTERVAL utc_offset_minutes MINUTE)
                    >= %s
                AND DATE_SUB(datetime, INTERVAL utc_offset_minutes MINUTE)
                    <= %s
            """,
            (symbol, opened_at_utc, exit_candle_open_utc),
        )
        row = cursor.fetchone()
        if row is None or row[0] < 1:
            raise ValueError(
                "Nie znaleziono świec M1 potrzebnych do obliczenia czasu transakcji."
            )
        return int(row[0])


def _as_utc_naive(value: datetime) -> datetime:
    """Normalizuje czas UTC do postaci naiwnej używanej przez kolumnę DATETIME."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _exit_levels_touched(
    direction: str,
    candle: TransactionCandle,
    stop_loss: Decimal,
    take_profit: Decimal,
) -> tuple[bool, bool]:
    """Sprawdza SL i TP, zwracając oba trafienia do konserwatywnego rozstrzygnięcia."""
    if direction == "long":
        return candle.low <= stop_loss, candle.high >= take_profit
    if direction == "short":
        return candle.high >= stop_loss, candle.low <= take_profit
    raise ValueError("Kierunek transakcji musi mieć wartość long albo short.")


def _entry_level_reached(
    direction: str,
    candle: TransactionCandle,
    entry_price: Decimal,
    signal_price: Decimal,
) -> bool:
    """Sprawdza ruch ceny do ENTRY względem zamknięcia świecy sygnałowej."""
    if direction not in ("long", "short"):
        raise ValueError("Kierunek transakcji musi mieć wartość long albo short.")
    if entry_price > signal_price:
        return candle.high >= entry_price
    if entry_price < signal_price:
        return candle.low <= entry_price
    return False


def _calculate_balance(
    direction: str,
    open_price: Decimal,
    exit_price: Decimal,
    spread: Decimal,
) -> Decimal:
    """Liczy wynik po odjęciu spreadu dla zamkniętej pozycji."""
    if direction == "long":
        balance = exit_price - open_price - spread
    elif direction == "short":
        balance = open_price - exit_price - spread
    else:
        raise ValueError("Kierunek transakcji musi mieć wartość long albo short.")
    return balance.quantize(BALANCE_QUANTUM, rounding=ROUND_DOWN)
