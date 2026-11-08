#pragma once

#include <cstddef>
#include <string>
#include <vector>

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

    /**
     * Pobiera maksymalną liczbę najnowszych świec 1-minutowych jednym żądaniem.
     * @param symbol Symbol instrumentu, np. EUR/USD.
     * @param maximumCandles Maksymalna liczba świec do pobrania (1-5000).
     * @param candles Otrzymuje świece w kolejności od najnowszej.
     * @param error Otrzymuje opis błędu, jeśli żądanie lub odpowiedź są nieprawidłowe.
     * @return true, jeśli odpowiedź zawiera prawidłowe dane.
     */
    static bool fetchLatestOneMinuteCandles(
        const std::string& symbol,
        std::size_t maximumCandles,
        std::vector<CandleData>& candles,
        std::string& error);

    /**
     * Pobiera świece 1-minutowe z jednego zakresu UTC jednym żądaniem.
     * @param symbol Symbol instrumentu, np. EUR/USD.
     * @param startUtc Początek zakresu w formacie YYYY-MM-DD HH:MM:SS.
     * @param endUtc Koniec zakresu w formacie YYYY-MM-DD HH:MM:SS.
     * @param maximumCandles Maksymalna liczba świec do pobrania (1-5000).
     * @param candles Otrzymuje świece w kolejności chronologicznej.
     * @param error Otrzymuje opis błędu, jeśli żądanie lub odpowiedź są nieprawidłowe.
     * @return true, jeśli odpowiedź zawiera prawidłowe dane.
     */
    static bool fetchOneMinuteCandleRange(
        const std::string& symbol,
        const std::string& startUtc,
        const std::string& endUtc,
        std::size_t maximumCandles,
        std::vector<CandleData>& candles,
        std::string& error);
};
