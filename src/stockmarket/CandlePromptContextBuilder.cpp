#include "CandlePromptContextBuilder.h"

#include "CandleHistoryClient.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <charconv>
#include <cmath>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <limits>
#include <map>
#include <sstream>
#include <string_view>
#include <system_error>
#include <utility>

namespace {

constexpr std::size_t maximumHistorySize = 1000;
constexpr std::size_t minuteCandlesToReturn = 50;
constexpr std::size_t fiveMinuteCandlesToReturn = 30;
constexpr std::int64_t secondsPerMinute = 60;
constexpr std::int64_t secondsPerFiveMinutes = 5 * secondsPerMinute;
constexpr int maximumPricePrecision = 10;
constexpr char warningLine[] =
    "[warning] wykryto luke w swiecy podczas wysyłania prompta</br>";

struct FiveMinuteCandle {
    std::int64_t timestampUtc = 0;
    std::string open;
    std::string high;
    std::string low;
    std::string close;
};

struct PriceRange {
    long double minimum = std::numeric_limits<long double>::max();
    int precision = 0;
};

/**
 * Dzieli wiersz TSV na dokładną liczbę pól, bez kopiowania tekstu.
 */
bool splitTsvRow(
    std::string_view row,
    std::array<std::string_view, 6>& fields) {
    std::size_t fieldStart = 0;
    for (std::size_t fieldIndex = 0; fieldIndex < fields.size(); ++fieldIndex) {
        const std::size_t delimiter = row.find('\t', fieldStart);
        if (fieldIndex + 1 == fields.size()) {
            if (delimiter != std::string_view::npos) {
                return false;
            }
            fields[fieldIndex] = row.substr(fieldStart);
        } else {
            if (delimiter == std::string_view::npos) {
                return false;
            }
            fields[fieldIndex] = row.substr(fieldStart, delimiter - fieldStart);
            fieldStart = delimiter + 1;
        }
    }
    return true;
}

/**
 * Odczytuje dodatnią cenę dziesiętną bez separatorów regionalnych.
 */
bool parsePrice(std::string_view text, long double& value) {
    if (text.empty()) {
        return false;
    }

    bool hasDigit = false;
    bool hasDecimalPoint = false;
    int precision = 0;
    for (const char character : text) {
        if (character >= '0' && character <= '9') {
            hasDigit = true;
            if (hasDecimalPoint) {
                ++precision;
            }
        } else if (character == '.' && !hasDecimalPoint) {
            hasDecimalPoint = true;
        } else {
            return false;
        }
    }
    if (!hasDigit || precision > maximumPricePrecision) {
        return false;
    }

    std::string terminated(text);
    char* parsedEnd = nullptr;
    errno = 0;
    value = std::strtold(terminated.c_str(), &parsedEnd);
    return errno != ERANGE &&
        parsedEnd == terminated.c_str() + terminated.size() &&
        std::isfinite(value) && value >= 0.0L;
}

/**
 * Odczytuje znacznik czasu Unix bez akceptowania częściowych wartości.
 */
bool parseTimestamp(std::string_view text, std::int64_t& timestamp) {
    if (text.empty()) {
        return false;
    }
    const char* const first = text.data();
    const char* const last = first + text.size();
    const auto result = std::from_chars(first, last, timestamp);
    return result.ec == std::errc{} && result.ptr == last &&
        timestamp >= 0 && timestamp % secondsPerMinute == 0;
}

/**
 * Parsuje odpowiedź API i odrzuca puste, powtórzone lub nieuporządkowane wiersze.
 */
bool parseHistoryTsv(
    const std::string& response,
    std::vector<CandlePromptRow>& history,
    std::string& error) {
    constexpr std::string_view expectedHeader =
        "timestamp_utc\topen\thigh\tlow\tclose\tgap_before";
    history.clear();
    const std::size_t headerEnd = response.find('\n');
    if (headerEnd == std::string::npos ||
        std::string_view(response).substr(0, headerEnd) != expectedHeader) {
        error = "Lokalne API zwróciło nieprawidłowy nagłówek historii świec.";
        return false;
    }

    std::size_t lineStart = headerEnd + 1;
    while (lineStart < response.size()) {
        const std::size_t lineEnd = response.find('\n', lineStart);
        const std::size_t effectiveEnd =
            lineEnd == std::string::npos ? response.size() : lineEnd;
        std::string_view row(
            response.data() + lineStart, effectiveEnd - lineStart);
        if (!row.empty() && row.back() == '\r') {
            row.remove_suffix(1);
        }
        if (!row.empty()) {
            std::array<std::string_view, 6> fields;
            CandlePromptRow candle;
            long double parsedPrice = 0.0L;
            if (history.size() >= maximumHistorySize ||
                !splitTsvRow(row, fields) ||
                !parseTimestamp(fields[0], candle.timestampUtc) ||
                !parsePrice(fields[1], parsedPrice) ||
                !parsePrice(fields[2], parsedPrice) ||
                !parsePrice(fields[3], parsedPrice) ||
                !parsePrice(fields[4], parsedPrice) ||
                (fields[5] != "0" && fields[5] != "1")) {
                error = "Lokalne API zwróciło nieprawidłowe dane świecy.";
                return false;
            }
            if (!history.empty() &&
                candle.timestampUtc <= history.back().timestampUtc) {
                error = "Świece z lokalnego API nie są uporządkowane rosnąco.";
                return false;
            }
            candle.open = fields[1];
            candle.high = fields[2];
            candle.low = fields[3];
            candle.close = fields[4];
            candle.gapBefore = fields[5] == "1";
            history.push_back(std::move(candle));
        }
        if (lineEnd == std::string::npos) {
            break;
        }
        lineStart = lineEnd + 1;
    }
    return true;
}

/**
 * Zwraca maksymalną liczbę cyfr po przecinku w cenie.
 */
int decimalPrecision(const std::string& price) {
    const std::size_t decimalPoint = price.find('.');
    return decimalPoint == std::string::npos
        ? 0
        : static_cast<int>(price.size() - decimalPoint - 1);
}

/**
 * Dodaje cenę do zakresu, który wyznacza wspólną bazę i tick_size.
 */
bool includePrice(const std::string& text, PriceRange& range) {
    long double value = 0.0L;
    if (!parsePrice(text, value)) {
        return false;
    }
    range.minimum = std::min(range.minimum, value);
    range.precision = std::max(range.precision, decimalPrecision(text));
    return true;
}

/**
 * Zapisuje liczbę dziesiętną w formacie JSON bez notacji wykładniczej.
 */
std::string formatJsonNumber(long double value, int precision) {
    std::ostringstream formatted;
    formatted << std::fixed << std::setprecision(std::max(1, precision)) << value;
    std::string result = formatted.str();
    while (result.size() > 2 && result.back() == '0') {
        result.pop_back();
    }
    if (!result.empty() && result.back() == '.') {
        result.push_back('0');
    }
    return result;
}

/**
 * Chroni symbol przed znakami mającymi znaczenie w łańcuchu JSON.
 */
std::string escapeJsonString(const std::string& value) {
    std::string escaped;
    escaped.reserve(value.size());
    for (const unsigned char character : value) {
        switch (character) {
            case '"':
                escaped += "\\\"";
                break;
            case '\\':
                escaped += "\\\\";
                break;
            case '\n':
                escaped += "\\n";
                break;
            case '\r':
                escaped += "\\r";
                break;
            case '\t':
                escaped += "\\t";
                break;
            default:
                if (character < 0x20) {
                    escaped += '?';
                } else {
                    escaped += static_cast<char>(character);
                }
                break;
        }
    }
    return escaped;
}

/**
 * Zamienia znacznik czasu Unix na datę ISO 8601 UTC.
 */
bool formatUtcTimestamp(std::int64_t timestamp, std::string& formatted) {
    const std::time_t unixTime = static_cast<std::time_t>(timestamp);
    std::tm utcTime{};
#ifdef _WIN32
    if (gmtime_s(&utcTime, &unixTime) != 0) {
        return false;
    }
#else
    if (gmtime_r(&unixTime, &utcTime) == nullptr) {
        return false;
    }
#endif

    std::array<char, 21> buffer{};
    if (std::strftime(
            buffer.data(), buffer.size(), "%Y-%m-%dT%H:%M:%SZ", &utcTime) == 0) {
        return false;
    }
    formatted = buffer.data();
    return true;
}

/**
 * Agreguje dokładnie pięć kolejnych świec M1 należących do jednego koszyka UTC.
 */
std::vector<FiveMinuteCandle> aggregateFiveMinuteCandles(
    const std::vector<CandlePromptRow>& history) {
    std::map<std::int64_t, std::vector<const CandlePromptRow*>> buckets;
    for (const CandlePromptRow& candle : history) {
        const std::int64_t bucketStart =
            candle.timestampUtc -
            (candle.timestampUtc % secondsPerFiveMinutes);
        buckets[bucketStart].push_back(&candle);
    }

    std::vector<FiveMinuteCandle> aggregated;
    for (const auto& [bucketStart, rows] : buckets) {
        if (rows.size() != 5) {
            continue;
        }
        bool isComplete = true;
        for (std::size_t index = 0; index < rows.size(); ++index) {
            if (rows[index]->timestampUtc !=
                bucketStart + static_cast<std::int64_t>(index) *
                    secondsPerMinute) {
                isComplete = false;
                break;
            }
        }
        if (!isComplete) {
            continue;
        }

        FiveMinuteCandle candle;
        candle.timestampUtc = bucketStart;
        candle.open = rows.front()->open;
        candle.high = rows.front()->high;
        candle.low = rows.front()->low;
        candle.close = rows.back()->close;
        long double highestPrice = 0.0L;
        long double lowestPrice = 0.0L;
        parsePrice(candle.high, highestPrice);
        parsePrice(candle.low, lowestPrice);
        for (const CandlePromptRow* row : rows) {
            long double high = 0.0L;
            long double low = 0.0L;
            parsePrice(row->high, high);
            parsePrice(row->low, low);
            if (high > highestPrice) {
                highestPrice = high;
                candle.high = row->high;
            }
            if (low < lowestPrice) {
                lowestPrice = low;
                candle.low = row->low;
            }
        }
        aggregated.push_back(std::move(candle));
    }
    return aggregated;
}

/**
 * Znajduje log projektu; jawna ścieżka środowiskowa ma pierwszeństwo.
 */
std::filesystem::path defaultWarningLogPath() {
    if (const char* configuredPath =
            std::getenv("PROMPT_CANDLE_WARNING_LOG_PATH");
        configuredPath != nullptr && configuredPath[0] != '\0') {
        return std::filesystem::path(configuredPath);
    }

    std::error_code fileSystemError;
    std::filesystem::path directory =
        std::filesystem::current_path(fileSystemError);
    if (fileSystemError) {
        return std::filesystem::path("LogError-11.2026");
    }

    while (!directory.empty()) {
        if (std::filesystem::exists(directory / "CMakeLists.txt", fileSystemError) &&
            !fileSystemError) {
            return directory / "LogError-11.2026";
        }
        fileSystemError.clear();
        const std::filesystem::path parent = directory.parent_path();
        if (parent == directory) {
            break;
        }
        directory = parent;
    }
    return std::filesystem::current_path() / "LogError-11.2026";
}

/**
 * Dopisuje pojedyncze ostrzeżenie o luce, nie zastępując wcześniejszych wpisów.
 */
bool appendGapWarning(
    const std::filesystem::path& path,
    std::string& error) {
    std::ofstream logFile(path, std::ios::binary | std::ios::app);
    if (!logFile.is_open()) {
        error = "Nie można otworzyć pliku logu luk świec: " + path.string();
        return false;
    }
    logFile << warningLine << '\n';
    if (!logFile) {
        error = "Nie udało się dopisać ostrzeżenia do pliku logu luk świec.";
        return false;
    }
    return true;
}

/**
 * Zamienia cenę na całkowitą liczbę ticków względem wspólnej bazy.
 */
bool toTicks(
    const std::string& text,
    long double basePrice,
    long double tickScale,
    std::int64_t& ticks) {
    long double price = 0.0L;
    if (!parsePrice(text, price)) {
        return false;
    }
    const long double tickValue = std::round((price - basePrice) * tickScale);
    if (tickValue < 0.0L ||
        tickValue > static_cast<long double>(
            std::numeric_limits<std::int64_t>::max())) {
        return false;
    }
    ticks = static_cast<std::int64_t>(tickValue);
    return true;
}

/**
 * Dołącza uporządkowane wiersze OHLC w postaci całkowitych ticków.
 */
template <typename Candle>
bool appendTickRows(
    std::string& json,
    const std::vector<Candle>& candles,
    long double basePrice,
    long double tickScale,
    std::string& error) {
    json.push_back('[');
    for (std::size_t index = 0; index < candles.size(); ++index) {
        std::int64_t ticks[4]{};
        const Candle& candle = candles[index];
        if (!toTicks(candle.open, basePrice, tickScale, ticks[0]) ||
            !toTicks(candle.high, basePrice, tickScale, ticks[1]) ||
            !toTicks(candle.low, basePrice, tickScale, ticks[2]) ||
            !toTicks(candle.close, basePrice, tickScale, ticks[3])) {
            error = "Nie można bezstratnie przeliczyć ceny świecy na ticki.";
            return false;
        }
        if (index > 0) {
            json.push_back(',');
        }
        json += "[" + std::to_string(ticks[0]) + "," +
            std::to_string(ticks[1]) + "," +
            std::to_string(ticks[2]) + "," +
            std::to_string(ticks[3]) + "]";
    }
    json.push_back(']');
    return true;
}

} // namespace

/**
 * Pobiera historię M1 przez lokalne API i przekazuje ją do budowniczego JSON.
 */
bool CandlePromptContextBuilder::fetchJson(
    const std::string& symbol,
    std::string& json,
    std::string& error) {
    json.clear();
    std::string response;
    if (!CandleHistoryClient::fetchPromptCandleHistory(
            symbol, maximumHistorySize, response, error)) {
        return false;
    }

    std::vector<CandlePromptRow> history;
    if (!parseHistoryTsv(response, history, error)) {
        return false;
    }
    return buildJson(symbol, history, json, error);
}

/**
 * Waliduje wejście, agreguje świece M5 i zwraca skompresowany JSON.
 */
bool CandlePromptContextBuilder::buildJson(
    const std::string& symbol,
    const std::vector<CandlePromptRow>& history,
    std::string& json,
    std::string& error,
    const std::filesystem::path& warningLogPath) {
    json.clear();
    if (symbol.empty() || symbol.size() > 32 ||
        history.size() > maximumHistorySize) {
        error = "Symbol lub liczba świec do kontekstu prompta jest nieprawidłowa.";
        return false;
    }

    std::int64_t previousTimestamp = -1;
    for (const CandlePromptRow& candle : history) {
        long double parsedPrice = 0.0L;
        if (candle.timestampUtc < 0 ||
            candle.timestampUtc % secondsPerMinute != 0 ||
            candle.timestampUtc <= previousTimestamp ||
            !parsePrice(candle.open, parsedPrice) ||
            !parsePrice(candle.high, parsedPrice) ||
            !parsePrice(candle.low, parsedPrice) ||
            !parsePrice(candle.close, parsedPrice)) {
            error = "Historia świec zawiera nieprawidłowe ceny lub czasy UTC.";
            return false;
        }
        previousTimestamp = candle.timestampUtc;
    }
    if (history.size() < minuteCandlesToReturn) {
        error = "Do kontekstu prompta potrzeba co najmniej 50 zamkniętych świec M1.";
        return false;
    }

    const std::vector<CandlePromptRow> minuteCandles(
        history.end() - static_cast<std::ptrdiff_t>(minuteCandlesToReturn),
        history.end());
    std::vector<FiveMinuteCandle> allFiveMinuteCandles =
        aggregateFiveMinuteCandles(history);

    const std::int64_t gapSearchStart = allFiveMinuteCandles.size() >=
            fiveMinuteCandlesToReturn
        ? allFiveMinuteCandles[
              allFiveMinuteCandles.size() - fiveMinuteCandlesToReturn]
              .timestampUtc
        : minuteCandles.front().timestampUtc;
    const std::int64_t gapSearchEnd = minuteCandles.back().timestampUtc;
    const bool relevantGap = std::any_of(
        history.begin(), history.end(),
        [gapSearchStart, gapSearchEnd](const CandlePromptRow& candle) {
            return candle.gapBefore &&
                candle.timestampUtc >= gapSearchStart &&
                candle.timestampUtc <= gapSearchEnd;
        });
    if (relevantGap) {
        const std::filesystem::path logPath = warningLogPath.empty()
            ? defaultWarningLogPath()
            : warningLogPath;
        if (!appendGapWarning(logPath, error)) {
            return false;
        }
    }

    if (allFiveMinuteCandles.size() < fiveMinuteCandlesToReturn) {
        error = "Do kontekstu prompta potrzeba 30 pełnych świec M5.";
        return false;
    }
    std::vector<FiveMinuteCandle> fiveMinuteCandles(
        allFiveMinuteCandles.end() -
            static_cast<std::ptrdiff_t>(fiveMinuteCandlesToReturn),
        allFiveMinuteCandles.end());

    PriceRange priceRange;
    for (const CandlePromptRow& candle : minuteCandles) {
        if (!includePrice(candle.open, priceRange) ||
            !includePrice(candle.high, priceRange) ||
            !includePrice(candle.low, priceRange) ||
            !includePrice(candle.close, priceRange)) {
            error = "Cena świecy M1 nie ma prawidłowego formatu dziesiętnego.";
            return false;
        }
    }
    for (const FiveMinuteCandle& candle : fiveMinuteCandles) {
        if (!includePrice(candle.open, priceRange) ||
            !includePrice(candle.high, priceRange) ||
            !includePrice(candle.low, priceRange) ||
            !includePrice(candle.close, priceRange)) {
            error = "Cena świecy M5 nie ma prawidłowego formatu dziesiętnego.";
            return false;
        }
    }

    const long double basePrice = std::floor(priceRange.minimum);
    const long double tickScale = std::pow(
        10.0L, static_cast<long double>(priceRange.precision));
    const int tickPrecision = std::max(1, priceRange.precision);
    std::string minuteStart;
    std::string fiveMinuteStart;
    if (!formatUtcTimestamp(minuteCandles.front().timestampUtc, minuteStart) ||
        !formatUtcTimestamp(
            fiveMinuteCandles.front().timestampUtc, fiveMinuteStart)) {
        error = "Nie można sformatować czasu świecy jako UTC.";
        return false;
    }

    json = "{\"synthetic\":false,\"symbol\":\"" +
        escapeJsonString(symbol) + "\",\"price_base\":" +
        formatJsonNumber(basePrice, 1) + ",\"tick_size\":" +
        formatJsonNumber(1.0L / tickScale, tickPrecision) +
        ",\"row_format\":\"[open,high,low,close]\",\"M1\":{\"start_utc\":\"" +
        minuteStart + "\",\"step_minutes\":1,\"ohlc_ticks\":";
    if (!appendTickRows(
            json, minuteCandles, basePrice, tickScale, error)) {
        json.clear();
        return false;
    }
    json += "},\"M5\":{\"start_utc\":\"" + fiveMinuteStart +
        "\",\"step_minutes\":5,\"ohlc_ticks\":";
    if (!appendTickRows(
            json, fiveMinuteCandles, basePrice, tickScale, error)) {
        json.clear();
        return false;
    }
    json += "}}";
    return true;
}
