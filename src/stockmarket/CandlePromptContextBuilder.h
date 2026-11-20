#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

/**
 * Przechowuje jedną zamkniętą świecę M1 pobraną z lokalnego API.
 */
struct CandlePromptRow {
    std::int64_t timestampUtc = 0;
    std::string open;
    std::string high;
    std::string low;
    std::string close;
    bool gapBefore = false;
};

// Backend
/**
 * Buduje zwięzły JSON historii M1 i M5 dla prompta weryfikującego sygnał.
 */
class CandlePromptContextBuilder {
public:
    /**
     * Pobiera historię z lokalnego API i zwraca gotowy JSON kontekstu.
     * @param symbol Symbol instrumentu zapisany w bazie.
     * @param json Otrzymuje JSON z 50 świecami M1 i 30 świecami M5.
     * @param error Otrzymuje opis błędu pobierania lub walidacji danych.
     * @return true, jeśli historia jest poprawna i JSON został zbudowany.
     */
    static bool fetchJson(
        const std::string& symbol,
        std::string& json,
        std::string& error);

    /**
     * Waliduje świece, agreguje kompletne M5 i buduje JSON.
     * @param symbol Symbol instrumentu.
     * @param history Świece M1 uporządkowane rosnąco według czasu UTC.
     * @param json Otrzymuje gotowy JSON.
     * @param error Otrzymuje opis błędu walidacji, agregacji lub zapisu logu.
     * @param warningLogPath Opcjonalny dodatkowy plik dla ostrzeżenia o luce.
     * @return true, jeśli udało się utworzyć kompletny JSON.
     */
    static bool buildJson(
        const std::string& symbol,
        const std::vector<CandlePromptRow>& history,
        std::string& json,
        std::string& error,
        const std::filesystem::path& warningLogPath = {});
};
