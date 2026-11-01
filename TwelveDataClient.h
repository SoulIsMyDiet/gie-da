#pragma once

#include <string>

class TwelveDataClient {
public:
    /**
     * Requests the latest EUR/USD price using the configured Twelve Data API key.
     * @param price Receives the price string when the request succeeds.
     * @param error Receives a diagnostic message when the request fails.
     * @return true if a valid price was received; otherwise false.
     */
    static bool fetchEurUsdPrice(std::string& price, std::string& error);
};
