"""Front: uruchomienie analizy jednej wskazanej świecy S&P500."""

# Backend: krótki opis reguł strategii do wykorzystania przez przyszły walidator.
STRATEGY_CONTEXT = (
    "Instrument ES (wynik S&P500); zamknięte świece M1. LONG/SHORT wymagają "
    "zgodnego trendu EMA, pullbacku, RSI, dodatniego ATR, względnego wolumenu, "
    "cech świecy, ADX i kierunkowego DI; wszystkie warunki muszą być spełnione. "
    "Brak danych, świeżości lub któregokolwiek warunku daje NONE."
)

from trigger import main


if __name__ == "__main__":
    raise SystemExit(main())
