#pragma once

#include <string>
#include <vector>

#include "CandlesDataClient.h"

/**
 * Przechowuje świecę wyświetlaną w raporcie.
 */
struct CandleReportEntry {
    std::string symbol;
    std::string datetime;
    std::string open;
    std::string high;
    std::string low;
    std::string close;
    std::string volume;
};

/**
 * Przechowuje stan jednej brakującej świecy i czasy jej obsługi.
 */
struct CandleGapReportEntry {
    std::string symbol;
    std::string candleDatetimeUtc;
    std::string status;
    std::string detectedAtUtc;
    std::string filledAtUtc;
};

class CandleHistoryClient {
public:
    /**
     * Zapisuje świecę w lokalnym API historii rynkowej.
     * @param symbol Symbol instrumentu, którego dotyczy świeca.
     * @param candle Dane OHLCV i czas otwarcia świecy w UTC.
     * @param newlyDetectedGapCount Otrzymuje liczbę nowo wykrytych luk.
     * @param error Otrzymuje opis błędu, jeśli zapis się nie powiedzie.
     * @return true, jeśli API potwierdzi zapis; w przeciwnym razie false.
     */
    static bool saveCandle(
        const std::string& symbol,
        const CandleData& candle,
        std::size_t& newlyDetectedGapCount,
        std::string& error);

    /**
     * Pobiera z bazy do 1000 brakujących minut dla wskazanego symbolu.
     * @param symbol Symbol instrumentu, którego dotyczy historia.
     * @param hasExistingCandles Otrzymuje informację, czy baza zawiera już świece.
     * @param missingTimes Otrzymuje brakujące znaczniki czasu UTC.
     * @param totalMissingCount Otrzymuje łączną liczbę wykrytych luk.
     * @param error Otrzymuje opis błędu, jeśli odczyt się nie powiedzie.
     * @return true, jeśli lokalne API zwróciło poprawny plan uzupełnienia.
     */
    static bool fetchMissingCandleTimes(
        const std::string& symbol,
        bool& hasExistingCandles,
        std::vector<std::string>& missingTimes,
        std::size_t& totalMissingCount,
        std::string& error);

    /**
     * Zapisuje paczkę świec w jednym żądaniu do lokalnego API.
     * @param symbol Symbol instrumentu, którego dotyczą świece.
     * @param candles Świece zapisane w formacie czasu UTC.
     * @param error Otrzymuje opis błędu, jeśli zapis się nie powiedzie.
     * @return true, jeśli API potwierdzi zapis paczki.
     */
    static bool saveCandles(
        const std::string& symbol,
        const std::vector<CandleData>& candles,
        std::string& error);

    /**
     * Pobiera stronę zawierającą do 20 świec, od najnowszych.
     * @param page Numer strony liczony od zera.
     * @param candles Otrzymuje dane świec.
     * @param hasNextPage Otrzymuje informację, czy istnieje kolejna strona.
     * @param error Otrzymuje opis błędu, jeśli pobranie się nie powiedzie.
     * @return true, jeśli API zwróciło poprawną stronę.
     */
    static bool fetchCandlePage(
        std::size_t page,
        std::vector<CandleReportEntry>& candles,
        bool& hasNextPage,
        std::string& error);

    /**
     * Pobiera stronę 20 wpisów z historii brakujących świec.
     * @param page Numer strony liczony od zera.
     * @param gaps Otrzymuje wpisy luk świecowych.
     * @param hasNextPage Otrzymuje informację, czy istnieje kolejna strona.
     * @param error Otrzymuje opis błędu, jeśli pobranie się nie powiedzie.
     * @return true, jeśli lokalne API zwróciło poprawną stronę.
     */
    static bool fetchCandleGapPage(
        std::size_t page,
        std::vector<CandleGapReportEntry>& gaps,
        bool& hasNextPage,
        std::string& error);
};
