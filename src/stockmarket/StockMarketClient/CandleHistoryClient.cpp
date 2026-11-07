#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winhttp.h>

#include "CandleHistoryClient.h"

#include <limits>
#include <string>
#include <string_view>
#include <utility>

namespace {

constexpr wchar_t apiHost[] = L"127.0.0.1";
constexpr INTERNET_PORT apiPort = 8000;
constexpr wchar_t apiPath[] = L"/api/candles";
constexpr DWORD maxResponseSize = 64 * 1024;
constexpr std::size_t pageSize = 20;

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

/**
 * Odczytuje odpowiedź do bufora o ograniczonym rozmiarze.
 */
bool readResponse(HINTERNET request, std::string& response, std::string& error) {
    while (true) {
        DWORD bytesAvailable = 0;
        if (!WinHttpQueryDataAvailable(request, &bytesAvailable)) {
            error = "Nie udało się odczytać odpowiedzi raportu świec.";
            return false;
        }
        if (bytesAvailable == 0) {
            return true;
        }
        if (response.size() + bytesAvailable > maxResponseSize) {
            error = "Odpowiedź raportu świec jest zbyt duża.";
            return false;
        }

        const std::size_t previousSize = response.size();
        response.resize(previousSize + bytesAvailable);
        DWORD bytesRead = 0;
        if (!WinHttpReadData(
                request, response.data() + previousSize,
                bytesAvailable, &bytesRead)) {
            error = "Nie udało się odczytać odpowiedzi raportu świec.";
            return false;
        }
        if (bytesRead == 0) {
            error = "Odpowiedź raportu świec zakończyła się nieoczekiwanie.";
            return false;
        }
        response.resize(previousSize + bytesRead);
    }
}

/**
 * Dzieli wiersz raportu na siedem wymaganych pól.
 */
bool splitCandleRow(std::string_view row, CandleReportEntry& candle) {
    std::string_view fields[7];
    std::size_t fieldStart = 0;
    for (std::size_t index = 0; index < 6; ++index) {
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
    fields[6] = row.substr(fieldStart);

    for (std::size_t index = 0; index < 7; ++index) {
        if ((index != 6 && fields[index].empty()) || fields[index].size() > 512) {
            return false;
        }
    }

    candle.symbol.assign(fields[0]);
    candle.datetime.assign(fields[1]);
    candle.open.assign(fields[2]);
    candle.high.assign(fields[3]);
    candle.low.assign(fields[4]);
    candle.close.assign(fields[5]);
    candle.volume.assign(fields[6]);
    return true;
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

/**
 * Pobiera stronę raportu świec i sprawdza poprawność każdego wiersza TSV.
 */
bool CandleHistoryClient::fetchCandlePage(
    std::size_t page,
    std::vector<CandleReportEntry>& candles,
    bool& hasNextPage,
    std::string& error) {
    if (page > 1000000 ||
        page > static_cast<std::size_t>(
                   std::numeric_limits<unsigned long long>::max() / pageSize)) {
        error = "Numer strony raportu świec jest poza dozwolonym zakresem.";
        return false;
    }

    InternetHandle session{WinHttpOpen(
        L"gie-da/1.0", WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0)};
    if (session.value == nullptr) {
        error = "Nie udało się rozpocząć żądania raportu świec.";
        return false;
    }
    WinHttpSetTimeouts(session.value, 3000, 3000, 3000, 3000);

    InternetHandle connection{WinHttpConnect(session.value, apiHost, apiPort, 0)};
    if (connection.value == nullptr) {
        error = "Nie można połączyć się z lokalnym API raportów.";
        return false;
    }

    const std::wstring requestPath =
        L"/api/candles/report?page=" + std::to_wstring(page);
    InternetHandle request{WinHttpOpenRequest(
        connection.value, L"GET", requestPath.c_str(), nullptr,
        WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0)};
    if (request.value == nullptr ||
        !WinHttpSendRequest(
            request.value, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
            WINHTTP_NO_REQUEST_DATA, 0, 0, 0) ||
        !WinHttpReceiveResponse(request.value, nullptr)) {
        error = "Lokalne API raportów nie odpowiedziało.";
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
    if (statusCode != 200) {
        error = "Lokalne API raportów zwróciło HTTP " +
                std::to_string(statusCode) + ".";
        return false;
    }

    std::string response;
    if (!readResponse(request.value, response, error)) {
        return false;
    }
    const std::size_t headerEnd = response.find('\n');
    const std::string_view hasNextPageText =
        headerEnd == std::string::npos
            ? std::string_view(response)
            : std::string_view(response).substr(0, headerEnd);
    if (hasNextPageText != "0" && hasNextPageText != "1") {
        error = "Lokalne API raportów zwróciło nieprawidłową flagę paginacji.";
        return false;
    }

    std::vector<CandleReportEntry> receivedCandles;
    std::size_t lineStart = headerEnd == std::string::npos
        ? response.size()
        : headerEnd + 1;
    while (lineStart < response.size()) {
        const std::size_t lineEnd = response.find('\n', lineStart);
        const std::size_t effectiveEnd =
            lineEnd == std::string::npos ? response.size() : lineEnd;
        if (effectiveEnd > lineStart) {
            const std::string_view row =
                std::string_view(response).substr(lineStart, effectiveEnd - lineStart);
            CandleReportEntry candle;
            if (receivedCandles.size() == pageSize ||
                !splitCandleRow(row, candle)) {
                error = "Lokalne API raportów zwróciło nieprawidłowe dane świec.";
                return false;
            }
            receivedCandles.push_back(std::move(candle));
        }
        if (lineEnd == std::string::npos) {
            break;
        }
        lineStart = lineEnd + 1;
    }

    candles = std::move(receivedCandles);
    hasNextPage = hasNextPageText == "1";
    return true;
}
