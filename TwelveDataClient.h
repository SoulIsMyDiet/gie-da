#pragma once

#include <string>

enum class ForexCandleInterval {
    oneMinute,
    fiveMinutes
};

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
     * Requests the latest EUR/USD price using the configured Twelve Data API key.
     * @param price Receives the price string when the request succeeds.
     * @param error Receives a diagnostic message when the request fails.
     * @return true if a valid price was received; otherwise false.
     */
    static bool fetchEurUsdPrice(std::string& price, std::string& error);

    /**
     * Pobiera ostatnią świecę EUR/USD dla wskazanego interwału.
     * @param interval Interwał świecy: jedna minuta albo pięć minut.
     * @param candle Otrzymuje czas otwarcia oraz wartości OHLC świecy.
     * @param error Otrzymuje opis przyczyny niepowodzenia.
     * @return true, jeśli odpowiedź zawiera kompletną i spójną świecę.
     */
    static bool fetchEurUsdCandle(
        ForexCandleInterval interval, ForexCandle& candle, std::string& error);
};
