#include "ForexPriceMonitor.h"

#include "TwelveDataClient.h"

#include <cstdio>
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
 * Builds a display snapshot of the latest prices and any current error.
 */
std::string ForexPriceMonitor::status() const {
    AcquireSRWLockShared(&statusLock_);
    std::string currentStatus;
    if (priceHistory_.empty()) {
        currentStatus = status_;
    } else {
        currentStatus = status_;
        for (const std::string& entry : priceHistory_) {
            if (!currentStatus.empty()) {
                currentStatus += "\r\n";
            }
            currentStatus += entry;
        }
    }
    ReleaseSRWLockShared(&statusLock_);
    return currentStatus;
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
        if (succeeded) {
            SYSTEMTIME fetchedAt{};
            GetLocalTime(&fetchedAt);
            addPrice(std::move(price), fetchedAt);
        } else {
            setStatus(std::move(error));
        }

        if (!PostMessageA(notificationWindow_, forexUpdateMessage, 0, 0)) {
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
void ForexPriceMonitor::addPrice(std::string price, const SYSTEMTIME& fetchedAt) {
    char timestampedPrice[32]{};
    std::snprintf(timestampedPrice, sizeof(timestampedPrice),
                  "%02u:%02u:%02u  EUR/USD: ",
                  static_cast<unsigned>(fetchedAt.wHour),
                  static_cast<unsigned>(fetchedAt.wMinute),
                  static_cast<unsigned>(fetchedAt.wSecond));

    AcquireSRWLockExclusive(&statusLock_);
    status_.clear();
    priceHistory_.push_front(std::string(timestampedPrice) + price);
    if (priceHistory_.size() > 8) {
        priceHistory_.pop_back();
    }
    ReleaseSRWLockExclusive(&statusLock_);
}
