#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "CandleBackfillClient.h"

#include "CandleHistoryClient.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

namespace {

constexpr std::size_t maximumBackfillCandles = 1000;
constexpr std::size_t maximumProviderCandles = 5000;
constexpr ULONGLONG ticksPerMinute = 60ULL * 10000000ULL;

/**
 * Zamienia znacznik czasu UTC na liczbę tików FILETIME do porównywania zakresów.
 */
bool getUtcFileTimeTicks(const std::string& timestamp, ULONGLONG& ticks) {
    unsigned int year = 0;
    unsigned int month = 0;
    unsigned int day = 0;
    unsigned int hour = 0;
    unsigned int minute = 0;
    unsigned int second = 0;
    int consumedCharacters = 0;
    if (std::sscanf(
            timestamp.c_str(), "%4u-%2u-%2u %2u:%2u:%2u%n",
            &year, &month, &day, &hour, &minute, &second,
            &consumedCharacters) != 6 ||
        consumedCharacters != 19) {
        return false;
    }

    SYSTEMTIME utcTime{};
    utcTime.wYear = static_cast<WORD>(year);
    utcTime.wMonth = static_cast<WORD>(month);
    utcTime.wDay = static_cast<WORD>(day);
    utcTime.wHour = static_cast<WORD>(hour);
    utcTime.wMinute = static_cast<WORD>(minute);
    utcTime.wSecond = static_cast<WORD>(second);
    FILETIME fileTime{};
    if (!SystemTimeToFileTime(&utcTime, &fileTime)) {
        return false;
    }

    ULARGE_INTEGER value{};
    value.LowPart = fileTime.dwLowDateTime;
    value.HighPart = fileTime.dwHighDateTime;
    ticks = value.QuadPart;
    return true;
}

/**
 * Formatuje czas działania do jednego miejsca po przecinku w komunikacie ekranu.
 */
void updateElapsedTime(
    CandleBackfillResult& result,
    std::chrono::steady_clock::time_point startedAt) {
    result.elapsedSeconds =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - startedAt)
            .count();
}

} // namespace

/**
 * Pobiera brakujące minuty z Twelve Data i zapisuje je paczkami do lokalnej bazy.
 */
bool CandleBackfillClient::synchronize(
    const std::string& symbol,
    CandleBackfillResult& result,
    std::string& error) {
    result = {};
    const auto startedAt = std::chrono::steady_clock::now();
    bool hasExistingCandles = false;
    std::size_t detectedGapCount = 0;
    std::vector<std::string> missingTimes;
    if (!CandleHistoryClient::fetchMissingCandleTimes(
            symbol, hasExistingCandles, missingTimes,
            detectedGapCount, error)) {
        updateElapsedTime(result, startedAt);
        return false;
    }
    result.detectedGapCount = detectedGapCount;

    if (!hasExistingCandles) {
        std::vector<CandleData> latestCandles;
        if (!CandlesDataClient::fetchLatestOneMinuteCandles(
                symbol, maximumBackfillCandles, latestCandles, error)) {
            updateElapsedTime(result, startedAt);
            return false;
        }
        if (!latestCandles.empty()) {
            std::size_t savedCandleCount = 0;
            std::string latestSavedDatetime;
            if (!CandleHistoryClient::saveCandles(
                    symbol, latestCandles, savedCandleCount,
                    latestSavedDatetime, error)) {
                updateElapsedTime(result, startedAt);
                return false;
            }
            result.savedCandleCount = savedCandleCount;
            const auto latestSavedCandle = std::find_if(
                latestCandles.begin(), latestCandles.end(),
                [&latestSavedDatetime](const CandleData& candle) {
                    return candle.datetime == latestSavedDatetime;
                });
            if (latestSavedCandle != latestCandles.end()) {
                result.latestCandle = *latestSavedCandle;
            }
        }
        updateElapsedTime(result, startedAt);
        return true;
    }

    if (missingTimes.size() > maximumBackfillCandles) {
        missingTimes.resize(maximumBackfillCandles);
    }
    std::unordered_set<std::string> missingTimeSet(
        missingTimes.begin(), missingTimes.end());

    std::size_t firstIndex = 0;
    while (firstIndex < missingTimes.size()) {
        ULONGLONG firstTicks = 0;
        if (!getUtcFileTimeTicks(missingTimes[firstIndex], firstTicks)) {
            error = "Lokalne API zwróciło nieprawidłowy czas brakującej świecy.";
            updateElapsedTime(result, startedAt);
            return false;
        }

        std::size_t nextIndex = firstIndex + 1;
        while (nextIndex < missingTimes.size()) {
            ULONGLONG nextTicks = 0;
            if (!getUtcFileTimeTicks(missingTimes[nextIndex], nextTicks)) {
                error = "Lokalne API zwróciło nieprawidłowy czas brakującej świecy.";
                updateElapsedTime(result, startedAt);
                return false;
            }
            if (nextTicks < firstTicks ||
                (nextTicks - firstTicks) / ticksPerMinute >=
                    maximumProviderCandles) {
                break;
            }
            ++nextIndex;
        }

        std::vector<CandleData> receivedCandles;
        if (!CandlesDataClient::fetchOneMinuteCandleRange(
                symbol,
                missingTimes[firstIndex],
                missingTimes[nextIndex - 1],
                maximumProviderCandles,
                receivedCandles,
                error)) {
            updateElapsedTime(result, startedAt);
            return false;
        }

        std::vector<CandleData> missingCandles;
        for (CandleData& candle : receivedCandles) {
            if (missingTimeSet.erase(candle.datetime) > 0) {
                missingCandles.push_back(std::move(candle));
            }
        }
        if (!missingCandles.empty()) {
            std::size_t savedCandleCount = 0;
            std::string latestSavedDatetime;
            if (!CandleHistoryClient::saveCandles(
                    symbol, missingCandles, savedCandleCount,
                    latestSavedDatetime, error)) {
                updateElapsedTime(result, startedAt);
                return false;
            }
            result.savedCandleCount += savedCandleCount;
            const auto latestSavedCandle = std::find_if(
                missingCandles.begin(), missingCandles.end(),
                [&latestSavedDatetime](const CandleData& candle) {
                    return candle.datetime == latestSavedDatetime;
                });
            if (latestSavedCandle != missingCandles.end() &&
                (result.latestCandle.datetime.empty() ||
                 result.latestCandle.datetime < latestSavedCandle->datetime)) {
                result.latestCandle = *latestSavedCandle;
            }
        }
        firstIndex = nextIndex;
    }

    if (!missingTimeSet.empty()) {
        error = "Dostawca nie zwrócił " +
            std::to_string(missingTimeSet.size()) +
            " brakujących świec; monitor ponowi próbę.";
        updateElapsedTime(result, startedAt);
        return false;
    }

    updateElapsedTime(result, startedAt);
    return true;
}
