#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winhttp.h>

#include "DailySentenceClient.h"

#include <filesystem>
#include <fstream>
#include <string>
#include <string_view>
#include <utility>

// Backend
namespace {

constexpr wchar_t apiHost[] = L"api.groq.com";
constexpr wchar_t apiPath[] = L"/openai/v1/chat/completions";
constexpr DWORD maxResponseSize = 64 * 1024;
constexpr std::size_t maxApiKeySize = 512;

/**
 * Zamyka uchwyt WinHTTP automatycznie po zakończeniu bieżącego zakresu.
 */
struct InternetHandle {
    HINTERNET value;

    /**
     * Przejmuje własność uchwytu zwróconego przez funkcję WinHTTP.
     * @param handle Uchwyt sesji, połączenia lub żądania.
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
 * Usuwa białe znaki z początku i końca wiersza lub wartości konfiguracji.
 * @param value Tekst wymagający oczyszczenia.
 * @return Tekst bez białych znaków na początku i końcu.
 */
std::string trim(std::string value) {
    const std::size_t first = value.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) {
        return {};
    }
    const std::size_t last = value.find_last_not_of(" \t\r\n");
    return value.substr(first, last - first + 1);
}

/**
 * Sprawdza, czy klucz zawiera tylko znaki bezpieczne dla nagłówka HTTP.
 * @param apiKey Klucz API do sprawdzenia.
 * @return true, jeśli klucz jest niepusty i ma dozwolony format.
 */
bool isValidApiKey(const std::string& apiKey) {
    if (apiKey.empty() || apiKey.size() > maxApiKeySize) {
        return false;
    }
    for (const unsigned char character : apiKey) {
        if (!((character >= 'a' && character <= 'z') ||
              (character >= 'A' && character <= 'Z') ||
              (character >= '0' && character <= '9') ||
              character == '_' || character == '-')) {
            return false;
        }
    }
    return true;
}

/**
 * Odczytuje klucz najpierw ze zmiennych systemowych, a potem z pliku .env.
 * @param apiKey Otrzymuje odczytany klucz.
 * @param error Otrzymuje opis przyczyny niepowodzenia.
 * @return true, jeśli znaleziono klucz o prawidłowym formacie.
 */
bool readApiKey(std::string& apiKey, std::string& error) {
    const DWORD requiredSize =
        GetEnvironmentVariableW(L"GROK_API_KEY", nullptr, 0);
    if (requiredSize > 0) {
        if (requiredSize > maxApiKeySize + 1) {
            error = "Wartość GROK_API_KEY jest zbyt długa.";
            return false;
        }
        std::wstring environmentValue(requiredSize, L'\0');
        const DWORD copiedSize = GetEnvironmentVariableW(
            L"GROK_API_KEY", environmentValue.data(), requiredSize);
        if (copiedSize == 0 || copiedSize >= requiredSize) {
            error = "Nie udało się odczytać zmiennej GROK_API_KEY.";
            return false;
        }
        environmentValue.resize(copiedSize);
        for (const wchar_t character : environmentValue) {
            if (character > 0x7F) {
                error = "Zmienna GROK_API_KEY zawiera nieprawidłowe znaki.";
                return false;
            }
            apiKey.push_back(static_cast<char>(character));
        }
        if (!isValidApiKey(apiKey)) {
            apiKey.clear();
            error = "Zmienna GROK_API_KEY ma nieprawidłowy format.";
            return false;
        }
        return true;
    }

    std::error_code pathError;
    std::filesystem::path directory = std::filesystem::current_path(pathError);
    if (pathError) {
        error = "Nie udało się ustalić folderu aplikacji.";
        return false;
    }

    for (int level = 0; level < 6; ++level) {
        std::ifstream environmentFile(directory / ".env");
        std::string line;
        while (std::getline(environmentFile, line)) {
            const std::string trimmedLine = trim(std::move(line));
            if (trimmedLine.empty() || trimmedLine.front() == '#') {
                continue;
            }
            const std::size_t separator = trimmedLine.find('=');
            if (separator == std::string::npos ||
                trim(trimmedLine.substr(0, separator)) != "GROK_API_KEY") {
                continue;
            }

            apiKey = trim(trimmedLine.substr(separator + 1));
            if (apiKey.size() >= 2 &&
                ((apiKey.front() == '"' && apiKey.back() == '"') ||
                 (apiKey.front() == '\'' && apiKey.back() == '\''))) {
                apiKey = apiKey.substr(1, apiKey.size() - 2);
            }
            if (!isValidApiKey(apiKey)) {
                apiKey.clear();
                error = "Wartość GROK_API_KEY w pliku .env ma nieprawidłowy format.";
                return false;
            }
            return true;
        }

        const std::filesystem::path parent = directory.parent_path();
        if (parent == directory || parent.empty()) {
            break;
        }
        directory = parent;
    }

    error = "Nie znaleziono GROK_API_KEY ani w środowisku, ani w pliku .env.";
    return false;
}

/**
 * Dopisuje znak Unicode do tekstu zapisanego w formacie UTF-8.
 * @param text Tekst, do którego zostanie dopisany znak.
 * @param codePoint Numer znaku Unicode.
 */
void appendUtf8(std::string& text, unsigned int codePoint) {
    if (codePoint <= 0x7F) {
        text.push_back(static_cast<char>(codePoint));
    } else if (codePoint <= 0x7FF) {
        text.push_back(static_cast<char>(0xC0 | (codePoint >> 6)));
        text.push_back(static_cast<char>(0x80 | (codePoint & 0x3F)));
    } else if (codePoint <= 0xFFFF) {
        text.push_back(static_cast<char>(0xE0 | (codePoint >> 12)));
        text.push_back(static_cast<char>(0x80 | ((codePoint >> 6) & 0x3F)));
        text.push_back(static_cast<char>(0x80 | (codePoint & 0x3F)));
    } else {
        text.push_back(static_cast<char>(0xF0 | (codePoint >> 18)));
        text.push_back(static_cast<char>(0x80 | ((codePoint >> 12) & 0x3F)));
        text.push_back(static_cast<char>(0x80 | ((codePoint >> 6) & 0x3F)));
        text.push_back(static_cast<char>(0x80 | (codePoint & 0x3F)));
    }
}

/**
 * Odczytuje cztery cyfry szesnastkowe kodujące znak JSON w formacie Unicode.
 * @param json Odpowiedź JSON zawierająca kod znaku.
 * @param position Pozycja czytania; po sukcesie wskazuje znak za kodem.
 * @param codePoint Otrzymuje odczytany numer znaku Unicode.
 * @return true, jeśli kod składa się z czterech poprawnych cyfr.
 */
bool readUnicodeEscape(
    std::string_view json, std::size_t& position, unsigned int& codePoint) {
    if (position + 4 > json.size()) {
        return false;
    }
    codePoint = 0;
    for (std::size_t index = 0; index < 4; ++index) {
        const char digit = json[position++];
        codePoint <<= 4;
        if (digit >= '0' && digit <= '9') {
            codePoint |= static_cast<unsigned int>(digit - '0');
        } else if (digit >= 'a' && digit <= 'f') {
            codePoint |= static_cast<unsigned int>(digit - 'a' + 10);
        } else if (digit >= 'A' && digit <= 'F') {
            codePoint |= static_cast<unsigned int>(digit - 'A' + 10);
        } else {
            return false;
        }
    }
    return true;
}

/**
 * Odczytuje tekst JSON, obsługując znaki specjalne i pary zastępcze Unicode.
 * @param json Odpowiedź JSON.
 * @param position Pozycja otwierającego cudzysłowu; po sukcesie wskazuje tekst za nim.
 * @param value Otrzymuje zdekodowany tekst.
 * @return true, jeśli znaleziono kompletny i poprawny tekst.
 */
bool readJsonString(
    std::string_view json, std::size_t& position, std::string& value) {
    if (position >= json.size() || json[position] != '"') {
        return false;
    }
    ++position;
    value.clear();

    while (position < json.size()) {
        const unsigned char character =
            static_cast<unsigned char>(json[position++]);
        if (character == '"') {
            return true;
        }
        if (character < 0x20) {
            return false;
        }
        if (character != '\\') {
            value.push_back(static_cast<char>(character));
            continue;
        }
        if (position >= json.size()) {
            return false;
        }

        const char escapedCharacter = json[position++];
        switch (escapedCharacter) {
            case '"':
            case '\\':
            case '/':
                value.push_back(escapedCharacter);
                break;
            case 'b':
                value.push_back('\b');
                break;
            case 'f':
                value.push_back('\f');
                break;
            case 'n':
                value.push_back('\n');
                break;
            case 'r':
                value.push_back('\r');
                break;
            case 't':
                value.push_back('\t');
                break;
            case 'u': {
                unsigned int codePoint = 0;
                if (!readUnicodeEscape(json, position, codePoint)) {
                    return false;
                }
                if (codePoint >= 0xD800 && codePoint <= 0xDBFF) {
                    if (position + 2 > json.size() ||
                        json[position] != '\\' || json[position + 1] != 'u') {
                        return false;
                    }
                    position += 2;
                    unsigned int lowSurrogate = 0;
                    if (!readUnicodeEscape(json, position, lowSurrogate) ||
                        lowSurrogate < 0xDC00 || lowSurrogate > 0xDFFF) {
                        return false;
                    }
                    codePoint = 0x10000 +
                        ((codePoint - 0xD800) << 10) +
                        (lowSurrogate - 0xDC00);
                } else if (codePoint >= 0xDC00 && codePoint <= 0xDFFF) {
                    return false;
                }
                appendUtf8(value, codePoint);
                break;
            }
            default:
                return false;
        }
    }
    return false;
}

/**
 * Pobiera tekst pola content z odpowiedzi czatu Groq.
 * @param response Pełna odpowiedź API w formacie JSON.
 * @param sentence Otrzymuje tekst sentencji.
 * @return true, jeśli odpowiedź zawiera niepustą sentencję.
 */
bool extractSentence(
    const std::string& response, std::string& sentence) {
    const std::string_view json(response);
    const std::size_t choicesPosition = json.find("\"choices\"");
    const std::size_t messagePosition =
        choicesPosition == std::string_view::npos
            ? std::string_view::npos
            : json.find("\"message\"", choicesPosition);
    const std::size_t contentPosition =
        messagePosition == std::string_view::npos
            ? std::string_view::npos
            : json.find("\"content\"", messagePosition);
    if (contentPosition == std::string_view::npos) {
        return false;
    }

    std::size_t valuePosition = json.find(':', contentPosition + 9);
    if (valuePosition == std::string_view::npos) {
        return false;
    }
    ++valuePosition;
    while (valuePosition < json.size() &&
           (json[valuePosition] == ' ' || json[valuePosition] == '\t' ||
            json[valuePosition] == '\r' || json[valuePosition] == '\n')) {
        ++valuePosition;
    }
    return readJsonString(json, valuePosition, sentence) &&
           !sentence.empty() && sentence.size() <= 1024;
}

/**
 * Odczytuje odpowiedź HTTP do bufora o ograniczonym rozmiarze.
 * @param request Uchwyt otwartego żądania WinHTTP.
 * @param response Otrzymuje odczytane bajty odpowiedzi.
 * @param error Otrzymuje opis błędu, jeśli odczyt się nie powiedzie.
 * @return true, jeśli cała odpowiedź została odczytana.
 */
bool readResponse(HINTERNET request, std::string& response, std::string& error) {
    while (true) {
        DWORD bytesAvailable = 0;
        if (!WinHttpQueryDataAvailable(request, &bytesAvailable)) {
            error = "Nie udało się odczytać odpowiedzi Groq.";
            return false;
        }
        if (bytesAvailable == 0) {
            return true;
        }
        if (response.size() + bytesAvailable > maxResponseSize) {
            error = "Odpowiedź Groq jest zbyt duża.";
            return false;
        }

        const std::size_t previousSize = response.size();
        response.resize(previousSize + bytesAvailable);
        DWORD bytesRead = 0;
        if (!WinHttpReadData(
                request, response.data() + previousSize, bytesAvailable, &bytesRead)) {
            error = "Nie udało się odczytać odpowiedzi Groq.";
            return false;
        }
        if (bytesRead == 0) {
            error = "Odpowiedź Groq zakończyła się nieoczekiwanie.";
            return false;
        }
        response.resize(previousSize + bytesRead);
    }
}

} // namespace

/**
 * Wysyła żądanie bezpośrednio do Groq i zwraca wygenerowaną sentencję.
 */
bool DailySentenceClient::fetchDailySentence(
    std::string& sentence, std::string& error) {
    std::string apiKey;
    if (!readApiKey(apiKey, error)) {
        return false;
    }

    InternetHandle session{WinHttpOpen(
        L"gie-da/1.0", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0)};
    if (session.value == nullptr) {
        error = "Nie udało się rozpocząć połączenia HTTPS z Groq.";
        return false;
    }
    WinHttpSetTimeouts(session.value, 5000, 5000, 5000, 20000);

    InternetHandle connection{WinHttpConnect(
        session.value, apiHost, INTERNET_DEFAULT_HTTPS_PORT, 0)};
    if (connection.value == nullptr) {
        error = "Nie można połączyć się z api.groq.com.";
        return false;
    }

    InternetHandle request{WinHttpOpenRequest(
        connection.value, L"POST", apiPath, nullptr,
        WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES,
        WINHTTP_FLAG_SECURE)};
    if (request.value == nullptr) {
        error = "Nie udało się utworzyć żądania do Groq.";
        return false;
    }

    const std::wstring wideApiKey(apiKey.begin(), apiKey.end());
    const std::wstring requestHeaders =
        L"Authorization: Bearer " + wideApiKey +
        L"\r\nContent-Type: application/json\r\n";
    constexpr char requestBody[] =
        "{\"model\":\"qwen/qwen3.8-27b\","
        "\"messages\":["
        "{\"role\":\"system\",\"content\":\"Return one short, original Polish "
        "sentence only, without quotes or an author.\"},"
        "{\"role\":\"user\",\"content\":\"Give me the sentence of the day.\"}],"
        "\"max_completion_tokens\":160,"
        "\"reasoning_effort\":\"none\","
        "\"temperature\":0.9}";

    if (!WinHttpSendRequest(
            request.value, requestHeaders.c_str(),
            static_cast<DWORD>(requestHeaders.size()),
            const_cast<char*>(requestBody),
            static_cast<DWORD>(sizeof(requestBody) - 1),
            static_cast<DWORD>(sizeof(requestBody) - 1), 0) ||
        !WinHttpReceiveResponse(request.value, nullptr)) {
        error = "Nie udało się wysłać żądania do Groq. Sprawdź połączenie z internetem.";
        return false;
    }

    DWORD statusCode = 0;
    DWORD statusCodeSize = sizeof(statusCode);
    if (!WinHttpQueryHeaders(
            request.value,
            WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            WINHTTP_HEADER_NAME_BY_INDEX, &statusCode, &statusCodeSize,
            WINHTTP_NO_HEADER_INDEX)) {
        error = "Nie udało się odczytać statusu odpowiedzi Groq.";
        return false;
    }
    if (statusCode != 200) {
        error = "Groq zwrócił błąd HTTP " + std::to_string(statusCode) +
                ". Sprawdź klucz API, dostęp do modelu i limit zapytań.";
        return false;
    }

    std::string response;
    if (!readResponse(request.value, response, error)) {
        return false;
    }
    if (!extractSentence(response, sentence)) {
        error = "Groq nie zwrócił prawidłowego tekstu sentencji.";
        return false;
    }
    return true;
}
