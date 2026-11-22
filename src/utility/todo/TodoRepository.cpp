#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <winhttp.h>

#include "TodoRepository.hpp"

#include <charconv>
#include <limits>
#include <string_view>
#include <unordered_map>
#include <utility>

// Backend
namespace {

constexpr wchar_t apiHost[] = L"127.0.0.1";
constexpr INTERNET_PORT apiPort = 8000;
constexpr DWORD apiTimeoutMs = 3000;

/**
 * Zamyka uchwyt WinHTTP automatycznie po zakończeniu żądania.
 */
class InternetHandle {
public:
    /**
     * Przejmuje uchwyt zwrócony przez funkcję WinHTTP.
     */
    explicit InternetHandle(HINTERNET handle) : handle_(handle) {}

    /**
     * Zwalnia przejęty uchwyt, jeśli jest poprawny.
     */
    ~InternetHandle() {
        if (handle_ != nullptr) {
            WinHttpCloseHandle(handle_);
        }
    }

    InternetHandle(const InternetHandle&) = delete;
    InternetHandle& operator=(const InternetHandle&) = delete;

    /**
     * Zwraca uchwyt używany przez następne wywołanie WinHTTP.
     */
    HINTERNET get() const {
        return handle_;
    }

private:
    HINTERNET handle_ = nullptr;
};

/**
 * Chroni tekst przed znakami specjalnymi formatu JSON.
 */
std::string escapeJson(const std::string& value) {
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
 * Odczytuje odpowiedź HTTP bez arbitralnego limitu długości promptu.
 */
bool readResponse(
    HINTERNET request, std::string& response, std::string& error) {
    while (true) {
        DWORD bytesAvailable = 0;
        if (!WinHttpQueryDataAvailable(request, &bytesAvailable)) {
            error = "Nie udało się odczytać odpowiedzi lokalnego API To-do.";
            return false;
        }
        if (bytesAvailable == 0) {
            return true;
        }
        if (response.size() >
            response.max_size() - static_cast<std::size_t>(bytesAvailable)) {
            error = "Odpowiedź lokalnego API jest zbyt duża dla pamięci aplikacji.";
            return false;
        }

        const std::size_t previousSize = response.size();
        response.resize(previousSize + bytesAvailable);
        DWORD bytesRead = 0;
        if (!WinHttpReadData(
                request, response.data() + previousSize,
                bytesAvailable, &bytesRead)) {
            error = "Nie udało się odczytać odpowiedzi lokalnego API To-do.";
            return false;
        }
        if (bytesRead == 0) {
            error = "Odpowiedź lokalnego API zakończyła się nieoczekiwanie.";
            return false;
        }
        response.resize(previousSize + bytesRead);
    }
}

/**
 * Wysyła żądanie do lokalnego API i sprawdza jego status HTTP.
 */
bool sendApiRequest(
    const wchar_t* method,
    const std::wstring& path,
    const std::string& requestBody,
    std::string& responseBody,
    std::string& error) {
    responseBody.clear();
    error.clear();
    if (requestBody.size() > std::numeric_limits<DWORD>::max()) {
        error = "Treść żądania przekracza limit transportu WinHTTP.";
        return false;
    }

    InternetHandle session{WinHttpOpen(
        L"gie-da/1.0", WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0)};
    if (session.get() == nullptr) {
        error = "Nie udało się rozpocząć żądania To-do.";
        return false;
    }
    WinHttpSetTimeouts(
        session.get(), apiTimeoutMs, apiTimeoutMs, apiTimeoutMs, apiTimeoutMs);

    InternetHandle connection{
        WinHttpConnect(session.get(), apiHost, apiPort, 0)};
    if (connection.get() == nullptr) {
        error = "Nie można połączyć się z lokalnym API bazy danych.";
        return false;
    }

    InternetHandle request{WinHttpOpenRequest(
        connection.get(), method, path.c_str(), nullptr,
        WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, 0)};
    if (request.get() == nullptr) {
        error = "Nie udało się utworzyć żądania lokalnego API To-do.";
        return false;
    }

    const wchar_t* headers = requestBody.empty()
        ? WINHTTP_NO_ADDITIONAL_HEADERS
        : L"Content-Type: application/json\r\n";
    const DWORD headerLength = requestBody.empty()
        ? 0
        : static_cast<DWORD>(-1L);
    void* body = requestBody.empty()
        ? WINHTTP_NO_REQUEST_DATA
        : const_cast<char*>(requestBody.data());
    const DWORD bodyLength = static_cast<DWORD>(requestBody.size());
    if (!WinHttpSendRequest(
            request.get(), headers, headerLength, body,
            bodyLength, bodyLength, 0) ||
        !WinHttpReceiveResponse(request.get(), nullptr)) {
        error = "Lokalne API nie odpowiedziało na żądanie To-do.";
        return false;
    }

    DWORD statusCode = 0;
    DWORD statusCodeSize = sizeof(statusCode);
    if (!WinHttpQueryHeaders(
            request.get(),
            WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            WINHTTP_HEADER_NAME_BY_INDEX, &statusCode, &statusCodeSize,
            WINHTTP_NO_HEADER_INDEX)) {
        error = "Nie udało się odczytać statusu lokalnego API.";
        return false;
    }
    if (statusCode < 200 || statusCode >= 300) {
        error = "Lokalne API To-do zwróciło HTTP " +
            std::to_string(statusCode) + ".";
        return false;
    }
    return readResponse(request.get(), responseBody, error);
}

/**
 * Dekoduje pole zakodowane procentowo przez endpoint listy zadań.
 */
bool decodePercent(
    std::string_view encoded, std::string& decoded) {
    constexpr auto hexValue = [](char character) -> int {
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
    };

    decoded.clear();
    decoded.reserve(encoded.size());
    for (std::size_t index = 0; index < encoded.size(); ++index) {
        if (encoded[index] != '%') {
            decoded.push_back(encoded[index]);
            continue;
        }
        if (index + 2 >= encoded.size()) {
            return false;
        }
        const int high = hexValue(encoded[index + 1]);
        const int low = hexValue(encoded[index + 2]);
        if (high < 0 || low < 0) {
            return false;
        }
        decoded.push_back(static_cast<char>((high << 4) | low));
        index += 2;
    }
    return true;
}

/**
 * Rozdziela rekord TSV, zachowując puste pola między separatorami.
 */
std::vector<std::string_view> splitTabs(std::string_view line) {
    std::vector<std::string_view> fields;
    std::size_t start = 0;
    while (true) {
        const std::size_t separator = line.find('\t', start);
        if (separator == std::string_view::npos) {
            fields.push_back(line.substr(start));
            return fields;
        }
        fields.push_back(line.substr(start, separator - start));
        start = separator + 1;
    }
}

/**
 * Odczytuje dodatnią liczbę całkowitą z rekordu API.
 */
bool parseId(std::string_view value, std::uint64_t& id) {
    if (value.empty()) {
        return false;
    }
    const auto parsed = std::from_chars(
        value.data(), value.data() + value.size(), id);
    return parsed.ec == std::errc{} &&
        parsed.ptr == value.data() + value.size() && id > 0;
}

/**
 * Odczytuje wartość logiczną zapisaną jako 0 albo 1.
 */
bool parseBoolean(std::string_view value, bool& result) {
    if (value == "0") {
        result = false;
        return true;
    }
    if (value == "1") {
        result = true;
        return true;
    }
    return false;
}

/**
 * Odczytuje dodatni czas kolejnego sprawdzenia z odpowiedzi JSON API.
 */
bool parseNextCheckDelay(
    const std::string& response, std::chrono::seconds& delay) {
    constexpr std::string_view fieldName = "\"next_check_seconds\"";
    const std::size_t fieldPosition = response.find(fieldName);
    if (fieldPosition == std::string::npos) {
        return false;
    }

    std::size_t valuePosition = fieldPosition + fieldName.size();
    while (valuePosition < response.size() &&
           (response[valuePosition] == ' ' ||
            response[valuePosition] == '\t' ||
            response[valuePosition] == '\r' ||
            response[valuePosition] == '\n')) {
        ++valuePosition;
    }
    if (valuePosition >= response.size() ||
        response[valuePosition] != ':') {
        return false;
    }
    ++valuePosition;
    while (valuePosition < response.size() &&
           (response[valuePosition] == ' ' ||
            response[valuePosition] == '\t' ||
            response[valuePosition] == '\r' ||
            response[valuePosition] == '\n')) {
        ++valuePosition;
    }

    std::uint64_t seconds = 0;
    const char* first = response.data() + valuePosition;
    const char* last = response.data() + response.size();
    const auto parsed = std::from_chars(first, last, seconds);
    if (parsed.ec != std::errc{} || seconds == 0 ||
        seconds > static_cast<std::uint64_t>(
            std::chrono::seconds::max().count())) {
        return false;
    }
    delay = std::chrono::seconds(
        static_cast<std::chrono::seconds::rep>(seconds));
    return true;
}

/**
 * Buduje ścieżkę API dla pola przypisanego do wskazanego podzadania.
 */
std::wstring subtaskTextPath(
    std::uint64_t subtaskId, const wchar_t* field) {
    return L"/api/todos/subtasks/" + std::to_wstring(subtaskId) +
        L"/" + field;
}

/**
 * Zapisuje prompt albo opis przez endpoint PUT.
 */
bool saveSubtaskText(
    std::uint64_t subtaskId,
    const wchar_t* field,
    const std::string& value,
    std::string& error) {
    const std::string body = "{\"value\":\"" + escapeJson(value) + "\"}";
    std::string response;
    return sendApiRequest(
        L"PUT", subtaskTextPath(subtaskId, field), body, response, error);
}

/**
 * Pobiera prompt albo opis przez endpoint GET.
 */
bool fetchSubtaskText(
    std::uint64_t subtaskId,
    const wchar_t* field,
    std::string& value,
    std::string& error) {
    return sendApiRequest(
        L"GET", subtaskTextPath(subtaskId, field), {}, value, error);
}

} // namespace

/**
 * Pobiera i sprawdza wszystkie rekordy zadania z lokalnego API.
 */
bool TodoRepository::fetchTasks(
    std::vector<TodoTask>& tasks, std::string& error) const {
    tasks.clear();
    std::string response;
    if (!sendApiRequest(
            L"GET", L"/api/todos", {}, response, error)) {
        return false;
    }

    std::unordered_map<std::uint64_t, std::size_t> taskIndices;
    std::size_t lineStart = 0;
    while (lineStart < response.size()) {
        const std::size_t lineEnd = response.find('\n', lineStart);
        const std::size_t length = lineEnd == std::string::npos
            ? response.size() - lineStart
            : lineEnd - lineStart;
        std::string_view line(response.data() + lineStart, length);
        if (!line.empty() && line.back() == '\r') {
            line.remove_suffix(1);
        }
        if (!line.empty()) {
            const std::vector<std::string_view> fields = splitTabs(line);
            if (fields[0] == "M" && fields.size() == 5) {
                TodoTask task;
                if (!parseId(fields[1], task.id) ||
                    !decodePercent(fields[2], task.title)) {
                    error = "API zwróciło nieprawidłowe zadanie główne.";
                    tasks.clear();
                    return false;
                }
                task.createdAt = fields[3];
                task.updatedAt = fields[4];
                taskIndices.emplace(task.id, tasks.size());
                tasks.push_back(std::move(task));
            } else if (fields[0] == "S" && fields.size() == 9) {
                TodoSubtask subtask;
                std::uint64_t mainTaskId = 0;
                if (!parseId(fields[1], subtask.id) ||
                    !parseId(fields[2], mainTaskId) ||
                    !decodePercent(fields[3], subtask.title) ||
                    !parseBoolean(fields[4], subtask.completed) ||
                    !parseBoolean(fields[5], subtask.promptExists) ||
                    !parseBoolean(fields[6], subtask.descriptionExists)) {
                    error = "API zwróciło nieprawidłowe zadanie podrzędne.";
                    tasks.clear();
                    return false;
                }
                const auto parent = taskIndices.find(mainTaskId);
                if (parent == taskIndices.end()) {
                    error = "Zadanie podrzędne nie ma zadania głównego.";
                    tasks.clear();
                    return false;
                }
                subtask.createdAt = fields[7];
                subtask.updatedAt = fields[8];
                tasks[parent->second].subtasks.push_back(std::move(subtask));
            } else {
                error = "API zwróciło rekord zadania w nieobsługiwanym formacie.";
                tasks.clear();
                return false;
            }
        }
        if (lineEnd == std::string::npos) {
            break;
        }
        lineStart = lineEnd + 1;
    }
    return true;
}

/**
 * Zapisuje nowe zadanie główne w API.
 */
bool TodoRepository::createMainTask(
    const std::string& title, std::string& error) const {
    const std::string body = "{\"title\":\"" + escapeJson(title) + "\"}";
    std::string response;
    return sendApiRequest(
        L"POST", L"/api/todos/main", body, response, error);
}

/**
 * Zapisuje nowe podzadanie w API.
 */
bool TodoRepository::createSubtask(
    std::uint64_t mainTaskId,
    const std::string& title,
    std::string& error) const {
    const std::string body = "{\"title\":\"" + escapeJson(title) + "\"}";
    const std::wstring path =
        L"/api/todos/main/" + std::to_wstring(mainTaskId) + L"/subtasks";
    std::string response;
    return sendApiRequest(L"POST", path, body, response, error);
}

/**
 * Zapisuje nowy stan ukończenia podzadania.
 */
bool TodoRepository::setSubtaskCompleted(
    std::uint64_t subtaskId, bool completed, std::string& error) const {
    const std::string body = completed
        ? "{\"completed\":true}"
        : "{\"completed\":false}";
    const std::wstring path =
        L"/api/todos/subtasks/" + std::to_wstring(subtaskId) +
        L"/completion";
    std::string response;
    return sendApiRequest(L"PATCH", path, body, response, error);
}

/**
 * Pobiera prompt zapisany dla podzadania.
 */
bool TodoRepository::fetchPrompt(
    std::uint64_t subtaskId,
    std::string& prompt,
    std::string& error) const {
    return fetchSubtaskText(subtaskId, L"prompt", prompt, error);
}

/**
 * Zapisuje prompt bez ograniczenia długości po stronie aplikacji.
 */
bool TodoRepository::savePrompt(
    std::uint64_t subtaskId,
    const std::string& prompt,
    std::string& error) const {
    return saveSubtaskText(subtaskId, L"prompt", prompt, error);
}

/**
 * Pobiera opis techniczny zapisany dla podzadania.
 */
bool TodoRepository::fetchDescription(
    std::uint64_t subtaskId,
    std::string& description,
    std::string& error) const {
    return fetchSubtaskText(
        subtaskId, L"description", description, error);
}

/**
 * Zapisuje opis techniczny bez ograniczenia długości po stronie aplikacji.
 */
bool TodoRepository::saveDescription(
    std::uint64_t subtaskId,
    const std::string& description,
    std::string& error) const {
    return saveSubtaskText(
        subtaskId, L"description", description, error);
}

/**
 * Trwale usuwa podzadanie w istniejącym API.
 */
bool TodoRepository::deleteSubtask(
    std::uint64_t subtaskId, std::string& error) const {
    const std::wstring path =
        L"/api/todos/subtasks/" + std::to_wstring(subtaskId);
    std::string response;
    return sendApiRequest(L"DELETE", path, {}, response, error);
}

/**
 * Trwale usuwa zadanie główne wraz z podzadaniami.
 */
bool TodoRepository::deleteMainTask(
    std::uint64_t mainTaskId, std::string& error) const {
    const std::wstring path =
        L"/api/todos/main/" + std::to_wstring(mainTaskId);
    std::string response;
    return sendApiRequest(L"DELETE", path, {}, response, error);
}

/**
 * Przekazuje rejestrowane widoki do idempotentnego endpointu tygodniowego.
 */
bool TodoRepository::createWeeklyReview(
    const std::vector<std::string>& viewNames,
    std::chrono::seconds& nextCheckDelay,
    std::string& error) const {
    if (viewNames.empty()) {
        error = "Nie można utworzyć przeglądu bez zarejestrowanych widoków.";
        return false;
    }

    std::string body = "{\"view_names\":[";
    for (std::size_t index = 0; index < viewNames.size(); ++index) {
        if (index > 0) {
            body += ',';
        }
        body += "\"" + escapeJson(viewNames[index]) + "\"";
    }
    body += "]}";
    std::string response;
    if (!sendApiRequest(
            L"POST", L"/api/todos/weekly-review", body, response, error)) {
        return false;
    }
    if (!parseNextCheckDelay(response, nextCheckDelay)) {
        error = "API zwróciło nieprawidłowy czas kolejnego sprawdzenia.";
        return false;
    }
    return true;
}
