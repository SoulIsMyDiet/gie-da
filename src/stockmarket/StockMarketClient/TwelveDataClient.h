#pragma once

#include <string>

class TwelveDataClient {
public:
    /**
     * Wysyła żądanie HTTPS do Twelve Data i zwraca treść odpowiedzi.
     * @param requestPath Ścieżka endpointu zakończona pustym parametrem apikey=.
     * @param response Otrzymuje treść odpowiedzi serwera po udanym żądaniu.
     * @param error Otrzymuje opis przyczyny niepowodzenia.
     * @return true, jeśli serwer zwrócił odpowiedź HTTP 200; w przeciwnym razie false.
     */
    static bool fetchApiResponse(
        const std::wstring& requestPath,
        std::string& response,
        std::string& error);
};
