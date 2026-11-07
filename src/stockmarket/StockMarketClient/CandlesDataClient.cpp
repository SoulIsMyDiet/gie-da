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
 * Odczytuje pierwszą świecę z odpowiedzi i sprawdza poprawność jej wartości OHLC.
 */
bool extractCandle(const std::string& response, CandleData& candle) {
    const std::string_view json(response);
    const std::size_t valuesPosition = json.find("\"values\"");
    const std::size_t arrayStart = valuesPosition == std::string_view::npos
        ? std::string_view::npos
        : json.find('[', valuesPosition);
    const std::size_t objectStart = arrayStart == std::string_view::npos
        ? std::string_view::npos
        : json.find('{', arrayStart);
    const std::size_t objectEnd = objectStart == std::string_view::npos
        ? std::string_view::npos
        : json.find('}', objectStart);
    if (objectStart == std::string_view::npos ||
        objectEnd == std::string_view::npos) {
        return false;
    }

    const std::string_view candleObject =
        json.substr(objectStart, objectEnd - objectStart + 1);
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

} // namespace

/**
 * Pobiera świecę 1-minutową dla dowolnego symbolu obsługiwanego przez Twelve Data.
 * Żądany czas jest UTC, aby identycznie zapisywać znaczniki czasu w bazie.
 */
bool CandlesDataClient::fetchLatestOneMinuteCandle(
    const std::string& symbol, CandleData& candle, std::string& error) {
    std::wstring encodedSymbol;
    if (!encodeSymbolForUrl(symbol, encodedSymbol)) {
        error = "Symbol instrumentu jest pusty, za długi albo zawiera niedozwolone białe znaki.";
        return false;
    }

    const std::wstring requestPath =
        L"/time_series?symbol=" + encodedSymbol +
        L"&interval=1min&outputsize=1&timezone=UTC&apikey=";
    std::string response;
    if (!TwelveDataClient::fetchApiResponse(requestPath, response, error)) {
        return false;
    }

    if (!extractCandle(response, candle)) {
        error = "Twelve Data nie zwróciło prawidłowej świecy dla symbolu " +
                symbol + ".";
        return false;
    }
    return true;
}
