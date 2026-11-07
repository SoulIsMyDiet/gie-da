#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string>
#include <vector>

/**
 * Przechowuje wartości jednego wiersza raportu z historii cen.
 */
struct PriceReportEntry {
    std::string id;
    std::string recordedAt;
    std::string symbol;
    std::string price;
    std::string goldenAdvice;
};

class PriceHistoryClient {
public:
    /**
     * Zapisuje cenę produktu w lokalnym API historii cen.
     * @param symbol Symbol produktu, np. EUR/USD.
     * @param price Cena w zapisie dziesiętnym.
     * @param recordedAt Czas pobrania ceny w UTC.
     * @param goldenAdvice Sentencja wygenerowana przez model Groq.
     * @param error Otrzymuje opis błędu, jeśli zapis się nie powiedzie.
     * @return true, jeśli API potwierdzi zapis; w przeciwnym razie false.
     */
    static bool savePrice(
        const std::string& symbol,
        const std::string& price,
        const SYSTEMTIME& recordedAt,
        const std::string& goldenAdvice,
        std::string& error);

    /**
     * Pobiera maksymalnie 20 zapisów wcześniejszych niż podany czas UTC.
     * @param beforeUtcMicroseconds Czas kliknięcia jako mikrosekundy od epoki Unix.
     * @param entries Otrzymuje wiersze raportu, jeśli pobranie się powiedzie.
     * @param error Otrzymuje opis błędu, jeśli żądanie się nie powiedzie.
     * @return true, jeśli odpowiedź zawiera prawidłowe dane; w przeciwnym razie false.
     */
    static bool fetchRecentPrices(
        unsigned long long beforeUtcMicroseconds,
        std::vector<PriceReportEntry>& entries,
        std::string& error);
}; // :)
