#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winhttp.h>

#include "TwelveDataClient.h"

#include <string>

namespace {

// Host API i maksymalny rozmiar odpowiedzi pobieranej przez klienta.
constexpr wchar_t apiHost[] = L"api.twelvedata.com";
constexpr DWORD maxResponseSize = 4 * 1024 * 1024;

/**
 * Automatycznie zamyka uchwyt WinHTTP po zakończeniu bieżącego zakresu.
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
 * Odczytuje i sprawdza klucz API ze zmiennych środowiskowych.
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
 * Zamienia typowe kody HTTP na komunikaty zrozumiałe dla użytkownika.
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
bool sendApiRequest(
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
 * Udostępnia warstwę połączenia HTTP innym klientom Twelve Data.
 */
bool TwelveDataClient::fetchApiResponse(
    const std::wstring& requestPath, std::string& response, std::string& error) {
    if (requestPath.empty() || requestPath.front() != L'/' ||
        requestPath.find(L'#') != std::wstring::npos ||
        requestPath.size() < 7 ||
        requestPath.compare(requestPath.size() - 7, 7, L"apikey=") != 0) {
        error = "Ścieżka żądania do Twelve Data jest nieprawidłowa.";
        return false;
    }
    return sendApiRequest(requestPath.c_str(), response, error);
}
