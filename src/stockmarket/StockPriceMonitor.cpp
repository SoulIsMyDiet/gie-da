#include "StockPriceMonitor.h"

#include <utility>

namespace {

constexpr UINT candleUpdateMessage = WM_APP + 1;
constexpr DWORD candleRefreshIntervalMs = 60000;

} // namespace

/**
 * Przygotowuje monitor do pobierania świec 1-minutowych.
 */
StockPriceMonitor::StockPriceMonitor() = default;

/**
 * Zatrzymuje wątek pobierania przed zniszczeniem monitora.
 */
StockPriceMonitor::~StockPriceMonitor() {
    stop();
}

/**
 * Tworzy zdarzenie zatrzymania i uruchamia wątek dla wskazanego okna.
 */
bool StockPriceMonitor::start(HWND notificationWindow) {
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
 * Zatrzymuje wątek, czeka na jego zakończenie i zwalnia uchwyty.
 */
void StockPriceMonitor::stop() {
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
 * Zwraca świecę i status skopiowane pod blokadą współdzieloną.
 */
StockPriceSnapshot StockPriceMonitor::snapshot() const {
    AcquireSRWLockShared(&statusLock_);
    StockPriceSnapshot currentSnapshot{oneMinuteCandle_, status_};
    ReleaseSRWLockShared(&statusLock_);
    return currentSnapshot;
}

/**
 * Uruchamia pętlę pobierania dla monitora przekazanego przez Windows.
 */
DWORD WINAPI StockPriceMonitor::threadEntryPoint(LPVOID parameter) {
    auto* monitor = static_cast<StockPriceMonitor*>(parameter);
    monitor->run();
    return 0;
}

/**
 * Pobiera świecę 1-minutową, a następnie czeka minutę przed kolejnym żądaniem.
 */
void StockPriceMonitor::run() {
    while (true) {
        updateCandle();
        if (!PostMessageA(notificationWindow_, candleUpdateMessage, 0, 0)) {
            break;
        }
        if (WaitForSingleObject(stopEvent_, candleRefreshIntervalMs) != WAIT_TIMEOUT) {
            break;
        }
    }
}

/**
 * Pobiera świecę poza blokadą i atomowo aktualizuje stan monitora.
 */
void StockPriceMonitor::updateCandle() {
    CandleData candle;
    std::string error;
    const bool succeeded =
        CandlesDataClient::fetchLatestOneMinuteCandle("EUR/USD", candle, error);

    AcquireSRWLockExclusive(&statusLock_);
    if (succeeded) {
        oneMinuteCandle_ = std::move(candle);
        status_.clear();
    } else {
        status_ = "Błąd świecy 1-minutowej: " + error;
    }
    ReleaseSRWLockExclusive(&statusLock_);
}
