#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winhttp.h>

#include "CandleHistoryClient.h"

#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

namespace {

constexpr wchar_t apiHost[] = L"127.0.0.1";
constexpr INTERNET_PORT apiPort = 8000;
constexpr wchar_t singleCandleApiPath[] = L"/api/candles";
constexpr wchar_t batchApiPath[] = L"/api/candles/batch";
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
 * Składa żądanie JSON z maksymalnie 1000 świec.
 */
bool createBatchRequestBody(
    const std::string& symbol,
    const std::vector<CandleData>& candles,
    std::string& requestBody,
    std::string& error) {
    if (candles.empty() || candles.size() > 1000) {
        error = "Paczka musi zawierać od 1 do 1000 świec.";
        return false;
    }

    requestBody = "{\"candles\":[";
    for (std::size_t index = 0; index < candles.size(); ++index) {
        std::string utcDatetime;
        if (!formatUtcDatetime(candles[index].datetime, utcDatetime)) {
            error = "Twelve Data zwróciło czas świecy w nieobsługiwanym formacie.";
            return false;
        }
        if (index > 0) {
            requestBody += ',';
        }
        requestBody += createRequestBody(symbol, candles[index], utcDatetime);
    }
    requestBody += "]}";
    return true;
}

/**
 * Koduje symbol dla parametru lokalnego API.
 */
bool encodeSymbolForUrl(
    const std::string& symbol, std::wstring& encodedSymbol) {
    constexpr char hexadecimalDigits[] = "0123456789ABCDEF";
    if (symbol.empty() || symbol.size() > 32) {
        return false;
    }
    encodedSymbol.clear();
    for (const unsigned char character : symbol) {
        if ((character >= 'A' && character <= 'Z') ||
            (character >= '0' && character <= '9') ||
            character == '-' || character == '_' || character == '.') {
            encodedSymbol.push_back(static_cast<wchar_t>(character));
        } else if (character == '/') {
            encodedSymbol += L"%2F";
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
 * Wysyła żądanie JSON do lokalnego API i sprawdza jego kod odpowiedzi.
 */
bool sendApiRequest(
    const wchar_t* path,
    const std::string& requestBody,
    DWORD expectedStatus,
    std::string& responseBody,
    std::string& error) {
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
        connection.value, L"POST", path, nullptr,
        WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0)};
    if (request.value == nullptr) {
        error = "Nie udało się utworzyć żądania zapisu świec.";
        return false;
    }

    constexpr wchar_t requestHeaders[] = L"Content-Type: application/json\r\n";
    if (!WinHttpSendRequest(
            request.value, requestHeaders, static_cast<DWORD>(-1L),
            const_cast<char*>(requestBody.data()),
            static_cast<DWORD>(requestBody.size()),
            static_cast<DWORD>(requestBody.size()), 0) ||
        !WinHttpReceiveResponse(request.value, nullptr)) {
        error = "Lokalne API nie odpowiedziało na żądanie zapisu świec.";
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
    if (statusCode != expectedStatus) {
        error = "Lokalne API zwróciło HTTP " +
                std::to_string(statusCode) + " podczas zapisu świec.";
        return false;
    }
    return readResponse(request.value, responseBody, error);
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
    const std::string& symbol,
    const CandleData& candle,
    std::size_t& newlyDetectedGapCount,
    std::string& error) {
    newlyDetectedGapCount = 0;
    std::string utcDatetime;
    if (!formatUtcDatetime(candle.datetime, utcDatetime)) {
        error = "Twelve Data zwróciło czas świecy w nieobsługiwanym formacie.";
        return false;
    }
    const std::string requestBody =
        createRequestBody(symbol, candle, utcDatetime);
    std::string responseBody;
    if (!sendApiRequest(
            singleCandleApiPath, requestBody, 200, responseBody, error)) {
        return false;
    }
    if (responseBody.empty()) {
        error = "Lokalne API nie zwróciło liczby wykrytych luk.";
        return false;
    }
    for (const unsigned char character : responseBody) {
        if (character < '0' || character > '9') {
            error = "Lokalne API zwróciło nieprawidłową liczbę wykrytych luk.";
            return false;
        }
    }
    try {
        newlyDetectedGapCount =
            static_cast<std::size_t>(std::stoull(responseBody));
    } catch (const std::out_of_range&) {
        error = "Lokalne API zwróciło zbyt dużą liczbę wykrytych luk.";
        return false;
    } catch (const std::invalid_argument&) {
        error = "Lokalne API zwróciło nieprawidłową liczbę wykrytych luk.";
        return false;
    }
    return true;
}

/**
 * Zapisuje do 1000 świec w jednym żądaniu do lokalnego API.
 */
bool CandleHistoryClient::saveCandles(
    const std::string& symbol,
    const std::vector<CandleData>& candles,
    std::string& error) {
    std::string requestBody;
    if (!createBatchRequestBody(symbol, candles, requestBody, error)) {
        return false;
    }
    std::string responseBody;
    return sendApiRequest(
        batchApiPath, requestBody, 204, responseBody, error);
}

/**
 * Pobiera status bazy i do 1000 najnowszych brakujących znaczników UTC.
 */
bool CandleHistoryClient::fetchMissingCandleTimes(
    const std::string& symbol,
    bool& hasExistingCandles,
    std::vector<std::string>& missingTimes,
    std::size_t& totalMissingCount,
    std::string& error) {
    std::wstring encodedSymbol;
    if (!encodeSymbolForUrl(symbol, encodedSymbol)) {
        error = "Symbol instrumentu do sprawdzenia luk jest nieprawidłowy.";
        return false;
    }

    InternetHandle session{WinHttpOpen(
        L"gie-da/1.0", WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0)};
    if (session.value == nullptr) {
        error = "Nie udało się rozpocząć sprawdzania brakujących świec.";
        return false;
    }
    WinHttpSetTimeouts(session.value, 3000, 3000, 3000, 3000);

    InternetHandle connection{WinHttpConnect(session.value, apiHost, apiPort, 0)};
    if (connection.value == nullptr) {
        error = "Nie można połączyć się z lokalnym API bazy danych.";
        return false;
    }

    const std::wstring requestPath =
        L"/api/candles/missing?symbol=" + encodedSymbol;
    InternetHandle request{WinHttpOpenRequest(
        connection.value, L"GET", requestPath.c_str(), nullptr,
        WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0)};
    if (request.value == nullptr ||
        !WinHttpSendRequest(
            request.value, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
            WINHTTP_NO_REQUEST_DATA, 0, 0, 0) ||
        !WinHttpReceiveResponse(request.value, nullptr)) {
        error = "Lokalne API nie odpowiedziało podczas sprawdzania luk.";
        return false;
    }

    DWORD statusCode = 0;
    DWORD statusCodeSize = sizeof(statusCode);
    if (!WinHttpQueryHeaders(
            request.value,
            WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            WINHTTP_HEADER_NAME_BY_INDEX, &statusCode, &statusCodeSize,
            WINHTTP_NO_HEADER_INDEX) ||
        statusCode != 200) {
        error = "Lokalne API nie zwróciło planu uzupełniania świec.";
        return false;
    }

    std::string response;
    if (!readResponse(request.value, response, error)) {
        return false;
    }
    const std::size_t firstLineEnd = response.find('\n');
    std::string_view hasDataText = firstLineEnd == std::string::npos
        ? std::string_view(response)
        : std::string_view(response).substr(0, firstLineEnd);
    if (!hasDataText.empty() && hasDataText.back() == '\r') {
        hasDataText.remove_suffix(1);
    }
    if (hasDataText != "0" && hasDataText != "1") {
        error = "Lokalne API zwróciło nieprawidłowy plan uzupełniania.";
        return false;
    }

    std::vector<std::string> receivedTimes;
    const std::size_t countLineStart = firstLineEnd == std::string::npos
        ? response.size()
        : firstLineEnd + 1;
    const std::size_t countLineEnd = response.find('\n', countLineStart);
    std::string_view countText = countLineEnd == std::string::npos
        ? std::string_view(response).substr(countLineStart)
        : std::string_view(response).substr(
              countLineStart, countLineEnd - countLineStart);
    if (!countText.empty() && countText.back() == '\r') {
        countText.remove_suffix(1);
    }
    if (countText.empty()) {
        error = "Lokalne API zwróciło nieprawidłową liczbę brakujących świec.";
        return false;
    }
    for (const unsigned char character : countText) {
        if (character < '0' || character > '9') {
            error = "Lokalne API zwróciło nieprawidłową liczbę brakujących świec.";
            return false;
        }
    }
    try {
        totalMissingCount =
            static_cast<std::size_t>(std::stoull(std::string(countText)));
    } catch (const std::out_of_range&) {
        error = "Lokalne API zwróciło zbyt dużą liczbę brakujących świec.";
        return false;
    } catch (const std::invalid_argument&) {
        error = "Lokalne API zwróciło nieprawidłową liczbę brakujących świec.";
        return false;
    }

    std::size_t lineStart = countLineEnd == std::string::npos
        ? response.size()
        : countLineEnd + 1;
    while (lineStart < response.size()) {
        const std::size_t lineEnd = response.find('\n', lineStart);
        const std::size_t effectiveEnd =
            lineEnd == std::string::npos ? response.size() : lineEnd;
        std::string_view timestamp =
            std::string_view(response).substr(lineStart, effectiveEnd - lineStart);
        if (!timestamp.empty() && timestamp.back() == '\r') {
            timestamp.remove_suffix(1);
        }
        if (timestamp.size() != 19 || receivedTimes.size() == 1000) {
            error = "Lokalne API zwróciło nieprawidłową listę brakujących świec.";
            return false;
        }
        receivedTimes.emplace_back(timestamp);
        if (lineEnd == std::string::npos) {
            break;
        }
        lineStart = lineEnd + 1;
    }

    hasExistingCandles = hasDataText == "1";
    missingTimes = std::move(receivedTimes);
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
