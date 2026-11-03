#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string>

class PriceHistoryClient {
public:
    /**
     * Zapisuje cenę produktu w lokalnym API historii cen.
     * @param symbol Symbol produktu, np. EUR/USD.
     * @param price Cena w zapisie dziesiętnym.
     * @param recordedAt Czas pobrania ceny w UTC.
     * @param error Otrzymuje opis błędu, jeśli zapis się nie powiedzie.
     * @return true, jeśli API potwierdzi zapis; w przeciwnym razie false.
     */
    static bool savePrice(
        const std::string& symbol,
        const std::string& price,
        const SYSTEMTIME& recordedAt,
        std::string& error);
}; // :)
