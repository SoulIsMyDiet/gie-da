#include "StockPriceMonitor.h"

#include "CandleBackfillClient.h"
#include "CandleHistoryClient.h"
#include "AudioSignal.h"
#include "SignalGenerator.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdint>
#include <utility>
#include <vector>

namespace {

constexpr UINT candleUpdateMessage = WM_APP + 1;
constexpr DWORD candleCloseBufferMs = 2000;
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
    std::string symbolsError;
    while (!CandleHistoryClient::fetchMonitoredSymbols(
        monitoredSymbols_, symbolsError)) {
        AcquireSRWLockExclusive(&statusLock_);
        status_ = "Nie udało się pobrać listy instrumentów: " + symbolsError;
        backfillStatus_ = "Oczekiwanie na konfigurację produktów.";
        ReleaseSRWLockExclusive(&statusLock_);
        if (!PostMessageA(notificationWindow_, candleUpdateMessage, 0, 0) ||
            !waitForNextCandleClose(stopEvent_)) {
            return;
        }
    }

    std::vector<std::string> startupOpenSymbols;
    std::string startupSessionError;
    while (!CandleHistoryClient::fetchOpenSymbols(
        startupOpenSymbols, startupSessionError)) {
        AcquireSRWLockExclusive(&statusLock_);
        status_ = "Nie udało się pobrać otwartych sesji: " +
            startupSessionError;
        ReleaseSRWLockExclusive(&statusLock_);
        if (!PostMessageA(notificationWindow_, candleUpdateMessage, 0, 0) ||
            !waitForNextCandleClose(stopEvent_)) {
            return;
        }
    }

    CandleBackfillResult backfillResult;
    std::string backfillError;
    std::string deferredBackfillStatus;
    bool backfillSucceeded = true;
    const auto backfillStartedAt = std::chrono::steady_clock::now();
    for (const std::string& symbol : monitoredSymbols_) {
        if (std::find(
                startupOpenSymbols.begin(),
                startupOpenSymbols.end(),
                symbol) == startupOpenSymbols.end()) {
            if (!deferredBackfillStatus.empty()) {
                deferredBackfillStatus += " ";
            }
            deferredBackfillStatus +=
                symbol + ": backfill odłożono do otwarcia sesji.";
            continue;
        }

        CandleBackfillResult productResult;
        std::string productError;
        const bool productSucceeded = CandleBackfillClient::synchronize(
            symbol, productResult, productError);
        if (productSucceeded) {
            backfilledSymbols_.insert(symbol);
        }
        backfillResult.savedCandleCount += productResult.savedCandleCount;
        backfillResult.detectedGapCount += productResult.detectedGapCount;
        if (symbol == "EURUSD/USD" &&
            !productResult.latestCandle.datetime.empty()) {
            backfillResult.latestCandle = std::move(productResult.latestCandle);
        }
        if (!productSucceeded) {
            backfillSucceeded = false;
            if (!backfillError.empty()) {
                backfillError += " ";
            }
            backfillError += symbol + ": " + productError;
        }
    }
    backfillResult.elapsedSeconds =
        std::chrono::duration<double>(
            std::chrono::steady_clock::now() - backfillStartedAt)
            .count();
    const bool startupGapSoundPlayed =
        backfillResult.detectedGapCount == 0 || AudioSignal::play();
    AcquireSRWLockExclusive(&statusLock_);
    if (!backfillResult.latestCandle.datetime.empty()) {
        oneMinuteCandle_ = std::move(backfillResult.latestCandle);
    }
    backfillStatus_ = createBackfillStatus(
        backfillResult, backfillSucceeded ? std::string{} : backfillError);
    if (!deferredBackfillStatus.empty()) {
        backfillStatus_ += " " + deferredBackfillStatus;
    }
    if (!startupGapSoundPlayed) {
        backfillStatus_ += " Nie udało się odtworzyć sygnału dźwiękowego.";
    }
    ReleaseSRWLockExclusive(&statusLock_);
    if (!PostMessageA(notificationWindow_, candleUpdateMessage, 0, 0)) {
        return;
    }

    while (true) {
        if (!waitForNextCandleClose(stopEvent_)) {
            break;
        }

        updateAdditionalProducts();
        if (!PostMessageA(notificationWindow_, candleUpdateMessage, 0, 0)) {
            break;
        }
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

/**
 * Pobiera tylko produkty z otwartą sesją, aby nie zużywać zapytań poza handlem.
 */
void StockPriceMonitor::updateAdditionalProducts() {
    std::vector<std::string> configuredSymbols;
    std::string configurationError;
    if (!CandleHistoryClient::fetchMonitoredSymbols(
            configuredSymbols, configurationError)) {
        AcquireSRWLockExclusive(&statusLock_);
        status_ += " Błąd odświeżania konfiguracji produktów: " +
            configurationError;
        ReleaseSRWLockExclusive(&statusLock_);
        return;
    }

    for (auto backfilledSymbol = backfilledSymbols_.begin();
         backfilledSymbol != backfilledSymbols_.end();) {
        if (std::find(
                configuredSymbols.begin(),
                configuredSymbols.end(),
                *backfilledSymbol) == configuredSymbols.end()) {
            backfilledSymbol = backfilledSymbols_.erase(backfilledSymbol);
        } else {
            ++backfilledSymbol;
        }
    }
    monitoredSymbols_ = std::move(configuredSymbols);

    std::vector<std::string> openSymbols;
    std::string symbolsError;
    if (!CandleHistoryClient::fetchOpenSymbols(openSymbols, symbolsError)) {
        AcquireSRWLockExclusive(&statusLock_);
        status_ += " Błąd pobierania aktywnych sesji: " + symbolsError;
        ReleaseSRWLockExclusive(&statusLock_);
        return;
    }

    if (openSymbols.empty()) {
        AcquireSRWLockExclusive(&statusLock_);
        status_ = "Brak obecnie otwartych sesji aktywnych produktów.";
        ReleaseSRWLockExclusive(&statusLock_);
        return;
    }

    std::string additionalStatus;
    bool anySignalSaved = false;
    bool anyGapDetected = false;
    for (const std::string& symbol : openSymbols) {
        if (backfilledSymbols_.find(symbol) == backfilledSymbols_.end()) {
            CandleBackfillResult backfillResult;
            std::string backfillError;
            const bool backfillSucceeded =
                CandleBackfillClient::synchronize(
                    symbol, backfillResult, backfillError);
            anyGapDetected =
                anyGapDetected || backfillResult.detectedGapCount > 0;
            if (!backfillSucceeded) {
                if (!additionalStatus.empty()) {
                    additionalStatus += " | ";
                }
                additionalStatus += symbol +
                    ": błąd uzupełniania historii: " + backfillError;
                continue;
            }
            backfilledSymbols_.insert(symbol);
            if (symbol == "EURUSD/USD" &&
                !backfillResult.latestCandle.datetime.empty()) {
                AcquireSRWLockExclusive(&statusLock_);
                oneMinuteCandle_ = std::move(backfillResult.latestCandle);
                ReleaseSRWLockExclusive(&statusLock_);
            }
            if (!additionalStatus.empty()) {
                additionalStatus += " | ";
            }
            additionalStatus += symbol +
                ": zakończono początkowe uzupełnianie historii.";
            continue;
        }

        if (symbol == "EURUSD/USD") {
            updateCandle();
            continue;
        }

        CandleData candle;
        std::string error;
        if (!CandlesDataClient::fetchLatestOneMinuteCandle(
                symbol, candle, error)) {
            if (!additionalStatus.empty()) {
                additionalStatus += " | ";
            }
            additionalStatus += symbol + ": błąd pobierania świecy: " + error;
            continue;
        }

        std::size_t detectedGapCount = 0;
        bool signalSaved = false;
        if (!CandleHistoryClient::saveCandle(
                symbol, candle, detectedGapCount, signalSaved, error)) {
            if (!additionalStatus.empty()) {
                additionalStatus += " | ";
            }
            additionalStatus += symbol + ": błąd zapisu świecy: " + error;
            continue;
        }
        anySignalSaved = anySignalSaved || signalSaved;
        anyGapDetected = anyGapDetected || detectedGapCount > 0;
        if (!additionalStatus.empty()) {
            additionalStatus += " | ";
        }
        additionalStatus += symbol + ": świeca została zapisana";
        if (detectedGapCount > 0) {
            additionalStatus += ", wykryto " +
                std::to_string(detectedGapCount) + " brakujących świec";
            CandleBackfillResult backfillResult;
            std::string backfillError;
            if (!CandleBackfillClient::synchronize(
                    symbol, backfillResult, backfillError)) {
                backfilledSymbols_.erase(symbol);
                additionalStatus +=
                    "; nie udało się ich uzupełnić: " + backfillError;
            } else {
                additionalStatus +=
                    "; brakujące świece zostały uzupełnione";
            }
        }
        additionalStatus += ".";
    }

    if (anyGapDetected && !AudioSignal::play()) {
        additionalStatus += " Nie udało się odtworzyć sygnału dźwiękowego.";
    }
    if (anySignalSaved && !AudioSignal::playSignalSaved()) {
        additionalStatus += " Nie udało się odtworzyć dźwięku zapisu sygnału.";
    }

    AcquireSRWLockExclusive(&statusLock_);
    const bool eurusdIsOpen = std::find(
        openSymbols.begin(), openSymbols.end(), "EURUSD/USD") !=
        openSymbols.end();
    if (!eurusdIsOpen) {
        status_ = additionalStatus;
    } else if (!additionalStatus.empty()) {
        status_ += " | " + additionalStatus;
    }
    ReleaseSRWLockExclusive(&statusLock_);
}
