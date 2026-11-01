#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winhttp.h>

#include "TwelveDataClient.h"

#include <cmath>
#include <cstdlib>
#include <memory>
#include <string>

namespace {

// Endpoint configuration and a defensive cap on the downloaded response size.
constexpr wchar_t apiHost[] = L"api.twelvedata.com";
constexpr wchar_t apiPath[] = L"/price?symbol=EUR%2FUSD&apikey=";
constexpr DWORD maxResponseSize = 64 * 1024;

/**
 * Owns a WinHTTP handle and closes it automatically when it leaves scope.
 */
struct InternetHandle {
    HINTERNET value;

    /**
     * Takes ownership of a WinHTTP handle returned by a WinHTTP API call.
     */
    explicit InternetHandle(HINTERNET handle) : value(handle) {}

    /**
     * Closes the owned handle if it is valid.
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
 * Reads and validates the API key from the process environment.
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
 * Extracts and validates the numeric "price" field from the JSON response.
 */
bool extractPrice(const std::string& response, std::string& price) {
    const std::string key = "\"price\"";
    const std::size_t keyPosition = response.find(key);
    if (keyPosition == std::string::npos) {
        return false;
    }

    const std::size_t colonPosition = response.find(':', keyPosition + key.size());
    if (colonPosition == std::string::npos) {
        return false;
    }
    const std::size_t valueStart = response.find_first_not_of(" \t\r\n\"", colonPosition + 1);
    if (valueStart == std::string::npos) {
        return false;
    }
    const std::size_t valueEnd = response.find_first_of("\" \t\r\n,}", valueStart);
    if (valueEnd == std::string::npos || valueEnd == valueStart) {
        return false;
    }

    const std::string value = response.substr(valueStart, valueEnd - valueStart);
    char* parsedEnd = nullptr;
    const double parsedValue = std::strtod(value.c_str(), &parsedEnd);
    if (parsedEnd == value.c_str() || *parsedEnd != '\0' || !std::isfinite(parsedValue) ||
        parsedValue <= 0.0) {
        return false;
    }

    price = value;
    return true;
}

/**
 * Converts common HTTP failure codes into user-readable diagnostics.
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

} // namespace

/**
 * Fetches EUR/USD over HTTPS and returns either the validated price or an error.
 */
bool TwelveDataClient::fetchEurUsdPrice(std::string& price, std::string& error) {
    std::wstring apiKey;
    if (!readApiKey(apiKey)) {
        error = "Set the TWELVE_DATA_API_KEY environment variable.";
        return false;
    }

    InternetHandle session{WinHttpOpen(
        L"gie-da/1.0", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
        WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0)};
    if (session.value == nullptr) {
        error = "Could not start the HTTPS session.";
        return false;
    }
    WinHttpSetTimeouts(session.value, 5000, 5000, 5000, 5000);

    InternetHandle connection{WinHttpConnect(
        session.value, apiHost, INTERNET_DEFAULT_HTTPS_PORT, 0)};
    if (connection.value == nullptr) {
        error = "Could not connect to Twelve Data.";
        return false;
    }

    std::wstring requestPath = apiPath;
    requestPath += apiKey;
    InternetHandle request{WinHttpOpenRequest(
        connection.value, L"GET", requestPath.c_str(), nullptr,
        WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, WINHTTP_FLAG_SECURE)};
    if (request.value == nullptr ||
        !WinHttpSendRequest(request.value, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                            WINHTTP_NO_REQUEST_DATA, 0, 0, 0) ||
        !WinHttpReceiveResponse(request.value, nullptr)) {
        error = "The request to Twelve Data failed.";
        return false;
    }

    DWORD statusCode = 0;
    DWORD statusCodeSize = sizeof(statusCode);
    if (!WinHttpQueryHeaders(request.value, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                             WINHTTP_HEADER_NAME_BY_INDEX, &statusCode, &statusCodeSize,
                             WINHTTP_NO_HEADER_INDEX)) {
        error = "Could not read the HTTP status from Twelve Data.";
        return false;
    }
    if (statusCode != 200) {
        error = describeHttpError(statusCode);
        return false;
    }

    std::string response;
    while (true) {
        DWORD bytesAvailable = 0;
        if (!WinHttpQueryDataAvailable(request.value, &bytesAvailable)) {
            error = "Could not read the Twelve Data response.";
            return false;
        }
        if (bytesAvailable == 0) {
            break;
        }
        if (response.size() + bytesAvailable > maxResponseSize) {
            error = "The response from Twelve Data was too large.";
            return false;
        }

        const std::size_t oldSize = response.size();
        response.resize(oldSize + bytesAvailable);
        DWORD bytesRead = 0;
        if (!WinHttpReadData(request.value, response.data() + oldSize, bytesAvailable, &bytesRead)) {
            error = "Could not read the Twelve Data response.";
            return false;
        }
        if (bytesRead == 0) {
            error = "The Twelve Data response ended unexpectedly.";
            return false;
        }
        response.resize(oldSize + bytesRead);
    }

    if (!extractPrice(response, price)) {
        error = "No EUR/USD price was found in the Twelve Data response.";
        return false;
    }
    return true;
}
