#pragma once

#include "DefaultEWrapper.h"
#include "EReaderOSSignal.h"

#include <atomic>
#include <functional>
#include <mutex>
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
     * Zwraca true dla komunikatów IBKR informujących o stanie farm serwerów.
     * @param errorCode Kod błędu lub ostrzeżenia IBKR.
     * @return true, jeśli komunikat jest informacyjny.
     */
    static bool isInformationalError(int errorCode);

    EReaderOSSignal readerSignal_{250};
    std::atomic<bool> stopRequested_{false};
    mutable std::mutex statusMutex_;
    IBGatewayConnectionStatus status_;
    StatusChangedCallback statusChangedCallback_;
    std::thread connectionThread_;
};
