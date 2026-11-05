#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winhttp.h>

#include "PriceHistoryClient.h"

#include <cstdio>
#include <limits>
#include <string_view>
#include <utility>
#include <vector>

// Backend
namespace {

constexpr wchar_t apiHost[] = L"127.0.0.1";
constexpr INTERNET_PORT apiPort = 8000;
constexpr wchar_t apiPath[] = L"/api/prices";
constexpr DWORD maxResponseSize = 64 * 1024;

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
    const SYSTEMTIME& recordedAt,
    const std::string& goldenAdvice) {
    return "{\"symbol\":\"" + escapeJsonString(symbol) + "\",\"price\":\"" +
           escapeJsonString(price) +
           "\",\"recorded_at\":\"" + formatUtcTimestamp(recordedAt) +
           "\",\"golden_advice\":\"" + escapeJsonString(goldenAdvice) + "\"}";
}

/**
 * Dzieli wiersz TSV na pięć pól bez kopiowania tekstu.
 */
bool splitReportRow(std::string_view row, PriceReportEntry& entry) {
    std::string_view fields[5];
    std::size_t fieldStart = 0;
    for (std::size_t index = 0; index < 4; ++index) {
        const std::size_t separator = row.find('\t', fieldStart);
        if (separator == std::string_view::npos) {
            return false;
        }
        fields[index] = row.substr(fieldStart, separator - fieldStart);
        fieldStart = separator + 1;
    }

    if (row.find('\t', fieldStart) != std::string_view::npos) {
        return false;
    }
    fields[4] = row.substr(fieldStart);
    for (const std::string_view field : fields) {
        if (field.empty() || field.size() > 512) {
            return false;
        }
    }

    entry.id.assign(fields[0]);
    entry.recordedAt.assign(fields[1]);
    entry.symbol.assign(fields[2]);
    entry.price.assign(fields[3]);
    entry.goldenAdvice.assign(fields[4]);
    return true;
}

/**
 * Odczytuje odpowiedź API do ograniczonego bufora.
 */
bool readResponse(HINTERNET request, std::string& response, std::string& error) {
    while (true) {
        DWORD bytesAvailable = 0;
        if (!WinHttpQueryDataAvailable(request, &bytesAvailable)) {
            error = "Could not read the reports response.";
            return false;
        }
        if (bytesAvailable == 0) {
            return true;
        }
        if (response.size() + bytesAvailable > maxResponseSize) {
            error = "The reports response was too large.";
            return false;
        }

        const std::size_t previousSize = response.size();
        response.resize(previousSize + bytesAvailable);
        DWORD bytesRead = 0;
        if (!WinHttpReadData(
                request, response.data() + previousSize, bytesAvailable, &bytesRead)) {
            error = "Could not read the reports response.";
            return false;
        }
        if (bytesRead == 0) {
            error = "The reports response ended unexpectedly.";
            return false;
        }
        response.resize(previousSize + bytesRead);
    }
}

} // namespace

/**
 * Wysyła cenę i sentencję do lokalnego API, a następnie sprawdza potwierdzenie.
 */
bool PriceHistoryClient::savePrice(
    const std::string& symbol,
    const std::string& price,
    const SYSTEMTIME& recordedAt,
    const std::string& goldenAdvice,
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

    std::string requestBody =
        createRequestBody(symbol, price, recordedAt, goldenAdvice);
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

/**
 * Pobiera wpisy raportu zapisane przed chwilą kliknięcia przycisku.
 */
bool PriceHistoryClient::fetchRecentPrices(
    unsigned long long beforeUtcMicroseconds,
    std::vector<PriceReportEntry>& entries,
    std::string& error) {
    if (beforeUtcMicroseconds >
        static_cast<unsigned long long>(std::numeric_limits<long long>::max())) {
        error = "The reports cutoff time is invalid.";
        return false;
    }

    InternetHandle session{WinHttpOpen(
        L"gie-da/1.0", WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0)};
    if (session.value == nullptr) {
        error = "Could not start the reports request.";
        return false;
    }
    WinHttpSetTimeouts(session.value, 3000, 3000, 3000, 3000);

    InternetHandle connection{WinHttpConnect(session.value, apiHost, apiPort, 0)};
    if (connection.value == nullptr) {
        error = "Could not connect to the local reports API.";
        return false;
    }

    std::wstring requestPath = L"/api/prices/report?before_utc_us=";
    requestPath += std::to_wstring(beforeUtcMicroseconds);
    InternetHandle request{WinHttpOpenRequest(
        connection.value, L"GET", requestPath.c_str(), nullptr,
        WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0)};
    if (request.value == nullptr ||
        !WinHttpSendRequest(
            request.value, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
            WINHTTP_NO_REQUEST_DATA, 0, 0, 0) ||
        !WinHttpReceiveResponse(request.value, nullptr)) {
        error = "The local reports API did not respond.";
        return false;
    }

    DWORD statusCode = 0;
    DWORD statusCodeSize = sizeof(statusCode);
    if (!WinHttpQueryHeaders(
            request.value,
            WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            WINHTTP_HEADER_NAME_BY_INDEX, &statusCode, &statusCodeSize,
            WINHTTP_NO_HEADER_INDEX)) {
        error = "Could not read the reports API response.";
        return false;
    }
    if (statusCode != 200) {
        error = "The reports API returned HTTP " + std::to_string(statusCode) + ".";
        return false;
    }

    std::string response;
    if (!readResponse(request.value, response, error)) {
        return false;
    }

    std::vector<PriceReportEntry> receivedEntries;
    std::size_t lineStart = 0;
    while (lineStart < response.size()) {
        const std::size_t lineEnd = response.find('\n', lineStart);
        const std::size_t lineLength =
            (lineEnd == std::string::npos ? response.size() : lineEnd) - lineStart;
        if (lineLength > 0) {
            PriceReportEntry entry;
            if (!splitReportRow(
                    std::string_view(response).substr(lineStart, lineLength), entry) ||
                receivedEntries.size() == 20) {
                error = "The reports API returned invalid data.";
                return false;
            }
            receivedEntries.push_back(std::move(entry));
        }
        if (lineEnd == std::string::npos) {
            break;
        }
        lineStart = lineEnd + 1;
    }

    entries = std::move(receivedEntries);
    return true;
}
