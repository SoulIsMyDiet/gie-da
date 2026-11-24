#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "IBGatewayClient.h"

#include "CandleHistoryClient.h"
#include "Decimal.h"
#include "EClientSocket.h"
#include "EReader.h"
#include "ErrorLogger.h"

#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cmath>
#include <iomanip>
#include <exception>
#include <memory>
#include <sstream>
#include <system_error>
#include <utility>

// Backend
namespace {

constexpr auto connectionHandshakeTimeout = std::chrono::seconds(15);
constexpr auto candleSaveRetryDelay = std::chrono::seconds(5);
constexpr int eurUsdHistoricalRequestId = 9251;
constexpr int eurUsdQuoteRequestId = 9252;
constexpr std::size_t maximumHistoryCandlesPerInstrument = 1000;

/**
 * Opisuje kontrakt, ustawienia świec i symbol używany przez lokalną bazę.
 */
struct IBKRInstrumentDefinition {
    int historicalRequestId;
    int quoteRequestId;
    const char* databaseSymbol;
    const char* contractSymbol;
    const char* securityType;
    const char* exchange;
    const char* currency;
    const char* whatToShow;
    bool useRegularTradingHours;
};

// Rejestr musi odpowiadać instrumentom z market_products.py.
constexpr std::array<IBKRInstrumentDefinition, 6> ibkrInstrumentRegistry{{
    {
        9251, 9252, "EUR/USD", "EUR", "CASH", "IDEALPRO", "USD",
        "MIDPOINT", false},
    {
        9253, 9258, "USD/MXN", "USD", "CASH", "IDEALPRO", "MXN",
        "MIDPOINT", false},
    {
        9254, 9259, "USD/JPY", "USD", "CASH", "IDEALPRO", "JPY",
        "MIDPOINT", false},
    {
        9255, 0, "XAU/USD", "XAUUSD", "CMDTY", "SMART", "USD", "MIDPOINT",
        false},
    // Świece rynku krypto wymagają typu AGGTRADES w IBKR.
    {
        9256, 0, "BTC/USD", "BTC", "CRYPTO", "PAXOS", "USD", "AGGTRADES",
        false},
    {9257, 0, "AAPL", "AAPL", "STK", "SMART", "USD", "TRADES", true},
}};

/**
 * Wypełnia kontrakt IBKR danymi z rejestru instrumentów.
 */
void configureContract(
    Contract& contract, const IBKRInstrumentDefinition& instrument) {
    contract.symbol = instrument.contractSymbol;
    contract.secType = instrument.securityType;
    contract.exchange = instrument.exchange;
    contract.currency = instrument.currency;
}

/**
 * Znajduje konfigurację instrumentu po identyfikatorze strumienia IBKR.
 * @param requestId Identyfikator żądania świec zwrócony przez IBKR.
 * @return Wskaźnik do konfiguracji albo nullptr dla innego żądania.
 */
const IBKRInstrumentDefinition* findInstrumentByRequestId(int requestId) {
    for (const IBKRInstrumentDefinition& instrument :
         ibkrInstrumentRegistry) {
        if (instrument.historicalRequestId == requestId) {
            return &instrument;
        }
    }
    return nullptr;
}

/**
 * Znajduje konfigurację instrumentu po identyfikatorze kwotowań IBKR.
 * @param requestId Identyfikator żądania kwotowań zwrócony przez IBKR.
 * @return Wskaźnik do konfiguracji albo nullptr dla innego żądania.
 */
const IBKRInstrumentDefinition* findInstrumentByQuoteRequestId(int requestId) {
    for (const IBKRInstrumentDefinition& instrument :
         ibkrInstrumentRegistry) {
        if (instrument.quoteRequestId != 0 &&
            instrument.quoteRequestId == requestId) {
            return &instrument;
        }
    }
    return nullptr;
}

/**
 * Odczytuje wolumen z paska i pomija ujemny znacznik braku danych IBKR.
 * @param bar Pasek otrzymany z API IBKR.
 * @return Wolumen jako tekst albo pusty tekst, jeśli IBKR go nie podało.
 */
std::string formatIbkrVolume(const Bar& bar) {
    std::string volume = DecimalFunctions::decimalToString(bar.volume);
    if (!volume.empty() && volume.front() == '-') {
        volume.clear();
    }
    return volume;
}

/**
 * Odczytuje czas świecy IBKR jako znacznik Unix i zaokrągla go do pełnej minuty.
 * @param value Tekstowy znacznik czasu odebrany od IBKR.
 * @param timestampUtc Otrzymuje czas otwarcia świecy w sekundach UTC.
 * @return true, jeśli wartość jest dodatnim znacznikiem Unix.
 */
bool parseIbkrTimestamp(
    const std::string& value, std::int64_t& timestampUtc) {
    std::int64_t parsedTimestamp = 0;
    const auto result = std::from_chars(
        value.data(), value.data() + value.size(), parsedTimestamp);
    if (result.ec != std::errc{} ||
        result.ptr != value.data() + value.size() ||
        parsedTimestamp <= 0) {
        return false;
    }

    const auto minute = std::chrono::floor<std::chrono::minutes>(
        std::chrono::seconds{parsedTimestamp});
    timestampUtc = std::chrono::duration_cast<std::chrono::seconds>(
        minute).count();
    return true;
}

/**
 * Formatuje czas UTC świecy według strefy ustawionej w systemie Windows.
 * @param timestampUtc Czas otwarcia świecy w sekundach UTC.
 * @param formattedTimestamp Otrzymuje czas w lokalnym formacie daty i godziny.
 * @return true, jeśli system przeliczył czas na swoją strefę czasową.
 */
bool formatLocalTimestamp(
    std::int64_t timestampUtc, std::string& formattedTimestamp) {
    const std::chrono::sys_seconds timestamp{
        std::chrono::seconds{timestampUtc}};
    const auto day = std::chrono::floor<std::chrono::days>(timestamp);
    const std::chrono::year_month_day date{day};
    if (!date.ok()) {
        return false;
    }
    const std::chrono::hh_mm_ss time{timestamp - day};
    SYSTEMTIME utcTime{};
    utcTime.wYear = static_cast<WORD>(static_cast<int>(date.year()));
    utcTime.wMonth =
        static_cast<WORD>(static_cast<unsigned int>(date.month()));
    utcTime.wDay = static_cast<WORD>(static_cast<unsigned int>(date.day()));
    utcTime.wHour = static_cast<WORD>(time.hours().count());
    utcTime.wMinute = static_cast<WORD>(time.minutes().count());
    utcTime.wSecond = static_cast<WORD>(time.seconds().count());

    TIME_ZONE_INFORMATION localTimeZone{};
    if (GetTimeZoneInformation(&localTimeZone) == TIME_ZONE_ID_INVALID) {
        return false;
    }
    SYSTEMTIME localTime{};
    if (!SystemTimeToTzSpecificLocalTime(
            &localTimeZone, &utcTime, &localTime)) {
        return false;
    }

    std::ostringstream result;
    result << std::setfill('0')
           << std::setw(4) << localTime.wYear << '-'
           << std::setw(2) << localTime.wMonth << '-'
           << std::setw(2) << localTime.wDay << ' '
           << std::setw(2) << localTime.wHour << ':'
           << std::setw(2) << localTime.wMinute << ':'
           << std::setw(2) << localTime.wSecond;
    formattedTimestamp = result.str();
    return true;
}

/**
 * Przygotowuje czytelny opis błędu zwróconego przez oficjalne API IBKR.
 * @param errorCode Kod błędu przekazany przez IBKR.
 * @param errorString Tekst komunikatu przekazany przez IBKR.
 * @return Komunikat zawierający kod i opis błędu.
 * @throws std::bad_alloc, jeśli nie można przydzielić pamięci na tekst.
 */
std::string makeApiErrorMessage(
    int errorCode, const std::string& errorString) {
    std::ostringstream message;
    message << "IBKR API zgłosiło błąd " << errorCode << ": "
            << errorString;
    return message.str();
}

} // namespace

/**
 * Zatrzymuje połączenie przed zniszczeniem sygnału i danych statusu.
 */
IBGatewayClient::~IBGatewayClient() {
    disconnect();
}

/**
 * Sprawdza ustawienia i uruchamia wątek obsługujący sesję IBKR.
 */
bool IBGatewayClient::connect(
    const std::string& host,
    int port,
    int clientId,
    std::string& errorMessage) {
    errorMessage.clear();
    if (host.empty()) {
        errorMessage = "Adres IB Gateway nie może być pusty.";
        return false;
    }
    if (port < 1 || port > 65535) {
        errorMessage = "Port IB Gateway musi należeć do zakresu 1-65535.";
        return false;
    }
    if (clientId < 0) {
        errorMessage = "Identyfikator klienta API nie może być ujemny.";
        return false;
    }

    const IBGatewayConnectionStatus currentStatus = status();
    if (currentStatus.state == IBGatewayConnectionState::Connecting ||
        currentStatus.state == IBGatewayConnectionState::Connected ||
        currentStatus.state == IBGatewayConnectionState::Disconnecting) {
        errorMessage = "Połączenie z IB Gateway jest już aktywne.";
        return false;
    }
    if (connectionThread_.joinable()) {
        connectionThread_.join();
    }

    stopRequested_.store(false);
    updateStatus(
        IBGatewayConnectionState::Connecting,
        "Łączenie z IB Gateway...");
    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        candle_ = {};
        candle_.message =
            "Łączenie z IB Gateway przed pobraniem świecy...";
        pendingCandlesBySymbol_.clear();
        quote_ = {};
        quote_.message = "Oczekiwanie na kwotowania bid/ask z IBKR.";
        quotesBySymbol_.clear();
        historySyncRequested_ = false;
        historySyncInProgress_ = false;
        historySyncRequestId_ = 0;
        historySyncInstrumentIndex_ = 0;
        historySyncSavedCandleCount_ = 0;
        historySyncSkippedInstrumentCount_ = 0;
        historySyncSymbol_.clear();
        historySyncCandles_.clear();
    }
    notifyStatusChanged();
    try {
        connectionThread_ = std::thread(
            &IBGatewayClient::runConnection,
            this,
            host,
            port,
            clientId);
    } catch (const std::system_error& exception) {
        errorMessage =
            "Nie można uruchomić wątku połączenia IBKR: " +
            std::string(exception.what());
        updateStatus(IBGatewayConnectionState::Error, errorMessage);
        return false;
    }
    return true;
}

/**
 * Wysyła sygnał do pętli odczytu i porządkuje zakończenie wątku.
 */
void IBGatewayClient::disconnect() {
    if (!connectionThread_.joinable()) {
        updateStatus(
            IBGatewayConnectionState::Disconnected,
            "Rozłączono.");
        return;
    }

    stopRequested_.store(true);
    updateStatus(
        IBGatewayConnectionState::Disconnecting,
        "Rozłączanie z IB Gateway...");
    readerSignal_.issueSignal();
    if (connectionThread_.get_id() != std::this_thread::get_id()) {
        connectionThread_.join();
    }
    updateStatus(
        IBGatewayConnectionState::Disconnected,
        "Rozłączono.");
}

/**
 * Przyjmuje jedno zlecenie synchronizacji historii dla całego rejestru.
 */
bool IBGatewayClient::requestHistorySync(std::string& errorMessage) {
    errorMessage.clear();
    if (status().state != IBGatewayConnectionState::Connected) {
        errorMessage = "Połącz się z IB Gateway przed synchronizacją historii.";
        return false;
    }

    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        if (historySyncInProgress_) {
            errorMessage = "Synchronizacja historii IBKR już trwa.";
            return false;
        }
        historySyncRequested_ = true;
        historySyncInProgress_ = true;
        historySyncRequestId_ = 0;
        historySyncInstrumentIndex_ = 0;
        historySyncSavedCandleCount_ = 0;
        historySyncSkippedInstrumentCount_ = 0;
        historySyncSymbol_.clear();
        historySyncCandles_.clear();
        candle_.message = "Przygotowywanie synchronizacji historii IBKR...";
    }
    readerSignal_.issueSignal();
    notifyStatusChanged();
    return true;
}

/**
 * Odczytuje stan synchronizacji pod tą samą blokadą co jej dane.
 */
bool IBGatewayClient::historySyncInProgress() const {
    const std::lock_guard<std::mutex> lock(statusMutex_);
    return historySyncInProgress_;
}

/**
 * Zwraca kopię statusu chronioną blokadą.
 */
IBGatewayConnectionStatus IBGatewayClient::status() const {
    const std::lock_guard<std::mutex> lock(statusMutex_);
    return status_;
}

/**
 * Zwraca kopię świecy chronioną tą samą blokadą co status połączenia.
 */
IBGatewayOneMinuteCandle IBGatewayClient::candleSnapshot() const {
    const std::lock_guard<std::mutex> lock(statusMutex_);
    return candle_;
}

/**
 * Zwraca kopię kwotowań chronioną blokadą.
 */
IBGatewayQuoteSnapshot IBGatewayClient::quoteSnapshot() const {
    const std::lock_guard<std::mutex> lock(statusMutex_);
    return quote_;
}

/**
 * Podmienia funkcję powiadamiającą o zmianie statusu.
 */
void IBGatewayClient::setStatusChangedCallback(
    StatusChangedCallback callback) {
    const std::lock_guard<std::mutex> lock(statusMutex_);
    statusChangedCallback_ = std::move(callback);
}

/**
 * Oznacza połączenie jako gotowe po zakończeniu uzgadniania protokołu.
 */
void IBGatewayClient::nextValidId(int orderId) {
    static_cast<void>(orderId);
    if (!stopRequested_.load() &&
        status().state == IBGatewayConnectionState::Connecting) {
        updateStatus(
            IBGatewayConnectionState::Connected,
            "Połączono z IB Gateway.");
    }
}

/**
 * Rozdziela błędy inicjalizacji od informacyjnych komunikatów Gateway.
 */
void IBGatewayClient::error(
    int id,
    long long errorTimeMs,
    int errorCode,
    const std::string& errorString,
    const std::string& advancedOrderRejectJson) {
    static_cast<void>(errorTimeMs);
    static_cast<void>(advancedOrderRejectJson);

    if (isInformationalError(errorCode)) {
        return;
    }

    const std::string message = makeApiErrorMessage(errorCode, errorString);
    const IBGatewayConnectionStatus currentStatus = status();
    if (currentStatus.state == IBGatewayConnectionState::Connecting ||
        currentStatus.state == IBGatewayConnectionState::Error) {
        updateStatus(IBGatewayConnectionState::Error, message);
    } else if (currentStatus.state == IBGatewayConnectionState::Connected) {
        const IBKRInstrumentDefinition* instrument =
            findInstrumentByRequestId(id);
        const IBKRInstrumentDefinition* quoteInstrument =
            findInstrumentByQuoteRequestId(id);
        if (id == eurUsdHistoricalRequestId) {
            ErrorLogger::warning(message);
            updateCandleMessage(message);
        } else if (quoteInstrument != nullptr) {
            const std::string symbol = quoteInstrument->databaseSymbol;
            ErrorLogger::warning(
                "Błąd kwotowań IBKR dla " + symbol + ": " + message);
            if (id == eurUsdQuoteRequestId) {
                updateQuoteMessage(message);
            }
        } else if (instrument != nullptr) {
            ErrorLogger::warning(
                "Błąd strumienia IBKR dla " +
                std::string(instrument->databaseSymbol) + ": " + message);
        } else {
            bool historyRequestFailed = false;
            {
                const std::lock_guard<std::mutex> lock(statusMutex_);
                historyRequestFailed =
                    historySyncInProgress_ &&
                    id == historySyncRequestId_;
            }
            if (historyRequestFailed) {
                skipCurrentHistoryInstrument(message);
            } else {
                ErrorLogger::warning(message);
            }
        }
    }
}

/**
 * Rozróżnia rozłączenie zainicjowane przez aplikację od utraty połączenia.
 */
void IBGatewayClient::connectionClosed() {
    const bool requestedByApplication = stopRequested_.load();
    const bool historyWasInProgress = historySyncInProgress();
    if (historyWasInProgress) {
        failHistorySync("Połączenie z IB Gateway zostało przerwane.");
    }
    const IBGatewayConnectionStatus currentStatus = status();
    if (currentStatus.state == IBGatewayConnectionState::Error) {
        return;
    }
    if (!requestedByApplication &&
        currentStatus.state == IBGatewayConnectionState::Connecting) {
        updateStatus(
            IBGatewayConnectionState::Error,
            "IB Gateway zamknął połączenie przed zakończeniem "
            "uzgadniania API.");
        return;
    }
    updateStatus(
        IBGatewayConnectionState::Disconnected,
        requestedByApplication
            ? "Rozłączono."
            : "Połączenie z IB Gateway zostało przerwane.");
    if (!historyWasInProgress) {
        updateCandleMessage(
            requestedByApplication
                ? "Rozłączono strumień świec EUR/USD."
                : "Utracono połączenie; ostatnia odebrana świeca pozostaje widoczna.");
    }
    updateQuoteMessage(
        requestedByApplication
            ? "Rozłączono kwotowania EUR/USD."
            : "Utracono połączenie z kwotowaniami EUR/USD.");
}

/**
 * Zachowuje nową cenę bid albo ask i odświeża widok.
 */
void IBGatewayClient::tickPrice(
    int requestId,
    TickType tickType,
    double price,
    const TickAttrib& attrib) {
    static_cast<void>(attrib);
    const IBKRInstrumentDefinition* instrument =
        findInstrumentByQuoteRequestId(requestId);
    const bool isBid = tickType == BID || tickType == DELAYED_BID;
    const bool isAsk = tickType == ASK || tickType == DELAYED_ASK;
    if (instrument == nullptr ||
        !std::isfinite(price) || price <= 0.0 || (!isBid && !isAsk)) {
        return;
    }

    bool shouldNotify = false;
    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        IBGatewayQuoteSnapshot& quote =
            quotesBySymbol_[instrument->databaseSymbol];
        quote.delayed =
            tickType == DELAYED_BID || tickType == DELAYED_ASK;
        if (isBid) {
            quote.bid = price;
            quote.bidAvailable = true;
        } else {
            quote.ask = price;
            quote.askAvailable = true;
        }
        const std::string symbol = instrument->databaseSymbol;
        quote.message = quote.delayed
            ? "Uwaga: kwotowania " + symbol + " są opóźnione."
            : "Bieżące kwotowania " + symbol + " z IBKR.";
        if (requestId == eurUsdQuoteRequestId) {
            quote_ = quote;
            shouldNotify = true;
        }
    }
    if (shouldNotify) {
        notifyStatusChanged();
    }
}

/**
 * Odbiera świece z początkowej historii lub bieżącej synchronizacji.
 */
void IBGatewayClient::historicalData(int requestId, const Bar& bar) {
    const IBKRInstrumentDefinition* liveInstrument =
        findInstrumentByRequestId(requestId);
    if (liveInstrument != nullptr) {
        if (requestId != eurUsdHistoricalRequestId) {
            return;
        }
        std::int64_t timestampUtc = 0;
        if (!parseIbkrTimestamp(bar.time, timestampUtc)) {
            ErrorLogger::warning(
                "IBKR zwróciło nieprawidłowy znacznik czasu "
                "świecy historycznej.");
            updateCandleMessage(
                "IBKR zwróciło nieprawidłowy czas świecy historycznej.");
            return;
        }

        const std::lock_guard<std::mutex> lock(statusMutex_);
        candle_.available = true;
        candle_.timestampUtc = timestampUtc;
        if (!formatLocalTimestamp(timestampUtc, candle_.timestamp)) {
            candle_.timestamp = "Nie można przeliczyć czasu świecy.";
        }
        candle_.open = bar.open;
        candle_.high = bar.high;
        candle_.low = bar.low;
        candle_.close = bar.close;
        return;
    }

    std::string symbol;
    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        if (!historySyncInProgress_ ||
            requestId != historySyncRequestId_) {
            return;
        }
        symbol = historySyncSymbol_;
    }

    std::int64_t timestampUtc = 0;
    if (!parseIbkrTimestamp(bar.time, timestampUtc)) {
        ErrorLogger::warning(
            "Pominięto świecę synchronizacji IBKR z nieprawidłowym czasem.");
        return;
    }
    const auto currentMinute = std::chrono::floor<std::chrono::minutes>(
        std::chrono::system_clock::now());
    const auto currentMinuteUtc = std::chrono::duration_cast<
        std::chrono::seconds>(currentMinute.time_since_epoch()).count();
    if (timestampUtc >= currentMinuteUtc) {
        return;
    }
    if (!std::isfinite(bar.open) || !std::isfinite(bar.high) ||
        !std::isfinite(bar.low) || !std::isfinite(bar.close) ||
        bar.high < bar.open || bar.high < bar.close ||
        bar.low > bar.open || bar.low > bar.close ||
        bar.low > bar.high) {
        ErrorLogger::warning(
            "Pominięto świecę synchronizacji IBKR z nieprawidłowym OHLC.");
        return;
    }

    IBKRCandleData candle;
    candle.symbol = std::move(symbol);
    candle.timestampUtc = timestampUtc;
    candle.open = bar.open;
    candle.high = bar.high;
    candle.low = bar.low;
    candle.close = bar.close;
    candle.volume = formatIbkrVolume(bar);
    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        if (historySyncInProgress_ &&
            requestId == historySyncRequestId_) {
            historySyncCandles_.push_back(std::move(candle));
        }
    }
}

/**
 * Pokazuje aktywną świecę i zapisuje poprzednią po rozpoczęciu kolejnej minuty.
 */
void IBGatewayClient::historicalDataUpdate(int requestId, const Bar& bar) {
    const IBKRInstrumentDefinition* instrument =
        findInstrumentByRequestId(requestId);
    if (instrument == nullptr) {
        return;
    }
    const std::string symbol = instrument->databaseSymbol;

    std::int64_t timestampUtc = 0;
    if (!parseIbkrTimestamp(bar.time, timestampUtc)) {
        ErrorLogger::warning(
            "IBKR zwróciło nieprawidłowy znacznik czasu aktualnej świecy.");
        updateCandleMessage(
            "IBKR zwróciło nieprawidłowy czas aktualnej świecy.");
        return;
    }

    IBKRCandleData updatedCandle;
    updatedCandle.symbol = symbol;
    updatedCandle.timestampUtc = timestampUtc;
    updatedCandle.open = bar.open;
    updatedCandle.high = bar.high;
    updatedCandle.low = bar.low;
    updatedCandle.close = bar.close;
    updatedCandle.volume = formatIbkrVolume(bar);

    const bool isDisplayedInstrument =
        requestId == eurUsdHistoricalRequestId;

    const auto currentMinute = std::chrono::floor<std::chrono::minutes>(
        std::chrono::system_clock::now());
    const auto currentMinuteUtc = std::chrono::duration_cast<
        std::chrono::seconds>(currentMinute.time_since_epoch()).count();

    bool isOutOfOrderUpdate = false;
    bool shouldUpdateDisplay = false;
    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        const auto quote = quotesBySymbol_.find(symbol);
        if (quote != quotesBySymbol_.end()) {
            updatedCandle.bidAvailable = quote->second.bidAvailable;
            updatedCandle.bid = quote->second.bid;
            updatedCandle.askAvailable = quote->second.askAvailable;
            updatedCandle.ask = quote->second.ask;
        }
        const auto lastCompleted =
            lastCompletedCandleTimestampBySymbol_.find(symbol);
        const auto pending =
            pendingCandlesBySymbol_.find(symbol);
        if (lastCompleted != lastCompletedCandleTimestampBySymbol_.end() &&
            timestampUtc <= lastCompleted->second) {
            isOutOfOrderUpdate = true;
        } else if (pending != pendingCandlesBySymbol_.end() &&
                   timestampUtc < pending->second.timestampUtc) {
            isOutOfOrderUpdate = true;
        } else {
            if (pending != pendingCandlesBySymbol_.end() &&
                timestampUtc > pending->second.timestampUtc) {
                const std::int64_t completedTimestamp =
                    pending->second.timestampUtc;
                completedCandlesPendingSave_.push_back(
                    std::move(pending->second));
                lastCompletedCandleTimestampBySymbol_[symbol] =
                    completedTimestamp;
            }

            pendingCandlesBySymbol_[symbol] = std::move(updatedCandle);
            if (timestampUtc < currentMinuteUtc) {
                const auto closed = pendingCandlesBySymbol_.find(symbol);
                if (closed != pendingCandlesBySymbol_.end()) {
                    completedCandlesPendingSave_.push_back(
                        std::move(closed->second));
                    lastCompletedCandleTimestampBySymbol_[symbol] =
                        timestampUtc;
                    pendingCandlesBySymbol_.erase(closed);
                }
            }
            if (isDisplayedInstrument) {
                candle_.available = true;
                candle_.timestampUtc = timestampUtc;
                if (!formatLocalTimestamp(timestampUtc, candle_.timestamp)) {
                    candle_.timestamp = "Nie można przeliczyć czasu świecy.";
                }
                candle_.open = bar.open;
                candle_.high = bar.high;
                candle_.low = bar.low;
                candle_.close = bar.close;
                candle_.message =
                    "Aktualizacja świecy EUR/USD 1-minutowej z IBKR.";
                shouldUpdateDisplay = true;
            }
        }
    }

    if (isOutOfOrderUpdate) {
        ErrorLogger::warning(
            "IBKR zwróciło aktualizację świecy starszą niż bieżąca; "
            "pominięto ją.");
    } else if (shouldUpdateDisplay) {
        notifyStatusChanged();
    }
    persistNextCompletedCandle();
}

/**
 * Przenosi świece starsze od bieżącej minuty do kolejki zapisu.
 */
void IBGatewayClient::flushClosedPendingCandles() {
    const auto currentMinute = std::chrono::floor<std::chrono::minutes>(
        std::chrono::system_clock::now());
    const auto currentMinuteUtc = std::chrono::duration_cast<
        std::chrono::seconds>(currentMinute.time_since_epoch()).count();

    const std::lock_guard<std::mutex> lock(statusMutex_);
    for (auto pending = pendingCandlesBySymbol_.begin();
         pending != pendingCandlesBySymbol_.end();) {
        const std::int64_t timestampUtc = pending->second.timestampUtc;
        if (timestampUtc >= currentMinuteUtc) {
            ++pending;
            continue;
        }

        const auto lastCompleted =
            lastCompletedCandleTimestampBySymbol_.find(pending->first);
        if (lastCompleted == lastCompletedCandleTimestampBySymbol_.end() ||
            timestampUtc > lastCompleted->second) {
            completedCandlesPendingSave_.push_back(
                std::move(pending->second));
            lastCompletedCandleTimestampBySymbol_[pending->first] =
                timestampUtc;
        }
        pending = pendingCandlesBySymbol_.erase(pending);
    }
}

/**
 * Kończy początkowy odczyt lub zapis historii synchronizacyjnej.
 */
void IBGatewayClient::historicalDataEnd(
    int requestId,
    const std::string& startDateStr,
    const std::string& endDateStr) {
    static_cast<void>(startDateStr);
    static_cast<void>(endDateStr);

    std::vector<IBKRCandleData> synchronizedCandles;
    std::size_t instrumentIndex = 0;
    bool isHistorySyncRequest = false;
    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        if (historySyncInProgress_ &&
            requestId == historySyncRequestId_) {
            isHistorySyncRequest = true;
            instrumentIndex = historySyncInstrumentIndex_;
            historySyncRequestId_ = 0;
            historySyncSymbol_.clear();
            synchronizedCandles.swap(historySyncCandles_);
        }
    }

    if (isHistorySyncRequest) {
        std::sort(
            synchronizedCandles.begin(),
            synchronizedCandles.end(),
            [](const IBKRCandleData& first, const IBKRCandleData& second) {
                return first.timestampUtc < second.timestampUtc;
            });
        synchronizedCandles.erase(
            std::unique(
                synchronizedCandles.begin(),
                synchronizedCandles.end(),
                [](const IBKRCandleData& first, const IBKRCandleData& second) {
                    return first.symbol == second.symbol &&
                           first.timestampUtc == second.timestampUtc;
                }),
            synchronizedCandles.end());
        if (synchronizedCandles.size() > maximumHistoryCandlesPerInstrument) {
            synchronizedCandles.erase(
                synchronizedCandles.begin(),
                synchronizedCandles.end() -
                    maximumHistoryCandlesPerInstrument);
        }

        std::size_t savedCandleCount = 0;
        std::string saveError;
        if (!synchronizedCandles.empty() &&
            !CandleHistoryClient::saveIbkrCandles(
                synchronizedCandles, savedCandleCount, saveError)) {
            failHistorySync(saveError);
            return;
        }

        bool moreInstrumentsRemain = false;
        {
            const std::lock_guard<std::mutex> lock(statusMutex_);
            if (!historySyncInProgress_) {
                return;
            }
            historySyncSavedCandleCount_ += savedCandleCount;
            ++historySyncInstrumentIndex_;
            moreInstrumentsRemain =
                historySyncInstrumentIndex_ < ibkrInstrumentRegistry.size();
            if (moreInstrumentsRemain) {
                historySyncRequested_ = true;
                candle_.message =
                    "Zapisano historię " +
                    std::string(
                        ibkrInstrumentRegistry[instrumentIndex].databaseSymbol) +
                    "; pobieranie kolejnego instrumentu.";
            } else {
                historySyncInProgress_ = false;
                historySyncRequested_ = false;
                candle_.message =
                    "Synchronizacja historii IBKR zakończona. Zapisano " +
                    std::to_string(historySyncSavedCandleCount_) +
                    " świec.";
                if (historySyncSkippedInstrumentCount_ > 0) {
                    candle_.message +=
                        " Pominięto instrumentów: " +
                        std::to_string(historySyncSkippedInstrumentCount_) +
                        ".";
                }
            }
        }
        notifyStatusChanged();
        return;
    }

    if (requestId != eurUsdHistoricalRequestId) {
        return;
    }

    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        candle_.message = candle_.available
            ? "Ostatnia świeca EUR/USD 1-minutowa z IBKR."
            : "IBKR nie zwróciło świecy EUR/USD dla ostatnich 30 minut.";
    }
    notifyStatusChanged();
}

/**
 * Łączy socket, odbiera komunikaty API i zawsze zamyka zasoby po zakończeniu.
 */
void IBGatewayClient::runConnection(
    std::string host, int port, int clientId) {
    std::unique_ptr<EClientSocket> client;
    bool disconnected = false;
    std::array<bool, ibkrInstrumentRegistry.size()> historicalRequestsStarted{};
    std::array<bool, ibkrInstrumentRegistry.size()> quoteRequestsStarted{};
    try {
        client = std::make_unique<EClientSocket>(this, &readerSignal_);
        client->asyncEConnect(true);
        if (!client->eConnect(host.c_str(), port, clientId)) {
            if (!stopRequested_.load() &&
                status().state == IBGatewayConnectionState::Connecting) {
                updateStatus(
                    IBGatewayConnectionState::Error,
                    "Nie udało się otworzyć połączenia. Sprawdź, czy "
                    "IB Gateway działa, API socket jest włączony i port "
                    "jest poprawny.");
            }
        } else {
            EReader reader(client.get(), &readerSignal_);
            reader.start();
            const auto handshakeDeadline =
                std::chrono::steady_clock::now() +
                connectionHandshakeTimeout;
            bool startApiSent = false;

            while (!stopRequested_.load()) {
                const IBGatewayConnectionStatus currentStatus = status();
                if (currentStatus.state == IBGatewayConnectionState::Error ||
                    currentStatus.state ==
                        IBGatewayConnectionState::Disconnecting) {
                    break;
                }
                if (!client->isConnected()) {
                    if (currentStatus.state ==
                        IBGatewayConnectionState::Connecting) {
                        updateStatus(
                            IBGatewayConnectionState::Error,
                            "IB Gateway zamknął połączenie przed "
                            "zakończeniem logowania API.");
                    } else if (currentStatus.state ==
                               IBGatewayConnectionState::Connected) {
                        updateStatus(
                            IBGatewayConnectionState::Disconnected,
                            "Połączenie z IB Gateway zostało przerwane.");
                    }
                    break;
                }
                if (!startApiSent && client->EClient::serverVersion() > 0) {
                    client->startApi();
                    startApiSent = true;
                }
                if (currentStatus.state ==
                    IBGatewayConnectionState::Connected) {
                    for (std::size_t index = 0;
                         index < ibkrInstrumentRegistry.size();
                         ++index) {
                        if (historicalRequestsStarted[index]) {
                            continue;
                        }
                        const IBKRInstrumentDefinition& instrument =
                            ibkrInstrumentRegistry[index];
                        Contract contract;
                        configureContract(contract, instrument);
                        if (instrument.historicalRequestId ==
                            eurUsdHistoricalRequestId) {
                            updateCandleMessage(
                                "Pobieranie świec EUR/USD "
                                "1-minutowych z IBKR...");
                        }
                        client->reqHistoricalData(
                            instrument.historicalRequestId,
                            contract,
                            "",
                            "1800 S",
                            "1 min",
                            instrument.whatToShow,
                            instrument.useRegularTradingHours ? 1 : 0,
                            2,
                            true,
                            TagValueListSPtr());
                        historicalRequestsStarted[index] = true;
                    }
                }
                if (currentStatus.state ==
                    IBGatewayConnectionState::Connected) {
                    for (std::size_t index = 0;
                         index < ibkrInstrumentRegistry.size();
                         ++index) {
                        const IBKRInstrumentDefinition& instrument =
                            ibkrInstrumentRegistry[index];
                        if (instrument.quoteRequestId == 0 ||
                            quoteRequestsStarted[index]) {
                            continue;
                        }

                        Contract contract;
                        configureContract(contract, instrument);
                        if (instrument.quoteRequestId ==
                            eurUsdQuoteRequestId) {
                            updateQuoteMessage(
                                "Oczekiwanie na kwotowania bid/ask z IBKR...");
                        }
                        client->reqMktData(
                            instrument.quoteRequestId,
                            contract,
                            "",
                            false,
                            false,
                            TagValueListSPtr());
                        quoteRequestsStarted[index] = true;
                    }
                }
                int historyRequestId = 0;
                std::size_t historyInstrumentIndex = 0;
                {
                    const std::lock_guard<std::mutex> lock(statusMutex_);
                    if (historySyncRequested_ &&
                        historySyncInProgress_ &&
                        historySyncInstrumentIndex_ <
                            ibkrInstrumentRegistry.size() &&
                        currentStatus.state ==
                            IBGatewayConnectionState::Connected) {
                        historySyncRequested_ = false;
                        historyInstrumentIndex =
                            historySyncInstrumentIndex_;
                        historyRequestId = nextHistorySyncRequestId_++;
                        historySyncRequestId_ = historyRequestId;
                        historySyncSymbol_ =
                            ibkrInstrumentRegistry[
                                historyInstrumentIndex].databaseSymbol;
                        historySyncCandles_.clear();
                    }
                }
                if (historyRequestId != 0) {
                    const IBKRInstrumentDefinition& instrument =
                        ibkrInstrumentRegistry[historyInstrumentIndex];
                    Contract contract;
                    configureContract(contract, instrument);
                    updateCandleMessage(
                        "Pobieranie 1000 ostatnich zamkniętych świec " +
                        std::string(instrument.databaseSymbol) + " z IBKR...");
                    client->reqHistoricalData(
                        historyRequestId,
                        contract,
                        "",
                        "1 W",
                        "1 min",
                        instrument.whatToShow,
                        instrument.useRegularTradingHours ? 1 : 0,
                        2,
                        false,
                        TagValueListSPtr());
                }
                if (currentStatus.state ==
                    IBGatewayConnectionState::Connected) {
                    flushClosedPendingCandles();
                    persistNextCompletedCandle();
                }
                if (currentStatus.state ==
                        IBGatewayConnectionState::Connecting &&
                    std::chrono::steady_clock::now() >= handshakeDeadline) {
                    updateStatus(
                        IBGatewayConnectionState::Error,
                        "IB Gateway nie zakończył uzgadniania API w ciągu "
                        "15 sekund.");
                    break;
                }

                readerSignal_.waitForSignal();
                if (!stopRequested_.load() && client->isConnected()) {
                    reader.processMsgs();
                }
            }

            flushClosedPendingCandles();
            persistNextCompletedCandle();
            for (std::size_t index = 0;
                 index < historicalRequestsStarted.size();
                 ++index) {
                if (historicalRequestsStarted[index] &&
                    client->isConnected()) {
                    client->cancelHistoricalData(
                        ibkrInstrumentRegistry[index].historicalRequestId);
                }
            }
            int activeHistoryRequestId = 0;
            {
                const std::lock_guard<std::mutex> lock(statusMutex_);
                activeHistoryRequestId = historySyncRequestId_;
            }
            if (activeHistoryRequestId != 0 && client->isConnected()) {
                client->cancelHistoricalData(activeHistoryRequestId);
            }
            for (std::size_t index = 0;
                 index < quoteRequestsStarted.size();
                 ++index) {
                if (quoteRequestsStarted[index] && client->isConnected()) {
                    client->cancelMktData(
                        ibkrInstrumentRegistry[index].quoteRequestId);
                }
            }
            client->eDisconnect();
            disconnected = true;
        }
    } catch (const std::exception& exception) {
        const std::string message =
            "Błąd podczas obsługi połączenia IBKR: " +
            std::string(exception.what());
        updateStatus(IBGatewayConnectionState::Error, message);
    } catch (...) {
        const std::string message =
            "Nieznany błąd podczas obsługi połączenia IBKR.";
        updateStatus(IBGatewayConnectionState::Error, message);
    }

    if (client != nullptr && !disconnected) {
        for (std::size_t index = 0;
             index < historicalRequestsStarted.size();
             ++index) {
            if (historicalRequestsStarted[index] &&
                client->isConnected()) {
                client->cancelHistoricalData(
                    ibkrInstrumentRegistry[index].historicalRequestId);
            }
        }
        int activeHistoryRequestId = 0;
        {
            const std::lock_guard<std::mutex> lock(statusMutex_);
            activeHistoryRequestId = historySyncRequestId_;
        }
        if (activeHistoryRequestId != 0 && client->isConnected()) {
            client->cancelHistoricalData(activeHistoryRequestId);
        }
        for (std::size_t index = 0;
             index < quoteRequestsStarted.size();
             ++index) {
            if (quoteRequestsStarted[index] && client->isConnected()) {
                client->cancelMktData(
                    ibkrInstrumentRegistry[index].quoteRequestId);
            }
        }
        client->eDisconnect();
    }
    if (historySyncInProgress()) {
        failHistorySync(
            "Połączenie z IB Gateway zakończyło się przed zapisem historii.");
    }
    const IBGatewayConnectionStatus finalStatus = status();
    if (finalStatus.state == IBGatewayConnectionState::Disconnecting) {
        updateStatus(
            IBGatewayConnectionState::Disconnected,
            "Rozłączono.");
    } else if (finalStatus.state == IBGatewayConnectionState::Connected) {
        updateStatus(
            IBGatewayConnectionState::Disconnected,
            "Połączenie z IB Gateway zostało przerwane.");
    }
}

/**
 * Zmienia status pod blokadą, a obserwatora wywołuje już poza nią.
 */
void IBGatewayClient::updateStatus(
    IBGatewayConnectionState state, std::string message) {
    StatusChangedCallback callback;
    const std::string logMessage =
        state == IBGatewayConnectionState::Error ? message : std::string{};
    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        if (status_.state == state && status_.message == message) {
            return;
        }
        status_ = {state, std::move(message)};
        callback = statusChangedCallback_;
    }
    if (state == IBGatewayConnectionState::Error) {
        ErrorLogger::error(logMessage);
    }
    if (callback) {
        callback();
    }
}

/**
 * Ustawia komunikat świecy i odświeża widok bez blokowania go podczas API.
 */
void IBGatewayClient::updateCandleMessage(std::string message) {
    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        candle_.message = std::move(message);
    }
    notifyStatusChanged();
}

/**
 * Kończy bieżącą synchronizację i przekazuje błąd do logu oraz widoku.
 */
void IBGatewayClient::failHistorySync(std::string message) {
    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        if (!historySyncInProgress_) {
            return;
        }
        historySyncRequested_ = false;
        historySyncInProgress_ = false;
        historySyncRequestId_ = 0;
        historySyncSymbol_.clear();
        historySyncCandles_.clear();
        candle_.message = message;
    }
    ErrorLogger::warning("Synchronizacja historii IBKR nie powiodła się: " +
                         message);
    notifyStatusChanged();
}

/**
 * Pomija niedostępny instrument i kontynuuje synchronizację pozostałych.
 * @param message Komunikat błędu zwrócony przez IBKR.
 */
void IBGatewayClient::skipCurrentHistoryInstrument(std::string message) {
    std::string symbol;
    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        if (!historySyncInProgress_) {
            return;
        }

        symbol = historySyncSymbol_;
        ++historySyncSkippedInstrumentCount_;
        ++historySyncInstrumentIndex_;
        historySyncRequestId_ = 0;
        historySyncSymbol_.clear();
        historySyncCandles_.clear();

        if (historySyncInstrumentIndex_ < ibkrInstrumentRegistry.size()) {
            historySyncRequested_ = true;
            candle_.message =
                "Pominięto " + symbol + ": " + message +
                "; pobieranie kolejnego instrumentu.";
        } else {
            historySyncInProgress_ = false;
            historySyncRequested_ = false;
            candle_.message =
                "Pominięto " + symbol +
                "; synchronizacja historii zakończona. Zapisano " +
                std::to_string(historySyncSavedCandleCount_) +
                " świec; pominięto instrumentów łącznie: " +
                std::to_string(historySyncSkippedInstrumentCount_) + ".";
        }
    }

    ErrorLogger::warning(
        "Pominięto historię IBKR dla " + symbol + ": " + message);
    notifyStatusChanged();
}

/**
 * Zapisuje najstarszą zamkniętą świecę, zostawiając ją w kolejce przy błędzie.
 */
void IBGatewayClient::persistNextCompletedCandle() {
    IBKRCandleData candleToSave;
    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        if (completedCandlesPendingSave_.empty() ||
            completedCandleSaveInProgress_ ||
            std::chrono::steady_clock::now() < nextCandleSaveRetry_) {
            return;
        }
        candleToSave = completedCandlesPendingSave_.front();
        completedCandleSaveInProgress_ = true;
    }

    bool candleSaved = false;
    std::string saveError;
    if (!CandleHistoryClient::saveIbkrCandle(
            candleToSave, candleSaved, saveError)) {
        {
            const std::lock_guard<std::mutex> lock(statusMutex_);
            completedCandleSaveInProgress_ = false;
            nextCandleSaveRetry_ =
                std::chrono::steady_clock::now() + candleSaveRetryDelay;
        }
        ErrorLogger::warning(
            "Nie udało się zapisać zamkniętej świecy IBKR dla " +
            candleToSave.symbol +
            " w candles_IBKR: " + saveError);
        if (candleToSave.symbol == "EUR/USD") {
            updateCandleMessage(
                "Nie udało się zapisać zamkniętej świecy IBKR "
                "w candles_IBKR: " + saveError);
        }
        return;
    }

    if (!candleSaved) {
        ErrorLogger::warning(
            "Pominięto zamkniętą świecę IBKR dla " +
            candleToSave.symbol + ", ponieważ rynek był zamknięty.");
    }
    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        if (!completedCandlesPendingSave_.empty() &&
            completedCandlesPendingSave_.front().symbol ==
                candleToSave.symbol &&
            completedCandlesPendingSave_.front().timestampUtc ==
                candleToSave.timestampUtc) {
            completedCandlesPendingSave_.pop_front();
        }
        completedCandleSaveInProgress_ = false;
        nextCandleSaveRetry_ = {};
    }
}

/**
 * Ustawia status bid/ask i odświeża widok poza blokadą.
 */
void IBGatewayClient::updateQuoteMessage(std::string message) {
    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        quote_.message = std::move(message);
    }
    notifyStatusChanged();
}

/**
 * Kopiuje funkcję widoku pod blokadą i wywołuje ją już poza blokadą.
 */
void IBGatewayClient::notifyStatusChanged() {
    StatusChangedCallback callback;
    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        callback = statusChangedCallback_;
    }
    if (callback) {
        callback();
    }
}

/**
 * Rozpoznaje komunikaty o stanie farm danych, które nie oznaczają błędu API.
 */
bool IBGatewayClient::isInformationalError(int errorCode) {
    return errorCode >= 2100 && errorCode <= 2199;
}
