#pragma once

#include <cstddef>
#include <string>

#include "CandlesDataClient.h"

/**
 * Przechowuje wynik pobrania i zapisu brakujących świec.
 */
struct CandleBackfillResult {
    std::size_t savedCandleCount = 0;
    std::size_t detectedGapCount = 0;
    double elapsedSeconds = 0.0;
    CandleData latestCandle;
};

class CandleBackfillClient {
public:
    /**
     * Uzupełnia maksymalnie 1000 brakujących świec wskazanego instrumentu.
     * @param symbol Symbol instrumentu skonfigurowanego w lokalnym API.
     * @param result Otrzymuje liczbę zapisanych i brakujących świec, czas i ostatnią świecę.
     * @param error Otrzymuje opis błędu, jeśli pobieranie lub zapis się nie powiedzie.
     * @return true, jeśli wszystkie pobrane zakresy zostały obsłużone.
     */
    static bool synchronize(
        const std::string& symbol,
        CandleBackfillResult& result,
        std::string& error);
};
