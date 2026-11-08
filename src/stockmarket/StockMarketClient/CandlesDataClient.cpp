#include "CandlesDataClient.h"
#include "TwelveDataClient.h"

#include <cmath>
#include <cstdlib>
#include <string_view>
#include <utility>

namespace {

/**
 * Koduje symbol dla parametru URL, aby znaki symbolu nie zmieniały zapytania.
 */
bool encodeSymbolForUrl(
    const std::string& symbol, std::wstring& encodedSymbol) {
    constexpr char hexadecimalDigits[] = "0123456789ABCDEF";
    if (symbol.empty() || symbol.size() > 64) {
        return false;
    }

    encodedSymbol.clear();
    for (const unsigned char character : symbol) {
        if (character <= 0x20 || character == 0x7F) {
            encodedSymbol.clear();
            return false;
        }
        if ((character >= 'a' && character <= 'z') ||
            (character >= 'A' && character <= 'Z') ||
            (character >= '0' && character <= '9') ||
            character == '-' || character == '_' ||
            character == '.' || character == '~') {
            encodedSymbol.push_back(static_cast<wchar_t>(character));
        } else {
            encodedSymbol.push_back(L'%');
            encodedSymbol.push_back(
                static_cast<wchar_t>(hexadecimalDigits[character >> 4]));
            encodedSymbol.push_back(
                static_cast<wchar_t>(hexadecimalDigits[character & 0x0F]));
        }
    }
    return true;
}

/**
 * Odczytuje tekstowe pole JSON z ograniczonego fragmentu odpowiedzi.
 */
bool extractJsonString(
    std::string_view object, const char* fieldName, std::string& value) {
    const std::string key = "\"" + std::string(fieldName) + "\"";
    const std::size_t keyPosition = object.find(key);
    if (keyPosition == std::string_view::npos) {
        return false;
    }

    const std::size_t colonPosition = object.find(':', keyPosition + key.size());
    if (colonPosition == std::string_view::npos) {
        return false;
    }
    const std::size_t valueStart =
        object.find_first_not_of(" \t\r\n", colonPosition + 1);
    if (valueStart == std::string_view::npos || object[valueStart] != '"') {
        return false;
    }

    const std::size_t valueEnd = object.find('"', valueStart + 1);
    if (valueEnd == std::string_view::npos) {
        return false;
    }
    value.assign(object.substr(valueStart + 1, valueEnd - valueStart - 1));
    return !value.empty();
}

/**
 * Odczytuje opcjonalny wolumen zapisany jako tekst, liczba albo null.
 */
bool extractOptionalVolume(std::string_view object, std::string& volume) {
    const std::string key = "\"volume\"";
    const std::size_t keyPosition = object.find(key);
    if (keyPosition == std::string_view::npos) {
        return true;
    }

    const std::size_t colonPosition = object.find(':', keyPosition + key.size());
    if (colonPosition == std::string_view::npos) {
        return false;
    }
    const std::size_t valueStart =
        object.find_first_not_of(" \t\r\n", colonPosition + 1);
    if (valueStart == std::string_view::npos) {
        return false;
    }
    if (object.substr(valueStart, 4) == "null") {
        return true;
    }
    if (object[valueStart] == '"') {
        const std::size_t valueEnd = object.find('"', valueStart + 1);
        if (valueEnd == std::string_view::npos) {
            return false;
        }
        volume.assign(object.substr(valueStart + 1, valueEnd - valueStart - 1));
        return true;
    }

    const std::size_t valueEnd = object.find_first_of(",} \t\r\n", valueStart);
    if (valueEnd == std::string_view::npos || valueEnd == valueStart) {
        return false;
    }
    volume.assign(object.substr(valueStart, valueEnd - valueStart));
    return true;
}

/**
 * Odczytuje obiekt świecy i sprawdza poprawność jego wartości OHLC.
 */
bool extractCandleObject(std::string_view candleObject, CandleData& candle) {
    CandleData parsedCandle;
    if (!extractJsonString(candleObject, "datetime", parsedCandle.datetime) ||
        !extractJsonString(candleObject, "open", parsedCandle.open) ||
        !extractJsonString(candleObject, "high", parsedCandle.high) ||
        !extractJsonString(candleObject, "low", parsedCandle.low) ||
        !extractJsonString(candleObject, "close", parsedCandle.close)) {
        return false;
    }
    if (!extractOptionalVolume(candleObject, parsedCandle.volume)) {
        return false;
    }

    char* parsedEnd = nullptr;
    const double open = std::strtod(parsedCandle.open.c_str(), &parsedEnd);
    if (parsedEnd == parsedCandle.open.c_str() || *parsedEnd != '\0' ||
        !std::isfinite(open) || open <= 0.0) {
        return false;
    }
    const double high = std::strtod(parsedCandle.high.c_str(), &parsedEnd);
    if (parsedEnd == parsedCandle.high.c_str() || *parsedEnd != '\0' ||
        !std::isfinite(high) || high <= 0.0) {
        return false;
    }
    const double low = std::strtod(parsedCandle.low.c_str(), &parsedEnd);
    if (parsedEnd == parsedCandle.low.c_str() || *parsedEnd != '\0' ||
        !std::isfinite(low) || low <= 0.0) {
        return false;
    }
    const double close = std::strtod(parsedCandle.close.c_str(), &parsedEnd);
    if (parsedEnd == parsedCandle.close.c_str() || *parsedEnd != '\0' ||
        !std::isfinite(close) || close <= 0.0 ||
        high < open || high < close || low > open || low > close || low > high) {
        return false;
    }
    if (!parsedCandle.volume.empty()) {
        const double volume = std::strtod(parsedCandle.volume.c_str(), &parsedEnd);
        if (parsedEnd == parsedCandle.volume.c_str() || *parsedEnd != '\0' ||
            !std::isfinite(volume) || volume < 0.0) {
            return false;
        }
    }

    candle = std::move(parsedCandle);
    return true;
}

/**
 * Odczytuje tablicę świec z odpowiedzi Twelve Data.
 */
bool extractCandles(
    const std::string& response, std::vector<CandleData>& candles) {
    const std::string_view json(response);
    const std::size_t valuesPosition = json.find("\"values\"");
    const std::size_t arrayStart = valuesPosition == std::string_view::npos
        ? std::string_view::npos
        : json.find('[', valuesPosition);
    const std::size_t arrayEnd = arrayStart == std::string_view::npos
        ? std::string_view::npos
        : json.find(']', arrayStart);
    if (arrayStart == std::string_view::npos ||
        arrayEnd == std::string_view::npos) {
        return false;
    }

    std::vector<CandleData> parsedCandles;
    std::size_t objectStart = arrayStart;
    while ((objectStart = json.find('{', objectStart)) != std::string_view::npos &&
           objectStart < arrayEnd) {
        const std::size_t objectEnd = json.find('}', objectStart);
        if (objectEnd == std::string_view::npos || objectEnd > arrayEnd) {
            return false;
        }
        CandleData candle;
        if (!extractCandleObject(
                json.substr(objectStart, objectEnd - objectStart + 1), candle)) {
            return false;
        }
        parsedCandles.push_back(std::move(candle));
        objectStart = objectEnd + 1;
    }

    candles = std::move(parsedCandles);
    return true;
}

/**
 * Koduje znacznik czasu UTC, aby spacje i dwukropki nie zmieniały parametrów URL.
 */
bool encodeDatetimeForUrl(
    const std::string& datetime, std::wstring& encodedDatetime) {
    if (datetime.size() != 19 || datetime[10] != ' ') {
        return false;
    }
    encodedDatetime.clear();
    for (std::size_t index = 0; index < datetime.size(); ++index) {
        const char character = datetime[index];
        if (index == 10) {
            encodedDatetime += L"%20";
        } else if (character == ':') {
            encodedDatetime += L"%3A";
        } else if ((character >= '0' && character <= '9') ||
                   character == '-') {
            encodedDatetime.push_back(static_cast<wchar_t>(character));
        } else {
            encodedDatetime.clear();
            return false;
        }
    }
    return true;
}

/**
 * Pobiera i waliduje świece z jednego żądania Twelve Data.
 */
bool fetchOneMinuteCandles(
    const std::string& symbol,
    const std::string* startUtc,
    const std::string* endUtc,
    std::size_t maximumCandles,
    std::vector<CandleData>& candles,
    std::string& error) {
    std::wstring encodedSymbol;
    if (!encodeSymbolForUrl(symbol, encodedSymbol)) {
        error = "Symbol instrumentu jest pusty, za długi albo zawiera niedozwolone białe znaki.";
        return false;
    }
    if (maximumCandles == 0 || maximumCandles > 5000) {
        error = "Liczba świec do pobrania musi mieścić się w zakresie 1-5000.";
        return false;
    }

    std::wstring requestPath = L"/time_series?symbol=" + encodedSymbol +
        L"&interval=1min&outputsize=" +
        std::to_wstring(maximumCandles) + L"&timezone=UTC";
    if (startUtc != nullptr && endUtc != nullptr) {
        std::wstring encodedStart;
        std::wstring encodedEnd;
        if (!encodeDatetimeForUrl(*startUtc, encodedStart) ||
            !encodeDatetimeForUrl(*endUtc, encodedEnd)) {
            error = "Zakres czasu świec ma nieprawidłowy format.";
            return false;
        }
        requestPath += L"&start_date=" + encodedStart +
            L"&end_date=" + encodedEnd + L"&order=ASC";
    }
    requestPath += L"&apikey=";

    std::string response;
    if (!TwelveDataClient::fetchApiResponse(requestPath, response, error)) {
        return false;
    }
    if (!extractCandles(response, candles)) {
        error = "Twelve Data nie zwróciło prawidłowej listy świec dla symbolu " +
                symbol + ".";
        return false;
    }
    return true;
}

} // namespace

/**
 * Pobiera świecę 1-minutową dla dowolnego symbolu obsługiwanego przez Twelve Data.
 * Żądany czas jest UTC, aby identycznie zapisywać znaczniki czasu w bazie.
 */
bool CandlesDataClient::fetchLatestOneMinuteCandle(
    const std::string& symbol, CandleData& candle, std::string& error) {
    std::vector<CandleData> candles;
    if (!fetchLatestOneMinuteCandles(symbol, 1, candles, error)) {
        return false;
    }
    if (candles.empty()) {
        error = "Twelve Data nie zwróciło świec dla symbolu " + symbol + ".";
        return false;
    }
    candle = std::move(candles.front());
    return true;
}

/**
 * Pobiera maksymalną liczbę najnowszych świec 1-minutowych jednym żądaniem.
 */
bool CandlesDataClient::fetchLatestOneMinuteCandles(
    const std::string& symbol,
    std::size_t maximumCandles,
    std::vector<CandleData>& candles,
    std::string& error) {
    return fetchOneMinuteCandles(
        symbol, nullptr, nullptr, maximumCandles, candles, error);
}

/**
 * Pobiera świece z wybranego zakresu UTC jednym żądaniem.
 */
bool CandlesDataClient::fetchOneMinuteCandleRange(
    const std::string& symbol,
    const std::string& startUtc,
    const std::string& endUtc,
    std::size_t maximumCandles,
    std::vector<CandleData>& candles,
    std::string& error) {
    return fetchOneMinuteCandles(
        symbol, &startUtc, &endUtc, maximumCandles, candles, error);
}
