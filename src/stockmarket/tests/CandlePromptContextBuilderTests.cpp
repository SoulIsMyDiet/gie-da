#include "CandlePromptContextBuilder.h"

#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

/**
 * Kończy test czytelnym komunikatem, gdy warunek nie został spełniony.
 */
bool require(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << '\n';
        return false;
    }
    return true;
}

/**
 * Formatuje testową cenę z czterema miejscami po przecinku.
 */
std::string formatPrice(long double value) {
    std::ostringstream result;
    result.precision(4);
    result << std::fixed << value;
    return result.str();
}

/**
 * Tworzy 300 równomiernie rozmieszczonych świec z oznaczoną luką.
 */
std::vector<CandlePromptRow> createHistory() {
    std::vector<CandlePromptRow> history;
    const std::int64_t startUtc = 1'800'000'000 -
        (1'800'000'000 % (5 * 60));
    for (std::int64_t index = 0; index < 300; ++index) {
        const long double open = 17.0L +
            static_cast<long double>(index) * 0.0001L;
        history.push_back(
            {
                startUtc + index * 60,
                formatPrice(open),
                formatPrice(open + 0.0002L),
                formatPrice(open - 0.0001L),
                formatPrice(open + 0.0001L),
                index == 180,
            });
    }
    return history;
}

/**
 * Sprawdza liczbę świec, kodowanie ticków i zapis ostrzeżenia o luce.
 */
bool testBuildsFiftyM1AndThirtyM5Candles() {
    const std::vector<CandlePromptRow> history = createHistory();
    const auto uniquePart = std::chrono::steady_clock::now()
        .time_since_epoch().count();
    const std::filesystem::path logPath =
        std::filesystem::temp_directory_path() /
        ("candle-prompt-context-" + std::to_string(uniquePart) + ".log");

    std::string json;
    std::string error;
    const bool built = CandlePromptContextBuilder::buildJson(
        "USD/MXN", history, json, error, logPath);
    if (!require(built, error.c_str())) {
        return false;
    }
    if (!require(
            json.find("\"synthetic\":false") != std::string::npos,
            "JSON nie oznacza danych jako rzeczywistych.")) {
        return false;
    }
    if (!require(
            json.find("\"tick_size\":0.0001") != std::string::npos,
            "JSON nie zachowuje precyzji cen.")) {
        return false;
    }
    std::size_t rowSeparators = 0;
    std::size_t position = 0;
    while ((position = json.find("],[", position)) != std::string::npos) {
        ++rowSeparators;
        position += 3;
    }
    if (!require(
            rowSeparators == 78,
            "JSON nie zawiera dokładnie 50 świec M1 i 30 świec M5.")) {
        return false;
    }

    std::ifstream logFile(logPath, std::ios::binary);
    std::string logContent(
        (std::istreambuf_iterator<char>(logFile)),
        std::istreambuf_iterator<char>());
    std::error_code fileSystemError;
    std::filesystem::remove(logPath, fileSystemError);
    return require(
        logContent ==
            "[warning] wykryto luke w swiecy podczas wysyłania prompta</br>\n",
        "Ostrzeżenie o luce nie zostało zapisane w wymaganej postaci.");
}

/**
 * Sprawdza, że krótsza historia nie zwraca niekompletnego JSON-u.
 */
bool testRejectsInsufficientMinuteHistory() {
    std::vector<CandlePromptRow> history = createHistory();
    history.resize(49);
    std::string json;
    std::string error;
    const bool built = CandlePromptContextBuilder::buildJson(
        "USD/MXN", history, json, error);
    return require(
        !built && json.empty(),
        "Klasa powinna odrzucić historię krótszą niż 50 świec M1.");
}

} // namespace

/**
 * Uruchamia testy budowania JSON-u i walidacji niepełnej historii.
 */
int main() {
    if (!testBuildsFiftyM1AndThirtyM5Candles()) {
        return 1;
    }
    if (!testRejectsInsufficientMinuteHistory()) {
        return 1;
    }
    return 0;
}
