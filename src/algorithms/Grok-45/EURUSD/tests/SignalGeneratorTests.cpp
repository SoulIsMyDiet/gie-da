#include "SignalGenerator.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

/**
 * Przerywa test z czytelnym komunikatem, gdy warunek nie jest spełniony.
 */
void require(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

/**
 * Porównuje liczby zmiennoprzecinkowe z uwzględnieniem małego błędu zaokrągleń.
 */
void requireNear(double actual, double expected, double tolerance,
                 const std::string& message) {
    require(std::abs(actual - expected) <= tolerance, message);
}

/**
 * Buduje serię świec z powtarzalnego generatora liczb o określonym ziarnie.
 */
std::vector<grok45::Candle> createMarketCandles(
    std::uint32_t seed, double trendPerCandle) {
    std::vector<grok45::Candle> candles;
    candles.reserve(grok45::SignalGenerator::analysisWindowCandles);

    std::uint32_t randomState = seed;
    double close = 1.08;
    for (std::size_t index = 0;
         index < grok45::SignalGenerator::analysisWindowCandles;
         ++index) {
        randomState = randomState * 1664525u + 1013904223u;
        const double randomFraction =
            static_cast<double>(randomState >> 8) / 16777216.0;
        const double priceChange =
            trendPerCandle + (randomFraction - 0.5) * 0.00018;
        const double nextClose = close + priceChange;
        const double open = index == 0 ? close : candles.back().close;
        const double high = std::max(open, nextClose) + 0.00005;
        const double low = std::min(open, nextClose) - 0.00005;

        candles.push_back(grok45::Candle{
            static_cast<std::int64_t>(index) * 60,
            open,
            high,
            low,
            nextClose,
            0.0});
        close = nextClose;
    }
    return candles;
}

/**
 * Sprawdza inicjalizację EMA średnią SMA oraz mnożnik wygładzania.
 */
void testEmaUsesSmaSeedAndExpectedMultiplier() {
    const std::vector<std::optional<double>> values =
        grok45::IndicatorCalculator::calculateEmaSeries(
            {1.0, 2.0, 3.0}, 2);

    require(values.size() == 3, "Seria EMA powinna zachować długość wejścia.");
    require(!values[0], "EMA przed okresem rozgrzewki powinna być pusta.");
    requireNear(*values[1], 1.5, 1e-12, "Seed EMA powinien być średnią SMA.");
    requireNear(*values[2], 2.5, 1e-12, "EMA powinna użyć mnożnika 2/(N+1).");
    require(!grok45::IndicatorCalculator::calculateEma({1.0}, 2),
            "EMA z za małej liczby cen powinna być pusta.");
}

/**
 * Sprawdza RSI Wildera dla wzrostu, spadku i niezmiennych cen.
 */
void testRsiHandlesRisingFallingAndFlatPrices() {
    const auto rising =
        grok45::IndicatorCalculator::calculateRsi({1, 2, 3, 4}, 2);
    const auto falling =
        grok45::IndicatorCalculator::calculateRsi({4, 3, 2, 1}, 2);
    const auto flat =
        grok45::IndicatorCalculator::calculateRsi({5, 5, 5, 5}, 2);

    require(rising && falling && flat, "RSI powinien być dostępny po rozgrzewce.");
    requireNear(*rising, 100.0, 1e-12, "RSI tylko dla wzrostów powinien wynosić 100.");
    requireNear(*falling, 0.0, 1e-12, "RSI tylko dla spadków powinien wynosić 0.");
    requireNear(*flat, 50.0, 1e-12, "RSI przy niezmiennej cenie powinien wynosić 50.");
    require(!grok45::IndicatorCalculator::calculateRsi({1, 2}, 2),
            "RSI z za małej liczby zmian powinien być pusty.");
}

/**
 * Sprawdza wstęgi z odchyleniem populacyjnym oraz walidację parametrów.
 */
void testBollingerBandsUsePopulationDeviation() {
    const auto bands =
        grok45::IndicatorCalculator::calculateBollingerBands(
            {1.0, 2.0, 3.0}, 3, 2.0);
    const double populationDeviation = std::sqrt(2.0 / 3.0);

    require(bands.has_value(), "Wstęgi powinny być dostępne dla pełnego okna.");
    requireNear(bands->middle, 2.0, 1e-12, "Środkowa wstęga powinna być średnią.");
    requireNear(
        bands->upper, 2.0 + 2.0 * populationDeviation, 1e-12,
        "Górna wstęga powinna używać odchylenia populacyjnego.");
    requireNear(
        bands->lower, 2.0 - 2.0 * populationDeviation, 1e-12,
        "Dolna wstęga powinna używać odchylenia populacyjnego.");
    require(!grok45::IndicatorCalculator::calculateBollingerBands(
                {1.0, 2.0}, 0, 2.0),
            "Okres Bollingera równy zero powinien zostać odrzucony.");
}

/**
 * Sprawdza ATR Wildera dla świec o stałym zakresie i niepoprawne OHLC.
 */
void testAtrUsesCandleRangesAndAcceptsZeroVolume() {
    std::vector<grok45::Candle> candles;
    for (std::size_t index = 0; index < 16; ++index) {
        candles.push_back(grok45::Candle{
            static_cast<std::int64_t>(index) * 60,
            1.0,
            1.0001,
            0.9999,
            1.0,
            0.0});
    }

    const auto atr = grok45::IndicatorCalculator::calculateAtr(candles, 14);
    require(atr.has_value(), "ATR powinien ignorować brak wolumenu.");
    requireNear(*atr, 0.0002, 1e-12, "ATR powinien być średnią zakresów świec.");
    candles.back().high = 0.5;
    require(!grok45::IndicatorCalculator::calculateAtr(candles, 14),
            "ATR powinien odrzucać niepoprawne OHLC.");
}

/**
 * Sprawdza, czy seria wzrostowa zwraca sygnał LONG z polami JSON.
 */
void testGeneratorProducesLongForBullishPullback() {
    const std::vector<grok45::Candle> candles =
        createMarketCandles(75, 0.000025);
    const std::string response = grok45::SignalGenerator{}.generate(candles);

    require(response.find("\"instrument\":\"EURUSD/USD\"") != std::string::npos,
            "JSON powinien wskazywać EURUSD/USD.");
    require(response.find("\"signal\":\"LONG\"") != std::string::npos,
            "Przykładowa seria wzrostowa powinna wygenerować LONG.");
    require(response.find("\"timestamp\":5940") != std::string::npos,
            "JSON powinien zawierać czas ostatniej świecy.");
    require(response.find("\"ema9\":") != std::string::npos &&
                response.find("\"ema21\":") != std::string::npos &&
                response.find("\"rsi14\":") != std::string::npos &&
                response.find("\"bb_upper\":") != std::string::npos &&
                response.find("\"bb_middle\":") != std::string::npos &&
                response.find("\"bb_lower\":") != std::string::npos &&
                response.find("\"atr14\":") != std::string::npos &&
                response.find("\"reason\":") != std::string::npos,
            "JSON powinien zawierać wszystkie wskaźniki i uzasadnienie.");
}

/**
 * Sprawdza, czy seria spadkowa zwraca sygnał SHORT.
 */
void testGeneratorProducesShortForBearishPullback() {
    const std::vector<grok45::Candle> candles =
        createMarketCandles(194, -0.000025);
    const std::string response = grok45::SignalGenerator{}.generate(candles);

    require(response.find("\"signal\":\"SHORT\"") != std::string::npos,
            "Przykładowa seria spadkowa powinna wygenerować SHORT.");
    require(response.find("\"signal\":\"LONG\"") == std::string::npos,
            "Jedna analiza nie powinna jednocześnie zwracać LONG i SHORT.");
}

/**
 * Sprawdza bezpieczne odpowiedzi dla krótkich, płaskich i nieprawidłowych serii.
 */
void testGeneratorReturnsNoneForUnsafeInputs() {
    grok45::SignalGenerator generator;
    std::vector<grok45::Candle> candles =
        createMarketCandles(75, 0.000025);

    candles.resize(grok45::SignalGenerator::minimumCandles - 1);
    require(generator.generate(candles).find("\"signal\":\"NONE\"") !=
                std::string::npos,
            "Mniej niż 50 świec powinno zwrócić NONE.");

    candles.assign(grok45::SignalGenerator::minimumCandles, {});
    for (std::size_t index = 0; index < candles.size(); ++index) {
        candles[index] = grok45::Candle{
            static_cast<std::int64_t>(index) * 60,
            1.0, 1.0, 1.0, 1.0, 0.0};
    }
    const std::string flatResponse = generator.generate(candles);
    require(flatResponse.find("\"signal\":\"NONE\"") != std::string::npos,
            "Niezmienne ceny powinny zwrócić NONE.");

    candles = createMarketCandles(75, 0.000025);
    candles.back().timestamp = candles[candles.size() - 2].timestamp;
    require(generator.generate(candles).find("\"signal\":\"NONE\"") !=
                std::string::npos,
            "Niechronologiczne znaczniki czasu powinny zwrócić NONE.");
}

/**
 * Uruchamia testy wskaźników i scenariusze sygnałowe.
 */
void runAllTests() {
    testEmaUsesSmaSeedAndExpectedMultiplier();
    testRsiHandlesRisingFallingAndFlatPrices();
    testBollingerBandsUsePopulationDeviation();
    testAtrUsesCandleRangesAndAcceptsZeroVolume();
    testGeneratorProducesLongForBullishPullback();
    testGeneratorProducesShortForBearishPullback();
    testGeneratorReturnsNoneForUnsafeInputs();
}

} // namespace

/**
 * Uruchamia wszystkie testy i zwraca kod błędu, gdy któryś warunek nie przejdzie.
 */
int main() {
    try {
        runAllTests();
        std::cout << "Wszystkie testy Grok-45 zakończyły się powodzeniem.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Test Grok-45 nie powiódł się: " << error.what() << '\n';
        return 1;
    }
}
