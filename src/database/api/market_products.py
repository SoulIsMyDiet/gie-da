"""Rejestr aktywnych instrumentów i kalendarzy sesji rynkowych."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class SessionWindow:
    """Opisuje przedział handlu w lokalnym czasie danej giełdy."""

    start_minute: int
    end_minute: int

    def contains(self, minute_of_day: int) -> bool:
        """Sprawdza, czy minuta dnia należy do przedziału handlu."""
        return self.start_minute <= minute_of_day < self.end_minute


@dataclass(frozen=True)
class MarketProfile:
    """Przechowuje strefę czasową i tygodniowy kalendarz jednego typu rynku."""

    market_type: str
    timezone_name: str
    sessions_by_weekday: tuple[tuple[SessionWindow, ...], ...]
    summer_sessions_by_weekday: (
        tuple[tuple[SessionWindow, ...], ...] | None
    ) = None

    def is_open(self, utc_time: datetime) -> bool:
        """Zwraca informację, czy rynek jest otwarty w podanym czasie UTC."""
        if utc_time.tzinfo is None or utc_time.utcoffset() is None:
            raise ValueError("Czas rynku musi zawierać strefę czasową.")
        local_time = utc_time.astimezone(ZoneInfo(self.timezone_name))
        minute_of_day = local_time.hour * 60 + local_time.minute
        sessions = self.sessions_by_weekday
        if (
            self.summer_sessions_by_weekday is not None
            and local_time.dst() != timedelta(0)
        ):
            sessions = self.summer_sessions_by_weekday
        return any(
            session.contains(minute_of_day)
            for session in sessions[local_time.weekday()]
        )


@dataclass(frozen=True)
class MarketProduct:
    """Łączy symbol Twelve Data z typem rynku i jego harmonogramem."""

    symbol: str
    profile_name: str


_CLOSED_DAY: tuple[SessionWindow, ...] = ()
_ALL_DAY = (SessionWindow(0, 24 * 60),)

MARKET_PROFILES = {
    "forex_ny_24_5": MarketProfile(
        market_type="forex",
        timezone_name="America/New_York",
        sessions_by_weekday=(
            _ALL_DAY,
            _ALL_DAY,
            _ALL_DAY,
            _ALL_DAY,
            (SessionWindow(0, 17 * 60),),
            _CLOSED_DAY,
            (SessionWindow(17 * 60, 24 * 60),),
        ),
    ),
    "us_equity": MarketProfile(
        market_type="equity",
        timezone_name="America/New_York",
        sessions_by_weekday=(
            (SessionWindow(9 * 60 + 30, 16 * 60),),
            (SessionWindow(9 * 60 + 30, 16 * 60),),
            (SessionWindow(9 * 60 + 30, 16 * 60),),
            (SessionWindow(9 * 60 + 30, 16 * 60),),
            (SessionWindow(9 * 60 + 30, 16 * 60),),
            _CLOSED_DAY,
            _CLOSED_DAY,
        ),
    ),
    "crypto_24_7": MarketProfile(
        market_type="crypto",
        timezone_name="UTC",
        sessions_by_weekday=(_ALL_DAY,) * 7,
    ),
    "dax_index": MarketProfile(
        market_type="index",
        timezone_name="Europe/Berlin",
        sessions_by_weekday=(
            (SessionWindow(70, 22 * 60),),
            (SessionWindow(70, 22 * 60),),
            (SessionWindow(70, 22 * 60),),
            (SessionWindow(70, 22 * 60),),
            (SessionWindow(70, 22 * 60),),
            _CLOSED_DAY,
            _CLOSED_DAY,
        ),
        summer_sessions_by_weekday=(
            (SessionWindow(130, 22 * 60),),
            (SessionWindow(130, 22 * 60),),
            (SessionWindow(130, 22 * 60),),
            (SessionWindow(130, 22 * 60),),
            (SessionWindow(130, 22 * 60),),
            _CLOSED_DAY,
            _CLOSED_DAY,
        ),
    ),
    "sp500_futures": MarketProfile(
        market_type="futures",
        timezone_name="America/New_York",
        sessions_by_weekday=(
            (SessionWindow(0, 17 * 60), SessionWindow(18 * 60, 24 * 60)),  # Poniedziałek - przerwa 17-18
            (SessionWindow(0, 17 * 60), SessionWindow(18 * 60, 24 * 60)),  # Wtorek - przerwa 17-18
            (SessionWindow(0, 17 * 60), SessionWindow(18 * 60, 24 * 60)),  # Środa - przerwa 17-18
            (SessionWindow(0, 17 * 60), SessionWindow(18 * 60, 24 * 60)),  # Czwartek - przerwa 17-18
            (SessionWindow(0, 17 * 60),),  # Piątek - zamyka się o 17:00
            _CLOSED_DAY,  # Sobota
            (SessionWindow(17 * 60, 24 * 60),),  # Niedziela - od 17:00
        ),
    ),
    "gold_spot": MarketProfile(
        market_type="metal",
        timezone_name="America/New_York",
        sessions_by_weekday=(
            _ALL_DAY,  # Poniedziałek
            _ALL_DAY,  # Wtorek
            _ALL_DAY,  # Środa
            _ALL_DAY,  # Czwartek
            (SessionWindow(0, 17 * 60),),  # Piątek - do 17:00
            _CLOSED_DAY,  # Sobota - zamknięte
            (SessionWindow(17 * 60, 24 * 60),),  # Niedziela - od 17:00
        ),
    ),
}

# Dodaj nowe symbole tutaj i przypisz im odpowiedni profil rynku.
MONITORED_PRODUCTS = (
    MarketProduct("EURUSD/USD", "forex_ny_24_5"),
    MarketProduct("GER30", "dax_index"),
    MarketProduct("ES", "sp500_futures"),
    MarketProduct("XAU/USD", "gold_spot"),
)


def get_market_profile(symbol: str) -> MarketProfile:
    """Zwraca profil instrumentu lub zgłasza błąd dla nieznanego symbolu."""
    for product in MONITORED_PRODUCTS:
        if product.symbol == symbol:
            return MARKET_PROFILES[product.profile_name]
    raise ValueError(f"Instrument {symbol} nie jest skonfigurowany.")


def is_market_open(symbol: str, utc_time: datetime) -> bool:
    """Sprawdza sesję właściwą dla skonfigurowanego instrumentu."""
    return get_market_profile(symbol).is_open(utc_time)


def get_monitored_symbols() -> tuple[str, ...]:
    """Zwraca symbole aktywne w monitorze w kolejności z konfiguracji."""
    return tuple(product.symbol for product in MONITORED_PRODUCTS)


def get_open_symbols(utc_time: datetime) -> tuple[str, ...]:
    """Zwraca tylko aktywne produkty, których sesje są teraz otwarte."""
    return tuple(
        product.symbol
        for product in MONITORED_PRODUCTS
        if MARKET_PROFILES[product.profile_name].is_open(utc_time)
    )


def is_monitored_product(symbol: str) -> bool:
    """Sprawdza, czy symbol należy do aktywnej konfiguracji produktów."""
    return any(product.symbol == symbol for product in MONITORED_PRODUCTS)


def _validate_profile_configuration() -> None:
    """Sprawdza profile i powiązania produktów przy imporcie modułu."""
    for profile in MARKET_PROFILES.values():
        if len(profile.sessions_by_weekday) != 7:
            raise ValueError(
                f"Profil {profile.market_type} musi zawierać 7 dni tygodnia."
            )
        if (
            profile.summer_sessions_by_weekday is not None
            and len(profile.summer_sessions_by_weekday) != 7
        ):
            raise ValueError(
                f"Profil letni {profile.market_type} musi zawierać 7 dni tygodnia."
            )
        ZoneInfo(profile.timezone_name)
        schedules = (profile.sessions_by_weekday,)
        if profile.summer_sessions_by_weekday is not None:
            schedules += (profile.summer_sessions_by_weekday,)
        for schedule in schedules:
            for daily_sessions in schedule:
                previous_end = 0
                for session in daily_sessions:
                    if (
                        session.start_minute < previous_end
                        or session.start_minute < 0
                        or session.end_minute > 24 * 60
                        or session.start_minute >= session.end_minute
                    ):
                        raise ValueError(
                            f"Profil {profile.market_type} zawiera "
                            "nieprawidłową sesję."
                        )
                    previous_end = session.end_minute

    known_profiles = set(MARKET_PROFILES)
    configured_symbols = set()
    for product in MONITORED_PRODUCTS:
        valid_symbol = (
            0 < len(product.symbol) <= 32
            and product.symbol.isascii()
            and product.symbol == product.symbol.upper()
            and all(
                character.isalnum() or character in "./_-"
                for character in product.symbol
            )
        )
        if not valid_symbol:
            raise ValueError(f"Nieprawidłowy symbol produktu: {product.symbol}.")
        if product.symbol in configured_symbols:
            raise ValueError(f"Powtórzony symbol produktu: {product.symbol}.")
        if product.profile_name not in known_profiles:
            raise ValueError(
                f"Brak profilu {product.profile_name} dla {product.symbol}."
            )
        configured_symbols.add(product.symbol)


_validate_profile_configuration()
