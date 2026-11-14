"""Backend
Funkcje harmonogramu i identyfikowania minut handlowych GER30.
"""

from datetime import datetime, time, timedelta, timezone

from .config import (
    NEW_YORK_TIMEZONE,
    SESSION_END,
    SUMMER_SESSION_START,
    WARSAW_TIMEZONE,
    WINTER_SESSION_START,
)


def _as_utc(value: datetime) -> datetime:
    """Sprawdza świadomy znacznik czasu i przelicza go do UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Znacznik czasu musi zawierać strefę czasową.")
    return value.astimezone(timezone.utc)


def is_trading_minute(dt: datetime) -> bool:
    """Określa, czy otwarcie minuty przypada w sesji od poniedziałku do piątku."""
    local = _as_utc(dt).astimezone(WARSAW_TIMEZONE)
    if local.weekday() >= 5:
        return False
    start = SUMMER_SESSION_START if local.dst() else WINTER_SESSION_START
    wall_time = local.timetz().replace(tzinfo=None)
    return start <= wall_time < SESSION_END


def expected_candle_opens(start: datetime, end: datetime) -> list[datetime]:
    """Zwraca wszystkie oczekiwane otwarcia świec M1 w przedziale [start, end)."""
    start_utc, end_utc = _as_utc(start), _as_utc(end)
    if end_utc < start_utc:
        raise ValueError("Koniec przedziału nie może poprzedzać początku.")
    cursor = start_utc.replace(second=0, microsecond=0)
    if cursor < start_utc:
        cursor += timedelta(minutes=1)
    openings = []
    while cursor < end_utc:
        if is_trading_minute(cursor):
            openings.append(cursor)
        cursor += timedelta(minutes=1)
    return openings


def segment_id(dt: datetime) -> tuple[str, str]:
    """Zwraca klucz sesji lokalnej; luki w obrębie sesji rozdziela warstwa danych."""
    local = _as_utc(dt).astimezone(WARSAW_TIMEZONE)
    return local.date().isoformat(), "session"


def trading_openings_before(end_time: datetime, count: int) -> list[datetime]:
    """Zwraca ostatnie oczekiwane otwarcia świec przed wskazanym czasem."""
    if count < 0:
        raise ValueError("Liczba minut handlowych nie może być ujemna.")
    end_utc = _as_utc(end_time).replace(second=0, microsecond=0)
    cursor = end_utc
    result = []
    while len(result) < count:
        if is_trading_minute(cursor):
            result.append(cursor)
        cursor -= timedelta(minutes=1)
    return list(reversed(result))


def session_end_after(candle_open_time: datetime) -> datetime:
    """Zwraca koniec sesji danego dnia w UTC."""
    local = _as_utc(candle_open_time).astimezone(WARSAW_TIMEZONE)
    local_end = datetime.combine(local.date(), SESSION_END, WARSAW_TIMEZONE)
    return local_end.astimezone(timezone.utc)


def new_york_session_time(value: datetime) -> time:
    """Zwraca godzinę nowojorską z uwzględnieniem kalendarza DST USA."""
    return _as_utc(value).astimezone(NEW_YORK_TIMEZONE).timetz().replace(tzinfo=None)
