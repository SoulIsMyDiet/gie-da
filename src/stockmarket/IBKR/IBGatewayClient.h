#pragma once

#include "CandleHistoryClient.h"
#include "DefaultEWrapper.h"
#include "EReaderOSSignal.h"

#include <atomic>
#include <chrono>
#include <cstdint>
#include <deque>
#include <functional>
#include <mutex>
#include <optional>
#include <string>
#include <thread>

enum class IBGatewayConnectionState {
    Disconnected,
    Connecting,
    Connected,
    Disconnecting,
    Error
};

/**
 * Przechowuje stan połączenia i krótki komunikat do wyświetlenia użytkownikowi.
 */
struct IBGatewayConnectionStatus {
    IBGatewayConnectionState state = IBGatewayConnectionState::Disconnected;
    std::string message = "Rozłączono.";
};

/**
 * Przechowuje ostatnią świecę EUR/USD, także tę aktualnie tworzoną.
 */
struct IBGatewayOneMinuteCandle {
    bool available = false;
    std::int64_t timestampUtc = 0;
    std::string timestamp;
    double open = 0.0;
    double high = 0.0;
    double low = 0.0;
    double close = 0.0;
    std::string message =
        "Połącz z IB Gateway, aby pobrać świecę EUR/USD 1-minutową.";
};

/**
 * Przechowuje ostatnie dostępne ceny bid i ask dla EUR/USD.
 */
struct IBGatewayQuoteSnapshot {
    bool bidAvailable = false;
    bool askAvailable = false;
    bool delayed = false;
    double bid = 0.0;
    double ask = 0.0;
    std::string message = "Oczekiwanie na kwotowania bid/ask z IBKR.";
};

/**
 * Zarządza połączeniem socketowym z IB Gateway w osobnym wątku.
 */
class IBGatewayClient final : public DefaultEWrapper {
public:
    using StatusChangedCallback = std::function<void()>;

    /**
     * Zatrzymuje wątek klienta i zwalnia zasoby sygnału czytelnika.
     */
    ~IBGatewayClient();

    /**
     * Rozpoczyna asynchroniczne połączenie z IB Gateway.
     * @param host Adres serwera, na przykład 127.0.0.1.
     * @param port Port API Gateway.
     * @param clientId Identyfikator sesji klienta API; musi być nieujemny.
     * @param error Opis przyczyny, jeśli połączenie nie mogło wystartować.
     * @return true, jeśli rozpoczęto próbę połączenia.
     * @throws std::system_error, jeśli nie można dołączyć zakończonego wątku.
     */
    bool connect(
        const std::string& host,
        int port,
        int clientId,
        std::string& error);

    /**
     * Zatrzymuje połączenie i czeka na zakończenie wątku klienta.
     * @throws std::system_error, jeśli system nie może połączyć wątków.
     */
    void disconnect();

    /**
     * Zwraca bezpieczną dla wielu wątków kopię bieżącego statusu.
     * @return Aktualny stan połączenia i komunikat dla użytkownika.
     */
    IBGatewayConnectionStatus status() const;

    /**
     * Zwraca kopię ostatniej świecy i jej statusu.
     * @return Dane ostatniej świecy 1-minutowej albo opis oczekiwania/błędu.
     */
    IBGatewayOneMinuteCandle candleSnapshot() const;

    /**
     * Zwraca kopię ostatnich kwotowań bid/ask.
     * @return Dostępne ceny lub komunikat o ich braku.
     */
    IBGatewayQuoteSnapshot quoteSnapshot() const;

    /**
     * Ustawia funkcję wywoływaną po każdej zmianie statusu.
     * @param callback Funkcja powiadamiająca, może być pusta.
     */
    void setStatusChangedCallback(StatusChangedCallback callback);

    /**
     * Potwierdza gotowość API po otrzymaniu odpowiedzi nextValidId.
     * @param orderId Identyfikator następnego zlecenia, w tym miejscu nieużywany.
     */
    void nextValidId(int orderId) override;

    /**
     * Zapisuje błąd API i kończy nieudaną próbę nawiązywania połączenia.
     * @param id Identyfikator żądania powiązanego z błędem.
     * @param errorTimeMs Czas błędu przekazany przez serwer.
     * @param errorCode Kod błędu lub ostrzeżenia IBKR.
     * @param errorString Tekst błędu lub ostrzeżenia.
     * @param advancedOrderRejectJson Szczegóły odrzucenia zlecenia, jeśli występują.
     */
    void error(
        int id,
        long long errorTimeMs,
        int errorCode,
        const std::string& errorString,
        const std::string& advancedOrderRejectJson) override;

    /**
     * Aktualizuje status po zamknięciu socketu przez Gateway.
     */
    void connectionClosed() override;

    /**
     * Odbiera początkowe świece historyczne dla aktywnego żądania.
     */
    void historicalData(int requestId, const Bar& bar) override;

    /**
     * Odbiera aktualizację ostatniej, jeszcze tworzonej świecy.
     */
    void historicalDataUpdate(int requestId, const Bar& bar) override;

    /**
     * Zakończenie początkowej historii dla aktywnego żądania.
     */
    void historicalDataEnd(
        int requestId,
        const std::string& startDateStr,
        const std::string& endDateStr) override;

    /**
     * Aktualizuje najlepszą cenę bid lub ask otrzymaną z rynku.
     */
    void tickPrice(
        int requestId,
        TickType tickType,
        double price,
        const TickAttrib& attrib) override;

private:
    /**
     * Prowadzi protokół IBKR i przetwarza odpowiedzi serwera w wątku roboczym.
     * @param host Adres serwera Gateway.
     * @param port Port API Gateway.
     * @param clientId Identyfikator sesji klienta API.
     */
    void runConnection(std::string host, int port, int clientId);

    /**
     * Zmienia status i powiadamia obserwatora poza blokadą mutexu.
     * @param state Nowy stan połączenia.
     * @param message Komunikat wyświetlany użytkownikowi.
     */
    void updateStatus(
        IBGatewayConnectionState state, std::string message);

    /**
     * Ustawia komunikat świecy i powiadamia widok o zmianie.
     */
    void updateCandleMessage(std::string message);

    /**
     * Zapisuje najstarszą zamkniętą świecę i ponawia próbę po błędzie.
     */
    void persistNextCompletedCandle();

    /**
     * Ustawia komunikat kwotowań i powiadamia widok o zmianie.
     */
    void updateQuoteMessage(std::string message);

    /**
     * Powiadamia widok, gdy zmieniły się dane świecy lub jej komunikat.
     */
    void notifyStatusChanged();

    /**
     * Zwraca true dla komunikatów IBKR informujących o stanie farm serwerów.
     * @param errorCode Kod błędu lub ostrzeżenia IBKR.
     * @return true, jeśli komunikat jest informacyjny.
     */
    static bool isInformationalError(int errorCode);

    EReaderOSSignal readerSignal_{250};
    std::atomic<bool> stopRequested_{false};
    mutable std::mutex statusMutex_;
    IBGatewayConnectionStatus status_;
    IBGatewayOneMinuteCandle candle_;
    IBGatewayQuoteSnapshot quote_;
    // Backend: buforuje świecę w trakcie tworzenia do nadejścia kolejnej minuty.
    std::optional<IBKRCandleData> pendingCandle_;
    // Backend: przechowuje zamknięte świece do czasu potwierdzenia zapisu.
    std::deque<IBKRCandleData> completedCandlesPendingSave_;
    std::chrono::steady_clock::time_point nextCandleSaveRetry_{};
    bool completedCandleSaveInProgress_ = false;
    StatusChangedCallback statusChangedCallback_;
    std::thread connectionThread_;
};
