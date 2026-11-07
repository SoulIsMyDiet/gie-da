#pragma once

#include <string>

/**
 * Przechowuje czas otwarcia oraz ceny open, high, low i close pojedynczej świecy.
 */
struct ForexCandle {
    std::string datetime;
    std::string open;
    std::string high;
    std::string low;
    std::string close;
};

class TwelveDataClient {
public:
    /**
     * Pobiera ostatnią świecę EUR/USD z interwału jednej minuty.
     * @param candle Otrzymuje czas otwarcia oraz wartości OHLC świecy.
     * @param error Otrzymuje opis przyczyny niepowodzenia.
     * @return true, jeśli odpowiedź zawiera kompletną i spójną świecę.
     */
    static bool fetchEurUsdOneMinuteCandle(
        ForexCandle& candle, std::string& error);
};
