#include "DailySentencePicker.h"

#include "CandleHistoryClient.h"

#include <random>

/**
 * Pobiera pulę sentencji przez lokalne API i losuje jedną pozycję.
 */
bool DailySentencePicker::fetchRandomSentence(
    std::string& sentence, std::string& error) const {
    sentence.clear();
    error.clear();
    std::vector<std::string> sentences;
    if (!CandleHistoryClient::fetchDailySentences(sentences, error)) {
        return false;
    }
    return selectRandomSentence(sentences, sentence, error);
}

/**
 * Wybiera losową sentencję z niepustej listy.
 */
bool DailySentencePicker::selectRandomSentence(
    const std::vector<std::string>& sentences,
    std::string& sentence,
    std::string& error) {
    sentence.clear();
    error.clear();
    if (sentences.empty()) {
        error = "Nie ma sentencji do wylosowania.";
        return false;
    }

    std::random_device randomDevice;
    std::mt19937 randomGenerator(randomDevice());
    std::uniform_int_distribution<std::size_t> chooseIndex(
        0, sentences.size() - 1);
    const std::string& selectedSentence =
        sentences[chooseIndex(randomGenerator)];
    if (selectedSentence.empty()) {
        error = "Wylosowana sentencja jest pusta.";
        return false;
    }
    sentence = selectedSentence;
    return true;
}
