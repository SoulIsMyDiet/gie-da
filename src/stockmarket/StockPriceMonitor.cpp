#include "StockPriceMonitor.h"

#include "CandleBackfillClient.h"
#include "CandleHistoryClient.h"
#include "AudioSignal.h"
#include "ErrorLogger.h"
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
// Sprawne API odpytujemy co sekundę; po błędzie odstęp rośnie od 2 do 30 sekund.
constexpr DWORD notificationPollIntervalMs = 1000;
constexpr DWORD notificationRetryInitialMs = 2000;
constexpr DWORD notificationRetryMaximumMs = 30000;
// Ogranicza ponawianie kosztownego backfillu po błędzie dostawcy danych.
constexpr auto backfillRetryDelay = std::chrono::minutes(5);
constexpr ULONGLONG fileTimeTicksPerSecond = 10000000ULL;
constexpr ULONGLONG fileTimeTicksPerMinute = 60ULL * fileTimeTicksPerSecond;
constexpr ULONGLONG windowsToUnixEpochTicks = 116444736000000000ULL;

// Backend
/**
 * Sprawdza, czy świecę pobiera osobny strumień IBKR, a nie Twelve Data.
 * @param symbol Wewnętrzny symbol produktu.
 * @return true dla GER30 i ES, które są obsługiwane przez IBKR.
 */
bool isIbkrOnlyProduct(const std::string& symbol) {
    return symbol == "GER30" || symbol == "ES";
}

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
 * Przygotowuje obsługę powiadomień o nowych sygnałach.
 */
StockPriceMonitor::StockPriceMonitor() = default;

/**
 * Zatrzymuje wątek powiadomień przed zniszczeniem monitora.
 */
StockPriceMonitor::~StockPriceMonitor() {
    stop();
}

/**
 * Tworzy zdarzenie zatrzymania i uruchamia wątek powiadomień.
 * Nie uruchamia pobierania świec z Twelve Data; źródłem nowych świec jest IBKR.
 * @param notificationWindow Okno otrzymujące komunikaty o zmianach.
 * @return true, jeśli wątek powiadomień został uruchomiony.
 */
bool StockPriceMonitor::start(HWND notificationWindow) {
    if (notificationWindow == nullptr || notificationThread_ != nullptr) {
        return false;
    }

    notificationWindow_ = notificationWindow;
    stopEvent_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (stopEvent_ == nullptr) {
        return false;
    }

    notificationThread_ = CreateThread(
        nullptr, 0, notificationThreadEntryPoint, this, 0, nullptr);
    if (notificationThread_ == nullptr) {
        CloseHandle(stopEvent_);
        stopEvent_ = nullptr;
        notificationWindow_ = nullptr;
        return false;
    }
    return true;
}

/**
 * Zatrzymuje wątek powiadomień, czeka na jego zakończenie i zwalnia uchwyty.
 */
void StockPriceMonitor::stop() {
    if (stopEvent_ != nullptr) {
        SetEvent(stopEvent_);
    }
    if (notificationThread_ != nullptr) {
        WaitForSingleObject(notificationThread_, INFINITE);
        CloseHandle(notificationThread_);
        notificationThread_ = nullptr;
    }
    if (stopEvent_ != nullptr) {
        CloseHandle(stopEvent_);
        stopEvent_ = nullptr;
    }
    notificationWindow_ = nullptr;
}

/**
 * Zwraca świecę i status wraz z błędami powiadomień pod blokadą współdzieloną.
 * @return Kopia aktualnego stanu monitora.
 */
StockPriceSnapshot StockPriceMonitor::snapshot() const {
    AcquireSRWLockShared(&statusLock_);
    StockPriceSnapshot currentSnapshot{
        oneMinuteCandle_, status_, backfillStatus_};
    if (!notificationStatus_.empty()) {
        currentSnapshot.status += " " + notificationStatus_;
    }
    ReleaseSRWLockShared(&statusLock_);
    return currentSnapshot;
}

/**
 * Odtwarza po jednym dźwięku dla każdego zapisanego sygnału.
 */
bool StockPriceMonitor::playSavedSignalSounds(std::size_t signalCount) {
    for (std::size_t index = 0; index < signalCount; ++index) {
        if (!playSignalSavedAudio()) {
            return false;
        }
    }
    return true;
}

// Backend
/**
 * Uruchamia pętlę powiadomień dla monitora przekazanego przez Windows.
 * @param parameter Wskaźnik na monitor.
 * @return Kod zakończenia wątku Windows.
 */
DWORD WINAPI StockPriceMonitor::notificationThreadEntryPoint(LPVOID parameter) {
    auto* monitor = static_cast<StockPriceMonitor*>(parameter);
    monitor->runNotifications();
    return 0;
}

/**
 * Udostępnia błąd powiadomień w stanie monitora i powiadamia okno o zmianie.
 * @param status Opis błędu albo pusty tekst po odzyskaniu sprawności.
 */
void StockPriceMonitor::setNotificationStatus(const std::string& status) {
    AcquireSRWLockExclusive(&statusLock_);
    const bool changed = notificationStatus_ != status;
    notificationStatus_ = status;
    ReleaseSRWLockExclusive(&statusLock_);
    if (changed) {
        PostMessageA(notificationWindow_, candleUpdateMessage, 0, 0);
    }
}

/**
 * Serializuje pojedyncze odtwarzania, aby dźwięki monitora nie przerywały się.
 * Sieć i oczekiwanie między powiadomieniami nie zajmują blokady dźwięku.
 * @return true, jeśli odtwarzanie zakończyło się powodzeniem.
 */
bool StockPriceMonitor::playSignalSavedAudio() {
    AcquireSRWLockExclusive(&audioLock_);
    const bool played = AudioSignal::playSignalSaved();
    ReleaseSRWLockExclusive(&audioLock_);
    return played;
}

/**
 * Obsługuje kursor w pamięci, strony powiadomień i ponawianie błędów.
 * Kursor powiadomień Groka przesuwa się po odtworzeniu, a po restarcie
 * jest inicjalizowany ponownie z bieżącego maksimum i historia jest pomijana.
 */
void StockPriceMonitor::runNotifications() {
    std::uint64_t cursor = 0;
    bool initialized = false;
    DWORD retryDelayMs = notificationRetryInitialMs;
    while (WaitForSingleObject(stopEvent_, 0) == WAIT_TIMEOUT) {
        std::string error;
        bool succeeded = true;
        bool hasMore = false;
        if (!initialized) {
            if (CandleHistoryClient::fetchGrokNotificationCursor(
                    cursor, error)) {
                initialized = true;
            } else {
                succeeded = false;
            }
        } else {
            GrokNotificationPage page;
            if (CandleHistoryClient::fetchGrokNotifications(
                    cursor, page, error)) {
                for (const std::uint64_t id : page.ids) {
                    if (WaitForSingleObject(stopEvent_, 0) != WAIT_TIMEOUT) {
                        return;
                    }
                    if (!playSignalSavedAudio()) {
                        error = "Nie udało się odtworzyć dźwięku powiadomienia Groka.";
                        succeeded = false;
                        break;
                    }
                    cursor = id;
                }
                hasMore = page.hasMore;
            } else {
                succeeded = false;
            }
        }

        std::vector<std::uint64_t> usdmxnNotificationIds;
        std::string usdmxnError;
        bool usdmxnSucceeded =
            CandleHistoryClient::fetchUsdmxnNotificationIds(
                usdmxnNotificationIds, usdmxnError);
        if (usdmxnSucceeded) {
            for (const std::uint64_t id : usdmxnNotificationIds) {
                if (WaitForSingleObject(stopEvent_, 0) != WAIT_TIMEOUT) {
                    return;
                }
                if (!playSignalSavedAudio()) {
                    usdmxnError =
                        "Nie udało się odtworzyć dźwięku sygnału USD/MXN.";
                    usdmxnSucceeded = false;
                    break;
                }
                if (!CandleHistoryClient::acknowledgeUsdmxnNotification(
                        id, usdmxnError)) {
                    usdmxnSucceeded = false;
                    break;
                }
            }
            hasMore = hasMore || usdmxnNotificationIds.size() >= 100;
        }
        if (!usdmxnSucceeded) {
            if (succeeded) {
                error = usdmxnError;
            } else {
                error += " | USD/MXN: " + usdmxnError;
            }
            succeeded = false;
        }

        std::vector<std::uint64_t> usdjpyNotificationIds;
        std::string usdjpyError;
        bool usdjpySucceeded =
            CandleHistoryClient::fetchUsdjpyNotificationIds(
                usdjpyNotificationIds, usdjpyError);
        if (usdjpySucceeded) {
            for (const std::uint64_t id : usdjpyNotificationIds) {
                if (WaitForSingleObject(stopEvent_, 0) != WAIT_TIMEOUT) {
                    return;
                }
                // Potwierdzenie przed dźwiękiem zapobiega powtórkom po restarcie.
                // Awaria między tymi operacjami może jednak zgubić powiadomienie.
                if (!CandleHistoryClient::acknowledgeUsdjpyNotification(
                        id, usdjpyError)) {
                    usdjpySucceeded = false;
                    break;
                }
                if (!playSignalSavedAudio()) {
                    usdjpyError =
                        "Nie udało się odtworzyć dźwięku sygnału USD/JPY.";
                    usdjpySucceeded = false;
                    break;
                }
            }
            hasMore = hasMore || usdjpyNotificationIds.size() >= 100;
        }
        if (!usdjpySucceeded) {
            if (succeeded) {
                error = usdjpyError;
            } else {
                error += " | USD/JPY: " + usdjpyError;
            }
            succeeded = false;
        }

        if (succeeded) {
            setNotificationStatus({});
            retryDelayMs = notificationRetryInitialMs;
        } else {
            ErrorLogger::warning(
                "Nie udało się pobrać powiadomień sygnałów: " + error);
            setNotificationStatus("Powiadomienia sygnałów: " + error);
        }
        const DWORD waitMs = succeeded
            ? (hasMore ? 0 : notificationPollIntervalMs)
            : retryDelayMs;
        if (!succeeded) {
            retryDelayMs = std::min(
                notificationRetryMaximumMs, retryDelayMs * 2);
        }
        if (WaitForSingleObject(stopEvent_, waitMs) != WAIT_TIMEOUT) {
            return;
        }
    }
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
        if (isIbkrOnlyProduct(symbol)) {
            if (!deferredBackfillStatus.empty()) {
                deferredBackfillStatus += " ";
            }
            deferredBackfillStatus +=
                symbol + ": świece pobiera osobny strumień IBKR.";
            continue;
        }
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
            backfillRetryAfter_.erase(symbol);
        } else {
            backfillRetryAfter_[symbol] =
                std::chrono::steady_clock::now() + backfillRetryDelay;
        }
        backfillResult.savedCandleCount += productResult.savedCandleCount;
        backfillResult.detectedGapCount += productResult.detectedGapCount;
        if (symbol == "EUR/USD" &&
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
    AcquireSRWLockExclusive(&statusLock_);
    if (!backfillResult.latestCandle.datetime.empty()) {
        oneMinuteCandle_ = std::move(backfillResult.latestCandle);
    }
    backfillStatus_ = createBackfillStatus(
        backfillResult, backfillSucceeded ? std::string{} : backfillError);
    if (!deferredBackfillStatus.empty()) {
        backfillStatus_ += " " + deferredBackfillStatus;
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
    constexpr char monitoredSymbol[] = "EUR/USD";
    const bool fetched =
        CandlesDataClient::fetchLatestOneMinuteCandle(
            monitoredSymbol, candle, error);
    bool saved = false;
    std::size_t newlyDetectedGapCount = 0;
    std::size_t savedSignalCount = 0;
    if (fetched) {
        saved = CandleHistoryClient::saveCandle(
            monitoredSymbol,
            candle,
            newlyDetectedGapCount,
            savedSignalCount,
            error);
    }
    const bool closedCandleAvailable = fetched && isClosedCandle(candle);
    std::string updateStatus;
    if (!fetched) {
        ErrorLogger::error(
            "Błąd pobierania świecy 1-minutowej: " + error);
        updateStatus = "Błąd świecy 1-minutowej: " + error;
    } else if (!saved) {
        ErrorLogger::error(
            "Nie udało się zapisać świecy w bazie: " + error);
        updateStatus = "Nie udało się zapisać świecy w bazie: " + error;
    } else if (newlyDetectedGapCount > 0) {
        ErrorLogger::warning(
            "Wykryto " + std::to_string(newlyDetectedGapCount) +
            " brakujących świec dla EUR/USD.");
        updateStatus = "Wykryto " +
            std::to_string(newlyDetectedGapCount) +
            " brakujących świec.";
    } else {
        updateStatus = describeCandleFreshness(candle);
    }
    if (saved && closedCandleAvailable) {
        std::vector<SignalCandleData> analysisCandles;
        std::string signalError;
        if (!CandleHistoryClient::fetchLatestAnalysisCandles(
                grok45::SignalGenerator::analysisWindowCandles,
                analysisCandles, signalError)) {
            ErrorLogger::error(
                "Błąd pobierania danych do algorytmu Grok-45: " +
                signalError);
            updateStatus += " Błąd pobierania danych do algorytmu Grok-45: " +
                signalError;
        } else {
            std::int64_t savedCandleTimestamp = 0;
            if (analysisCandles.empty() ||
                !parseCandleUnixTimestamp(candle, savedCandleTimestamp) ||
                analysisCandles.back().timestamp != savedCandleTimestamp) {
                ErrorLogger::warning(
                    "Dane z bazy nie zawierają ostatniej zapisanej świecy; "
                    "sygnał Grok-45 nie został obliczony.");
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
                    bool grokSignalSaved = false;
                    if (!CandleHistoryClient::saveGeneratedSignal(
                            generatedSignal, grokSignalSaved, signalError)) {
                        ErrorLogger::error(
                            "Błąd zapisu sygnału Grok-45: " + signalError);
                        updateStatus += " Błąd zapisu sygnału Grok-45: " +
                            signalError;
                    } else if (grokSignalSaved) {
                        ++savedSignalCount;
                    }
                }
            }
        }
    }
    if (saved) {
        for (std::size_t index = 0; index < savedSignalCount; ++index) {
            if (!playSignalSavedAudio()) {
                ErrorLogger::error(
                    "Nie udało się odtworzyć dźwięku zapisu sygnału.");
                updateStatus += " Nie udało się odtworzyć dźwięku zapisu sygnału.";
                break;
            }
        }
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
 * Pobiera świece aktywnych produktów i odtwarza dźwięki dla zapisów sygnałów.
 * Liczniki z potwierdzonego zapisu częściowego są obsługiwane również wtedy,
 * gdy analiza co najmniej jednej strategii zwróci błąd HTTP 503.
 */
void StockPriceMonitor::updateAdditionalProducts() {
    std::vector<std::string> configuredSymbols;
    std::string configurationError;
    if (!CandleHistoryClient::fetchMonitoredSymbols(
            configuredSymbols, configurationError)) {
        ErrorLogger::error(
            "Błąd odświeżania konfiguracji produktów: " +
            configurationError);
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
        ErrorLogger::error(
            "Błąd pobierania aktywnych sesji: " + symbolsError);
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
    std::size_t savedSignalCount = 0;
    for (const std::string& symbol : openSymbols) {
        if (isIbkrOnlyProduct(symbol)) {
            if (!additionalStatus.empty()) {
                additionalStatus += " | ";
            }
            additionalStatus +=
                symbol + ": świeca jest pobierana przez IBKR.";
            continue;
        }

        if (backfilledSymbols_.find(symbol) == backfilledSymbols_.end()) {
            const auto retryAfter = backfillRetryAfter_.find(symbol);
            const bool retryBackfill =
                retryAfter == backfillRetryAfter_.end() ||
                std::chrono::steady_clock::now() >= retryAfter->second;
            if (retryBackfill) {
                CandleBackfillResult backfillResult;
                std::string backfillError;
                const bool backfillSucceeded =
                    CandleBackfillClient::synchronize(
                        symbol, backfillResult, backfillError);
                if (!backfillSucceeded) {
                    ErrorLogger::warning(
                        symbol + ": błąd uzupełniania historii: " +
                        backfillError);
                    backfillRetryAfter_[symbol] =
                        std::chrono::steady_clock::now() +
                        backfillRetryDelay;
                    if (!additionalStatus.empty()) {
                        additionalStatus += " | ";
                    }
                    additionalStatus += symbol +
                        ": błąd uzupełniania historii: " + backfillError;
                } else {
                    backfilledSymbols_.insert(symbol);
                    backfillRetryAfter_.erase(symbol);
                    if (symbol == "EUR/USD" &&
                        !backfillResult.latestCandle.datetime.empty()) {
                        AcquireSRWLockExclusive(&statusLock_);
                        oneMinuteCandle_ =
                            std::move(backfillResult.latestCandle);
                        ReleaseSRWLockExclusive(&statusLock_);
                    }
                    if (!additionalStatus.empty()) {
                        additionalStatus += " | ";
                    }
                    additionalStatus += symbol +
                        ": zakończono początkowe uzupełnianie historii.";
                    continue;
                }
            }
        }

        if (symbol == "EUR/USD") {
            updateCandle();
            continue;
        }

        CandleData candle;
        std::string error;
        if (!CandlesDataClient::fetchLatestOneMinuteCandle(
                symbol, candle, error)) {
            ErrorLogger::warning(
                symbol + ": błąd pobierania świecy: " + error);
            if (!additionalStatus.empty()) {
                additionalStatus += " | ";
            }
            additionalStatus += symbol + ": błąd pobierania świecy: " + error;
            continue;
        }

        std::size_t detectedGapCount = 0;
        std::size_t signalSaveCount = 0;
        bool candleSavedDespiteError = false;
        const bool saveSucceeded = CandleHistoryClient::saveCandle(
            symbol,
            candle,
            detectedGapCount,
            signalSaveCount,
            error,
            &candleSavedDespiteError);
        savedSignalCount += signalSaveCount;
        if (!saveSucceeded && !candleSavedDespiteError) {
            ErrorLogger::error(
                symbol + ": błąd zapisu świecy: " + error);
            if (!additionalStatus.empty()) {
                additionalStatus += " | ";
            }
            additionalStatus += symbol + ": błąd zapisu świecy: " + error;
            continue;
        }
        if (!additionalStatus.empty()) {
            additionalStatus += " | ";
        }
        additionalStatus += symbol + ": ";
        if (candleSavedDespiteError) {
            ErrorLogger::warning(
                symbol + ": świeca zapisana, ale wystąpił błąd: " + error);
            additionalStatus += "świeca zapisana, ale " + error;
        } else {
            additionalStatus += "świeca została zapisana";
        }
        if (detectedGapCount > 0) {
            additionalStatus += candleSavedDespiteError
                ? "; wykryto "
                : ", wykryto ";
            additionalStatus +=
                std::to_string(detectedGapCount) + " brakujących świec";
            CandleBackfillResult backfillResult;
            std::string backfillError;
            if (!CandleBackfillClient::synchronize(
                    symbol, backfillResult, backfillError)) {
                ErrorLogger::warning(
                    symbol + ": nie udało się uzupełnić brakujących świec: " +
                    backfillError);
                backfilledSymbols_.erase(symbol);
                backfillRetryAfter_[symbol] =
                    std::chrono::steady_clock::now() + backfillRetryDelay;
                additionalStatus +=
                    "; nie udało się ich uzupełnić: " + backfillError;
            } else {
                backfilledSymbols_.insert(symbol);
                backfillRetryAfter_.erase(symbol);
                additionalStatus +=
                    "; brakujące świece zostały uzupełnione";
            }
        }
        additionalStatus += ".";
    }

    for (std::size_t index = 0; index < savedSignalCount; ++index) {
        if (!playSignalSavedAudio()) {
            ErrorLogger::error(
                "Nie udało się odtworzyć dźwięku zapisu sygnału.");
            additionalStatus += " Nie udało się odtworzyć dźwięku zapisu sygnału.";
            break;
        }
    }

    AcquireSRWLockExclusive(&statusLock_);
    const bool eurusdIsOpen = std::find(
        openSymbols.begin(), openSymbols.end(), "EUR/USD") !=
        openSymbols.end();
    if (!eurusdIsOpen) {
        status_ = additionalStatus;
    } else if (!additionalStatus.empty()) {
        status_ += " | " + additionalStatus;
    }
    ReleaseSRWLockExclusive(&statusLock_);
}
