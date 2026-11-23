#include "IBGatewayClient.h"

#include "EClientSocket.h"
#include "EReader.h"
#include "ErrorLogger.h"

#include <chrono>
#include <exception>
#include <memory>
#include <sstream>
#include <system_error>
#include <utility>

// Backend
namespace {

constexpr auto connectionHandshakeTimeout = std::chrono::seconds(15);
constexpr int oneMinuteCandleRequestId = 9251;

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
    updateCandleMessage("Łączenie z IB Gateway przed pobraniem świecy...");
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
}

/**
 * Zachowuje ostatni bar z początkowej odpowiedzi historii bez odświeżania UI.
 */
void IBGatewayClient::historicalData(int requestId, const Bar& bar) {
    if (requestId != oneMinuteCandleRequestId) {
        return;
    }

    const std::lock_guard<std::mutex> lock(statusMutex_);
    candle_.available = true;
    candle_.timestamp = bar.time;
    candle_.open = bar.open;
    candle_.high = bar.high;
    candle_.low = bar.low;
    candle_.close = bar.close;
}

/**
 * Zapisuje i pokazuje nową wartość dla aktywnej świecy 1-minutowej.
 */
void IBGatewayClient::historicalDataUpdate(int requestId, const Bar& bar) {
    if (requestId != oneMinuteCandleRequestId) {
        return;
    }

    {
        const std::lock_guard<std::mutex> lock(statusMutex_);
        candle_.available = true;
        candle_.timestamp = bar.time;
        candle_.open = bar.open;
        candle_.high = bar.high;
        candle_.low = bar.low;
        candle_.close = bar.close;
        candle_.message = "Aktualizacja świecy EUR/USD 1-minutowej z IBKR.";
    }
    notifyStatusChanged();
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
                        1,
                        true,
                        TagValueListSPtr());
                    historicalRequestStarted = true;
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
