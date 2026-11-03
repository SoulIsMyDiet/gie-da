#include "ForexPriceMonitor.h"

#include "PriceHistoryClient.h"
#include "TwelveDataClient.h"

#include <cstdio>
#include <cstdlib>
#include <utility>

namespace {

constexpr UINT forexUpdateMessage = WM_APP + 1;
constexpr DWORD forexRefreshIntervalMs = 30000;

} // namespace

/**
 * Initializes the monitor with a status message shown before the first response.
 */
ForexPriceMonitor::ForexPriceMonitor() = default;

/**
 * Ensures the worker is stopped before the monitor is destroyed.
 */
ForexPriceMonitor::~ForexPriceMonitor() {
    stop();
}

/**
 * Creates the stop event and starts the worker thread for the given window.
 */
bool ForexPriceMonitor::start(HWND notificationWindow) {
    if (notificationWindow == nullptr || workerThread_ != nullptr) {
        return false;
    }

    notificationWindow_ = notificationWindow;
    stopEvent_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (stopEvent_ == nullptr) {
        return false;
    }

    workerThread_ = CreateThread(
        nullptr, 0, threadEntryPoint, this, 0, nullptr);
    if (workerThread_ == nullptr) {
        CloseHandle(stopEvent_);
        stopEvent_ = nullptr;
        return false;
    }
    return true;
}

/**
 * Signals the worker to stop, waits for completion, and releases its handles.
 */
void ForexPriceMonitor::stop() {
    if (stopEvent_ != nullptr) {
        SetEvent(stopEvent_);
    }
    if (workerThread_ != nullptr) {
        WaitForSingleObject(workerThread_, INFINITE);
        CloseHandle(workerThread_);
        workerThread_ = nullptr;
    }
    if (stopEvent_ != nullptr) {
        CloseHandle(stopEvent_);
        stopEvent_ = nullptr;
    }
    notificationWindow_ = nullptr;
}

/**
 * Copies the current status and price history under a shared lock.
 */
ForexPriceSnapshot ForexPriceMonitor::snapshot() const {
    AcquireSRWLockShared(&statusLock_);
    ForexPriceSnapshot currentSnapshot{status_, {}};
    currentSnapshot.prices.assign(priceHistory_.begin(), priceHistory_.end());
    ReleaseSRWLockShared(&statusLock_);
    return currentSnapshot;
}

/**
 * Starts the worker loop for the ForexPriceMonitor instance passed by Windows.
 */
DWORD WINAPI ForexPriceMonitor::threadEntryPoint(LPVOID parameter) {
    auto* monitor = static_cast<ForexPriceMonitor*>(parameter);
    monitor->run();
    return 0;
}

/**
 * Fetches EUR/USD immediately, then repeats after each refresh interval.
 */
void ForexPriceMonitor::run() {
    while (true) {
        std::string price;
        std::string error;
        const bool succeeded = TwelveDataClient::fetchEurUsdPrice(price, error);
        bool playAlarm = false;
        if (succeeded) {
            SYSTEMTIME fetchedAt{};
            GetLocalTime(&fetchedAt);
            SYSTEMTIME recordedAt{};
            GetSystemTime(&recordedAt);

            std::string storageError;
            const bool savedToDatabase =
                PriceHistoryClient::savePrice("EUR/USD", price, recordedAt, storageError);

            const double numericPrice = std::strtod(price.c_str(), nullptr);
            playAlarm = addPrice(std::move(price), numericPrice, fetchedAt);
            if (!savedToDatabase) {
                setStatus("Price fetched, but database save failed: " + storageError);
            }
        } else {
            setStatus(std::move(error));
        }

        if (!PostMessageA(notificationWindow_, forexUpdateMessage,
                          playAlarm ? 1 : 0, 0)) {
            break;
        }
        if (WaitForSingleObject(stopEvent_, forexRefreshIntervalMs) != WAIT_TIMEOUT) {
            break;
        }
    }
}

/**
 * Replaces the shared status string while holding an exclusive lock.
 */
void ForexPriceMonitor::setStatus(std::string status) {
    AcquireSRWLockExclusive(&statusLock_);
    status_ = std::move(status);
    ReleaseSRWLockExclusive(&statusLock_);
}

/**
 * Stores a timestamped price as the newest entry and retains only eight.
 */
bool ForexPriceMonitor::addPrice(
    std::string price, double numericPrice, const SYSTEMTIME& fetchedAt) {
    char timestamp[9]{};
    std::snprintf(timestamp, sizeof(timestamp),
                  "%02u:%02u:%02u",
                  static_cast<unsigned>(fetchedAt.wHour),
                  static_cast<unsigned>(fetchedAt.wMinute),
                  static_cast<unsigned>(fetchedAt.wSecond));

    AcquireSRWLockExclusive(&statusLock_);
    status_.clear();
    ForexRowColor color = ForexRowColor::gray;
    if (!priceHistory_.empty()) {
        const ForexPriceEntry& previous = priceHistory_.front();
        if (numericPrice > previous.numericPrice) {
            color = ForexRowColor::green;
        } else if (numericPrice < previous.numericPrice) {
            color = ForexRowColor::red;
        } else {
            color = previous.color;
        }
    }

    if (!priceHistory_.empty() && priceHistory_.front().color == color) {
        ++latestColorStreak_;
    } else {
        latestColorStreak_ = 1;
    }

    priceHistory_.push_front(ForexPriceEntry{
        timestamp, std::move(price), numericPrice, color});
    if (priceHistory_.size() > 8) {
        priceHistory_.pop_back();
    }
    ReleaseSRWLockExclusive(&statusLock_);
    return latestColorStreak_ == 3;
}
