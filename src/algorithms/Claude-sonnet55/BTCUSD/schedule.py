"""Backend: harmonogram rynku BTC/USD oraz konwersje stref czasowych."""

from datetime import date, datetime, time, timedelta, timezone

try:
    from . import config
except ImportError:
    import config


def as_utc(value: datetime) -> datetime:
    """Zamienia świadomy datetime na UTC; czas bez strefy zgłasza ValueError."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Czas musi zawierać informację o strefie.")
    return value.astimezone(timezone.utc)


def is_trading_minute(value: datetime) -> bool:
    """Zwraca True dla każdej minuty, ponieważ BTC/USD działa przez całą dobę."""
    as_utc(value)
    return True


def expected_candle_opens(start: datetime, end: datetime) -> list[datetime]:
    """Zwraca wszystkie oczekiwane otwarcia M1 w przedziale [start, end)."""
    first, last = as_utc(start), as_utc(end)
    if last < first:
        raise ValueError("Koniec przedziału poprzedza początek.")
    cursor = first.replace(second=0, microsecond=0)
    if cursor < first:
        cursor += config.CANDLE_DURATION
    result = []
    while cursor < last:
        result.append(cursor)
        cursor += config.CANDLE_DURATION
    return result


def segment_id(value: datetime) -> str:
    """Zwraca stały identyfikator rynku; luki danych tworzą segmenty osobno."""
    as_utc(value)
    return "BTCUSD-24/7"


def session_name(value: datetime) -> str:
    """Klasyfikuje sesję krypto według UTC, bez wpływu czasu letniego."""
    clock = as_utc(value).time()
    for name, start, end in config.UTC_SESSION_RANGES:
        if clock >= start and (end is None or clock < end):
            return name
    raise ValueError("Godzina UTC nie należy do żadnej sesji.")


def us_cash_open(day: date) -> datetime:
    """Zwraca otwarcie rynku kasowego USA jako czas UTC dla daty nowojorskiej."""
    if not isinstance(day, date):
        raise ValueError("Data sesji USA musi być obiektem date.")
    return datetime.combine(day, config.US_CASH_OPEN, config.NEW_YORK).astimezone(
        timezone.utc
    )


def event_time_utc(day: date, clock: time) -> datetime:
    """Przelicza lokalny termin zdarzenia USA na UTC, uwzględniając DST."""
    return datetime.combine(day, clock, config.NEW_YORK).astimezone(timezone.utc)


def is_weekend(value: datetime) -> bool:
    """Sprawdza weekend według dnia UTC bez wyłączania handlu."""
    return as_utc(value).weekday() >= 5
