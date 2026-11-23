#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "IBGatewayClient.h"

#include "CandleHistoryClient.h"
#include "Decimal.h"
#include "EClientSocket.h"
#include "EReader.h"
#include "ErrorLogger.h"

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
constexpr int oneMinuteCandleRequestId = 9251;
constexpr int eurUsdQuoteRequestId = 9252;

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
        pendingCandle_.reset();
        quote_ = {};
        quote_.message = "Oczekiwanie na kwotowania bid/ask z IBKR.";
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
    static_cast<void>(id);
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
        ErrorLogger::warning(message);
        if (id == oneMinuteCandleRequestId) {
            updateCandleMessage(message);
        } else if (id == eurUsdQuoteRequestId) {
            updateQuoteMessage(message);
        }
    }
}

/**
 * Rozróżnia rozłączenie zainicjowane przez aplikację od utraty połączenia.
 */
void IBGatewayClient::connectionClosed() {
    const bool requestedByApplication = stopRequested_.load();
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
    updateCandleMessage(
        requestedByApplication
            ? "Rozłączono strumień świec EUR/USD."
            : "Utracono połączenie; ostatnia odebrana świeca pozostaje widoczna.");
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
    const bool isBid = tickType == BID || tickType == DELAYED_BID;
    const bool isAsk = tickType == ASK || tickType == DELAYED_ASK;
    if (requestId != eurUsdQuoteRequestId ||
        !std::isfinite(price) || price <= 0.0 || (!isBid && !isAsk)) {
        return;
    }

    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        quote_.delayed =
            tickType == DELAYED_BID || tickType == DELAYED_ASK;
        if (isBid) {
            quote_.bid = price;
            quote_.bidAvailable = true;
        } else {
            quote_.ask = price;
            quote_.askAvailable = true;
        }
        quote_.message = quote_.delayed
            ? "Uwaga: kwotowania EUR/USD są opóźnione."
            : "Bieżące kwotowania EUR/USD z IBKR.";
    }
    notifyStatusChanged();
}

/**
 * Zachowuje ostatni bar z początkowej odpowiedzi historii bez odświeżania UI.
 */
void IBGatewayClient::historicalData(int requestId, const Bar& bar) {
    if (requestId != oneMinuteCandleRequestId) {
        return;
    }

    std::int64_t timestampUtc = 0;
    if (!parseIbkrTimestamp(bar.time, timestampUtc)) {
        ErrorLogger::warning(
            "IBKR zwróciło nieprawidłowy znacznik czasu świecy historycznej.");
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
}

/**
 * Pokazuje aktywną świecę i zapisuje poprzednią po rozpoczęciu kolejnej minuty.
 */
void IBGatewayClient::historicalDataUpdate(int requestId, const Bar& bar) {
    if (requestId != oneMinuteCandleRequestId) {
        return;
    }

    std::int64_t timestampUtc = 0;
    if (!parseIbkrTimestamp(bar.time, timestampUtc)) {
        ErrorLogger::warning(
            "IBKR zwróciło nieprawidłowy znacznik czasu aktualnej świecy.");
        updateCandleMessage(
            "IBKR zwróciło nieprawidłowy czas aktualnej świecy.");
        return;
    }

    IBKRCandleData updatedCandle;
    updatedCandle.timestampUtc = timestampUtc;
    updatedCandle.open = bar.open;
    updatedCandle.high = bar.high;
    updatedCandle.low = bar.low;
    updatedCandle.close = bar.close;
    updatedCandle.volume = DecimalFunctions::decimalToString(bar.volume);
    if (!updatedCandle.volume.empty() &&
        updatedCandle.volume.front() == '-') {
        // IBKR używa ujemnego wolumenu jako znacznika braku danych.
        updatedCandle.volume.clear();
    }

    const IBGatewayQuoteSnapshot quote = quoteSnapshot();
    updatedCandle.bidAvailable = quote.bidAvailable;
    updatedCandle.bid = quote.bid;
    updatedCandle.askAvailable = quote.askAvailable;
    updatedCandle.ask = quote.ask;

    bool isOutOfOrderUpdate = false;
    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        if (pendingCandle_.has_value() &&
            timestampUtc < pendingCandle_->timestampUtc) {
            isOutOfOrderUpdate = true;
        } else {
            if (pendingCandle_.has_value() &&
                timestampUtc > pendingCandle_->timestampUtc) {
                completedCandlesPendingSave_.push_back(
                    std::move(*pendingCandle_));
            }
            pendingCandle_ = std::move(updatedCandle);

            candle_.available = true;
            candle_.timestampUtc = timestampUtc;
            if (!formatLocalTimestamp(timestampUtc, candle_.timestamp)) {
                candle_.timestamp = "Nie można przeliczyć czasu świecy.";
            }
            candle_.open = bar.open;
            candle_.high = bar.high;
            candle_.low = bar.low;
            candle_.close = bar.close;
            candle_.message = "Aktualizacja świecy EUR/USD 1-minutowej z IBKR.";
        }
    }

    if (isOutOfOrderUpdate) {
        ErrorLogger::warning(
            "IBKR zwróciło aktualizację świecy starszą niż bieżąca; "
            "pominięto ją.");
    } else {
        notifyStatusChanged();
    }
    persistNextCompletedCandle();
}

/**
 * Kończy początkowy odczyt i informuje, jeśli IBKR nie zwróciło żadnej świecy.
 */
void IBGatewayClient::historicalDataEnd(
    int requestId,
    const std::string& startDateStr,
    const std::string& endDateStr) {
    static_cast<void>(startDateStr);
    static_cast<void>(endDateStr);
    if (requestId != oneMinuteCandleRequestId) {
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
    bool historicalRequestStarted = false;
    bool marketDataRequestStarted = false;
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
                if (!historicalRequestStarted &&
                    currentStatus.state ==
                        IBGatewayConnectionState::Connected) {
                    Contract contract;
                    contract.symbol = "EUR";
                    contract.secType = "CASH";
                    contract.exchange = "IDEALPRO";
                    contract.currency = "USD";
                    updateCandleMessage(
                        "Pobieranie świec EUR/USD 1-minutowych z IBKR...");
                    client->reqHistoricalData(
                        oneMinuteCandleRequestId,
                        contract,
                        "",
                        "1800 S",
                        "1 min",
                        "MIDPOINT",
                        0,
                        2,
                        true,
                        TagValueListSPtr());
                    historicalRequestStarted = true;
                }
                if (!marketDataRequestStarted &&
                    currentStatus.state ==
                        IBGatewayConnectionState::Connected) {
                    Contract contract;
                    contract.symbol = "EUR";
                    contract.secType = "CASH";
                    contract.exchange = "IDEALPRO";
                    contract.currency = "USD";
                    updateQuoteMessage(
                        "Oczekiwanie na kwotowania bid/ask z IBKR...");
                    client->reqMktData(
                        eurUsdQuoteRequestId,
                        contract,
                        "",
                        false,
                        false,
                        TagValueListSPtr());
                    marketDataRequestStarted = true;
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

            if (historicalRequestStarted && client->isConnected()) {
                client->cancelHistoricalData(oneMinuteCandleRequestId);
            }
            if (marketDataRequestStarted && client->isConnected()) {
                client->cancelMktData(eurUsdQuoteRequestId);
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
        if (historicalRequestStarted && client->isConnected()) {
            client->cancelHistoricalData(oneMinuteCandleRequestId);
        }
        if (marketDataRequestStarted && client->isConnected()) {
            client->cancelMktData(eurUsdQuoteRequestId);
        }
        client->eDisconnect();
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

    std::string saveError;
    if (!CandleHistoryClient::saveIbkrCandle(candleToSave, saveError)) {
        {
            const std::lock_guard<std::mutex> lock(statusMutex_);
            completedCandleSaveInProgress_ = false;
            nextCandleSaveRetry_ =
                std::chrono::steady_clock::now() + candleSaveRetryDelay;
        }
        ErrorLogger::warning(
            "Nie udało się zapisać zamkniętej świecy IBKR "
            "w candles_IBKR: " + saveError);
        updateCandleMessage(
            "Nie udało się zapisać zamkniętej świecy IBKR "
            "w candles_IBKR: " + saveError);
        return;
    }

    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        if (!completedCandlesPendingSave_.empty() &&
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
