#pragma once

#include "CandleHistoryClient.h"
#include "DefaultEWrapper.h"
#include "EReaderOSSignal.h"

#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

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
 * Przechowuje ostatnie dostępne ceny bid i ask dla instrumentu IBKR.
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
     * Zleca pobranie i zapis ostatnich 1000 zamkniętych świec dla rejestru IBKR.
     * @param error Otrzymuje opis błędu, jeśli synchronizacja nie mogła ruszyć.
     * @return true, jeśli zadanie zostało przyjęte.
     * @throws std::bad_alloc, jeśli nie można przygotować komunikatu statusu.
     */
    bool requestHistorySync(std::string& error);

    /**
     * Sprawdza, czy trwa pobieranie lub zapis historii IBKR.
     * @return true, gdy synchronizacja blokuje ponowne uruchomienie.
     */
    bool historySyncInProgress() const;

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
     * Przenosi zamknięte świece z bufora symboli do kolejki zapisu.
     */
    void flushClosedPendingCandles();

    /**
     * Zatrzymuje synchronizację historii i pokazuje jej przyczynę.
     * @param message Komunikat błędu lub przerwania do pokazania użytkownikowi.
     * @throws std::bad_alloc, jeśli nie można zapisać komunikatu błędu.
     */
    void failHistorySync(std::string message);

    /**
     * Pomija instrument, którego żądanie historii odrzuciło IBKR.
     * @param message Komunikat błędu zwrócony przez IBKR.
     */
    void skipCurrentHistoryInstrument(std::string message);

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
    // Backend: przechowuje osobne kwotowania dla każdego instrumentu z rejestru.
    std::unordered_map<std::string, IBGatewayQuoteSnapshot>
        quotesBySymbol_;
    // Backend: buforuje osobną, aktualnie tworzoną świecę każdego instrumentu.
    std::unordered_map<std::string, IBKRCandleData> pendingCandlesBySymbol_;
    // Backend: zapamiętuje czasy już zakolejkowanych świec dla każdego symbolu.
    std::unordered_map<std::string, std::int64_t>
        lastCompletedCandleTimestampBySymbol_;
    // Backend: przechowuje zamknięte świece do czasu potwierdzenia zapisu.
    std::deque<IBKRCandleData> completedCandlesPendingSave_;
    std::chrono::steady_clock::time_point nextCandleSaveRetry_{};
    bool completedCandleSaveInProgress_ = false;
    bool historySyncRequested_ = false;
    bool historySyncInProgress_ = false;
    int historySyncRequestId_ = 0;
    int nextHistorySyncRequestId_ = 10000;
    std::size_t historySyncInstrumentIndex_ = 0;
    std::size_t historySyncSavedCandleCount_ = 0;
    std::size_t historySyncSkippedInstrumentCount_ = 0;
    std::string historySyncSymbol_;
    std::vector<IBKRCandleData> historySyncCandles_;
    StatusChangedCallback statusChangedCallback_;
    std::thread connectionThread_;
};
