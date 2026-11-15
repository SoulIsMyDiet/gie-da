"""Backend: harmonogram dostaw ES i osobne ograniczenia sesji kasowej."""

from datetime import date, datetime, time, timedelta, timezone

import config


def as_utc(value: datetime) -> datetime:
    """Przelicza świadomy czas do UTC; brak strefy zgłasza jako ValueError."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Czas musi zawierać strefę czasową.")
    return value.astimezone(timezone.utc)


def is_trading_minute(value: datetime) -> bool:
    """Sprawdza harmonogram dostaw ES; święta nie modyfikują tego harmonogramu."""
    local = as_utc(value).astimezone(config.NEW_YORK)
    day, clock = local.weekday(), local.time().replace(tzinfo=None)
    if day == 5:
        return False
    if day == 6:
        return clock >= time(17)
    if day == 4:
        return clock < time(17)
    return clock < time(17) or clock >= time(18)


def segment_id(value: datetime) -> datetime:
    """Zwraca początek segmentu handlowego UTC; dla zamkniętego rynku zgłasza ValueError."""
    local = as_utc(value).astimezone(config.NEW_YORK)
    if not is_trading_minute(value):
        raise ValueError("Minuta nie należy do harmonogramu handlu.")
    if local.weekday() == 6:
        start_day, start_time = local.date(), time(17)
    elif local.time().replace(tzinfo=None) >= time(18):
        start_day, start_time = local.date(), time(18)
    else:
        start_day = local.date() - timedelta(days=1)
        start_time = time(17) if local.weekday() == 0 else time(18)
    return datetime.combine(start_day, start_time, config.NEW_YORK).astimezone(timezone.utc)


def cash_open(day: date) -> datetime:
    """Zwraca świadomy czas otwarcia sesji kasowej dla daty ET."""
    return datetime.combine(day, config.CASH_OPEN, config.NEW_YORK)


def cash_close(day: date) -> datetime:
    """Zwraca świadomy czas zamknięcia kasowego, także dla skróconej sesji."""
    return datetime.combine(day, config.EARLY_CASH_CLOSE if day in config.EARLY_CLOSE_DATES else config.CASH_CLOSE, config.NEW_YORK)


def is_signal_window(value: datetime) -> bool:
    """Sprawdza zamknięcie świecy: oba końce okna 09:40–15:50 ET są włączne."""
    local = as_utc(value).astimezone(config.NEW_YORK)
    end = config.EARLY_SIGNAL_END if local.date() in config.EARLY_CLOSE_DATES else config.SIGNAL_END
    return (local.weekday() < 5 and local.date() not in config.NO_TRADE_DATES and
            config.SIGNAL_START <= local.time().replace(tzinfo=None) <= end)


def expected_candle_opens(start: datetime, end: datetime) -> list[datetime]:
    """Zwraca zaplanowane otwarcia w [start, end); błędny zakres zgłasza ValueError."""
    start, end = as_utc(start), as_utc(end)
    if end < start:
        raise ValueError("Koniec przedziału poprzedza początek.")
    cursor = start.replace(second=0, microsecond=0)
    if cursor < start:
        cursor += timedelta(minutes=1)
    result = []
    while cursor < end:
        if is_trading_minute(cursor):
            result.append(cursor)
        cursor += timedelta(minutes=1)
    return result


def previous_trading_opens(end: datetime, count: int) -> list[datetime]:
    """Zwraca ostatnie count zaplanowanych otwarć do end włącznie; ujemna liczba to błąd."""
    if count < 0:
        raise ValueError("Liczba minut nie może być ujemna.")
    cursor = as_utc(end).replace(second=0, microsecond=0)
    result = []
    while len(result) < count:
        if is_trading_minute(cursor):
            result.append(cursor)
        cursor -= timedelta(minutes=1)
    return list(reversed(result))
