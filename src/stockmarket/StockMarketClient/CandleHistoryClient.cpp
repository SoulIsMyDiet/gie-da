#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winhttp.h>

#include "CandleHistoryClient.h"

#include <string>

namespace {

constexpr wchar_t apiHost[] = L"127.0.0.1";
constexpr INTERNET_PORT apiPort = 8000;
constexpr wchar_t apiPath[] = L"/api/candles";

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
     * Zamyka przejęty uchwyt, jeśli jest prawidłowy.
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
 * Chroni tekst przed znakami, które mają specjalne znaczenie w JSON.
 */
std::string escapeJsonString(const std::string& value) {
    constexpr char hexadecimalDigits[] = "0123456789abcdef";
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
            case '\b':
                escaped += "\\b";
                break;
            case '\f':
                escaped += "\\f";
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
                    escaped += "\\u00";
                    escaped += hexadecimalDigits[character >> 4];
                    escaped += hexadecimalDigits[character & 0x0F];
                } else {
                    escaped += static_cast<char>(character);
                }
                break;
        }
    }
    return escaped;
}

/**
 * Zamienia datę z Twelve Data na znacznik ISO 8601 w strefie UTC.
 */
bool formatUtcDatetime(const std::string& providerDatetime, std::string& result) {
    if (providerDatetime.size() != 19 || providerDatetime[10] != ' ') {
        return false;
    }
    result = providerDatetime;
    result[10] = 'T';
    result += 'Z';
    return true;
}

/**
 * Buduje JSON świecy; brak wolumenu jest przekazywany jako null.
 */
std::string createRequestBody(
    const std::string& symbol,
    const CandleData& candle,
    const std::string& utcDatetime) {
    const std::string volume = candle.volume.empty()
        ? "null"
        : "\"" + escapeJsonString(candle.volume) + "\"";
    return "{\"symbol\":\"" + escapeJsonString(symbol) +
           "\",\"datetime\":\"" + utcDatetime +
           "\",\"open\":\"" + escapeJsonString(candle.open) +
           "\",\"high\":\"" + escapeJsonString(candle.high) +
           "\",\"low\":\"" + escapeJsonString(candle.low) +
           "\",\"close\":\"" + escapeJsonString(candle.close) +
           "\",\"volume\":" + volume + "}";
}

} // namespace

/**
 * Wysyła świecę do lokalnego API i sprawdza odpowiedź serwera.
 */
bool CandleHistoryClient::saveCandle(
    const std::string& symbol, const CandleData& candle, std::string& error) {
    std::string utcDatetime;
    if (!formatUtcDatetime(candle.datetime, utcDatetime)) {
        error = "Twelve Data zwróciło czas świecy w nieobsługiwanym formacie.";
        return false;
    }

    InternetHandle session{WinHttpOpen(
        L"gie-da/1.0", WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0)};
    if (session.value == nullptr) {
        error = "Nie udało się rozpocząć żądania do lokalnego API.";
        return false;
    }
    WinHttpSetTimeouts(session.value, 3000, 3000, 3000, 3000);

    InternetHandle connection{WinHttpConnect(session.value, apiHost, apiPort, 0)};
    if (connection.value == nullptr) {
        error = "Nie można połączyć się z lokalnym API bazy danych.";
        return false;
    }

    InternetHandle request{WinHttpOpenRequest(
        connection.value, L"POST", apiPath, nullptr,
        WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0)};
    if (request.value == nullptr) {
        error = "Nie udało się utworzyć żądania zapisu świecy.";
        return false;
    }

    std::string requestBody = createRequestBody(symbol, candle, utcDatetime);
    constexpr wchar_t requestHeaders[] = L"Content-Type: application/json\r\n";
    if (!WinHttpSendRequest(
            request.value, requestHeaders, static_cast<DWORD>(-1L),
            requestBody.data(), static_cast<DWORD>(requestBody.size()),
            static_cast<DWORD>(requestBody.size()), 0) ||
        !WinHttpReceiveResponse(request.value, nullptr)) {
        error = "Lokalne API nie odpowiedziało na żądanie zapisu świecy.";
        return false;
    }

    DWORD statusCode = 0;
    DWORD statusCodeSize = sizeof(statusCode);
    if (!WinHttpQueryHeaders(
            request.value,
            WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            WINHTTP_HEADER_NAME_BY_INDEX, &statusCode, &statusCodeSize,
            WINHTTP_NO_HEADER_INDEX)) {
        error = "Nie udało się odczytać odpowiedzi lokalnego API.";
        return false;
    }
    if (statusCode != 204) {
        error = "Lokalne API zwróciło HTTP " +
                std::to_string(statusCode) + " podczas zapisu świecy.";
        return false;
    }
    return true;
}
