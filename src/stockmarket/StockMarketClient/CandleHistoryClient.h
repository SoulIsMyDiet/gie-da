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

class CandleHistoryClient {
public:
    /**
     * Zapisuje świecę w lokalnym API historii rynkowej.
     * @param symbol Symbol instrumentu, którego dotyczy świeca.
     * @param candle Dane OHLCV i czas otwarcia świecy w UTC.
     * @param error Otrzymuje opis błędu, jeśli zapis się nie powiedzie.
     * @return true, jeśli API potwierdzi zapis; w przeciwnym razie false.
     */
    static bool saveCandle(
        const std::string& symbol, const CandleData& candle, std::string& error);

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
};
