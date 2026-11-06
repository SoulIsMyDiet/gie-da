#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winhttp.h>

#include "TwelveDataClient.h"

#include <cmath>
#include <cstdlib>
#include <memory>
#include <string>
#include <string_view>
#include <utility>

namespace {

// Endpoint configuration and a defensive cap on the downloaded response size.
constexpr wchar_t apiHost[] = L"api.twelvedata.com";
constexpr wchar_t apiPath[] = L"/price?symbol=EUR%2FUSD&apikey=";
constexpr wchar_t oneMinuteCandleApiPath[] =
    L"/time_series?symbol=EUR%2FUSD&interval=1min&outputsize=1&apikey=";
constexpr wchar_t fiveMinuteCandleApiPath[] =
    L"/time_series?symbol=EUR%2FUSD&interval=5min&outputsize=1&apikey=";
constexpr DWORD maxResponseSize = 64 * 1024;

/**
 * Owns a WinHTTP handle and closes it automatically when it leaves scope.
 */
struct InternetHandle {
    HINTERNET value;

    /**
     * Takes ownership of a WinHTTP handle returned by a WinHTTP API call.
     */
    explicit InternetHandle(HINTERNET handle) : value(handle) {}

    /**
     * Closes the owned handle if it is valid.
     */
    ~InternetHandle() {
        if (value != nullptr) {
            WinHttpCloseHandle(value);
        }
    }

    InternetHandle(const InternetHandle&) = delete;
    InternetHandle& operator=(const InternetHandle&) = delete;
};

/**
 * Reads and validates the API key from the process environment.
 */
bool readApiKey(std::wstring& apiKey) {
    const DWORD requiredSize = GetEnvironmentVariableW(L"TWELVE_DATA_API_KEY", nullptr, 0);
    if (requiredSize == 0) {
        return false;
    }

    apiKey.resize(requiredSize);
    const DWORD copiedSize = GetEnvironmentVariableW(
        L"TWELVE_DATA_API_KEY", apiKey.data(), requiredSize);
    if (copiedSize == 0 || copiedSize >= requiredSize) {
        apiKey.clear();
        return false;
    }
    apiKey.resize(copiedSize);

    for (const wchar_t character : apiKey) {
        if (!((character >= L'a' && character <= L'z') ||
              (character >= L'A' && character <= L'Z') ||
              (character >= L'0' && character <= L'9') ||
              character == L'-' || character == L'_')) {
            apiKey.clear();
            return false;
        }
    }
    return true;
}

/**
 * Extracts and validates the numeric "price" field from the JSON response.
 */
bool extractPrice(const std::string& response, std::string& price) {
    const std::string key = "\"price\"";
    const std::size_t keyPosition = response.find(key);
    if (keyPosition == std::string::npos) {
        return false;
    }

    const std::size_t colonPosition = response.find(':', keyPosition + key.size());
    if (colonPosition == std::string::npos) {
        return false;
    }
    const std::size_t valueStart = response.find_first_not_of(" \t\r\n\"", colonPosition + 1);
    if (valueStart == std::string::npos) {
        return false;
    }
    const std::size_t valueEnd = response.find_first_of("\" \t\r\n,}", valueStart);
    if (valueEnd == std::string::npos || valueEnd == valueStart) {
        return false;
    }

    const std::string value = response.substr(valueStart, valueEnd - valueStart);
    char* parsedEnd = nullptr;
    const double parsedValue = std::strtod(value.c_str(), &parsedEnd);
    if (parsedEnd == value.c_str() || *parsedEnd != '\0' || !std::isfinite(parsedValue) ||
        parsedValue <= 0.0) {
        return false;
    }

    price = value;
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
 * Odczytuje ostatni obiekt z tablicy values i sprawdza poprawność świecy OHLC.
 */
bool extractCandle(const std::string& response, ForexCandle& candle) {
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
    ForexCandle parsedCandle;
    if (!extractJsonString(candleObject, "datetime", parsedCandle.datetime) ||
        !extractJsonString(candleObject, "open", parsedCandle.open) ||
        !extractJsonString(candleObject, "high", parsedCandle.high) ||
        !extractJsonString(candleObject, "low", parsedCandle.low) ||
        !extractJsonString(candleObject, "close", parsedCandle.close)) {
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

    candle = std::move(parsedCandle);
    return true;
}

/**
 * Converts common HTTP failure codes into user-readable diagnostics.
 */
std::string describeHttpError(DWORD statusCode) {
    std::string error = "Twelve Data HTTP " + std::to_string(statusCode);
    switch (statusCode) {
        case 401:
            error += ": API key is invalid. Check that you set the new key correctly.";
            break;
        case 403:
            error += ": this request may not be included in your plan.";
            break;
        case 429:
            error += ": API request limit reached. Wait before retrying.";
            break;
        default:
            error += ": check your API key, account access, and Twelve Data service status.";
            break;
    }
    return error;
}

/**
 * Wysyła żądanie do wybranego endpointu i pobiera odpowiedź z limitem rozmiaru.
 */
bool fetchApiResponse(
    const wchar_t* endpoint, std::string& response, std::string& error) {
    std::wstring apiKey;
    if (!readApiKey(apiKey)) {
        error = "Ustaw zmienną środowiskową TWELVE_DATA_API_KEY.";
        return false;
    }

    InternetHandle session{WinHttpOpen(
        L"gie-da/1.0", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0)};
    if (session.value == nullptr) {
        error = "Nie udało się rozpocząć połączenia HTTPS z Twelve Data.";
        return false;
    }
    WinHttpSetTimeouts(session.value, 5000, 5000, 5000, 5000);

    InternetHandle connection{WinHttpConnect(
        session.value, apiHost, INTERNET_DEFAULT_HTTPS_PORT, 0)};
    if (connection.value == nullptr) {
        error = "Nie można połączyć się z Twelve Data.";
        return false;
    }

    std::wstring requestPath = endpoint;
    requestPath += apiKey;
    InternetHandle request{WinHttpOpenRequest(
        connection.value, L"GET", requestPath.c_str(), nullptr,
        WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, WINHTTP_FLAG_SECURE)};
    if (request.value == nullptr ||
        !WinHttpSendRequest(request.value, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                            WINHTTP_NO_REQUEST_DATA, 0, 0, 0) ||
        !WinHttpReceiveResponse(request.value, nullptr)) {
        error = "Nie udało się wysłać żądania do Twelve Data.";
        return false;
    }

    DWORD statusCode = 0;
    DWORD statusCodeSize = sizeof(statusCode);
    if (!WinHttpQueryHeaders(
            request.value, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            WINHTTP_HEADER_NAME_BY_INDEX, &statusCode, &statusCodeSize,
            WINHTTP_NO_HEADER_INDEX)) {
        error = "Nie udało się odczytać statusu odpowiedzi Twelve Data.";
        return false;
    }
    if (statusCode != 200) {
        error = describeHttpError(statusCode);
        return false;
    }

    while (true) {
        DWORD bytesAvailable = 0;
        if (!WinHttpQueryDataAvailable(request.value, &bytesAvailable)) {
            error = "Nie udało się odczytać odpowiedzi Twelve Data.";
            return false;
        }
        if (bytesAvailable == 0) {
            return true;
        }
        if (response.size() + bytesAvailable > maxResponseSize) {
            error = "Odpowiedź Twelve Data jest zbyt duża.";
            return false;
        }

        const std::size_t oldSize = response.size();
        response.resize(oldSize + bytesAvailable);
        DWORD bytesRead = 0;
        if (!WinHttpReadData(
                request.value, response.data() + oldSize, bytesAvailable, &bytesRead)) {
            error = "Nie udało się odczytać odpowiedzi Twelve Data.";
            return false;
        }
        if (bytesRead == 0) {
            error = "Odpowiedź Twelve Data zakończyła się nieoczekiwanie.";
            return false;
        }
        response.resize(oldSize + bytesRead);
    }
}

} // namespace

/**
 * Pobiera cenę EUR/USD i zwraca prawidłową wartość albo opis błędu.
 */
bool TwelveDataClient::fetchEurUsdPrice(std::string& price, std::string& error) {
    std::string response;
    if (!fetchApiResponse(apiPath, response, error)) {
        return false;
    }

    if (!extractPrice(response, price)) {
        error = "No EUR/USD price was found in the Twelve Data response.";
        return false;
    }
    return true;
}

/**
 * Pobiera jedną ostatnią świecę EUR/USD z interwału 1- albo 5-minutowego.
 */
bool TwelveDataClient::fetchEurUsdCandle(
    ForexCandleInterval interval, ForexCandle& candle, std::string& error) {
    const wchar_t* endpoint = nullptr;
    switch (interval) {
        case ForexCandleInterval::oneMinute:
            endpoint = oneMinuteCandleApiPath;
            break;
        case ForexCandleInterval::fiveMinutes:
            endpoint = fiveMinuteCandleApiPath;
            break;
        default:
            error = "Wybrano nieobsługiwany interwał świecy.";
            return false;
    }

    std::string response;
    if (!fetchApiResponse(endpoint, response, error)) {
        return false;
    }

    if (!extractCandle(response, candle)) {
        error = "Twelve Data nie zwróciło prawidłowej świecy EUR/USD.";
        return false;
    }
    return true;
}
