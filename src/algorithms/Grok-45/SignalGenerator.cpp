#include "SignalGenerator.h"

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <locale>
#include <sstream>

namespace grok45 {
namespace {

constexpr double bollingerDeviationMultiplier = 2.0;
constexpr double nearbyBandWidthFraction = 0.3;
constexpr double wickToBodyMultiplier = 1.5;
constexpr double wickToAtrFraction = 0.1;

/**
 * Sprawdza, czy wszystkie ceny w szeregu są skończonymi liczbami.
 */
bool areFinitePrices(const std::vector<double>& prices) {
    return std::all_of(prices.begin(), prices.end(), [](double price) {
        return std::isfinite(price);
    });
}

/**
 * Oblicza RSI z uśrednionych zmian ceny, uwzględniając przypadki brzegowe.
 */
double calculateRsiValue(double averageGain, double averageLoss) {
    if (averageLoss == 0.0) {
        return averageGain == 0.0 ? 50.0 : 100.0;
    }
    if (averageGain == 0.0) {
        return 0.0;
    }

    const double relativeStrength = averageGain / averageLoss;
    return 100.0 - 100.0 / (1.0 + relativeStrength);
}

/**
 * Sprawdza, czy świeca zawiera prawidłowe i skończone wartości OHLCV.
 */
bool isValidCandle(const Candle& candle) {
    return std::isfinite(candle.open) && std::isfinite(candle.high) &&
        std::isfinite(candle.low) && std::isfinite(candle.close) &&
        std::isfinite(candle.volume) && candle.open > 0.0 &&
        candle.high > 0.0 && candle.low > 0.0 && candle.close > 0.0 &&
        candle.volume >= 0.0 && candle.high >= candle.low &&
        candle.high >= std::max(candle.open, candle.close) &&
        candle.low <= std::min(candle.open, candle.close);
}

/**
 * Sprawdza poprawność wszystkich świec i ich kolejności czasowej.
 */
bool areValidCandles(const std::vector<Candle>& candles) {
    for (std::size_t index = 0; index < candles.size(); ++index) {
        if (!isValidCandle(candles[index])) {
            return false;
        }
        if (index > 0 &&
            candles[index].timestamp <= candles[index - 1].timestamp) {
            return false;
        }
    }
    return true;
}

/**
 * Sprawdza, czy analizowany szereg cen zamknięcia nie zmienił się ani razu.
 */
bool haveAllClosesStayedTheSame(const std::vector<Candle>& candles) {
    if (candles.empty()) {
        return false;
    }
    const double firstClose = candles.front().close;
    return std::all_of(candles.begin(), candles.end(), [firstClose](const Candle& candle) {
        return candle.close == firstClose;
    });
}

/**
 * Koduje tekst zgodnie z regułami napisów JSON.
 */
std::string escapeJsonString(const std::string& value) {
    std::ostringstream escaped;
    escaped << '"';
    for (const unsigned char character : value) {
        switch (character) {
        case '"':
            escaped << "\\\"";
            break;
        case '\\':
            escaped << "\\\\";
            break;
        case '\b':
            escaped << "\\b";
            break;
        case '\f':
            escaped << "\\f";
            break;
        case '\n':
            escaped << "\\n";
            break;
        case '\r':
            escaped << "\\r";
            break;
        case '\t':
            escaped << "\\t";
            break;
        default:
            if (character < 0x20) {
                constexpr char hexadecimalDigits[] = "0123456789abcdef";
                escaped << "\\u00"
                        << hexadecimalDigits[(character >> 4) & 0x0f]
                        << hexadecimalDigits[character & 0x0f];
            } else {
                escaped << static_cast<char>(character);
            }
        }
    }
    escaped << '"';
    return escaped.str();
}

/**
 * Zamienia skończoną liczbę na zapis JSON niezależny od ustawień regionalnych.
 */
std::string formatNumber(double value) {
    if (!std::isfinite(value)) {
        return "null";
    }

    std::ostringstream formatted;
    formatted.imbue(std::locale::classic());
    formatted << std::setprecision(12) << value;
    return formatted.str();
}

/**
 * Zamienia opcjonalną liczbę na liczbę JSON albo wartość null.
 */
std::string formatOptionalNumber(const std::optional<double>& value) {
    return value.has_value() ? formatNumber(*value) : "null";
}

/**
 * Zwraca maksymalną różnicę high-low z ostatnich świec.
 */
std::optional<double> calculateRecentRange(
    const std::vector<Candle>& candles, std::size_t period) {
    if (period == 0 || candles.size() < period) {
        return std::nullopt;
    }

    const std::size_t firstIndex = candles.size() - period;
    double highestPrice = candles[firstIndex].high;
    double lowestPrice = candles[firstIndex].low;
    for (std::size_t index = firstIndex + 1; index < candles.size(); ++index) {
        highestPrice = std::max(highestPrice, candles[index].high);
        lowestPrice = std::min(lowestPrice, candles[index].low);
    }
    return highestPrice - lowestPrice;
}

/**
 * Wykrywa przecięcie dwóch EMA w bieżącej lub jednej z dwóch poprzednich świec.
 */
bool crossedRecently(
    const std::vector<std::optional<double>>& fastEma,
    const std::vector<std::optional<double>>& slowEma,
    bool upward) {
    if (fastEma.size() != slowEma.size() || fastEma.size() < 2) {
        return false;
    }

    const std::size_t lastIndex = fastEma.size() - 1;
    for (std::size_t candlesAgo = 0; candlesAgo < 2; ++candlesAgo) {
        if (lastIndex <= candlesAgo) {
            continue;
        }
        const std::size_t currentIndex = lastIndex - candlesAgo;
        const std::size_t previousIndex = currentIndex - 1;
        if (!fastEma[currentIndex] || !slowEma[currentIndex] ||
            !fastEma[previousIndex] || !slowEma[previousIndex]) {
            continue;
        }

        if (upward &&
            *fastEma[previousIndex] <= *slowEma[previousIndex] &&
            *fastEma[currentIndex] > *slowEma[currentIndex]) {
            return true;
        }
        if (!upward &&
            *fastEma[previousIndex] >= *slowEma[previousIndex] &&
            *fastEma[currentIndex] < *slowEma[currentIndex]) {
            return true;
        }
    }
    return false;
}

/**
 * Oblicza, czy ostatnia świeca ma dolny cień wyraźny w stosunku do korpusu.
 */
bool hasClearLowerWick(const Candle& candle, double atr) {
    const double bodySize = std::abs(candle.close - candle.open);
    const double lowerWickSize = std::min(candle.open, candle.close) - candle.low;
    const double minimumWickSize =
        std::max(bodySize * wickToBodyMultiplier, atr * wickToAtrFraction);
    return lowerWickSize >= minimumWickSize;
}

/**
 * Oblicza, czy ostatnia świeca ma górny cień wyraźny w stosunku do korpusu.
 */
bool hasClearUpperWick(const Candle& candle, double atr) {
    const double bodySize = std::abs(candle.close - candle.open);
    const double upperWickSize = candle.high - std::max(candle.open, candle.close);
    const double minimumWickSize =
        std::max(bodySize * wickToBodyMultiplier, atr * wickToAtrFraction);
    return upperWickSize >= minimumWickSize;
}

/**
 * Buduje odpowiedź JSON z aktualnymi wskaźnikami i uzasadnieniem sygnału.
 */
std::string createJsonResponse(
    const std::vector<Candle>& candles,
    const std::string& signal,
    const std::string& reason,
    const std::optional<double>& ema9,
    const std::optional<double>& ema21,
    const std::optional<double>& rsi14,
    const std::optional<BollingerBands>& bands,
    const std::optional<double>& atr14) {
    const bool hasLatestCandle = !candles.empty();
    const Candle& latestCandle =
        hasLatestCandle ? candles.back() : Candle{};
    const std::optional<double> upperBand =
        bands ? std::optional<double>(bands->upper) : std::nullopt;
    const std::optional<double> middleBand =
        bands ? std::optional<double>(bands->middle) : std::nullopt;
    const std::optional<double> lowerBand =
        bands ? std::optional<double>(bands->lower) : std::nullopt;

    std::ostringstream json;
    json.imbue(std::locale::classic());
    json << "{\"instrument\":\"EUR/USD\",\"price\":"
         << (hasLatestCandle ? formatNumber(latestCandle.close) : "null")
         << ",\"timestamp\":";
    if (hasLatestCandle) {
        json << latestCandle.timestamp;
    } else {
        json << "null";
    }
    json << ",\"signal\":" << escapeJsonString(signal)
         << ",\"parameters\":{\"ema9\":" << formatOptionalNumber(ema9)
         << ",\"ema21\":" << formatOptionalNumber(ema21)
         << ",\"rsi14\":" << formatOptionalNumber(rsi14)
         << ",\"bb_upper\":" << formatOptionalNumber(upperBand)
         << ",\"bb_middle\":" << formatOptionalNumber(middleBand)
         << ",\"bb_lower\":" << formatOptionalNumber(lowerBand)
         << ",\"atr14\":" << formatOptionalNumber(atr14)
         << ",\"reason\":" << escapeJsonString(reason) << "}}";
    return json.str();
}

} // namespace

/**
 * Oblicza EMA z inicjalizacją jako średnia krocząca prostego okresu.
 */
std::vector<std::optional<double>> IndicatorCalculator::calculateEmaSeries(
    const std::vector<double>& prices, std::size_t period) {
    std::vector<std::optional<double>> values(prices.size());
    if (period == 0 || prices.size() < period || !areFinitePrices(prices)) {
        return values;
    }

    double initialAverage = 0.0;
    for (std::size_t index = 0; index < period; ++index) {
        initialAverage += prices[index] / static_cast<double>(period);
    }
    values[period - 1] = initialAverage;

    const double smoothingFactor = 2.0 / (static_cast<double>(period) + 1.0);
    for (std::size_t index = period; index < prices.size(); ++index) {
        const double previousValue = *values[index - 1];
        values[index] =
            (prices[index] - previousValue) * smoothingFactor + previousValue;
    }
    return values;
}

/**
 * Zwraca ostatnią EMA obliczoną przez calculateEmaSeries.
 */
std::optional<double> IndicatorCalculator::calculateEma(
    const std::vector<double>& prices, std::size_t period) {
    const std::vector<std::optional<double>> values =
        calculateEmaSeries(prices, period);
    return values.empty() ? std::nullopt : values.back();
}

/**
 * Oblicza serię RSI metodą wygładzania Wildera.
 */
std::vector<std::optional<double>> IndicatorCalculator::calculateRsiSeries(
    const std::vector<double>& prices, std::size_t period) {
    std::vector<std::optional<double>> values(prices.size());
    if (period == 0 || prices.size() <= period || !areFinitePrices(prices)) {
        return values;
    }

    double averageGain = 0.0;
    double averageLoss = 0.0;
    for (std::size_t index = 1; index <= period; ++index) {
        const double change = prices[index] - prices[index - 1];
        averageGain += std::max(change, 0.0) / static_cast<double>(period);
        averageLoss += std::max(-change, 0.0) / static_cast<double>(period);
    }
    values[period] = calculateRsiValue(averageGain, averageLoss);

    for (std::size_t index = period + 1; index < prices.size(); ++index) {
        const double change = prices[index] - prices[index - 1];
        const double gain = std::max(change, 0.0);
        const double loss = std::max(-change, 0.0);
        averageGain =
            (averageGain * static_cast<double>(period - 1) + gain) /
            static_cast<double>(period);
        averageLoss =
            (averageLoss * static_cast<double>(period - 1) + loss) /
            static_cast<double>(period);
        values[index] = calculateRsiValue(averageGain, averageLoss);
    }
    return values;
}

/**
 * Zwraca ostatni RSI obliczony przez calculateRsiSeries.
 */
std::optional<double> IndicatorCalculator::calculateRsi(
    const std::vector<double>& prices, std::size_t period) {
    const std::vector<std::optional<double>> values =
        calculateRsiSeries(prices, period);
    return values.empty() ? std::nullopt : values.back();
}

/**
 * Oblicza średnią oraz odchylenie standardowe ostatniego okna cen.
 */
std::optional<BollingerBands>
IndicatorCalculator::calculateBollingerBands(
    const std::vector<double>& prices,
    std::size_t period,
    double standardDeviationMultiplier) {
    if (period == 0 || prices.size() < period ||
        !std::isfinite(standardDeviationMultiplier) ||
        standardDeviationMultiplier < 0.0 || !areFinitePrices(prices)) {
        return std::nullopt;
    }

    const std::size_t firstIndex = prices.size() - period;
    double average = 0.0;
    for (std::size_t index = firstIndex; index < prices.size(); ++index) {
        average += prices[index] / static_cast<double>(period);
    }

    double variance = 0.0;
    for (std::size_t index = firstIndex; index < prices.size(); ++index) {
        const double difference = prices[index] - average;
        variance += difference * difference / static_cast<double>(period);
    }

    const double distance = standardDeviationMultiplier * std::sqrt(variance);
    return BollingerBands{average + distance, average, average - distance};
}

/**
 * Oblicza zakresy prawdziwe oraz ich wygładzoną średnią ATR Wildera.
 */
std::optional<double> IndicatorCalculator::calculateAtr(
    const std::vector<Candle>& candles, std::size_t period) {
    if (period == 0 || candles.size() < period) {
        return std::nullopt;
    }
    if (!std::all_of(candles.begin(), candles.end(), isValidCandle)) {
        return std::nullopt;
    }

    double averageTrueRange = 0.0;
    for (std::size_t index = 0; index < period; ++index) {
        const double trueRange = index == 0
            ? candles[index].high - candles[index].low
            : std::max({
                  candles[index].high - candles[index].low,
                  std::abs(candles[index].high - candles[index - 1].close),
                  std::abs(candles[index].low - candles[index - 1].close)});
        if (!std::isfinite(trueRange) || trueRange < 0.0) {
            return std::nullopt;
        }
        averageTrueRange += trueRange / static_cast<double>(period);
    }

    for (std::size_t index = period; index < candles.size(); ++index) {
        const double trueRange = std::max({
            candles[index].high - candles[index].low,
            std::abs(candles[index].high - candles[index - 1].close),
            std::abs(candles[index].low - candles[index - 1].close)});
        if (!std::isfinite(trueRange) || trueRange < 0.0) {
            return std::nullopt;
        }
        averageTrueRange =
            (averageTrueRange * static_cast<double>(period - 1) + trueRange) /
            static_cast<double>(period);
    }
    return averageTrueRange;
}

/**
 * Waliduje dane, oblicza wskaźniki i stosuje podane warunki LONG/SHORT.
 */
std::string SignalGenerator::generate(
    const std::vector<Candle>& candles) const {
    if (candles.empty()) {
        return createJsonResponse(
            candles, "NONE", "Brak świec do analizy.",
            std::nullopt, std::nullopt, std::nullopt, std::nullopt, std::nullopt);
    }
    const std::size_t analyzedCandleCount =
        std::min(candles.size(), analysisWindowCandles);
    const std::vector<Candle> analyzedCandles(
        candles.end() - static_cast<std::ptrdiff_t>(analyzedCandleCount),
        candles.end());
    if (!areValidCandles(analyzedCandles)) {
        return createJsonResponse(
            analyzedCandles, "NONE",
            "Dane świec są nieprawidłowe albo nie są ułożone chronologicznie.",
            std::nullopt, std::nullopt, std::nullopt, std::nullopt, std::nullopt);
    }

    std::vector<double> closingPrices;
    closingPrices.reserve(analyzedCandles.size());
    for (const Candle& candle : analyzedCandles) {
        closingPrices.push_back(candle.close);
    }

    const std::optional<double> ema9 =
        IndicatorCalculator::calculateEma(closingPrices, emaShortPeriod);
    const std::optional<double> ema21 =
        IndicatorCalculator::calculateEma(closingPrices, emaLongPeriod);
    const std::optional<double> rsi14 =
        IndicatorCalculator::calculateRsi(closingPrices, rsiPeriod);
    const std::optional<BollingerBands> bands =
        IndicatorCalculator::calculateBollingerBands(
            closingPrices, bollingerPeriod, bollingerDeviationMultiplier);
    const std::optional<double> atr14 =
        IndicatorCalculator::calculateAtr(analyzedCandles, atrPeriod);

    if (analyzedCandles.size() < minimumCandles) {
        return createJsonResponse(
            analyzedCandles, "NONE",
            "Za mało danych: wymagane są co najmniej 50 zamkniętych świec.",
            ema9, ema21, rsi14, bands, atr14);
    }
    if (haveAllClosesStayedTheSame(analyzedCandles)) {
        return createJsonResponse(
            analyzedCandles, "NONE",
            "Ceny zamknięcia nie zmieniły się w analizowanym okresie.",
            ema9, ema21, rsi14, bands, atr14);
    }
    if (!ema9 || !ema21 || !rsi14 || !bands || !atr14) {
        return createJsonResponse(
            analyzedCandles, "NONE",
            "Nie można obliczyć wszystkich wymaganych wskaźników.",
            ema9, ema21, rsi14, bands, atr14);
    }

    const std::vector<std::optional<double>> ema9Series =
        IndicatorCalculator::calculateEmaSeries(closingPrices, emaShortPeriod);
    const std::vector<std::optional<double>> ema21Series =
        IndicatorCalculator::calculateEmaSeries(closingPrices, emaLongPeriod);
    const std::vector<std::optional<double>> rsiSeries =
        IndicatorCalculator::calculateRsiSeries(closingPrices, rsiPeriod);
    if (rsiSeries.size() < 2 || !rsiSeries[rsiSeries.size() - 2]) {
        return createJsonResponse(
            analyzedCandles, "NONE", "Brak poprzedniej wartości RSI do porównania.",
            ema9, ema21, rsi14, bands, atr14);
    }

    const Candle& latestCandle = analyzedCandles.back();
    const double bandWidth = bands->upper - bands->lower;
    const bool closeBelowMiddleOrNearLower =
        latestCandle.close <= bands->middle ||
        std::abs(latestCandle.close - bands->lower) <=
            nearbyBandWidthFraction * bandWidth;
    const bool closeAboveMiddleOrNearUpper =
        latestCandle.close >= bands->middle ||
        std::abs(latestCandle.close - bands->upper) <=
            nearbyBandWidthFraction * bandWidth;
    const std::optional<double> recentRange =
        calculateRecentRange(analyzedCandles, volatilityRangeCandles);
    const bool volatilityIsSufficient =
        *atr14 > minimumAtr ||
        (recentRange && *recentRange > minimumAtr);

    const bool longTrend =
        *ema9 > *ema21 || crossedRecently(ema9Series, ema21Series, true);
    const bool shortTrend =
        *ema9 < *ema21 || crossedRecently(ema9Series, ema21Series, false);
    const bool rsiIsRising = *rsiSeries.back() > *rsiSeries[rsiSeries.size() - 2];
    const bool rsiIsFalling = *rsiSeries.back() < *rsiSeries[rsiSeries.size() - 2];
    const bool longRsiCondition = *rsi14 > 40.0 && rsiIsRising && *rsi14 <= 70.0;
    const bool shortRsiCondition = *rsi14 < 60.0 && rsiIsFalling && *rsi14 >= 30.0;
    const bool bullishCandle =
        latestCandle.close > latestCandle.open ||
        hasClearLowerWick(latestCandle, *atr14);
    const bool bearishCandle =
        latestCandle.close < latestCandle.open ||
        hasClearUpperWick(latestCandle, *atr14);

    if (volatilityIsSufficient && longTrend && longRsiCondition &&
        closeBelowMiddleOrNearLower && bullishCandle) {
        return createJsonResponse(
            analyzedCandles, "LONG",
            "Warunki trendu EMA, rosnącego RSI, wstęg Bollingera, świecy i zmienności są spełnione.",
            ema9, ema21, rsi14, bands, atr14);
    }
    if (volatilityIsSufficient && shortTrend && shortRsiCondition &&
        closeAboveMiddleOrNearUpper && bearishCandle) {
        return createJsonResponse(
            analyzedCandles, "SHORT",
            "Warunki trendu EMA, spadającego RSI, wstęg Bollingera, świecy i zmienności są spełnione.",
            ema9, ema21, rsi14, bands, atr14);
    }

    std::string reason = "Warunki LONG ani SHORT nie zostały spełnione.";
    if (!volatilityIsSufficient) {
        reason = "Zmienność jest niższa od wymaganego progu.";
    }
    return createJsonResponse(
        analyzedCandles, "NONE", reason, ema9, ema21, rsi14, bands, atr14);
}

} // namespace grok45
