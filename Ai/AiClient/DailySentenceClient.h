#pragma once

#include <string>

class DailySentenceClient {
public:
    /**
     * Pobiera sentencję dnia bezpośrednio z API Groq.
     * @param sentence Otrzymuje sentencję po udanym pobraniu.
     * @param error Otrzymuje opis błędu, jeśli żądanie się nie powiedzie.
     * @return true, jeśli API zwróciło niepustą sentencję; w przeciwnym razie false.
     * Nie rzuca wyjątków; błędy są zwracane w parametrze error.
     */
    static bool fetchDailySentence(std::string& sentence, std::string& error);
};
