#pragma once

#include <string>

class TwelveDataClient {
public:
    static bool fetchEurUsdPrice(std::string& price, std::string& error);
};
