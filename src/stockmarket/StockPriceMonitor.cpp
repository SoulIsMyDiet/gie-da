#include "StockPriceMonitor.h"

#include "CandleHistoryClient.h"

#include <cstdio>
#include <utility>

namespace {

constexpr UINT candleUpdateMessage = WM_APP + 1;
constexpr DWORD candleCloseBufferMs = 2000;
constexpr ULONGLONG fileTimeTicksPerSecond = 10000000ULL;
constexpr ULONGLONG fileTimeTicksPerMinute = 60ULL * fileTimeTicksPerSecond;

/**
 * Parsuje znacznik czasu świecy zapisany przez Twelve Data jako czas UTC.
 */
bool parseCandleUtcTime(const std::string& value, SYSTEMTIME& utcTime) {
    unsigned int year = 0;
    unsigned int month = 0;
    unsigned int day = 0;
    unsigned int hour = 0;
    unsigned int minute = 0;
    unsigned int second = 0;
    int consumedCharacters = 0;
    if (std::sscanf(
            value.c_str(), "%4u-%2u-%2u %2u:%2u:%2u%n",
            &year, &month, &day, &hour, &minute, &second,
            &consumedCharacters) != 6 ||
        consumedCharacters != 19 || year > 65535 || month > 65535 ||
        day > 65535 || hour > 65535 || minute > 65535 || second > 65535) {
        return false;
    }

    utcTime = {};
    utcTime.wYear = static_cast<WORD>(year);
    utcTime.wMonth = static_cast<WORD>(month);
    utcTime.wDay = static_cast<WORD>(day);
    utcTime.wHour = static_cast<WORD>(hour);
    utcTime.wMinute = static_cast<WORD>(minute);
    utcTime.wSecond = static_cast<WORD>(second);
    FILETIME validatedTime{};
    return SystemTimeToFileTime(&utcTime, &validatedTime) != FALSE;
}

/**
 * Porównuje świecę z ostatnią pełną minutą UTC i opisuje różnicę w sekundach.
 */
std::string describeCandleFreshness(const CandleData& candle) {
    SYSTEMTIME candleTime{};
    SYSTEMTIME currentTime{};
    if (!parseCandleUtcTime(candle.datetime, candleTime)) {
        return "Nie można sprawdzić czasu świecy zwróconego przez dostawcę.";
    }

    GetSystemTime(&currentTime);
    currentTime.wSecond = 0;
    currentTime.wMilliseconds = 0;
    FILETIME expectedCandleFileTime{};
    FILETIME actualCandleFileTime{};
    if (!SystemTimeToFileTime(&currentTime, &expectedCandleFileTime) ||
        !SystemTimeToFileTime(&candleTime, &actualCandleFileTime)) {
        return "Nie można porównać czasu świecy z bieżącym czasem UTC.";
    }

    ULARGE_INTEGER expectedTicks{};
    expectedTicks.LowPart = expectedCandleFileTime.dwLowDateTime;
    expectedTicks.HighPart = expectedCandleFileTime.dwHighDateTime;
    ULARGE_INTEGER actualTicks{};
    actualTicks.LowPart = actualCandleFileTime.dwLowDateTime;
    actualTicks.HighPart = actualCandleFileTime.dwHighDateTime;
    if (expectedTicks.QuadPart < fileTimeTicksPerMinute) {
        return "Nie można wyznaczyć oczekiwanego czasu świecy UTC.";
    }
    expectedTicks.QuadPart -= fileTimeTicksPerMinute;

    const LONGLONG differenceTicks =
        static_cast<LONGLONG>(expectedTicks.QuadPart) -
        static_cast<LONGLONG>(actualTicks.QuadPart);
    if (differenceTicks == 0) {
        return {};
    }

    const ULONGLONG differenceSeconds =
        static_cast<ULONGLONG>(
            differenceTicks < 0 ? -differenceTicks : differenceTicks) /
        fileTimeTicksPerSecond;
    char freshnessStatus[160]{};
    if (differenceTicks > 0) {
        std::snprintf(
            freshnessStatus, sizeof(freshnessStatus),
            "Dane dostawcy są opóźnione o około %llu s względem ostatniej zamkniętej minuty UTC.",
            static_cast<unsigned long long>(differenceSeconds));
    } else {
        std::snprintf(
            freshnessStatus, sizeof(freshnessStatus),
            "Świeca wyprzedza ostatnią zamkniętą minutę UTC o około %llu s.",
            static_cast<unsigned long long>(differenceSeconds));
    }
    return freshnessStatus;
}

/**
 * Czeka do dwóch sekund po najbliższej granicy minuty UTC.
 */
bool waitForNextCandleClose(HANDLE stopEvent) {
    SYSTEMTIME currentTime{};
    GetSystemTime(&currentTime);
    const DWORD elapsedMilliseconds =
        static_cast<DWORD>(currentTime.wSecond) * 1000 +
        currentTime.wMilliseconds;
    const DWORD waitMilliseconds =
        60000 - elapsedMilliseconds + candleCloseBufferMs;
    return WaitForSingleObject(stopEvent, waitMilliseconds) == WAIT_TIMEOUT;
}

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
 * Pobiera świecę 1-minutową dwie sekundy po jej planowanym zamknięciu.
 */
void StockPriceMonitor::run() {
    while (true) {
        if (!waitForNextCandleClose(stopEvent_)) {
            break;
        }
        updateCandle();
        if (!PostMessageA(notificationWindow_, candleUpdateMessage, 0, 0)) {
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
    constexpr char monitoredSymbol[] = "EUR/USD";
    const bool fetched =
        CandlesDataClient::fetchLatestOneMinuteCandle(
            monitoredSymbol, candle, error);
    bool saved = false;
    if (fetched) {
        saved = CandleHistoryClient::saveCandle(monitoredSymbol, candle, error);
    }
    std::string updateStatus;
    if (!fetched) {
        updateStatus = "Błąd świecy 1-minutowej: " + error;
    } else if (!saved) {
        updateStatus = "Nie udało się zapisać świecy w bazie: " + error;
    } else {
        updateStatus = describeCandleFreshness(candle);
    }

    AcquireSRWLockExclusive(&statusLock_);
    if (fetched) {
        oneMinuteCandle_ = std::move(candle);
    }
    status_ = updateStatus;
    ReleaseSRWLockExclusive(&statusLock_);
}
