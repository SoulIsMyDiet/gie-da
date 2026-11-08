#pragma once

#include <cstddef>
#include <string>

#include "CandlesDataClient.h"

/**
 * Przechowuje wynik pobrania i zapisu brakujących świec.
 */
struct CandleBackfillResult {
    std::size_t savedCandleCount = 0;
    double elapsedSeconds = 0.0;
    CandleData latestCandle;
};

class CandleBackfillClient {
public:
    /**
     * Uzupełnia maksymalnie 1000 brakujących świec dla symbolu EUR/USD.
     * @param result Otrzymuje liczbę zapisanych świec, czas operacji i najnowszą świecę.
     * @param error Otrzymuje opis błędu, jeśli pobieranie lub zapis się nie powiedzie.
     * @return true, jeśli wszystkie pobrane zakresy zostały obsłużone.
     */
    static bool synchronize(
        CandleBackfillResult& result, std::string& error);
};
