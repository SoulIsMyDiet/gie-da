#pragma once

#include <string>

#include "CandlesDataClient.h"

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
};
