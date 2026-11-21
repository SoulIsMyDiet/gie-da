#pragma once

#include <string>
#include <vector>

class DailySentencePicker {
public:
    /**
     * Pobiera pulę z lokalnego API i losuje z niej sentencję.
     * @param sentence Otrzymuje wylosowany tekst po udanym odczycie.
     * @param error Otrzymuje opis błędu sieci, bazy lub losowania.
     * @return true, jeśli udało się pobrać i wylosować sentencję.
     * @throws std::system_error, jeśli generator losowy nie jest dostępny.
     */
    bool fetchRandomSentence(std::string& sentence, std::string& error) const;

    /**
     * Wybiera losową pozycję z przekazanej puli.
     * @param sentences Lista kandydatów do losowania.
     * @param sentence Otrzymuje wybraną sentencję.
     * @param error Otrzymuje opis błędu, gdy lista jest pusta lub ma pusty wpis.
     * @return true, jeśli udało się wybrać niepustą sentencję.
     * @throws std::system_error, jeśli generator losowy nie jest dostępny.
     */
    static bool selectRandomSentence(
        const std::vector<std::string>& sentences,
        std::string& sentence,
        std::string& error);
};
