"""Kalendarz XAU/USD oparty na centralnym profilu oraz ustalonej nakładce."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    # Backend: kontener i skrypt udostępniają profil bezpośrednio w ścieżce modułów.
    from market_products import is_market_open
except ModuleNotFoundError as error:
    if error.name != "market_products":
        raise
    # Backend: w katalogu repozytorium moduł znajduje się pod przestrzenią src.
    from src.database.api.market_products import is_market_open

from .config import CONFIG

NEW_YORK = ZoneInfo("America/New_York")
WARSAW = ZoneInfo("Europe/Warsaw")
UTC = timezone.utc


def market_open(instant: datetime) -> bool:
    """Zwraca stan rynku w świadomej chwili; nieświadomy czas powoduje ValueError."""
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("Czas rynku musi zawierać strefę czasową.")
    if not is_market_open(CONFIG.market_symbol, instant):
        return False
    local = instant.astimezone(NEW_YORK)
    if local.weekday() < 4 and local.hour == 17:
        return False
    if local.weekday() == 6 and local.hour < 18:
        return False
    return True


def session_start(instant: datetime) -> datetime:
    """Zwraca początek sesji VWAP o 18 ET w UTC; wymaga świadomego czasu."""
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("Czas sesji musi zawierać strefę czasową.")
    local = instant.astimezone(NEW_YORK)
    day = local.date() if local.hour >= 18 else local.date() - timedelta(days=1)
    return datetime(day.year, day.month, day.day, 18, tzinfo=NEW_YORK).astimezone(UTC)


def missing_expected(previous: datetime, current: datetime) -> int:
    """Liczy oczekiwane minuty między otwarciami, pomijając przerwy planowane."""
    cursor = previous + timedelta(minutes=1)
    count = 0
    while cursor < current:
        if market_open(cursor):
            count += 1
        cursor += timedelta(minutes=1)
    return count
