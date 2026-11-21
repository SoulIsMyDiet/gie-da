#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winhttp.h>

#include "CandleHistoryClient.h"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdlib>
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
constexpr std::size_t pageSize = 20;
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
 * @return true, jeśli odpowiedź ma oczekiwany lub jawnie dozwolony status.
 */
bool sendApiRequest(
    const std::wstring& path,
    const std::string& requestBody,
    DWORD expectedStatus,
    std::string& responseBody,
    std::string& error,
    DWORD* responseStatus = nullptr,
    bool allowServiceUnavailable = false) {
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
 * Pobiera świece EURUSD/USD i zmienia kolejność odpowiedzi na chronologiczną.
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
        &details.bestCandle60Datetime,
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
