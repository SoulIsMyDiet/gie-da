#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winhttp.h>

#include "CandleHistoryClient.h"

#include <algorithm>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <limits>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

namespace {

constexpr wchar_t apiHost[] = L"127.0.0.1";
constexpr INTERNET_PORT apiPort = 8000;
constexpr wchar_t singleCandleApiPath[] = L"/api/candles";
constexpr wchar_t ibkrCandleApiPath[] = L"/api/candles/ibkr";
constexpr wchar_t ibkrCandleBatchApiPath[] = L"/api/candles/ibkr/batch";
constexpr wchar_t batchApiPath[] = L"/api/candles/batch";
constexpr wchar_t monitoredProductsApiPath[] = L"/api/products/monitored";
constexpr wchar_t openProductsApiPath[] = L"/api/products/open";
constexpr wchar_t dailySentencesApiPath[] = L"/api/daily-sentences";
constexpr wchar_t analysisCandlesApiPath[] = L"/api/candles/analysis?limit=";
constexpr wchar_t promptCandleHistoryApiPath[] =
    L"/api/candles/prompt-history?symbol=";
constexpr wchar_t generatedSignalApiPath[] = L"/api/signals/grok45";
constexpr wchar_t grokNotificationCursorPath[] =
    L"/api/signals/grok-sp500/notifications/cursor";
constexpr wchar_t grokNotificationsPath[] =
    L"/api/signals/grok-sp500/notifications?after_id=";
constexpr std::size_t grokNotificationPageSize = 100;
constexpr wchar_t usdmxnNotificationsPath[] =
    L"/api/signals/usdmxn/notifications?limit=100";
constexpr std::size_t usdmxnNotificationPageSize = 100;
constexpr wchar_t usdjpyNotificationsPath[] =
    L"/api/signals/usdjpy/notifications?limit=100";
constexpr std::size_t usdjpyNotificationPageSize = 100;
constexpr DWORD maxResponseSize = 64 * 1024;
// Pozostałe żądania mają limit 3 s; zapis ES ma 70 s na analizę do 60 s i bufor.
constexpr DWORD apiRequestTimeoutMs = 3000;
constexpr DWORD singleCandleReceiveTimeoutMs = 70000;
constexpr DWORD ibkrBatchReceiveTimeoutMs = 30000;
constexpr std::size_t maximumIbkrBatchSize = 1000;
constexpr std::size_t pageSize = 20;
constexpr std::size_t filteredCandlePageSize = 10;
constexpr std::size_t signalPageSize = 15;

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
 * Formatuje znacznik Unix jako pełną minutę w strefie UTC.
 * @param timestampUtc Czas świecy w sekundach od początku epoki Unix.
 * @param result Otrzymuje datę ISO 8601 w UTC.
 * @return true, jeśli znacznik można przedstawić jako prawidłową datę.
 */
bool formatUnixTimestampUtc(
    std::int64_t timestampUtc, std::string& result) {
    if (timestampUtc <= 0) {
        return false;
    }

    const auto timestamp = std::chrono::floor<std::chrono::minutes>(
        std::chrono::sys_seconds{
            std::chrono::seconds{timestampUtc}});
    const auto day = std::chrono::floor<std::chrono::days>(timestamp);
    const std::chrono::year_month_day date{day};
    if (!date.ok()) {
        return false;
    }
    const std::chrono::hh_mm_ss time{timestamp - day};
    std::ostringstream formatted;
    formatted.imbue(std::locale::classic());
    formatted << std::setfill('0')
              << std::setw(4) << static_cast<int>(date.year()) << '-'
              << std::setw(2) << static_cast<unsigned int>(date.month()) << '-'
              << std::setw(2) << static_cast<unsigned int>(date.day()) << 'T'
              << std::setw(2) << time.hours().count() << ':'
              << std::setw(2) << time.minutes().count() << ":00Z";
    result = formatted.str();
    return true;
}

/**
 * Formatuje cenę do precyzji DECIMAL zapisanej w bazie.
 * @param price Dodatnia, skończona cena z API IBKR.
 * @param result Otrzymuje tekst z dziesięcioma miejscami po przecinku.
 * @return true, jeśli cena nadaje się do zapisu.
 */
bool formatIbkrPrice(double price, std::string& result) {
    if (!std::isfinite(price) || price <= 0.0) {
        return false;
    }
    std::ostringstream formatted;
    formatted.imbue(std::locale::classic());
    formatted << std::fixed << std::setprecision(10) << price;
    result = formatted.str();
    return true;
}

/**
 * Buduje JSON do osobnego endpointu podglądowych świec IBKR.
 * @param candle Dane świecy i dostępnych kwotowań.
 * @param utcDatetime Czas świecy w formacie ISO 8601 UTC.
 * @param requestBody Otrzymuje gotową treść żądania JSON.
 * @param error Otrzymuje opis danych, których nie można wysłać.
 * @return true, jeśli treść JSON została poprawnie zbudowana.
 */
bool createIbkrRequestBody(
    const IBKRCandleData& candle,
    const std::string& utcDatetime,
    std::string& requestBody,
    std::string& error) {
    std::string open;
    std::string high;
    std::string low;
    std::string close;
    std::string bid;
    std::string ask;
    if (!formatIbkrPrice(candle.open, open) ||
        !formatIbkrPrice(candle.high, high) ||
        !formatIbkrPrice(candle.low, low) ||
        !formatIbkrPrice(candle.close, close) ||
        (candle.bidAvailable && !formatIbkrPrice(candle.bid, bid)) ||
        (candle.askAvailable && !formatIbkrPrice(candle.ask, ask))) {
        error = "IBKR zwróciło nieprawidłową cenę świecy lub kwotowania.";
        return false;
    }
    if (candle.high < candle.open || candle.high < candle.close ||
        candle.low > candle.open || candle.low > candle.close ||
        candle.low > candle.high) {
        error = "IBKR zwróciło niespójny zakres cen OHLC.";
        return false;
    }
    if (candle.bidAvailable && candle.askAvailable &&
        candle.bid > candle.ask) {
        error = "Kwotowanie bid z IBKR jest wyższe od kwotowania ask.";
        return false;
    }
    if (!candle.bidAvailable) {
        bid = "null";
    } else {
        bid = "\"" + bid + "\"";
    }
    if (!candle.askAvailable) {
        ask = "null";
    } else {
        ask = "\"" + ask + "\"";
    }
    if (candle.symbol.empty()) {
        error = "IBKR nie przypisało świecy do instrumentu.";
        return false;
    }
    const std::string volume = candle.volume.empty()
        ? "null"
        : "\"" + escapeJsonString(candle.volume) + "\"";
    requestBody =
        "{\"symbol\":\"" + escapeJsonString(candle.symbol) +
        "\",\"datetime\":\"" + utcDatetime +
        "\",\"open\":\"" + open + "\",\"high\":\"" + high +
        "\",\"low\":\"" + low + "\",\"close\":\"" + close +
        "\",\"volume\":" + volume + ",\"bid\":" + bid +
        ",\"ask\":" + ask + "}";
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
bool readResponse(
    HINTERNET request,
    std::string& response,
    std::string& error,
    std::size_t maximumResponseBytes = maxResponseSize) {
    while (true) {
        DWORD bytesAvailable = 0;
        if (!WinHttpQueryDataAvailable(request, &bytesAvailable)) {
            error = "Nie udało się odczytać odpowiedzi raportu świec.";
            return false;
        }
        if (bytesAvailable == 0) {
            return true;
        }
        if (response.size() + bytesAvailable > maximumResponseBytes) {
            error = "Odpowiedź lokalnego API przekroczyła dozwolony rozmiar.";
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
 * Wysyła żądanie JSON i odczytuje odpowiedź o oczekiwanym lub jawnie dozwolonym statusie.
 * @param path Ścieżka lokalnego endpointu.
 * @param requestBody Treść żądania w formacie JSON.
 * @param expectedStatus Oczekiwany kod HTTP.
 * @param responseBody Otrzymuje treść odpowiedzi.
 * @param error Otrzymuje opis błędu transportu lub nieoczekiwanego kodu HTTP.
 * @param responseStatus Opcjonalnie otrzymuje kod HTTP odczytany z odpowiedzi.
 * @param allowServiceUnavailable Czy wolno odczytać treść odpowiedzi HTTP 503.
 * @param includeErrorResponse Czy do błędu dołączyć szczegóły odpowiedzi API.
 * @return true, jeśli odpowiedź ma oczekiwany lub jawnie dozwolony status.
 */
bool sendApiRequest(
    const std::wstring& path,
    const std::string& requestBody,
    DWORD expectedStatus,
    std::string& responseBody,
    std::string& error,
    DWORD* responseStatus = nullptr,
    bool allowServiceUnavailable = false,
    bool includeErrorResponse = false) {
    InternetHandle session{WinHttpOpen(
        L"gie-da/1.0", WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0)};
    if (session.value == nullptr) {
        error = "Nie udało się rozpocząć żądania do lokalnego API.";
        return false;
    }
    const DWORD receiveTimeoutMs =
        std::wstring_view(path) == singleCandleApiPath
        ? singleCandleReceiveTimeoutMs
        : std::wstring_view(path) == ibkrCandleBatchApiPath
        ? ibkrBatchReceiveTimeoutMs
        : apiRequestTimeoutMs;
    WinHttpSetTimeouts(
        session.value,
        apiRequestTimeoutMs,
        apiRequestTimeoutMs,
        apiRequestTimeoutMs,
        receiveTimeoutMs);

    InternetHandle connection{WinHttpConnect(session.value, apiHost, apiPort, 0)};
    if (connection.value == nullptr) {
        error = "Nie można połączyć się z lokalnym API bazy danych.";
        return false;
    }

    InternetHandle request{WinHttpOpenRequest(
        connection.value, L"POST", path.c_str(), nullptr,
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
    if (responseStatus != nullptr) {
        *responseStatus = statusCode;
    }
    const bool serviceUnavailableAllowed =
        statusCode == 503 && allowServiceUnavailable;
    if (statusCode != expectedStatus && !serviceUnavailableAllowed) {
        error = "Lokalne API zwróciło HTTP " +
                std::to_string(statusCode) + " podczas zapisu świec.";
        if (includeErrorResponse) {
            std::string responseReadError;
            if (!readResponse(
                    request.value, responseBody, responseReadError)) {
                error += " " + responseReadError;
            } else if (!responseBody.empty()) {
                constexpr std::size_t maximumErrorDetailsLength = 512;
                if (responseBody.size() > maximumErrorDetailsLength) {
                    responseBody.resize(maximumErrorDetailsLength);
                }
                error += " Szczegóły: " + responseBody;
            }
        }
        return false;
    }
    return readResponse(request.value, responseBody, error);
}

/**
 * Pobiera tekstową odpowiedź GET z lokalnego API i sprawdza kod HTTP.
 */
bool fetchApiResponse(
    const std::wstring& path,
    std::string& responseBody,
    std::string& error,
    std::size_t maximumResponseBytes = maxResponseSize) {
    InternetHandle session{WinHttpOpen(
        L"gie-da/1.0", WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0)};
    if (session.value == nullptr) {
        error = "Nie udało się rozpocząć pobierania danych do analizy.";
        return false;
    }
    WinHttpSetTimeouts(session.value, 3000, 3000, 3000, 3000);

    InternetHandle connection{WinHttpConnect(session.value, apiHost, apiPort, 0)};
    if (connection.value == nullptr) {
        error = "Nie można połączyć się z lokalnym API bazy danych.";
        return false;
    }

    InternetHandle request{WinHttpOpenRequest(
        connection.value, L"GET", path.c_str(), nullptr,
        WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0)};
    if (request.value == nullptr ||
        !WinHttpSendRequest(
            request.value, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
            WINHTTP_NO_REQUEST_DATA, 0, 0, 0) ||
        !WinHttpReceiveResponse(request.value, nullptr)) {
        error = "Lokalne API nie odpowiedziało na żądanie danych do analizy.";
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
        error = "Lokalne API zwróciło HTTP " +
                std::to_string(statusCode) + " podczas pobierania danych.";
        return false;
    }
    return readResponse(
        request.value, responseBody, error, maximumResponseBytes);
}

/**
 * Wysyła treść JSON do lokalnego API i odczytuje odpowiedź tekstową.
 * @param path Ścieżka lokalnego endpointu API.
 * @param requestBody Treść żądania w formacie JSON.
 * @param responseBody Otrzymuje treść odpowiedzi serwera.
 * @param error Otrzymuje opis błędu transportu lub odpowiedzi serwera.
 * @param maximumResponseBytes Maksymalny rozmiar akceptowanej odpowiedzi.
 * @return true, jeśli serwer zwrócił odpowiedź HTTP z kodem sukcesu.
 * @throws std::bad_alloc Gdy nie można przydzielić pamięci na żądanie lub odpowiedź.
 */
bool postApiResponse(
    const std::wstring& path,
    const std::string& requestBody,
    std::string& responseBody,
    std::string& error,
    std::size_t maximumResponseBytes = 4 * 1024 * 1024) {
    if (requestBody.size() > std::numeric_limits<DWORD>::max()) {
        error = "Dane raportu transakcji są zbyt duże.";
        return false;
    }

    InternetHandle session{WinHttpOpen(
        L"gie-da/1.0", WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0)};
    if (session.value == nullptr) {
        error = "Nie udało się rozpocząć żądania raportu transakcji.";
        return false;
    }
    WinHttpSetTimeouts(session.value, 3000, 3000, 3000, 10000);

    InternetHandle connection{WinHttpConnect(session.value, apiHost, apiPort, 0)};
    if (connection.value == nullptr) {
        error = "Nie można połączyć się z lokalnym API bazy danych.";
        return false;
    }

    InternetHandle request{WinHttpOpenRequest(
        connection.value, L"POST", path.c_str(), nullptr,
        WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0)};
    if (request.value == nullptr) {
        error = "Nie udało się utworzyć żądania raportu transakcji.";
        return false;
    }

    constexpr wchar_t requestHeaders[] =
        L"Content-Type: application/json\r\n";
    const DWORD requestSize = static_cast<DWORD>(requestBody.size());
    if (!WinHttpSendRequest(
            request.value, requestHeaders, static_cast<DWORD>(-1L),
            const_cast<char*>(requestBody.data()), requestSize,
            requestSize, 0) ||
        !WinHttpReceiveResponse(request.value, nullptr)) {
        error = "Lokalne API nie odpowiedziało na żądanie raportu transakcji.";
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
        error = "Lokalne API zwróciło HTTP " +
                std::to_string(statusCode) +
                " podczas generowania raportu transakcji.";
        return false;
    }
    return readResponse(
        request.value, responseBody, error, maximumResponseBytes);
}

/**
 * Dodaje tekst UTF-8 do treści JSON, zabezpieczając znaki sterujące i cudzysłowy.
 * @param json Otrzymuje dopisywany tekst JSON.
 * @param value Tekst UTF-8 do zapisania jako wartość JSON.
 * @return Brak (funkcja dopisuje wynik do parametru json).
 * @throws std::bad_alloc Gdy nie można powiększyć bufora JSON.
 */
void appendJsonString(std::string& json, const std::string& value) {
    constexpr char hexDigits[] = "0123456789ABCDEF";
    json.push_back('"');
    for (const unsigned char character : value) {
        switch (character) {
        case '"':
            json += "\\\"";
            break;
        case '\\':
            json += "\\\\";
            break;
        case '\b':
            json += "\\b";
            break;
        case '\f':
            json += "\\f";
            break;
        case '\n':
            json += "\\n";
            break;
        case '\r':
            json += "\\r";
            break;
        case '\t':
            json += "\\t";
            break;
        default:
            if (character < 0x20) {
                json += "\\u00";
                json.push_back(hexDigits[character >> 4]);
                json.push_back(hexDigits[character & 0x0F]);
            } else {
                json.push_back(static_cast<char>(character));
            }
            break;
        }
    }
    json.push_back('"');
}

/**
 * Odczytuje cyfrę szesnastkową używaną w kodowaniu procentowym.
 * @param character Znak cyfry szesnastkowej.
 * @return Wartość od 0 do 15 albo -1 dla niepoprawnego znaku.
 */
int hexDigitValue(char character) {
    if (character >= '0' && character <= '9') {
        return character - '0';
    }
    if (character >= 'A' && character <= 'F') {
        return character - 'A' + 10;
    }
    if (character >= 'a' && character <= 'f') {
        return character - 'a' + 10;
    }
    return -1;
}

/**
 * Odtwarza tekst UTF-8 zakodowany procentowo w odpowiedzi TSV API.
 * @param encodedText Tekst z kodowaniem procentowym.
 * @param decodedText Otrzymuje zdekodowany tekst UTF-8.
 * @return true, jeśli kodowanie procentowe wejścia było poprawne.
 * @throws std::bad_alloc Gdy nie można przydzielić pamięci na wynik.
 */
bool decodePercentEncoded(
    std::string_view encodedText, std::string& decodedText) {
    std::string decoded;
    decoded.reserve(encodedText.size());
    for (std::size_t index = 0; index < encodedText.size(); ++index) {
        if (encodedText[index] != '%') {
            decoded.push_back(encodedText[index]);
            continue;
        }
        if (index + 2 >= encodedText.size()) {
            return false;
        }
        const int high = hexDigitValue(encodedText[index + 1]);
        const int low = hexDigitValue(encodedText[index + 2]);
        if (high < 0 || low < 0) {
            return false;
        }
        decoded.push_back(static_cast<char>((high << 4) | low));
        index += 2;
    }
    decodedText = std::move(decoded);
    return true;
}

/**
 * Rozdziela i dekoduje wiersz TSV o ustalonej liczbie kolumn.
 * @param row Wiersz danych z odpowiedzi API.
 * @param expectedColumns Oczekiwana liczba pól w wierszu.
 * @param fields Otrzymuje zdekodowane pola wiersza.
 * @return true, jeśli wszystkie pola wiersza są poprawnie zakodowane.
 * @throws std::bad_alloc Gdy nie można przydzielić pamięci na pola wiersza.
 */
bool parseEncodedTsvRow(
    std::string_view row,
    std::size_t expectedColumns,
    std::vector<std::string>& fields) {
    std::vector<std::string> decodedFields;
    decodedFields.reserve(expectedColumns);
    std::size_t fieldStart = 0;
    for (std::size_t index = 0; index < expectedColumns; ++index) {
        const std::size_t separator = row.find('\t', fieldStart);
        if (index + 1 < expectedColumns &&
            separator == std::string_view::npos) {
            return false;
        }
        if (index + 1 == expectedColumns &&
            separator != std::string_view::npos) {
            return false;
        }
        const std::size_t fieldEnd =
            separator == std::string_view::npos ? row.size() : separator;
        std::string decodedField;
        if (!decodePercentEncoded(
                row.substr(fieldStart, fieldEnd - fieldStart),
                decodedField)) {
            return false;
        }
        decodedFields.push_back(std::move(decodedField));
        fieldStart = fieldEnd + 1;
    }
    fields = std::move(decodedFields);
    return true;
}

// Backend
/**
 * Odczytuje ograniczony podzbiór JSON używany w odpowiedziach powiadomień.
 * Nie modyfikuje wejścia; niezgodne wartości i przepełnienia są odrzucane.
 */
class NotificationJsonReader {
public:
    /**
     * Przygotowuje czytnik dla treści odpowiedzi.
     * @param text Odpowiedź JSON przechowywana przez wywołującego.
     */
    explicit NotificationJsonReader(std::string_view text) : text_(text) {}

    /**
     * Odczytuje oczekiwany znak po pominięciu białych znaków JSON.
     * @param expected Oczekiwany znak.
     * @return true, jeśli znak wystąpił w bieżącym miejscu.
     */
    bool consume(char expected) {
        skipWhitespace();
        if (position_ == text_.size() || text_[position_] != expected) {
            return false;
        }
        ++position_;
        return true;
    }

    /**
     * Odczytuje nazwę pola z prostego łańcucha JSON.
     * @param key Otrzymuje widok nazwy pola.
     * @return true, jeśli nazwa jest poprawnym niekodowanym łańcuchem.
     */
    bool readKey(std::string_view& key) {
        if (!consume('"')) {
            return false;
        }
        const std::size_t start = position_;
        while (position_ < text_.size() && text_[position_] != '"') {
            const unsigned char character = text_[position_];
            if (character < 0x20 || character == '\\') {
                return false;
            }
            ++position_;
        }
        if (position_ == text_.size()) {
            return false;
        }
        key = text_.substr(start, position_ - start);
        ++position_;
        return true;
    }

    /**
     * Odczytuje nieujemną liczbę całkowitą JSON bez utraty precyzji.
     * @param number Otrzymuje liczbę mieszczącą się w uint64.
     * @return true, jeśli zapis i zakres liczby są prawidłowe.
     */
    bool readNumber(std::uint64_t& number) {
        skipWhitespace();
        const std::size_t start = position_;
        while (position_ < text_.size() &&
               text_[position_] >= '0' && text_[position_] <= '9') {
            ++position_;
        }
        if (position_ == start ||
            (position_ - start > 1 && text_[start] == '0')) {
            return false;
        }
        const auto result = std::from_chars(
            text_.data() + start, text_.data() + position_, number);
        return result.ec == std::errc{} &&
            result.ptr == text_.data() + position_;
    }

    /**
     * Odczytuje wartość logiczną JSON.
     * @param value Otrzymuje odczytaną wartość.
     * @return true, jeśli odczytano true albo false.
     */
    bool readBool(bool& value) {
        skipWhitespace();
        if (text_.substr(position_, 4) == "true") {
            position_ += 4;
            value = true;
            return true;
        }
        if (text_.substr(position_, 5) == "false") {
            position_ += 5;
            value = false;
            return true;
        }
        return false;
    }

    /**
     * Sprawdza, czy po zamknięciu obiektu pozostały tylko białe znaki.
     * @return true, jeśli wykorzystano całą odpowiedź.
     */
    bool atEnd() {
        skipWhitespace();
        return position_ == text_.size();
    }

private:
    /**
     * Pomija wyłącznie białe znaki dopuszczone przez JSON.
     */
    void skipWhitespace() {
        while (position_ < text_.size() &&
               (text_[position_] == ' ' || text_[position_] == '\t' ||
                text_[position_] == '\r' || text_[position_] == '\n')) {
            ++position_;
        }
    }

    std::string_view text_;
    std::size_t position_ = 0;
};

/**
 * Sprawdza odpowiedź kursora startowego bez zmiany wyniku przy błędzie.
 * @param response Treść odpowiedzi JSON.
 * @param cursor Otrzymuje odczytany kursor.
 * @return true, jeśli odpowiedź zawiera dokładnie poprawny kursor.
 */
bool parseGrokNotificationCursor(
    std::string_view response, std::uint64_t& cursor) {
    NotificationJsonReader reader(response);
    std::string_view key;
    std::uint64_t parsedCursor = 0;
    if (!reader.consume('{') || !reader.readKey(key) || key != "cursor" ||
        !reader.consume(':') || !reader.readNumber(parsedCursor) ||
        !reader.consume('}') || !reader.atEnd()) {
        return false;
    }
    cursor = parsedCursor;
    return true;
}

/**
 * Sprawdza wszystkie pola strony i ich spójność z poprzednim kursorem.
 * @param response Treść odpowiedzi JSON.
 * @param afterId Ostatni wcześniej obsłużony identyfikator.
 * @param page Otrzymuje poprawną stronę; przy błędzie pozostaje bez zmian.
 * @return true, jeśli cała odpowiedź jest poprawna.
 */
bool parseGrokNotificationPage(
    std::string_view response, std::uint64_t afterId,
    GrokNotificationPage& page) {
    NotificationJsonReader reader(response);
    GrokNotificationPage parsed;
    bool hasIds = false;
    bool hasCursor = false;
    bool hasMore = false;
    if (!reader.consume('{')) {
        return false;
    }
    for (std::size_t field = 0; field < 3; ++field) {
        std::string_view key;
        if ((field > 0 && !reader.consume(',')) ||
            !reader.readKey(key) || !reader.consume(':')) {
            return false;
        }
        if (key == "ids" && !hasIds) {
            hasIds = true;
            if (!reader.consume('[')) {
                return false;
            }
            if (!reader.consume(']')) {
                do {
                    std::uint64_t id = 0;
                    if (parsed.ids.size() >= grokNotificationPageSize ||
                        !reader.readNumber(id) ||
                        id <= (parsed.ids.empty() ? afterId : parsed.ids.back())) {
                        return false;
                    }
                    parsed.ids.push_back(id);
                    if (reader.consume(']')) {
                        break;
                    }
                    if (!reader.consume(',')) {
                        return false;
                    }
                } while (true);
            }
        } else if (key == "next_cursor" && !hasCursor) {
            hasCursor = true;
            if (!reader.readNumber(parsed.nextCursor)) {
                return false;
            }
        } else if (key == "has_more" && !hasMore) {
            hasMore = true;
            if (!reader.readBool(parsed.hasMore)) {
                return false;
            }
        } else {
            return false;
        }
    }
    if (!hasIds || !hasCursor || !hasMore || !reader.consume('}') ||
        !reader.atEnd() ||
        parsed.nextCursor != (parsed.ids.empty() ? afterId : parsed.ids.back()) ||
        (parsed.hasMore && parsed.ids.empty())) {
        return false;
    }
    page = std::move(parsed);
    return true;
}

/**
 * Sprawdza listę oczekujących identyfikatorów USD/MXN.
 * @param response Treść odpowiedzi JSON.
 * @param notificationIds Otrzymuje identyfikatory w rosnącej kolejności.
 * @return true, jeśli odpowiedź jest poprawna i nie przekracza limitu strony.
 */
bool parseUsdmxnNotificationIds(
    std::string_view response,
    std::vector<std::uint64_t>& notificationIds) {
    NotificationJsonReader reader(response);
    std::vector<std::uint64_t> parsed;
    std::string_view key;
    if (!reader.consume('{') || !reader.readKey(key) || key != "ids" ||
        !reader.consume(':') || !reader.consume('[')) {
        return false;
    }
    if (!reader.consume(']')) {
        do {
            std::uint64_t id = 0;
            if (parsed.size() >= usdmxnNotificationPageSize ||
                !reader.readNumber(id) ||
                id == 0 ||
                (!parsed.empty() && id <= parsed.back())) {
                return false;
            }
            parsed.push_back(id);
            if (reader.consume(']')) {
                break;
            }
            if (!reader.consume(',')) {
                return false;
            }
        } while (true);
    }
    if (!reader.consume('}') || !reader.atEnd()) {
        return false;
    }
    notificationIds = std::move(parsed);
    return true;
}

/**
 * Sprawdza potwierdzenie odtworzenia zwrócone przez API.
 * @param response Treść odpowiedzi JSON.
 * @return true, jeśli API potwierdziło powiadomienie.
 */
bool parseUsdmxnNotificationAck(std::string_view response) {
    NotificationJsonReader reader(response);
    std::string_view key;
    bool acknowledged = false;
    return reader.consume('{') && reader.readKey(key) &&
        key == "acknowledged" && reader.consume(':') &&
        reader.readBool(acknowledged) && acknowledged &&
        reader.consume('}') && reader.atEnd();
}

/**
 * Sprawdza listę oczekujących identyfikatorów USD/JPY.
 * @param response Treść odpowiedzi JSON.
 * @param notificationIds Otrzymuje identyfikatory w rosnącej kolejności.
 * @return true, jeśli odpowiedź jest poprawna i nie przekracza limitu strony.
 */
bool parseUsdjpyNotificationIds(
    std::string_view response,
    std::vector<std::uint64_t>& notificationIds) {
    NotificationJsonReader reader(response);
    std::vector<std::uint64_t> parsed;
    std::string_view key;
    if (!reader.consume('{') || !reader.readKey(key) || key != "ids" ||
        !reader.consume(':') || !reader.consume('[')) {
        return false;
    }
    if (!reader.consume(']')) {
        do {
            std::uint64_t id = 0;
            if (parsed.size() >= usdjpyNotificationPageSize ||
                !reader.readNumber(id) ||
                id == 0 ||
                (!parsed.empty() && id <= parsed.back())) {
                return false;
            }
            parsed.push_back(id);
            if (reader.consume(']')) {
                break;
            }
            if (!reader.consume(',')) {
                return false;
            }
        } while (true);
    }
    if (!reader.consume('}') || !reader.atEnd()) {
        return false;
    }
    notificationIds = std::move(parsed);
    return true;
}

/**
 * Sprawdza potwierdzenie odtworzenia zwrócone przez API.
 * @param response Treść odpowiedzi JSON.
 * @return true, jeśli API potwierdziło powiadomienie.
 */
bool parseUsdjpyNotificationAck(std::string_view response) {
    NotificationJsonReader reader(response);
    std::string_view key;
    bool acknowledged = false;
    return reader.consume('{') && reader.readKey(key) &&
        key == "acknowledged" && reader.consume(':') &&
        reader.readBool(acknowledged) && acknowledged &&
        reader.consume('}') && reader.atEnd();
}

/**
 * Pobiera i waliduje listę symboli zwróconą jako osobne wiersze tekstu.
 */
bool fetchSymbolList(
    const std::wstring& path,
    std::vector<std::string>& symbols,
    std::string& error,
    bool requireAtLeastOne) {
    symbols.clear();
    std::string responseBody;
    if (!fetchApiResponse(path, responseBody, error)) {
        return false;
    }

    std::size_t lineStart = 0;
    while (lineStart < responseBody.size()) {
        const std::size_t lineEnd = responseBody.find('\n', lineStart);
        const std::size_t effectiveEnd =
            lineEnd == std::string::npos ? responseBody.size() : lineEnd;
        std::string symbol = responseBody.substr(
            lineStart, effectiveEnd - lineStart);
        if (!symbol.empty() && symbol.back() == '\r') {
            symbol.pop_back();
        }

        std::wstring encodedSymbol;
        if (symbol.empty() ||
            !encodeSymbolForUrl(symbol, encodedSymbol) ||
            std::find(symbols.begin(), symbols.end(), symbol) != symbols.end()) {
            error = "Lokalne API zwróciło pusty, powtórzony lub nieprawidłowy symbol.";
            return false;
        }
        symbols.push_back(std::move(symbol));
        if (lineEnd == std::string::npos) {
            break;
        }
        lineStart = lineEnd + 1;
    }

    if (requireAtLeastOne && symbols.empty()) {
        error = "Lokalne API nie ma aktywnych instrumentów do monitorowania.";
        return false;
    }
    return true;
}

/**
 * Dzieli wiersz z danymi świecy na sześć pól rozdzielonych tabulatorami.
 */
bool splitAnalysisCandleRow(
    std::string_view row, std::string_view fields[6]) {
    std::size_t fieldStart = 0;
    for (std::size_t index = 0; index < 5; ++index) {
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
    fields[5] = row.substr(fieldStart);
    return true;
}

/**
 * Parsuje skończoną liczbę zmiennoprzecinkową z pola odpowiedzi API.
 */
bool parseAnalysisPrice(std::string_view text, double& value) {
    if (text.empty()) {
        return false;
    }
    const std::string ownedText(text);
    char* parsedEnd = nullptr;
    value = std::strtod(ownedText.c_str(), &parsedEnd);
    return parsedEnd == ownedText.c_str() + ownedText.size() &&
        std::isfinite(value);
}

/**
 * Dzieli wiersz raportu na pola i opcjonalne kwotowania bid/ask.
 */
bool splitCandleRow(
    std::string_view row,
    CandleReportEntry& candle,
    bool includeQuotes) {
    std::string_view fields[9];
    const std::size_t fieldCount = includeQuotes ? 9 : 7;
    std::size_t fieldStart = 0;
    for (std::size_t index = 0; index + 1 < fieldCount; ++index) {
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
    fields[fieldCount - 1] = row.substr(fieldStart);

    for (std::size_t index = 0; index < fieldCount; ++index) {
        const bool optionalValue =
            index == 6 || (includeQuotes && index >= 7);
        if ((!optionalValue && fields[index].empty()) ||
            fields[index].size() > 512) {
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
    candle.bid = includeQuotes ? fields[7] : std::string_view{};
    candle.ask = includeQuotes ? fields[8] : std::string_view{};
    return true;
}

/**
 * Wysyła żądanie stronicowanego raportu i pobiera jego surowe wiersze TSV.
 */
bool fetchPagedTsvRows(
    const std::wstring& requestPath,
    std::size_t maximumRows,
    std::vector<std::string>& rows,
    bool& hasNextPage,
    std::string& error) {
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

    std::vector<std::string> receivedRows;
    std::size_t lineStart = headerEnd == std::string::npos
        ? response.size()
        : headerEnd + 1;
    while (lineStart < response.size()) {
        const std::size_t lineEnd = response.find('\n', lineStart);
        const std::size_t effectiveEnd =
            lineEnd == std::string::npos ? response.size() : lineEnd;
        if (effectiveEnd > lineStart) {
            if (receivedRows.size() == maximumRows ||
                effectiveEnd - lineStart > 4096) {
                error = "Lokalne API raportów zwróciło zbyt wiele lub za długie wiersze.";
                return false;
            }
            receivedRows.emplace_back(
                response.substr(lineStart, effectiveEnd - lineStart));
        }
        if (lineEnd == std::string::npos) {
            break;
        }
        lineStart = lineEnd + 1;
    }

    rows = std::move(receivedRows);
    hasNextPage = hasNextPageText == "1";
    return true;
}

/**
 * Sprawdza wiersze raportu świec z opcjonalnymi kolumnami bid/ask.
 */
bool fetchCandlePageFromApi(
    const std::wstring& requestPath,
    std::size_t maximumRows,
    std::vector<CandleReportEntry>& candles,
    bool& hasNextPage,
    std::string& error,
    bool includeQuotes = false) {
    std::vector<std::string> rows;
    bool receivedHasNextPage = false;
    if (!fetchPagedTsvRows(
            requestPath, maximumRows, rows, receivedHasNextPage, error)) {
        return false;
    }

    std::vector<CandleReportEntry> receivedCandles;
    receivedCandles.reserve(rows.size());
    for (const std::string& row : rows) {
        CandleReportEntry candle;
        if (!splitCandleRow(row, candle, includeQuotes)) {
            error = "Lokalne API raportów zwróciło nieprawidłowe dane świec.";
            return false;
        }
        receivedCandles.push_back(std::move(candle));
    }

    candles = std::move(receivedCandles);
    hasNextPage = receivedHasNextPage;
    return true;
}

/**
 * Dzieli wiersz zestawienia na etykietę czasu i osiem cen.
 */
bool splitCandleComparisonRow(
    std::string_view row, CandleComparisonEntry& candle) {
    std::string_view fields[9];
    std::size_t fieldStart = 0;
    for (std::size_t index = 0; index < 8; ++index) {
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
    fields[8] = row.substr(fieldStart);

    for (const std::string_view field : fields) {
        if (field.empty() || field.size() > 512) {
            return false;
        }
    }

    candle.symbolAndDatetime.assign(fields[0]);
    for (std::size_t index = 0; index < candle.prices.size(); ++index) {
        candle.prices[index].assign(fields[index + 1]);
    }
    return true;
}

/**
 * Pobiera i sprawdza wiersze dziewięciokolumnowego zestawienia świec.
 */
bool fetchCandleComparisonPageFromApi(
    const std::wstring& requestPath,
    std::size_t maximumRows,
    std::vector<CandleComparisonEntry>& candles,
    bool& hasNextPage,
    std::string& error) {
    std::vector<std::string> rows;
    bool receivedHasNextPage = false;
    if (!fetchPagedTsvRows(
            requestPath, maximumRows, rows, receivedHasNextPage, error)) {
        return false;
    }

    std::vector<CandleComparisonEntry> receivedCandles;
    receivedCandles.reserve(rows.size());
    for (const std::string& row : rows) {
        CandleComparisonEntry candle;
        if (!splitCandleComparisonRow(row, candle)) {
            error =
                "Lokalne API raportów zwróciło nieprawidłowe zestawienie świec.";
            return false;
        }
        receivedCandles.push_back(std::move(candle));
    }

    candles = std::move(receivedCandles);
    hasNextPage = receivedHasNextPage;
    return true;
}

/**
 * Dzieli wiersz raportu luki na pięć wymaganych pól.
 */
bool splitCandleGapRow(std::string_view row, CandleGapReportEntry& gap) {
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

    for (std::size_t index = 0; index < 5; ++index) {
        if (fields[index].empty() || fields[index].size() > 128) {
            return false;
        }
    }
    if (fields[2] != "missing" && fields[2] != "filled") {
        return false;
    }

    gap.symbol.assign(fields[0]);
    gap.candleDatetimeUtc.assign(fields[1]);
    gap.status.assign(fields[2]);
    gap.detectedAtUtc.assign(fields[3]);
    gap.filledAtUtc.assign(fields[4]);
    return true;
}

/**
 * Sprawdza i rozdziela wiersz raportu zawierający dziesięć pól sygnału.
 */
bool splitSignalReportRow(
    std::string_view row, SignalReportEntry& signal) {
    std::string_view fields[10];
    std::size_t fieldStart = 0;
    for (std::size_t index = 0; index < 9; ++index) {
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
    fields[9] = row.substr(fieldStart);

    for (std::size_t index = 0; index < 10; ++index) {
        if ((index != 9 && fields[index].empty()) ||
            fields[index].size() > (index == 8 ? 8192 : 512)) {
            return false;
        }
    }

    signal.id.assign(fields[0]);
    signal.datetime.assign(fields[1]);
    signal.product.assign(fields[2]);
    signal.direction.assign(fields[3]);
    signal.algorithmName.assign(fields[4]);
    signal.aiDecision.assign(fields[5]);
    signal.transactionId.assign(fields[6]);
    signal.candlesCount.assign(fields[7]);
    signal.signalParameters.assign(fields[8]);
    signal.maxProfitAfter20Minutes.assign(fields[9]);
    return true;
}

} // namespace

/**
 * Pobiera początkowy kursor powiadomień Groka bez odtwarzania historii.
 * @param cursor Otrzymuje największy identyfikator zapisany w chwili odczytu.
 * @param error Otrzymuje opis błędu żądania lub nieprawidłowej odpowiedzi.
 * @return true, jeśli kursor został poprawnie pobrany.
 */
bool CandleHistoryClient::fetchGrokNotificationCursor(
    std::uint64_t& cursor, std::string& error) {
    std::string response;
    if (!fetchApiResponse(grokNotificationCursorPath, response, error)) {
        return false;
    }
    if (!parseGrokNotificationCursor(response, cursor)) {
        error = "Lokalne API zwróciło nieprawidłowy kursor powiadomień Groka.";
        return false;
    }
    return true;
}

/**
 * Pobiera stronę identyfikatorów i sprawdza jej spójność przed przekazaniem.
 * @param afterId Ostatni wcześniej obsłużony identyfikator.
 * @param page Otrzymuje poprawną stronę bez zmiany przy błędzie.
 * @param error Otrzymuje opis błędu żądania lub nieprawidłowej odpowiedzi.
 * @return true, jeśli strona została poprawnie pobrana i sprawdzona.
 */
bool CandleHistoryClient::fetchGrokNotifications(
    std::uint64_t afterId, GrokNotificationPage& page, std::string& error) {
    std::string response;
    if (!fetchApiResponse(
            std::wstring(grokNotificationsPath) + std::to_wstring(afterId) +
                L"&limit=" + std::to_wstring(grokNotificationPageSize),
            response, error)) {
        return false;
    }
    if (!parseGrokNotificationPage(response, afterId, page)) {
        error = "Lokalne API zwróciło nieprawidłową stronę powiadomień Groka.";
        return false;
    }
    return true;
}

/**
 * Pobiera oczekujące powiadomienia USD/MXN bez przesuwania ich stanu.
 */
bool CandleHistoryClient::fetchUsdmxnNotificationIds(
    std::vector<std::uint64_t>& notificationIds, std::string& error) {
    std::string response;
    if (!fetchApiResponse(usdmxnNotificationsPath, response, error)) {
        return false;
    }
    std::vector<std::uint64_t> parsedIds;
    if (!parseUsdmxnNotificationIds(response, parsedIds)) {
        error = "Lokalne API zwróciło nieprawidłowe powiadomienia USD/MXN.";
        return false;
    }
    notificationIds = std::move(parsedIds);
    return true;
}

/**
 * Potwierdza pojedyncze powiadomienie dopiero po udanym odtworzeniu dźwięku.
 */
bool CandleHistoryClient::acknowledgeUsdmxnNotification(
    std::uint64_t notificationId, std::string& error) {
    if (notificationId == 0) {
        error = "Identyfikator powiadomienia USD/MXN musi być dodatni.";
        return false;
    }
    const std::wstring path =
        L"/api/signals/usdmxn/notifications/" +
        std::to_wstring(notificationId) + L"/ack";
    std::string response;
    if (!sendApiRequest(path, "{}", 200, response, error)) {
        return false;
    }
    if (!parseUsdmxnNotificationAck(response)) {
        error = "Lokalne API nie potwierdziło powiadomienia USD/MXN.";
        return false;
    }
    return true;
}

/**
 * Pobiera oczekujące powiadomienia USD/JPY bez przesuwania ich stanu.
 */
bool CandleHistoryClient::fetchUsdjpyNotificationIds(
    std::vector<std::uint64_t>& notificationIds, std::string& error) {
    std::string response;
    if (!fetchApiResponse(usdjpyNotificationsPath, response, error)) {
        return false;
    }
    std::vector<std::uint64_t> parsedIds;
    if (!parseUsdjpyNotificationIds(response, parsedIds)) {
        error = "Lokalne API zwróciło nieprawidłowe powiadomienia USD/JPY.";
        return false;
    }
    notificationIds = std::move(parsedIds);
    return true;
}

/**
 * Oznacza pojedyncze powiadomienie USD/JPY jako obsłużone w API.
 */
bool CandleHistoryClient::acknowledgeUsdjpyNotification(
    std::uint64_t notificationId, std::string& error) {
    if (notificationId == 0) {
        error = "Identyfikator powiadomienia USD/JPY musi być dodatni.";
        return false;
    }
    const std::wstring path =
        L"/api/signals/usdjpy/notifications/" +
        std::to_wstring(notificationId) + L"/ack";
    std::string response;
    if (!sendApiRequest(path, "{}", 200, response, error)) {
        return false;
    }
    if (!parseUsdjpyNotificationAck(response)) {
        error = "Lokalne API nie potwierdziło powiadomienia USD/JPY.";
        return false;
    }
    return true;
}

/**
 * Wysyła świecę i rozpoznaje potwierdzony zapis częściowy ES z odpowiedzi HTTP 503.
 * @param symbol Symbol instrumentu, którego dotyczy świeca.
 * @param candle Dane OHLCV i czas otwarcia świecy.
 * @param newlyDetectedGapCount Otrzymuje liczbę wykrytych luk.
 * @param savedSignalCount Otrzymuje liczbę sygnałów przekazywanych bezpośrednio
 *        w odpowiedzi; powiadomienia USD/MXN obsługuje trwały outbox.
 * @param error Otrzymuje opis błędu lub nieukończonej analizy strategii.
 * @param candleSavedDespiteError Opcjonalnie otrzymuje informację o poprawnie
 *        potwierdzonym zapisie świecy, mimo błędu dalszej analizy.
 * @return true po pełnym sukcesie; false po błędzie, również przy potwierdzonym
 *         zapisie częściowym, którego liczniki pozostają wtedy dostępne.
 */
bool CandleHistoryClient::saveCandle(
    const std::string& symbol,
    const CandleData& candle,
    std::size_t& newlyDetectedGapCount,
    std::size_t& savedSignalCount,
    std::string& error,
    bool* candleSavedDespiteError) {
    newlyDetectedGapCount = 0;
    savedSignalCount = 0;
    if (candleSavedDespiteError != nullptr) {
        *candleSavedDespiteError = false;
    }
    std::string utcDatetime;
    if (!formatUtcDatetime(candle.datetime, utcDatetime)) {
        error = "Twelve Data zwróciło czas świecy w nieobsługiwanym formacie.";
        return false;
    }
    const std::string requestBody =
        createRequestBody(symbol, candle, utcDatetime);
    std::string responseBody;
    DWORD responseStatus = 0;
    if (!sendApiRequest(
            singleCandleApiPath,
            requestBody,
            200,
            responseBody,
            error,
            &responseStatus,
            symbol == "ES")) {
        return false;
    }
    if (responseStatus == 503) {
        constexpr std::string_view partialResponseMarker = "SP500_PARTIAL\n";
        const std::string_view partialResponse(responseBody);
        if (symbol != "ES" ||
            partialResponse.substr(0, partialResponseMarker.size()) !=
                partialResponseMarker) {
            error = "Lokalne API zwróciło nieprawidłową odpowiedź HTTP 503.";
            return false;
        }

        const std::size_t gapLineEnd =
            partialResponse.find('\n', partialResponseMarker.size());
        if (gapLineEnd == std::string_view::npos ||
            gapLineEnd + 1 == partialResponse.size()) {
            error = "Lokalne API zwróciło niepełną odpowiedź o zapisie częściowym.";
            return false;
        }

        const std::string_view gapText = partialResponse.substr(
            partialResponseMarker.size(),
            gapLineEnd - partialResponseMarker.size());
        const std::string_view signalText =
            partialResponse.substr(gapLineEnd + 1);
        auto parseCount = [](std::string_view text, std::size_t& count) {
            if (text.empty()) {
                return false;
            }
            for (const unsigned char character : text) {
                if (character < '0' || character > '9') {
                    return false;
                }
            }
            std::size_t parsedCount = 0;
            const auto result = std::from_chars(
                text.data(), text.data() + text.size(), parsedCount);
            if (result.ec != std::errc{} ||
                result.ptr != text.data() + text.size()) {
                return false;
            }
            count = parsedCount;
            return true;
        };
        std::size_t parsedGapCount = 0;
        std::size_t parsedSignalCount = 0;
        if (!parseCount(gapText, parsedGapCount) ||
            !parseCount(signalText, parsedSignalCount)) {
            error = "Lokalne API zwróciło nieprawidłowe liczniki zapisu częściowego.";
            return false;
        }
        newlyDetectedGapCount = parsedGapCount;
        savedSignalCount = parsedSignalCount;
        if (candleSavedDespiteError != nullptr) {
            *candleSavedDespiteError = true;
        }
        error = "co najmniej jedna strategia nie zakończyła analizy (HTTP 503)";
        return false;
    }

    const std::size_t countLineEnd = responseBody.find('\n');
    if (countLineEnd == std::string::npos) {
        error = "Lokalne API nie zwróciło statusu zapisu sygnału.";
        return false;
    }
    std::string_view countText(responseBody.data(), countLineEnd);
    std::string_view signalText(responseBody.data() + countLineEnd + 1,
                                responseBody.size() - countLineEnd - 1);
    if (!countText.empty() && countText.back() == '\r') {
        countText.remove_suffix(1);
    }
    if (!signalText.empty() && signalText.back() == '\r') {
        signalText.remove_suffix(1);
    }
    for (const unsigned char character : countText) {
        if (character < '0' || character > '9') {
            error = "Lokalne API zwróciło nieprawidłową liczbę wykrytych luk.";
            return false;
        }
    }
    for (const unsigned char character : signalText) {
        if (character < '0' || character > '9') {
            error = "Lokalne API zwróciło nieprawidłową liczbę zapisanych sygnałów.";
            return false;
        }
    }
    std::size_t parsedGapCount = 0;
    std::size_t parsedSignalCount = 0;
    try {
        parsedGapCount =
            static_cast<std::size_t>(std::stoull(std::string(countText)));
    } catch (const std::out_of_range&) {
        error = "Lokalne API zwróciło zbyt dużą liczbę wykrytych luk.";
        return false;
    } catch (const std::invalid_argument&) {
        error = "Lokalne API zwróciło nieprawidłową liczbę wykrytych luk.";
        return false;
    }
    try {
        parsedSignalCount =
            static_cast<std::size_t>(std::stoull(std::string(signalText)));
    } catch (const std::out_of_range&) {
        error = "Lokalne API zwróciło zbyt dużą liczbę zapisanych sygnałów.";
        return false;
    } catch (const std::invalid_argument&) {
        error = "Lokalne API zwróciło nieprawidłową liczbę zapisanych sygnałów.";
        return false;
    }
    newlyDetectedGapCount = parsedGapCount;
    savedSignalCount = parsedSignalCount;
    return true;
}

/**
 * Wysyła świecę IBKR i odczytuje zapis oraz liczbę nowych sygnałów.
 */
bool CandleHistoryClient::saveIbkrCandle(
    const IBKRCandleData& candle,
    bool& candleSaved,
    std::size_t& savedSignalCount,
    std::string& error) {
    candleSaved = false;
    savedSignalCount = 0;
    error.clear();
    std::string utcDatetime;
    if (!formatUnixTimestampUtc(candle.timestampUtc, utcDatetime)) {
        error = "IBKR zwróciło nieprawidłowy czas świecy.";
        return false;
    }
    std::string requestBody;
    if (!createIbkrRequestBody(candle, utcDatetime, requestBody, error)) {
        return false;
    }
    std::string responseBody;
    DWORD responseStatus = 0;
    if (!sendApiRequest(
            ibkrCandleApiPath,
            requestBody,
            200,
            responseBody,
            error,
            &responseStatus,
            true,
            true)) {
        return false;
    }

    constexpr std::string_view partialResponseMarker = "SP500_PARTIAL\n";
    std::string_view response(responseBody);
    if (responseStatus == 503) {
        if (response.substr(0, partialResponseMarker.size()) !=
            partialResponseMarker) {
            error = "Lokalne API zwróciło nieprawidłową odpowiedź HTTP 503.";
            return false;
        }
        response.remove_prefix(partialResponseMarker.size());
    }

    while (!response.empty() &&
           (response.back() == '\n' || response.back() == '\r')) {
        response.remove_suffix(1);
    }
    const std::size_t statusLineEnd = response.find('\n');
    if (statusLineEnd == std::string_view::npos) {
        error = "Lokalne API nie zwróciło statusu zapisu świecy IBKR.";
        return false;
    }
    std::string_view candleSavedText = response.substr(0, statusLineEnd);
    std::string_view signalCountText = response.substr(statusLineEnd + 1);
    if (!candleSavedText.empty() && candleSavedText.back() == '\r') {
        candleSavedText.remove_suffix(1);
    }
    if (!signalCountText.empty() && signalCountText.back() == '\r') {
        signalCountText.remove_suffix(1);
    }
    if (signalCountText.find('\n') != std::string_view::npos) {
        error = "Lokalne API zwróciło nadmiarowe dane o sygnałach IBKR.";
        return false;
    }

    auto parseCount = [](std::string_view text, std::size_t& count) {
        if (text.empty()) {
            return false;
        }
        for (const unsigned char character : text) {
            if (character < '0' || character > '9') {
                return false;
            }
        }
        const auto result = std::from_chars(
            text.data(), text.data() + text.size(), count);
        return result.ec == std::errc{} &&
            result.ptr == text.data() + text.size();
    };
    std::size_t savedStatus = 0;
    if (!parseCount(candleSavedText, savedStatus) ||
        savedStatus > 1 ||
        !parseCount(signalCountText, savedSignalCount) ||
        (savedStatus == 0 && savedSignalCount != 0)) {
        error = "Lokalne API zwróciło nieprawidłowy status lub licznik sygnałów IBKR.";
        return false;
    }

    candleSaved = savedStatus == 1;
    if (responseStatus == 503) {
        if (!candleSaved) {
            error = "Lokalne API zwróciło błąd częściowy bez zapisu świecy IBKR.";
            return false;
        }
        error = "Lokalne API zapisało świecę, ale strategia zgłosiła błąd (HTTP 503).";
    }
    return true;
}

/**
 * Wysyła unikalną paczkę świec IBKR do lokalnego API.
 */
bool CandleHistoryClient::saveIbkrCandles(
    const std::vector<IBKRCandleData>& candles,
    std::size_t& savedCandleCount,
    std::string& error) {
    savedCandleCount = 0;
    error.clear();
    if (candles.empty() || candles.size() > maximumIbkrBatchSize) {
        error = "Paczka świec IBKR musi zawierać od 1 do 1000 rekordów.";
        return false;
    }

    std::string requestBody = "{\"candles\":[";
    for (std::size_t index = 0; index < candles.size(); ++index) {
        std::string utcDatetime;
        if (!formatUnixTimestampUtc(candles[index].timestampUtc, utcDatetime)) {
            error = "IBKR zwróciło nieprawidłowy czas świecy.";
            return false;
        }
        std::string candleBody;
        if (!createIbkrRequestBody(
                candles[index], utcDatetime, candleBody, error)) {
            return false;
        }
        if (index != 0) {
            requestBody += ",";
        }
        requestBody += candleBody;
    }
    requestBody += "]}";

    std::string responseBody;
    if (!sendApiRequest(
            ibkrCandleBatchApiPath,
            requestBody,
            200,
            responseBody,
            error,
            nullptr,
            false,
            true)) {
        return false;
    }
    while (!responseBody.empty() &&
           (responseBody.back() == '\n' || responseBody.back() == '\r')) {
        responseBody.pop_back();
    }
    const auto parseResult = std::from_chars(
        responseBody.data(),
        responseBody.data() + responseBody.size(),
        savedCandleCount);
    if (responseBody.empty() ||
        parseResult.ec != std::errc{} ||
        parseResult.ptr != responseBody.data() + responseBody.size() ||
        savedCandleCount > candles.size()) {
        savedCandleCount = 0;
        error = "Lokalne API zwróciło nieprawidłową liczbę zapisanych świec.";
        return false;
    }
    return true;
}

/**
 * Pobiera z API skonfigurowane symbole bez wpisywania ich ponownie w C++.
 */
bool CandleHistoryClient::fetchMonitoredSymbols(
    std::vector<std::string>& symbols, std::string& error) {
    return fetchSymbolList(
        monitoredProductsApiPath, symbols, error, true);
}

/**
 * Pobiera listę rynków otwartych; pusta odpowiedź jest prawidłowa.
 */
bool CandleHistoryClient::fetchOpenSymbols(
    std::vector<std::string>& symbols, std::string& error) {
    return fetchSymbolList(openProductsApiPath, symbols, error, false);
}

/**
 * Pobiera pule sentencji tekstem rozdzielonym wierszami.
 */
bool CandleHistoryClient::fetchDailySentences(
    std::vector<std::string>& sentences, std::string& error) {
    constexpr std::size_t maximumPoolResponseBytes = 5 * 1024 * 1024;
    std::string response;
    if (!fetchApiResponse(
            dailySentencesApiPath,
            response,
            error,
            maximumPoolResponseBytes)) {
        return false;
    }

    std::vector<std::string> parsedSentences;
    std::size_t lineStart = 0;
    while (lineStart < response.size()) {
        const std::size_t lineEnd = response.find('\n', lineStart);
        const std::size_t lineLength = lineEnd == std::string::npos
            ? response.size() - lineStart
            : lineEnd - lineStart;
        std::string sentence = response.substr(lineStart, lineLength);
        if (!sentence.empty() && sentence.back() == '\r') {
            sentence.pop_back();
        }
        if (sentence.empty()) {
            error = "Lokalne API zwróciło pustą sentencję.";
            return false;
        }
        parsedSentences.push_back(std::move(sentence));
        if (lineEnd == std::string::npos) {
            break;
        }
        lineStart = lineEnd + 1;
    }

    if (parsedSentences.empty()) {
        error = "Baza nie zawiera sentencji dnia. Uruchom jednorazowe zasilenie.";
        return false;
    }
    sentences = std::move(parsedSentences);
    return true;
}

/**
 * Pobiera świece EUR/USD i zmienia kolejność odpowiedzi na chronologiczną.
 */
bool CandleHistoryClient::fetchLatestAnalysisCandles(
    std::size_t maximumCandles,
    std::vector<SignalCandleData>& candles,
    std::string& error) {
    candles.clear();
    if (maximumCandles == 0 || maximumCandles > 100) {
        error = "Liczba świec do analizy musi mieścić się w zakresie 1-100.";
        return false;
    }

    std::string responseBody;
    if (!fetchApiResponse(
            std::wstring(analysisCandlesApiPath) +
                std::to_wstring(maximumCandles),
            responseBody, error)) {
        return false;
    }

    std::vector<SignalCandleData> receivedCandles;
    std::size_t lineStart = 0;
    while (lineStart < responseBody.size()) {
        const std::size_t lineEnd = responseBody.find('\n', lineStart);
        const std::size_t effectiveEnd =
            lineEnd == std::string::npos ? responseBody.size() : lineEnd;
        std::string_view row(responseBody.data() + lineStart,
                             effectiveEnd - lineStart);
        if (!row.empty() && row.back() == '\r') {
            row.remove_suffix(1);
        }
        if (!row.empty()) {
            std::string_view fields[6];
            SignalCandleData candle;
            if (receivedCandles.size() == maximumCandles ||
                !splitAnalysisCandleRow(row, fields)) {
                error = "Lokalne API zwróciło nieprawidłową liczbę pól świecy.";
                return false;
            }

            const auto timestampResult = std::from_chars(
                fields[0].data(), fields[0].data() + fields[0].size(),
                candle.timestamp);
            if (timestampResult.ec != std::errc{} ||
                timestampResult.ptr != fields[0].data() + fields[0].size() ||
                !parseAnalysisPrice(fields[1], candle.open) ||
                !parseAnalysisPrice(fields[2], candle.high) ||
                !parseAnalysisPrice(fields[3], candle.low) ||
                !parseAnalysisPrice(fields[4], candle.close) ||
                (!fields[5].empty() &&
                 !parseAnalysisPrice(fields[5], candle.volume))) {
                error = "Lokalne API zwróciło nieprawidłowe wartości świecy.";
                return false;
            }
            receivedCandles.push_back(candle);
        }
        if (lineEnd == std::string::npos) {
            break;
        }
        lineStart = lineEnd + 1;
    }

    std::reverse(receivedCandles.begin(), receivedCandles.end());
    candles = std::move(receivedCandles);
    return true;
}

/**
 * Pobiera ograniczoną historię świec M1 dla kontekstu prompta.
 */
bool CandleHistoryClient::fetchPromptCandleHistory(
    const std::string& symbol,
    std::size_t limit,
    std::string& response,
    std::string& error) {
    response.clear();
    if (limit == 0 || limit > 1000) {
        error = "Liczba świec do kontekstu prompta musi mieścić się w zakresie 1-1000.";
        return false;
    }

    std::wstring encodedSymbol;
    if (!encodeSymbolForUrl(symbol, encodedSymbol)) {
        error = "Symbol instrumentu jest pusty lub nieprawidłowy.";
        return false;
    }
    const std::wstring path =
        std::wstring(promptCandleHistoryApiPath) + encodedSymbol +
        L"&limit=" + std::to_wstring(limit);
    return fetchApiResponse(path, response, error);
}

/**
 * Wysyła sygnał Grok-45 do API i odczytuje potwierdzenie nowego zapisu.
 */
bool CandleHistoryClient::saveGeneratedSignal(
    const std::string& signalJson,
    bool& signalSaved,
    std::string& error) {
    signalSaved = false;
    std::string responseBody;
    if (!sendApiRequest(
            generatedSignalApiPath, signalJson, 200, responseBody, error)) {
        return false;
    }
    if (responseBody == "1") {
        signalSaved = true;
        return true;
    }
    if (responseBody == "0") {
        return true;
    }
    error = "Lokalne API zwróciło nieprawidłowy status zapisu sygnału Grok-45.";
    return false;
}

/**
 * Zapisuje do 1000 świec w jednym żądaniu do lokalnego API.
 */
bool CandleHistoryClient::saveCandles(
    const std::string& symbol,
    const std::vector<CandleData>& candles,
    std::size_t& savedCandleCount,
    std::string& latestSavedDatetime,
    std::string& error) {
    savedCandleCount = 0;
    latestSavedDatetime.clear();
    std::string requestBody;
    if (!createBatchRequestBody(symbol, candles, requestBody, error)) {
        return false;
    }
    std::string responseBody;
    if (!sendApiRequest(
            batchApiPath, requestBody, 200, responseBody, error)) {
        return false;
    }

    const std::size_t countLineEnd = responseBody.find('\n');
    const std::string_view countText = countLineEnd == std::string::npos
        ? std::string_view(responseBody)
        : std::string_view(responseBody).substr(0, countLineEnd);
    if (countText.empty()) {
        error = "Lokalne API nie zwróciło liczby zapisanych świec.";
        return false;
    }
    for (const unsigned char character : countText) {
        if (character < '0' || character > '9') {
            error = "Lokalne API zwróciło nieprawidłową liczbę zapisanych świec.";
            return false;
        }
    }
    try {
        savedCandleCount =
            static_cast<std::size_t>(std::stoull(std::string(countText)));
    } catch (const std::out_of_range&) {
        error = "Lokalne API zwróciło zbyt dużą liczbę zapisanych świec.";
        return false;
    } catch (const std::invalid_argument&) {
        error = "Lokalne API zwróciło nieprawidłową liczbę zapisanych świec.";
        return false;
    }

    if (countLineEnd != std::string::npos) {
        const std::string_view timestamp =
            std::string_view(responseBody).substr(countLineEnd + 1);
        if (!timestamp.empty() &&
            (timestamp.size() != 19 || timestamp[10] != ' ')) {
            error = "Lokalne API zwróciło nieprawidłowy czas ostatniej świecy.";
            return false;
        }
        latestSavedDatetime.assign(timestamp);
    }
    if (savedCandleCount > 0 && latestSavedDatetime.empty()) {
        error = "Lokalne API nie podało czasu zapisanej świecy.";
        return false;
    }
    return true;
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

    const std::wstring requestPath =
        L"/api/candles/report?page=" + std::to_wstring(page);
    return fetchCandlePageFromApi(
        requestPath, pageSize, candles, hasNextPage, error);
}

/**
 * Pobiera stronę raportu świec z osobnej tabeli IBKR.
 */
bool CandleHistoryClient::fetchIbkrCandlePage(
    std::size_t page,
    std::vector<CandleReportEntry>& candles,
    bool& hasNextPage,
    std::string& error) {
    if (page > 1000000 ||
        page > static_cast<std::size_t>(
                   std::numeric_limits<unsigned long long>::max() / pageSize)) {
        error = "Numer strony raportu świec IBKR jest poza dozwolonym zakresem.";
        return false;
    }

    const std::wstring requestPath =
        L"/api/candles/ibkr/report?page=" + std::to_wstring(page);
    return fetchCandlePageFromApi(
        requestPath, pageSize, candles, hasNextPage, error, true);
}

/**
 * Pobiera stronę wspólnych świec z tabel candles i candles_IBKR.
 */
bool CandleHistoryClient::fetchCandleComparisonPage(
    std::size_t page,
    std::vector<CandleComparisonEntry>& candles,
    bool& hasNextPage,
    std::string& error) {
    if (page > 1000000 ||
        page > static_cast<std::size_t>(
                   std::numeric_limits<unsigned long long>::max() / pageSize)) {
        error = "Numer strony zestawienia świec jest poza dozwolonym zakresem.";
        return false;
    }

    const std::wstring requestPath =
        L"/api/candles/comparison/report?page=" + std::to_wstring(page);
    return fetchCandleComparisonPageFromApi(
        requestPath, pageSize, candles, hasNextPage, error);
}

/**
 * Pobiera maksymalnie 10 świec z datami wybranymi w widoku transakcji.
 */
bool CandleHistoryClient::fetchFilteredCandlePage(
    const std::string& startDate,
    const std::string& endDate,
    std::size_t page,
    std::vector<CandleReportEntry>& candles,
    bool& hasNextPage,
    std::string& error) {
    if (page > 1000000 ||
        page > static_cast<std::size_t>(
                   std::numeric_limits<unsigned long long>::max() /
                   filteredCandlePageSize)) {
        error = "Numer strony filtrowanej tabeli świec jest poza zakresem.";
        return false;
    }

    const std::wstring wideStartDate(startDate.begin(), startDate.end());
    const std::wstring wideEndDate(endDate.begin(), endDate.end());
    const std::wstring requestPath =
        L"/api/candles/filtered-report?start_date=" + wideStartDate +
        L"&end_date=" + wideEndDate + L"&page=" + std::to_wstring(page);
    return fetchCandlePageFromApi(
        requestPath, filteredCandlePageSize, candles, hasNextPage, error);
}

/**
 * Pobiera i dekoduje aktualne opcje dynamicznych filtrów transakcji.
 */
bool CandleHistoryClient::fetchTransactionReportOptions(
    std::map<std::string, std::vector<TransactionReportFilterOption>>& options,
    std::string& error) {
    std::string response;
    if (!fetchApiResponse(
            L"/api/reports/transactions/options", response, error,
            4 * 1024 * 1024)) {
        return false;
    }

    std::map<std::string, std::vector<TransactionReportFilterOption>>
        receivedOptions;
    std::size_t lineStart = 0;
    while (lineStart < response.size()) {
        const std::size_t lineEnd = response.find('\n', lineStart);
        const std::size_t effectiveEnd =
            lineEnd == std::string::npos ? response.size() : lineEnd;
        std::string_view line(response.data() + lineStart,
                              effectiveEnd - lineStart);
        if (!line.empty() && line.back() == '\r') {
            line.remove_suffix(1);
        }
        const std::size_t firstSeparator = line.find('\t');
        const std::size_t secondSeparator =
            firstSeparator == std::string_view::npos
                ? std::string_view::npos
                : line.find('\t', firstSeparator + 1);
        if (firstSeparator == std::string_view::npos ||
            secondSeparator == std::string_view::npos ||
            line.find('\t', secondSeparator + 1) != std::string_view::npos) {
            error = "Lokalne API zwróciło nieprawidłowe opcje filtrów.";
            return false;
        }

        TransactionReportFilterOption option;
        if (!decodePercentEncoded(
                line.substr(firstSeparator + 1,
                            secondSeparator - firstSeparator - 1),
                option.value) ||
            !decodePercentEncoded(
                line.substr(secondSeparator + 1), option.label)) {
            error = "Lokalne API zwróciło nieprawidłowy tekst opcji filtra.";
            return false;
        }
        receivedOptions[std::string(line.substr(0, firstSeparator))]
            .push_back(std::move(option));

        if (lineEnd == std::string::npos) {
            break;
        }
        lineStart = lineEnd + 1;
    }

    options = std::move(receivedOptions);
    return true;
}

/**
 * Pobiera i sprawdza dynamiczną stronę raportu transakcji.
 */
bool CandleHistoryClient::fetchTransactionReportPage(
    const std::string& startDate,
    const std::string& endDate,
    std::size_t page,
    const std::vector<std::string>& columns,
    const std::map<std::string, std::vector<std::string>>& filters,
    TransactionReportPage& report,
    std::string& error) {
    if (page > 1000000) {
        error = "Numer strony raportu transakcji jest poza dozwolonym zakresem.";
        return false;
    }
    if (columns.empty() || columns.size() > 20) {
        error = "Raport transakcji musi zawierać od 1 do 20 kolumn.";
        return false;
    }

    std::string requestBody = "{\"columns\":[";
    for (std::size_t index = 0; index < columns.size(); ++index) {
        if (index != 0) {
            requestBody.push_back(',');
        }
        appendJsonString(requestBody, columns[index]);
    }
    requestBody += "],\"filters\":{";
    bool firstFilter = true;
    for (const auto& [columnName, selectedValues] : filters) {
        if (!firstFilter) {
            requestBody.push_back(',');
        }
        firstFilter = false;
        appendJsonString(requestBody, columnName);
        requestBody += ":[";
        for (std::size_t index = 0; index < selectedValues.size(); ++index) {
            if (index != 0) {
                requestBody.push_back(',');
            }
            appendJsonString(requestBody, selectedValues[index]);
        }
        requestBody.push_back(']');
    }
    requestBody += "}}";

    const std::wstring requestPath =
        L"/api/reports/transactions/filtered?start_date=" +
        std::wstring(startDate.begin(), startDate.end()) +
        L"&end_date=" + std::wstring(endDate.begin(), endDate.end()) +
        L"&page=" + std::to_wstring(page);
    std::string response;
    if (!postApiResponse(requestPath, requestBody, response, error)) {
        return false;
    }

    std::size_t responsePosition = 0;
    const auto readLine = [&response, &responsePosition](
                              std::string_view& line) {
        if (responsePosition >= response.size()) {
            return false;
        }
        const std::size_t lineEnd = response.find('\n', responsePosition);
        const std::size_t effectiveEnd =
            lineEnd == std::string::npos ? response.size() : lineEnd;
        line = std::string_view(response).substr(
            responsePosition, effectiveEnd - responsePosition);
        if (!line.empty() && line.back() == '\r') {
            line.remove_suffix(1);
        }
        responsePosition =
            lineEnd == std::string::npos ? response.size() : lineEnd + 1;
        return true;
    };

    std::string_view hasNextPageText;
    std::string_view rowCountText;
    if (!readLine(hasNextPageText) ||
        (hasNextPageText != "0" && hasNextPageText != "1") ||
        !readLine(rowCountText)) {
        error = "Lokalne API zwróciło nieprawidłowy nagłówek raportu.";
        return false;
    }

    std::size_t rowCount = 0;
    const auto rowCountResult = std::from_chars(
        rowCountText.data(), rowCountText.data() + rowCountText.size(),
        rowCount);
    if (rowCountResult.ec != std::errc{} ||
        rowCountResult.ptr != rowCountText.data() + rowCountText.size() ||
        rowCount > filteredCandlePageSize) {
        error = "Lokalne API zwróciło nieprawidłową liczbę rekordów.";
        return false;
    }

    TransactionReportPage receivedReport;
    receivedReport.hasNextPage = hasNextPageText == "1";
    receivedReport.rows.reserve(rowCount);
    for (std::size_t index = 0; index < rowCount; ++index) {
        std::string_view rowText;
        std::vector<std::string> row;
        if (!readLine(rowText) ||
            !parseEncodedTsvRow(rowText, columns.size(), row)) {
            error = "Lokalne API zwróciło nieprawidłowy wiersz raportu.";
            return false;
        }
        receivedReport.rows.push_back(std::move(row));
    }

    std::string_view summaryText;
    if (!readLine(summaryText) ||
        !parseEncodedTsvRow(
            summaryText, columns.size(), receivedReport.summary) ||
        responsePosition != response.size()) {
        error = "Lokalne API zwróciło nieprawidłowe podsumowanie raportu.";
        return false;
    }

    report = std::move(receivedReport);
    return true;
}

/**
 * Pobiera stronę historii luk i sprawdza poprawność każdego wiersza TSV.
 */
bool CandleHistoryClient::fetchCandleGapPage(
    std::size_t page,
    std::vector<CandleGapReportEntry>& gaps,
    bool& hasNextPage,
    std::string& error) {
    if (page > 1000000 ||
        page > static_cast<std::size_t>(
                   std::numeric_limits<unsigned long long>::max() / pageSize)) {
        error = "Numer strony tabeli brakujących świec jest poza zakresem.";
        return false;
    }

    InternetHandle session{WinHttpOpen(
        L"gie-da/1.0", WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0)};
    if (session.value == nullptr) {
        error = "Nie udało się rozpocząć żądania tabeli brakujących świec.";
        return false;
    }
    WinHttpSetTimeouts(session.value, 3000, 3000, 3000, 3000);

    InternetHandle connection{WinHttpConnect(session.value, apiHost, apiPort, 0)};
    if (connection.value == nullptr) {
        error = "Nie można połączyć się z lokalnym API raportów.";
        return false;
    }

    const std::wstring requestPath =
        L"/api/candles/gaps?page=" + std::to_wstring(page);
    InternetHandle request{WinHttpOpenRequest(
        connection.value, L"GET", requestPath.c_str(), nullptr,
        WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0)};
    if (request.value == nullptr ||
        !WinHttpSendRequest(
            request.value, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
            WINHTTP_NO_REQUEST_DATA, 0, 0, 0) ||
        !WinHttpReceiveResponse(request.value, nullptr)) {
        error = "Lokalne API tabeli brakujących świec nie odpowiedziało.";
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
        error = "Lokalne API zwróciło HTTP " +
                std::to_string(statusCode) + " dla tabeli brakujących świec.";
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
        error = "Lokalne API zwróciło nieprawidłową flagę paginacji luk.";
        return false;
    }

    std::vector<CandleGapReportEntry> receivedGaps;
    std::size_t lineStart = headerEnd == std::string::npos
        ? response.size()
        : headerEnd + 1;
    while (lineStart < response.size()) {
        const std::size_t lineEnd = response.find('\n', lineStart);
        const std::size_t effectiveEnd =
            lineEnd == std::string::npos ? response.size() : lineEnd;
        if (effectiveEnd > lineStart) {
            std::string_view row =
                std::string_view(response).substr(
                    lineStart, effectiveEnd - lineStart);
            if (!row.empty() && row.back() == '\r') {
                row.remove_suffix(1);
            }
            CandleGapReportEntry gap;
            if (receivedGaps.size() == pageSize ||
                !splitCandleGapRow(row, gap)) {
                error = "Lokalne API zwróciło nieprawidłowe dane luk.";
                return false;
            }
            receivedGaps.push_back(std::move(gap));
        }
        if (lineEnd == std::string::npos) {
            break;
        }
        lineStart = lineEnd + 1;
    }

    gaps = std::move(receivedGaps);
    hasNextPage = hasNextPageText == "1";
    return true;
}

/**
 * Pobiera i waliduje stronę historii sygnałów z lokalnego API.
 */
bool CandleHistoryClient::fetchSignalPage(
    std::size_t page,
    std::vector<SignalReportEntry>& signals,
    bool& hasNextPage,
    std::string& error) {
    if (page > 1000000 ||
        page > static_cast<std::size_t>(
                   std::numeric_limits<unsigned long long>::max() /
                       signalPageSize)) {
        error = "Numer strony tabeli sygnałów jest poza zakresem.";
        return false;
    }

    InternetHandle session{WinHttpOpen(
        L"gie-da/1.0", WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0)};
    if (session.value == nullptr) {
        error = "Nie udało się rozpocząć żądania tabeli sygnałów.";
        return false;
    }
    WinHttpSetTimeouts(session.value, 3000, 3000, 3000, 3000);

    InternetHandle connection{WinHttpConnect(session.value, apiHost, apiPort, 0)};
    if (connection.value == nullptr) {
        error = "Nie można połączyć się z lokalnym API raportów.";
        return false;
    }

    const std::wstring requestPath =
        L"/api/signals/report?page=" + std::to_wstring(page);
    InternetHandle request{WinHttpOpenRequest(
        connection.value, L"GET", requestPath.c_str(), nullptr,
        WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0)};
    if (request.value == nullptr ||
        !WinHttpSendRequest(
            request.value, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
            WINHTTP_NO_REQUEST_DATA, 0, 0, 0) ||
        !WinHttpReceiveResponse(request.value, nullptr)) {
        error = "Lokalne API tabeli sygnałów nie odpowiedziało.";
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
        error = "Lokalne API zwróciło HTTP " +
                std::to_string(statusCode) + " dla tabeli sygnałów.";
        return false;
    }

    std::string response;
    if (!readResponse(request.value, response, error)) {
        return false;
    }
    const std::size_t headerEnd = response.find('\n');
    std::string_view hasNextPageText = headerEnd == std::string::npos
        ? std::string_view(response)
        : std::string_view(response).substr(0, headerEnd);
    if (!hasNextPageText.empty() && hasNextPageText.back() == '\r') {
        hasNextPageText.remove_suffix(1);
    }
    if (hasNextPageText != "0" && hasNextPageText != "1") {
        error = "Lokalne API zwróciło nieprawidłową flagę paginacji sygnałów.";
        return false;
    }

    std::vector<SignalReportEntry> receivedSignals;
    std::size_t lineStart = headerEnd == std::string::npos
        ? response.size()
        : headerEnd + 1;
    while (lineStart < response.size()) {
        const std::size_t lineEnd = response.find('\n', lineStart);
        const std::size_t effectiveEnd =
            lineEnd == std::string::npos ? response.size() : lineEnd;
        if (effectiveEnd > lineStart) {
            std::string_view row =
                std::string_view(response).substr(
                    lineStart, effectiveEnd - lineStart);
            if (!row.empty() && row.back() == '\r') {
                row.remove_suffix(1);
            }
            SignalReportEntry signal;
            if (receivedSignals.size() == signalPageSize ||
                !splitSignalReportRow(row, signal)) {
                error = "Lokalne API zwróciło nieprawidłowe dane sygnałów.";
                return false;
            }
            receivedSignals.push_back(std::move(signal));
        }
        if (lineEnd == std::string::npos) {
            break;
        }
        lineStart = lineEnd + 1;
    }

    signals = std::move(receivedSignals);
    hasNextPage = hasNextPageText == "1";
    return true;
}

/**
 * Pobiera wartości tabeli signal_potention dla jednego sygnału.
 */
bool CandleHistoryClient::fetchSignalPotential(
    const std::string& signalId,
    SignalPotentialDetails& details,
    std::string& error) {
    if (signalId.empty() || signalId.size() > 20 ||
        !std::all_of(signalId.begin(), signalId.end(), [](char character) {
            return character >= '0' && character <= '9';
        })) {
        error = "Identyfikator sygnału ma nieprawidłowy format.";
        return false;
    }

    InternetHandle session{WinHttpOpen(
        L"gie-da/1.0", WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0)};
    if (session.value == nullptr) {
        error = "Nie udało się rozpocząć żądania szczegółów sygnału.";
        return false;
    }
    WinHttpSetTimeouts(session.value, 3000, 3000, 3000, 3000);

    InternetHandle connection{WinHttpConnect(session.value, apiHost, apiPort, 0)};
    if (connection.value == nullptr) {
        error = "Nie można połączyć się z lokalnym API raportów.";
        return false;
    }

    const std::wstring requestPath =
        L"/api/signals/potential?signal_id=" +
        std::wstring(signalId.begin(), signalId.end());
    InternetHandle request{WinHttpOpenRequest(
        connection.value, L"GET", requestPath.c_str(), nullptr,
        WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0)};
    if (request.value == nullptr ||
        !WinHttpSendRequest(
            request.value, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
            WINHTTP_NO_REQUEST_DATA, 0, 0, 0) ||
        !WinHttpReceiveResponse(request.value, nullptr)) {
        error = "Lokalne API szczegółów sygnału nie odpowiedziało.";
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
        error = "Lokalne API nie zwróciło szczegółów sygnału (HTTP " +
            std::to_string(statusCode) + ").";
        return false;
    }

    std::string response;
    if (!readResponse(request.value, response, error)) {
        return false;
    }

    std::string* fields[] = {
        &details.saldo20,
        &details.bestCandle20Minute,
        &details.deal100Pln20,
        &details.averageSpread,
        &details.bestCandle60Minute,
        &details.saldo60,
        &details.deal100Pln60
    };
    constexpr std::size_t detailFieldCount = 7;
    std::size_t fieldStart = 0;
    for (std::size_t index = 0; index < detailFieldCount; ++index) {
        const std::size_t fieldEnd = response.find('\t', fieldStart);
        if ((index + 1 < detailFieldCount && fieldEnd == std::string::npos) ||
            (index + 1 == detailFieldCount && fieldEnd != std::string::npos)) {
            error = "Lokalne API zwróciło nieprawidłowe szczegóły sygnału.";
            return false;
        }
        const std::size_t valueEnd =
            fieldEnd == std::string::npos ? response.size() : fieldEnd;
        *fields[index] = response.substr(fieldStart, valueEnd - fieldStart);
        fieldStart = valueEnd + 1;
    }
    if (!response.empty() && response.back() == '\n') {
        error = "Lokalne API zwróciło nieprawidłowe szczegóły sygnału.";
        return false;
    }
    return true;
}
