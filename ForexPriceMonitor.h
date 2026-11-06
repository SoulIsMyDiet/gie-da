#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <deque>
#include <string>
#include <vector>

#include "TwelveDataClient.h"

enum class ForexRowColor {
    gray,
    red,
    green
};

struct ForexPriceEntry {
    std::string timestamp;
    std::string price;
    double numericPrice = 0.0;
    ForexRowColor color = ForexRowColor::gray;
};

struct ForexPriceSnapshot {
    std::string status;
    std::vector<ForexPriceEntry> prices;
    ForexCandle oneMinuteCandle;
    ForexCandle fiveMinuteCandle;
    std::string oneMinuteCandleStatus;
    std::string fiveMinuteCandleStatus;
};

class ForexPriceMonitor {
public:
    /**
     * Initializes the displayed status and the synchronization lock.
     */
    ForexPriceMonitor();

    /**
     * Stops background work and releases any remaining Windows handles.
     */
    ~ForexPriceMonitor();

    ForexPriceMonitor(const ForexPriceMonitor&) = delete;
    ForexPriceMonitor& operator=(const ForexPriceMonitor&) = delete;

    /**
     * Starts the background worker, which fetches EUR/USD and notifies the window.
     */
    bool start(HWND notificationWindow);

    /**
     * Signals the worker to stop, waits for it, and closes its handles.
     */
    void stop();

    /**
     * Zwraca bezpieczną wątkowo kopię statusu, cen i ostatnich świec EUR/USD.
     */
    ForexPriceSnapshot snapshot() const;

private:
    /**
     * Adapts the Windows thread entry-point signature to the monitor instance.
     */
    static DWORD WINAPI threadEntryPoint(LPVOID parameter);

    /**
     * Pobiera cenę co 30 sekund, świecę 1-minutową co minutę
     * oraz świecę 5-minutową co pięć minut.
     */
    void run();

    /**
     * Replaces the displayed status while holding its synchronization lock.
     */
    void setStatus(std::string status);

    /**
     * Pobiera i zapisuje najnowszą świecę danego interwału.
     * Nie zastępuje ostatniej poprawnej świecy, jeśli zapytanie się nie uda.
     */
    void updateCandle(ForexCandleInterval interval);

    /**
     * Adds a price to display history and returns true only on the third
     * consecutive red or green entry, when the alarm should sound and save.
     */
    bool addPrice(
        std::string price, double numericPrice, const SYSTEMTIME& fetchedAt);

    HWND notificationWindow_ = nullptr;
    HANDLE stopEvent_ = nullptr;
    HANDLE workerThread_ = nullptr;
    mutable SRWLOCK statusLock_ = SRWLOCK_INIT;
    std::string status_ = "Connecting to Twelve Data...";
    std::deque<ForexPriceEntry> priceHistory_;
    std::size_t latestColorStreak_ = 0;
    ForexCandle oneMinuteCandle_;
    ForexCandle fiveMinuteCandle_;
    std::string oneMinuteCandleStatus_ = "Oczekiwanie na świecę 1-minutową...";
    std::string fiveMinuteCandleStatus_ = "Oczekiwanie na świecę 5-minutową...";
};
