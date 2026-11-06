#include "ForexPriceMonitor.h"

#include "DailySentenceClient.h"
#include "PriceHistoryClient.h"
#include "TwelveDataClient.h"

#include <cstdio>
#include <cstdlib>
#include <utility>

namespace {

constexpr UINT forexUpdateMessage = WM_APP + 1;
constexpr DWORD forexRefreshIntervalMs = 30000;
constexpr ULONGLONG oneMinuteCandleRefreshIntervalMs = 60000;
constexpr ULONGLONG fiveMinuteCandleRefreshIntervalMs = 300000;

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
 * Kopiuje status, historię cen i ostatnie świece przy użyciu blokady współdzielonej.
 */
ForexPriceSnapshot ForexPriceMonitor::snapshot() const {
    AcquireSRWLockShared(&statusLock_);
    ForexPriceSnapshot currentSnapshot{
        status_, {}, oneMinuteCandle_, fiveMinuteCandle_,
        oneMinuteCandleStatus_, fiveMinuteCandleStatus_};
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
 * Pobiera cenę co 30 sekund, świecę minutową co minutę,
 * a świecę pięciominutową co pięć minut.
 */
void ForexPriceMonitor::run() {
    ULONGLONG nextOneMinuteCandleFetch = 0;
    ULONGLONG nextFiveMinuteCandleFetch = 0;
    while (true) {
        ULONGLONG currentTick = GetTickCount64();
        if (currentTick >= nextOneMinuteCandleFetch) {
            updateCandle(ForexCandleInterval::oneMinute);
            nextOneMinuteCandleFetch =
                GetTickCount64() + oneMinuteCandleRefreshIntervalMs;
        }
        currentTick = GetTickCount64();
        if (currentTick >= nextFiveMinuteCandleFetch) {
            updateCandle(ForexCandleInterval::fiveMinutes);
            nextFiveMinuteCandleFetch =
                GetTickCount64() + fiveMinuteCandleRefreshIntervalMs;
        }

        std::string price;
        std::string error;
        const bool succeeded = TwelveDataClient::fetchEurUsdPrice(price, error);
        bool playAlarm = false;
        if (succeeded) {
            SYSTEMTIME fetchedAt{};
            GetLocalTime(&fetchedAt);
            const double numericPrice = std::strtod(price.c_str(), nullptr);
            playAlarm = addPrice(price, numericPrice, fetchedAt);

            if (playAlarm) {
                std::string goldenAdvice;
                std::string sentenceError;
                if (!DailySentenceClient::fetchDailySentence(
                        goldenAdvice, sentenceError)) {
                    setStatus(
                        "Nie udało się wygenerować sentencji AI: " +
                        sentenceError);
                } else {
                    SYSTEMTIME recordedAt{};
                    GetSystemTime(&recordedAt);

                    std::string storageError;
                    if (!PriceHistoryClient::savePrice(
                            "EUR/USD", price, recordedAt,
                            goldenAdvice, storageError)) {
                        setStatus(
                            "Nie udało się zapisać ceny w bazie: " +
                            storageError);
                    }
                }
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
 * Pobiera świecę poza blokadą i zapisuje wynik atomowo dla wątku interfejsu.
 */
void ForexPriceMonitor::updateCandle(ForexCandleInterval interval) {
    ForexCandle candle;
    std::string error;
    const bool succeeded =
        TwelveDataClient::fetchEurUsdCandle(interval, candle, error);

    AcquireSRWLockExclusive(&statusLock_);
    if (interval == ForexCandleInterval::oneMinute) {
        if (succeeded) {
            oneMinuteCandle_ = std::move(candle);
            oneMinuteCandleStatus_.clear();
        } else {
            oneMinuteCandleStatus_ = "Błąd świecy 1-minutowej: " + error;
        }
    } else {
        if (succeeded) {
            fiveMinuteCandle_ = std::move(candle);
            fiveMinuteCandleStatus_.clear();
        } else {
            fiveMinuteCandleStatus_ = "Błąd świecy 5-minutowej: " + error;
        }
    }
    ReleaseSRWLockExclusive(&statusLock_);
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
    return color != ForexRowColor::gray && latestColorStreak_ == 3;
}
