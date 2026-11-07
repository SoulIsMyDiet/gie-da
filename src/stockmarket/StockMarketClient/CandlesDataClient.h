#pragma once

#include <string>

/**
 * Przechowuje czas otwarcia oraz wartości OHLC jednej świecy.
 */
struct CandleData {
    std::string datetime;
    std::string open;
    std::string high;
    std::string low;
    std::string close;
    std::string volume;
};

class CandlesDataClient {
public:
    /**
     * Pobiera ostatnią świecę 1-minutową dla wskazanego symbolu.
     * @param symbol Symbol instrumentu, np. EUR/USD albo AAPL.
     * @param candle Otrzymuje czas otwarcia i wartości OHLC świecy.
     * @param error Otrzymuje opis błędu, jeśli żądanie lub odpowiedź są nieprawidłowe.
     * @return true, jeśli odpowiedź zawiera kompletną i spójną świecę.
     */
    static bool fetchLatestOneMinuteCandle(
        const std::string& symbol, CandleData& candle, std::string& error);
};
