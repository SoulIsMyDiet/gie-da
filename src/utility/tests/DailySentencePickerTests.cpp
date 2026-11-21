#include "DailySentencePicker.h"

#include <string>
#include <vector>

/**
 * Sprawdza losowanie z puli oraz jawne odrzucanie pustej listy.
 */
int main() {
    std::string sentence;
    std::string error;
    if (!DailySentencePicker::selectRandomSentence(
            {"Jedyna sentencja."}, sentence, error) ||
        sentence != "Jedyna sentencja.") {
        return 1;
    }

    if (DailySentencePicker::selectRandomSentence(
            std::vector<std::string>{}, sentence, error)) {
        return 2;
    }
    if (error != "Nie ma sentencji do wylosowania.") {
        return 3;
    }

    const std::vector<std::string> candidates{
        "Pierwsza sentencja.", "Druga sentencja."};
    if (!DailySentencePicker::selectRandomSentence(
            candidates, sentence, error)) {
        return 4;
    }
    if (sentence != candidates[0] && sentence != candidates[1]) {
        return 5;
    }
    return 0;
}
