#include "ForexPriceMonitor.h"

#include "TwelveDataClient.h"

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
 * Copies the latest monitor status under a shared lock.
 */
std::string ForexPriceMonitor::status() const {
    AcquireSRWLockShared(&statusLock_);
    const std::string currentStatus = status_;
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
        setStatus(succeeded ? "EUR/USD: " + price : std::move(error));

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
