#include "StockPriceMonitor.h"

#include "CandleBackfillClient.h"
#include "CandleHistoryClient.h"
#include "AudioSignal.h"
#include "SignalGenerator.h"

#include <cstdio>
#include <cstdint>
#include <utility>
#include <vector>

namespace {

constexpr UINT candleUpdateMessage = WM_APP + 1;
constexpr DWORD candleCloseBufferMs = 2000;
constexpr DWORD candleRetryIntervalMs = 5000;
constexpr ULONGLONG candleRetryWindowMs = 30000;
constexpr ULONGLONG fileTimeTicksPerSecond = 10000000ULL;
constexpr ULONGLONG fileTimeTicksPerMinute = 60ULL * fileTimeTicksPerSecond;
constexpr ULONGLONG windowsToUnixEpochTicks = 116444736000000000ULL;

/**
 * Buduje czytelny komunikat z liczbą zapisanych świec i czasem operacji.
 */
std::string createBackfillStatus(
    const CandleBackfillResult& result, const std::string& error) {
    char status[256]{};
    const std::size_t count = result.savedCandleCount;
    const bool fewCandles = count % 10 >= 2 && count % 10 <= 4 &&
        (count % 100 < 12 || count % 100 > 14);
    const char* noun = count == 1
        ? "brakującą świecę"
        : (fewCandles ? "brakujące świece" : "brakujących świec");
    std::snprintf(
        status, sizeof(status),
        "Zaciągnięto %zu %s. Operacja zajęła %.1f sekund.",
        count, noun, result.elapsedSeconds);
    if (!error.empty()) {
        std::string backfillStatus = std::string(status) + " Błąd: " + error;
        if (result.detectedGapCount > 0) {
            backfillStatus += " Wykryto " +
                std::to_string(result.detectedGapCount) +
                " brakujących świec.";
        }
        return backfillStatus;
    }
    if (result.detectedGapCount > 0) {
        return std::string(status) + " Wykryto " +
            std::to_string(result.detectedGapCount) +
            " brakujących świec.";
    }
    return status;
}

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
 * Sprawdza, czy świeca UTC jest zamknięta, nawet jeśli dostawca ją opóźnił.
 */
bool isClosedCandle(const CandleData& candle) {
    SYSTEMTIME candleTime{};
    SYSTEMTIME currentTime{};
    if (!parseCandleUtcTime(candle.datetime, candleTime)) {
        return false;
    }

    GetSystemTime(&currentTime);
    FILETIME currentFileTime{};
    FILETIME candleFileTime{};
    if (!SystemTimeToFileTime(&currentTime, &currentFileTime) ||
        !SystemTimeToFileTime(&candleTime, &candleFileTime)) {
        return false;
    }

    ULARGE_INTEGER currentTicks{};
    currentTicks.LowPart = currentFileTime.dwLowDateTime;
    currentTicks.HighPart = currentFileTime.dwHighDateTime;
    ULARGE_INTEGER candleTicks{};
    candleTicks.LowPart = candleFileTime.dwLowDateTime;
    candleTicks.HighPart = candleFileTime.dwHighDateTime;
    return currentTicks.QuadPart >= candleTicks.QuadPart &&
        currentTicks.QuadPart - candleTicks.QuadPart >= fileTimeTicksPerMinute;
}

/**
 * Sprawdza, czy świeca odpowiada dokładnie ostatniej zamkniętej minucie UTC.
 */
bool isLatestClosedMinute(const CandleData& candle) {
    SYSTEMTIME candleTime{};
    SYSTEMTIME currentTime{};
    if (!parseCandleUtcTime(candle.datetime, candleTime)) {
        return false;
    }

    GetSystemTime(&currentTime);
    currentTime.wSecond = 0;
    currentTime.wMilliseconds = 0;
    FILETIME expectedFileTime{};
    FILETIME candleFileTime{};
    if (!SystemTimeToFileTime(&currentTime, &expectedFileTime) ||
        !SystemTimeToFileTime(&candleTime, &candleFileTime)) {
        return false;
    }

    ULARGE_INTEGER expectedTicks{};
    expectedTicks.LowPart = expectedFileTime.dwLowDateTime;
    expectedTicks.HighPart = expectedFileTime.dwHighDateTime;
    ULARGE_INTEGER candleTicks{};
    candleTicks.LowPart = candleFileTime.dwLowDateTime;
    candleTicks.HighPart = candleFileTime.dwHighDateTime;
    return expectedTicks.QuadPart >= fileTimeTicksPerMinute &&
        candleTicks.QuadPart ==
            expectedTicks.QuadPart - fileTimeTicksPerMinute;
}

/**
 * Zamienia znacznik UTC świecy na sekundy od początku epoki Unix.
 */
bool parseCandleUnixTimestamp(
    const CandleData& candle, std::int64_t& unixTimestamp) {
    SYSTEMTIME utcTime{};
    FILETIME fileTime{};
    if (!parseCandleUtcTime(candle.datetime, utcTime) ||
        !SystemTimeToFileTime(&utcTime, &fileTime)) {
        return false;
    }

    ULARGE_INTEGER fileTimeTicks{};
    fileTimeTicks.LowPart = fileTime.dwLowDateTime;
    fileTimeTicks.HighPart = fileTime.dwHighDateTime;
    if (fileTimeTicks.QuadPart < windowsToUnixEpochTicks) {
        return false;
    }
    unixTimestamp = static_cast<std::int64_t>(
        (fileTimeTicks.QuadPart - windowsToUnixEpochTicks) /
        fileTimeTicksPerSecond);
    return true;
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
    StockPriceSnapshot currentSnapshot{
        oneMinuteCandle_, status_, backfillStatus_};
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
 * Uzupełnia braki po starcie, a następnie pobiera nową świecę co minutę.
 */
void StockPriceMonitor::run() {
    CandleBackfillResult backfillResult;
    std::string backfillError;
    const bool backfillSucceeded =
        CandleBackfillClient::synchronize(backfillResult, backfillError);
    AcquireSRWLockExclusive(&statusLock_);
    if (!backfillResult.latestCandle.datetime.empty()) {
        oneMinuteCandle_ = std::move(backfillResult.latestCandle);
    }
    backfillStatus_ = createBackfillStatus(
        backfillResult, backfillSucceeded ? std::string{} : backfillError);
    ReleaseSRWLockExclusive(&statusLock_);
    if (!PostMessageA(notificationWindow_, candleUpdateMessage, 0, 0)) {
        return;
    }

    while (true) {
        if (!waitForNextCandleClose(stopEvent_)) {
            break;
        }

        // Po zwiększeniu limitu API zastąp pojedyncze pobranie pętlą poniżej.
        updateCandle();
        if (!PostMessageA(notificationWindow_, candleUpdateMessage, 0, 0)) {
            break;
        }

        /*
        // Odkomentuj po zwiększeniu limitu zapytań Twelve Data.
        const ULONGLONG retryStartedAt = GetTickCount64();
        while (true) {
            const bool latestCandleAvailable = updateCandle();
            if (!PostMessageA(notificationWindow_, candleUpdateMessage, 0, 0)) {
                return;
            }
            if (latestCandleAvailable ||
                GetTickCount64() - retryStartedAt >= candleRetryWindowMs) {
                break;
            }
            if (WaitForSingleObject(stopEvent_, candleRetryIntervalMs) != WAIT_TIMEOUT) {
                return;
            }
        }
        */
    }
}

/**
 * Pobiera świecę i atomowo aktualizuje stan monitora.
 * @return true, jeśli pobrano świecę dla ostatniej zamkniętej minuty UTC.
 */
bool StockPriceMonitor::updateCandle() {
    CandleData candle;
    std::string error;
    constexpr char monitoredSymbol[] = "EURUSD/USD";
    const bool fetched =
        CandlesDataClient::fetchLatestOneMinuteCandle(
            monitoredSymbol, candle, error);
    bool saved = false;
    std::size_t newlyDetectedGapCount = 0;
    bool signalSaved = false;
    if (fetched) {
        saved = CandleHistoryClient::saveCandle(
            monitoredSymbol, candle, newlyDetectedGapCount, signalSaved, error);
    }
    const bool closedCandleAvailable = fetched && isClosedCandle(candle);
    std::string updateStatus;
    if (!fetched) {
        updateStatus = "Błąd świecy 1-minutowej: " + error;
    } else if (!saved) {
        updateStatus = "Nie udało się zapisać świecy w bazie: " + error;
    } else if (newlyDetectedGapCount > 0) {
        updateStatus = "Wykryto " +
            std::to_string(newlyDetectedGapCount) +
            " brakujących świec.";
    } else {
        updateStatus = describeCandleFreshness(candle);
    }
    bool grokSignalSaved = false;
    if (saved && closedCandleAvailable) {
        std::vector<SignalCandleData> analysisCandles;
        std::string signalError;
        if (!CandleHistoryClient::fetchLatestAnalysisCandles(
                grok45::SignalGenerator::analysisWindowCandles,
                analysisCandles, signalError)) {
            updateStatus += " Błąd pobierania danych do algorytmu Grok-45: " +
                signalError;
        } else {
            std::int64_t savedCandleTimestamp = 0;
            if (analysisCandles.empty() ||
                !parseCandleUnixTimestamp(candle, savedCandleTimestamp) ||
                analysisCandles.back().timestamp != savedCandleTimestamp) {
                updateStatus +=
                    " Dane z bazy nie zawierają ostatniej zapisanej świecy; "
                    "sygnał Grok-45 nie został obliczony.";
            } else {
                std::vector<grok45::Candle> candlesForAnalysis;
                candlesForAnalysis.reserve(analysisCandles.size());
                for (const SignalCandleData& analysisCandle : analysisCandles) {
                    candlesForAnalysis.push_back(grok45::Candle{
                        analysisCandle.timestamp,
                        analysisCandle.open,
                        analysisCandle.high,
                        analysisCandle.low,
                        analysisCandle.close,
                        analysisCandle.volume});
                }

                const std::string generatedSignal =
                    grok45::SignalGenerator{}.generate(candlesForAnalysis);
                if (generatedSignal.find("\"signal\":\"LONG\"") !=
                        std::string::npos ||
                    generatedSignal.find("\"signal\":\"SHORT\"") !=
                        std::string::npos) {
                    if (!CandleHistoryClient::saveGeneratedSignal(
                            generatedSignal, grokSignalSaved, signalError)) {
                        updateStatus += " Błąd zapisu sygnału Grok-45: " +
                            signalError;
                    }
                }
            }
        }
    }
    if (saved && newlyDetectedGapCount > 0 && !AudioSignal::play()) {
        updateStatus += " Nie udało się odtworzyć sygnału dźwiękowego.";
    }
    if (saved && (signalSaved || grokSignalSaved) &&
        !AudioSignal::playSignalSaved()) {
        updateStatus += " Nie udało się odtworzyć dźwięku zapisu sygnału.";
    }

    AcquireSRWLockExclusive(&statusLock_);
    if (fetched) {
        oneMinuteCandle_ = std::move(candle);
    }
    status_ = updateStatus;
    ReleaseSRWLockExclusive(&statusLock_);
    return fetched && isLatestClosedMinute(candle);
}
