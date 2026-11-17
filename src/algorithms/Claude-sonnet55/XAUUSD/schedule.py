"""Harmonogram rynku złota oraz okna sesji i wydarzeń."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from config import (
    BLACKOUTS,
    COMEX_OPEN,
    DAILY_SESSION_START_ET,
    DB_SYMBOL,
    EARLY_CLOSE_DATES,
    FOMC_DATES,
    HIGH_IMPACT_DAYS,
    LOCAL_TIME_ZONE,
    LONDON_TIME_ZONE,
    NEW_YORK_TIME_ZONE,
    NEWS_BLACKOUTS,
    LONDON_OPEN,
    LONDON_SESSION_END,
    LIQUIDITY_OVERLAP_END,
    LIQUIDITY_OVERLAP_START,
    MARKET_CLOSE_ET,
    NEW_YORK_SESSION_END,
    NEW_YORK_SESSION_START,
    NO_TRADE_DATES,
    SIGNAL_WINDOW_LONDON_START,
    SIGNAL_WINDOW_NEW_YORK_END,
    FRIDAY_LAST_SIGNAL_PL,
    SECONDS_PER_MINUTE,
)

try:
    from market_products import is_market_open
except ModuleNotFoundError as error:
    if error.name != "market_products":
        raise
    from src.database.api.market_products import is_market_open

WARSAW = ZoneInfo(LOCAL_TIME_ZONE)
LONDON = ZoneInfo(LONDON_TIME_ZONE)
NEW_YORK = ZoneInfo(NEW_YORK_TIME_ZONE)
UTC = timezone.utc


def _aware(value: datetime) -> datetime:
    """Zwraca świadomy czas; naiwny czas interpretuje jako czas Warszawy."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=WARSAW)
    return value


def is_trading_minute(instant: datetime) -> bool:
    """Sprawdza, czy minuta jest otwarta według profilu złota i ręcznych przerw."""
    local = _aware(instant).astimezone(WARSAW)
    if local.date() in NO_TRADE_DATES:
        return False
    if not is_market_open(DB_SYMBOL, local):
        return False
    close_time = EARLY_CLOSE_DATES.get(local.astimezone(NEW_YORK).date())
    return close_time is None or local.astimezone(NEW_YORK).time() < close_time


def expected_candle_opens(start: datetime, end: datetime) -> list[datetime]:
    """Zwraca oczekiwane otwarcia świec w przedziale [start, end), w UTC."""
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
    """Identyfikuje ciągły dzienny segment handlu, zaczynający się o 18:00 ET."""
    local = _aware(instant).astimezone(NEW_YORK)
    session_date = (
        local.date()
        if local.time() >= DAILY_SESSION_START_ET
        else local.date() - timedelta(days=1)
    )
    start = datetime.combine(session_date, DAILY_SESSION_START_ET, tzinfo=NEW_YORK)
    return start.astimezone(UTC).isoformat()


def london_open(day: date) -> datetime:
    """Zwraca otwarcie Londynu 08:00 dla podanej daty londyńskiej."""
    return datetime.combine(day, LONDON_OPEN, tzinfo=LONDON)


def ny_open(day: date) -> datetime:
    """Zwraca otwarcie COMEX 08:20 dla podanej daty nowojorskiej."""
    return datetime.combine(day, COMEX_OPEN, tzinfo=NEW_YORK)


def _at_local_time(day: date, clock: time, zone: ZoneInfo) -> datetime:
    """Buduje świadomy czas lokalny dla danego dnia i strefy."""
    return datetime.combine(day, clock, tzinfo=zone)


def session_name(instant: datetime) -> str:
    """Określa nazwę sesji na podstawie lokalnych godzin Londynu i Nowego Jorku."""
    london = _aware(instant).astimezone(LONDON)
    new_york = _aware(instant).astimezone(NEW_YORK)
    if LIQUIDITY_OVERLAP_START <= new_york.time() < LIQUIDITY_OVERLAP_END:
        return "OVERLAP"
    if LONDON_OPEN <= london.time() < LONDON_SESSION_END:
        return "LONDON"
    if NEW_YORK_SESSION_START <= new_york.time() < NEW_YORK_SESSION_END:
        return "NY"
    return "ASIA" if london.time() < LONDON_OPEN else "LATE"


def signal_window_open(instant: datetime) -> bool:
    """Sprawdza bazowe okno sygnałów oraz piątkowy limit czasu polskiego."""
    local = _aware(instant).astimezone(WARSAW)
    london = local.astimezone(LONDON)
    new_york = local.astimezone(NEW_YORK)
    if (
        local.weekday() > 4
        or london.time() < SIGNAL_WINDOW_LONDON_START
        or new_york.time() >= SIGNAL_WINDOW_NEW_YORK_END
    ):
        return False
    return local.weekday() != 4 or local.time() <= FRIDAY_LAST_SIGNAL_PL


def minutes_since_london_open(instant: datetime) -> int:
    """Zwraca liczbę minut od otwarcia Londynu; przed otwarciem wynik jest ujemny."""
    local = _aware(instant).astimezone(LONDON)
    return int((local - _at_local_time(local.date(), LONDON_OPEN, LONDON)).total_seconds() // 60)


def minutes_since_ny_open(instant: datetime) -> int:
    """Zwraca liczbę minut od otwarcia COMEX 08:20 ET."""
    local = _aware(instant).astimezone(NEW_YORK)
    return int((local - _at_local_time(local.date(), COMEX_OPEN, NEW_YORK)).total_seconds() // 60)


def blackout_codes(instant: datetime) -> list[str]:
    """Zwraca kody blokad czasowych oraz ręcznie skonfigurowanych wydarzeń."""
    local = _aware(instant)
    london = local.astimezone(LONDON)
    new_york = local.astimezone(NEW_YORK)
    codes = []
    if london.time() >= BLACKOUTS["london_open_start"] and london.time() < BLACKOUTS["london_open_end"]:
        codes.append("london_open")
    for fixing in BLACKOUTS["lbma_fixing_times"]:
        center = datetime.combine(london.date(), fixing, tzinfo=LONDON)
        if abs((london - center).total_seconds()) <= (
            BLACKOUTS["lbma_fixing_buffer_minutes"] * SECONDS_PER_MINUTE
        ):
            codes.append("lbma_fixing")
            break
    for clock, before, after, code in (
        (BLACKOUTS["us_0830_time"], BLACKOUTS["us_0830_before_minutes"], BLACKOUTS["us_0830_after_minutes"], "us_0830"),
        (BLACKOUTS["us_1000_time"], BLACKOUTS["us_1000_before_minutes"], BLACKOUTS["us_1000_after_minutes"], "us_1000"),
    ):
        center = datetime.combine(new_york.date(), clock, tzinfo=NEW_YORK)
        delta = (new_york - center).total_seconds() / 60
        if -before <= delta <= after:
            codes.append(code)
    if new_york.date() in FOMC_DATES:
        fomc = datetime.combine(new_york.date(), BLACKOUTS["fomc_start"], tzinfo=NEW_YORK)
        if fomc <= new_york < fomc + timedelta(minutes=BLACKOUTS["fomc_duration_minutes"]):
            codes.append("fomc")
    if new_york.time() >= BLACKOUTS["rollover_start"]:
        codes.append("rollover")
    for blackout in NEWS_BLACKOUTS:
        if _inside_manual_blackout(local, blackout):
            codes.append(str(blackout.get("code", "news")))
    if london.date() in HIGH_IMPACT_DAYS or new_york.date() in HIGH_IMPACT_DAYS:
        codes.append("high_impact_day")
    return codes


def _inside_manual_blackout(instant: datetime, blackout: dict[str, object]) -> bool:
    """Sprawdza wpis z NEWS_BLACKOUTS; błędna konfiguracja zgłasza ValueError."""
    try:
        start = datetime.fromisoformat(str(blackout["start"]))
        end = datetime.fromisoformat(str(blackout["end"]))
        zone_name = str(blackout.get("timezone", LOCAL_TIME_ZONE))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("NEWS_BLACKOUTS wymaga pól start i end w formacie ISO.") from error
    zone = ZoneInfo(zone_name)
    if start.tzinfo is None:
        start = start.replace(tzinfo=zone)
    if end.tzinfo is None:
        end = end.replace(tzinfo=zone)
    if end <= start:
        raise ValueError("Koniec NEWS_BLACKOUTS musi być późniejszy niż początek.")
    current = _aware(instant).astimezone(UTC)
    extended_end = end + timedelta(
        minutes=BLACKOUTS["news_after_minutes"]
    )
    return start.astimezone(UTC) <= current < extended_end.astimezone(UTC)


def is_holiday(instant: datetime) -> bool:
    """Zwraca, czy lokalna data została wpisana do ręcznej listy dni bez handlu."""
    return _aware(instant).astimezone(WARSAW).date() in NO_TRADE_DATES


def is_early_close(instant: datetime) -> bool:
    """Sprawdza, czy chwila przypada po skonfigurowanym skróconym zamknięciu."""
    new_york = _aware(instant).astimezone(NEW_YORK)
    close_time = EARLY_CLOSE_DATES.get(new_york.date())
    return close_time is not None and new_york.time() >= close_time
