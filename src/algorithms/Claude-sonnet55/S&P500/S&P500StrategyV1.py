"""Front: punkt wejścia strategii S&P 500."""

# Backend: krótki opis reguł strategii do wykorzystania przez przyszły walidator.
STRATEGY_CONTEXT = (
    "Instrument ES (wynik S&P500); zamknięte świece M1, z potwierdzeniem trendu "
    "M5 tam, gdzie wymaga go setup. LONG/SHORT wymagają zgodnego trendu EMA, "
    "pullbacku do EMA21 i zgodnych RSI, ATR, wolumenu, cech świecy, ADX oraz DI; "
    "dopuszczone są też skonfigurowane wybicie zakresu otwarcia i rewersja VWAP. "
    "NONE przy braku setupu lub niespełnieniu filtrów."
)

from trigger import main


if __name__ == "__main__":
    raise SystemExit(main())
