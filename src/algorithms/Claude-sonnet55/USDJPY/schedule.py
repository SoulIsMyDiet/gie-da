"""Harmonogram Forex oraz lokalne czasy sesji Tokio, Londynu i Nowego Jorku."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import (
    BLACKOUTS,
    DB_SYMBOL,
    EARLY_CLOSE_DATES,
    FOMC_DATES,
    FOMC_TIME_ET,
    FRIDAY_LAST_SIGNAL_PL,
    HIGH_IMPACT_DAYS,
    LONDON_OPEN,
    LONDON_SESSION_END,
    LIQUIDITY_OVERLAP_END_ET,
    LOCAL_TIME_ZONE,
    MARKET_CLOSE_ET,
    NEW_YORK_DATA_1000,
    NEW_YORK_DATA_OPEN,
    NEW_YORK_SESSION_END,
    NEWS_BLACKOUTS,
    NO_TRADE_DATES,
    SIGNAL_ROLLOVER_ET,
    SIGNAL_START_JST,
    TOKYO_FIXING_JST,
    TOKYO_OPEN_JST,
    TOKYO_SESSION_END_JST,
    WEEKEND_OPEN_ET,
)

WARSAW = ZoneInfo(LOCAL_TIME_ZONE)
TOKYO = ZoneInfo("Asia/Tokyo")
LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")
UTC = timezone.utc


def _aware(instant: datetime) -> datetime:
    """Zamienia naiwny czas na czas Warszawy; świadomy czas pozostaje bez zmian."""
    if instant.tzinfo is None or instant.utcoffset() is None:
        return instant.replace(tzinfo=WARSAW)
    return instant


def is_trading_minute(instant: datetime) -> bool:
    """Sprawdza tygodniowe godziny Forex z konfiguracji pobierania C++/API."""
    warsaw = _aware(instant).astimezone(WARSAW)
    if warsaw.date() in NO_TRADE_DATES:
        return False
    new_york = warsaw.astimezone(NEW_YORK)
    market_date = new_york.date()
    if market_date in EARLY_CLOSE_DATES:
        if new_york.time() >= EARLY_CLOSE_DATES[market_date]:
            return False
    weekday = new_york.weekday()
    if weekday < 4:
        return True
    if weekday == 4:
        return new_york.time() < MARKET_CLOSE_ET
    if weekday == 5:
        return False
    return new_york.time() >= WEEKEND_OPEN_ET


def expected_candle_opens(start: datetime, end: datetime) -> list[datetime]:
    """Zwraca otwarcia minut handlowych z przedziału [start, end) jako UTC."""
    start_utc = _aware(start).astimezone(UTC).replace(second=0, microsecond=0)
    end_utc = _aware(end).astimezone(UTC).replace(second=0, microsecond=0)
    result = []
    current = start_utc
    while current < end_utc:
        if is_trading_minute(current):
            result.append(current)
        current += timedelta(minutes=1)
    return result


def segment_id(instant: datetime) -> str:
    """Identyfikuje ciągły tygodniowy segment od niedzielnego otwarcia Forex."""
    new_york = _aware(instant).astimezone(NEW_YORK)
    days_since_sunday = (new_york.weekday() + 1) % 7
    sunday = new_york.date() - timedelta(days=days_since_sunday)
    if new_york.weekday() == 6 and new_york.time() < WEEKEND_OPEN_ET:
        sunday -= timedelta(days=7)
    segment_start = datetime.combine(sunday, WEEKEND_OPEN_ET, tzinfo=NEW_YORK)
    return segment_start.astimezone(UTC).isoformat()


def tokyo_open(day: date) -> datetime:
    """Zwraca otwarcie Tokio o 09:00 JST dla daty japońskiej."""
    return datetime.combine(day, TOKYO_OPEN_JST, tzinfo=TOKYO)


def tokyo_fixing(day: date) -> datetime:
    """Zwraca fixing tokijski o 09:55 JST dla daty japońskiej."""
    return datetime.combine(day, TOKYO_FIXING_JST, tzinfo=TOKYO)


def london_open(day: date) -> datetime:
    """Zwraca otwarcie Londynu o 08:00 czasu lokalnego Londynu."""
    return datetime.combine(day, LONDON_OPEN, tzinfo=LONDON)


def ny_open(day: date) -> datetime:
    """Zwraca początek okna danych USA o 08:30 czasu lokalnego Nowego Jorku."""
    return datetime.combine(day, NEW_YORK_DATA_OPEN, tzinfo=NEW_YORK)


def ny_1000(day: date) -> datetime:
    """Zwraca godzinę 10:00 ET używaną dla drugiego okna danych USA."""
    return datetime.combine(day, NEW_YORK_DATA_1000, tzinfo=NEW_YORK)


def signal_window_open(instant: datetime) -> bool:
    """Sprawdza okno od 09:15 JST do zamknięcia NY, z piątkowym limitem PL."""
    current = _aware(instant).astimezone(UTC)
    tokyo_day = current.astimezone(TOKYO).date()
    for candidate_day in (tokyo_day, tokyo_day - timedelta(days=1)):
        if candidate_day.weekday() >= 5:
            continue
        start = datetime.combine(candidate_day, SIGNAL_START_JST, tzinfo=TOKYO)
        end = datetime.combine(candidate_day, MARKET_CLOSE_ET, tzinfo=NEW_YORK)
        if candidate_day.weekday() == 4:
            friday_limit = datetime.combine(
                candidate_day, FRIDAY_LAST_SIGNAL_PL, tzinfo=WARSAW
            )
            end = min(end, friday_limit.astimezone(NEW_YORK))
        if start.astimezone(UTC) <= current < end.astimezone(UTC):
            return True
    return False


def session_name(instant: datetime) -> str:
    """Klasyfikuje chwilę jako TOKYO, LONDON, OVERLAP, NY albo LATE."""
    current = _aware(instant)
    tokyo = current.astimezone(TOKYO)
    london = current.astimezone(LONDON)
    new_york = current.astimezone(NEW_YORK)
    if (
        LONDON_OPEN <= london.time() < LONDON_SESSION_END
        and NEW_YORK_DATA_OPEN <= new_york.time() < LIQUIDITY_OVERLAP_END_ET
    ):
        return "OVERLAP"
    if TOKYO_OPEN_JST <= tokyo.time() < TOKYO_SESSION_END_JST:
        return "TOKYO"
    if LONDON_OPEN <= london.time() < LONDON_SESSION_END:
        return "LONDON"
    if NEW_YORK_DATA_OPEN <= new_york.time() < NEW_YORK_SESSION_END:
        return "NY"
    return "LATE"


def minutes_since_tokyo_open(instant: datetime) -> int:
    """Zwraca minuty od otwarcia Tokio; przed nim wynik jest ujemny."""
    local = _aware(instant).astimezone(TOKYO)
    start = tokyo_open(local.date())
    return int((local - start).total_seconds() // 60)


def minutes_since_london_open(instant: datetime) -> int:
    """Zwraca minuty od otwarcia Londynu; przed nim wynik jest ujemny."""
    local = _aware(instant).astimezone(LONDON)
    start = london_open(local.date())
    return int((local - start).total_seconds() // 60)


def minutes_since_ny_open(instant: datetime) -> int:
    """Zwraca minuty od otwarcia okna danych USA; przed nim wynik jest ujemny."""
    local = _aware(instant).astimezone(NEW_YORK)
    start = ny_open(local.date())
    return int((local - start).total_seconds() // 60)


def blackout_codes(instant: datetime) -> list[str]:
    """Zwraca kody blokad czasowych oraz ręcznie wpisanych wydarzeń."""
    current = _aware(instant)
    tokyo = current.astimezone(TOKYO)
    london = current.astimezone(LONDON)
    new_york = current.astimezone(NEW_YORK)
    codes = []

    tokyo_start = tokyo_open(tokyo.date())
    tokyo_fix = tokyo_fixing(tokyo.date())
    if tokyo_start <= tokyo < tokyo_start + timedelta(
        minutes=BLACKOUTS["tokyo_open_after_minutes"]
    ):
        codes.append("tokyo_open")
    if -BLACKOUTS["tokyo_fixing_before_minutes"] <= (
        tokyo - tokyo_fix
    ).total_seconds() / 60 <= BLACKOUTS["tokyo_fixing_after_minutes"]:
        codes.append("tokyo_fixing")

    london_start = london_open(london.date())
    if london_start <= london < london_start + timedelta(
        minutes=BLACKOUTS["london_open_duration_minutes"]
    ):
        codes.append("london_open")

    for clock, before, after, code in (
        (
            NEW_YORK_DATA_OPEN,
            BLACKOUTS["us_0830_before_minutes"],
            BLACKOUTS["us_0830_after_minutes"],
            "us_0830",
        ),
        (
            NEW_YORK_DATA_1000,
            BLACKOUTS["us_1000_before_minutes"],
            BLACKOUTS["us_1000_after_minutes"],
            "us_1000",
        ),
    ):
        center = datetime.combine(new_york.date(), clock, tzinfo=NEW_YORK)
        delta = (new_york - center).total_seconds() / 60
        if -before <= delta <= after:
            codes.append(code)

    if new_york.date() in FOMC_DATES:
        fomc_start = datetime.combine(
            new_york.date(), FOMC_TIME_ET, tzinfo=NEW_YORK
        )
        if fomc_start <= new_york < fomc_start + timedelta(
            minutes=BLACKOUTS["fomc_duration_minutes"]
        ):
            codes.append("fomc")
    if SIGNAL_ROLLOVER_ET <= new_york.time() < MARKET_CLOSE_ET:
        codes.append("rollover")
    if tokyo.date() in HIGH_IMPACT_DAYS or new_york.date() in HIGH_IMPACT_DAYS:
        codes.append("high_impact_day")
    for blackout in NEWS_BLACKOUTS:
        if _inside_manual_blackout(current, blackout):
            codes.append(str(blackout.get("code", "news")))
    return list(dict.fromkeys(codes))


def _inside_manual_blackout(
    instant: datetime, blackout: dict[str, object]
) -> bool:
    """Sprawdza ręcznie skonfigurowane okno; błędna konfiguracja zgłasza ValueError."""
    try:
        start = datetime.fromisoformat(str(blackout["start"]))
        end = datetime.fromisoformat(str(blackout["end"]))
        zone = ZoneInfo(str(blackout.get("timezone", "Europe/Warsaw")))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            "NEWS_BLACKOUTS wymaga pól start i end w formacie ISO."
        ) from error
    if start.tzinfo is None:
        start = start.replace(tzinfo=zone)
    if end.tzinfo is None:
        end = end.replace(tzinfo=zone)
    if end <= start:
        raise ValueError("Koniec NEWS_BLACKOUTS musi być późniejszy niż początek.")
    current_utc = _aware(instant).astimezone(UTC)
    return start.astimezone(UTC) <= current_utc < (
        end + timedelta(minutes=BLACKOUTS["news_after_minutes"])
    ).astimezone(UTC)


def is_holiday(instant: datetime) -> bool:
    """Sprawdza, czy warszawska data figuruje na ręcznej liście bez handlu."""
    return _aware(instant).astimezone(WARSAW).date() in NO_TRADE_DATES


def is_early_close(instant: datetime) -> bool:
    """Sprawdza, czy chwila przypada po skonfigurowanym skróconym zamknięciu."""
    local = _aware(instant).astimezone(NEW_YORK)
    close_time = EARLY_CLOSE_DATES.get(local.date())
    return close_time is not None and local.time() >= close_time


def trading_day_start(instant: datetime) -> datetime:
    """Zwraca początek dnia Forex o 17:00 ET jako czas UTC."""
    new_york = _aware(instant).astimezone(NEW_YORK)
    day = new_york.date()
    if new_york.time() < MARKET_CLOSE_ET:
        day -= timedelta(days=1)
    return datetime.combine(day, MARKET_CLOSE_ET, tzinfo=NEW_YORK).astimezone(UTC)


def minutes_until_market_close(instant: datetime) -> float | None:
    """Zwraca minuty do najbliższego zamknięcia tygodnia lub skróconej sesji."""
    new_york = _aware(instant).astimezone(NEW_YORK)
    close = EARLY_CLOSE_DATES.get(new_york.date(), MARKET_CLOSE_ET)
    if (
        new_york.time() < close
        and (new_york.weekday() == 4 or new_york.date() in EARLY_CLOSE_DATES)
    ):
        boundary = datetime.combine(new_york.date(), close, tzinfo=NEW_YORK)
        return (boundary - new_york).total_seconds() / 60
    return None
