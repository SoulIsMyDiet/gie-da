#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winhttp.h>

#include "PriceHistoryClient.h"

#include <cstdio>

// Backend
namespace {

constexpr wchar_t apiHost[] = L"127.0.0.1";
constexpr INTERNET_PORT apiPort = 8000;
constexpr wchar_t apiPath[] = L"/api/prices";

/**
 * Zamyka uchwyt WinHTTP automatycznie po zakończeniu bieżącego zakresu.
 */
struct InternetHandle {
    HINTERNET value;

    /**
     * Przejmuje własność uchwytu zwróconego przez funkcję WinHTTP.
     */
    explicit InternetHandle(HINTERNET handle) : value(handle) {}

    /**
     * Zamyka uchwyt, jeśli nadal jest prawidłowy.
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
 * Tworzy tekst czasu UTC w formacie ISO 8601 akceptowanym przez API.
 */
std::string formatUtcTimestamp(const SYSTEMTIME& timestamp) {
    char formattedTimestamp[32]{};
    std::snprintf(
        formattedTimestamp,
        sizeof(formattedTimestamp),
        "%04u-%02u-%02uT%02u:%02u:%02u.%03uZ",
        static_cast<unsigned>(timestamp.wYear),
        static_cast<unsigned>(timestamp.wMonth),
        static_cast<unsigned>(timestamp.wDay),
        static_cast<unsigned>(timestamp.wHour),
        static_cast<unsigned>(timestamp.wMinute),
        static_cast<unsigned>(timestamp.wSecond),
        static_cast<unsigned>(timestamp.wMilliseconds));
    return formattedTimestamp;
}

/**
 * Chroni znaki specjalne, aby wartość tekstowa nie uszkodziła treści JSON.
 */
std::string escapeJsonString(const std::string& value) {
    constexpr char hexadecimalDigits[] = "0123456789abcdef";
    std::string escapedValue;
    escapedValue.reserve(value.size());
    for (const unsigned char character : value) {
        switch (character) {
            case '"':
                escapedValue += "\\\"";
                break;
            case '\\':
                escapedValue += "\\\\";
                break;
            case '\b':
                escapedValue += "\\b";
                break;
            case '\f':
                escapedValue += "\\f";
                break;
            case '\n':
                escapedValue += "\\n";
                break;
            case '\r':
                escapedValue += "\\r";
                break;
            case '\t':
                escapedValue += "\\t";
                break;
            default:
                if (character < 0x20) {
                    escapedValue += "\\u00";
                    escapedValue += hexadecimalDigits[character >> 4];
                    escapedValue += hexadecimalDigits[character & 0x0F];
                } else {
                    escapedValue += static_cast<char>(character);
                }
                break;
        }
    }
    return escapedValue;
}

/**
 * Składa treść żądania JSON; pola wejściowe pochodzą z kontrolowanego źródła.
 */
std::string createRequestBody(
    const std::string& symbol,
    const std::string& price,
    const SYSTEMTIME& recordedAt) {
    return "{\"symbol\":\"" + escapeJsonString(symbol) + "\",\"price\":\"" +
           escapeJsonString(price) +
           "\",\"recorded_at\":\"" + formatUtcTimestamp(recordedAt) + "\"}";
}

} // namespace

/**
 * Wysyła cenę do lokalnego API i sprawdza, czy baza potwierdziła jej zapis.
 */
bool PriceHistoryClient::savePrice(
    const std::string& symbol,
    const std::string& price,
    const SYSTEMTIME& recordedAt,
    std::string& error) {
    InternetHandle session{WinHttpOpen(
        L"gie-da/1.0", WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0)};
    if (session.value == nullptr) {
        error = "Could not start the database API request.";
        return false;
    }
    WinHttpSetTimeouts(session.value, 3000, 3000, 3000, 3000);

    InternetHandle connection{WinHttpConnect(session.value, apiHost, apiPort, 0)};
    if (connection.value == nullptr) {
        error = "Could not connect to the local database API.";
        return false;
    }

    InternetHandle request{WinHttpOpenRequest(
        connection.value, L"POST", apiPath, nullptr,
        WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0)};
    if (request.value == nullptr) {
        error = "Could not create the database API request.";
        return false;
    }

    std::string requestBody = createRequestBody(symbol, price, recordedAt);
    constexpr wchar_t requestHeaders[] = L"Content-Type: application/json\r\n";
    if (!WinHttpSendRequest(
            request.value, requestHeaders, static_cast<DWORD>(-1L),
            requestBody.data(),
            static_cast<DWORD>(requestBody.size()),
            static_cast<DWORD>(requestBody.size()), 0) ||
        !WinHttpReceiveResponse(request.value, nullptr)) {
        error = "The local database API did not respond.";
        return false;
    }

    DWORD statusCode = 0;
    DWORD statusCodeSize = sizeof(statusCode);
    if (!WinHttpQueryHeaders(
            request.value,
            WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            WINHTTP_HEADER_NAME_BY_INDEX, &statusCode, &statusCodeSize,
            WINHTTP_NO_HEADER_INDEX)) {
        error = "Could not read the database API response.";
        return false;
    }
    if (statusCode != 201) {
        error = "The database API returned HTTP " + std::to_string(statusCode) + ".";
        return false;
    }
    return true;
}
