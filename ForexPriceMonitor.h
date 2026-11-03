#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <deque>
#include <string>
#include <vector>

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
     * Returns a thread-safe display of the latest eight prices or current status.
     */
    ForexPriceSnapshot snapshot() const;

private:
    /**
     * Adapts the Windows thread entry-point signature to the monitor instance.
     */
    static DWORD WINAPI threadEntryPoint(LPVOID parameter);

    /**
     * Fetches prices periodically until the stop event is signaled.
     */
    void run();

    /**
     * Replaces the displayed status while holding its synchronization lock.
     */
    void setStatus(std::string status);

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
};
