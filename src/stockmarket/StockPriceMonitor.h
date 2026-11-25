#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstddef>
#include <chrono>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "CandlesDataClient.h"

struct StockPriceSnapshot {
    CandleData oneMinuteCandle;
    std::string status;
    std::string backfillStatus;
};

class StockPriceMonitor {
public:
    /**
     * Przygotowuje monitor powiadomień o nowych sygnałach.
     */
    StockPriceMonitor();

    /**
     * Zatrzymuje powiadomienia w tle i zwalnia uchwyty Windows.
     */
    ~StockPriceMonitor();

    StockPriceMonitor(const StockPriceMonitor&) = delete;
    StockPriceMonitor& operator=(const StockPriceMonitor&) = delete;

    /**
     * Uruchamia wątek powiadomień o nowych sygnałach.
     * Pobieranie świec jest obsługiwane przez IBKR, a istniejące świece
     * Twelve Data pozostają w bazie bez zmian.
     * @param notificationWindow Okno, które ma otrzymywać komunikaty o zmianach.
     * @return true, jeśli uruchomiono wątek powiadomień.
     */
    bool start(HWND notificationWindow);

    /**
     * Zatrzymuje wątek powiadomień, czeka na jego zakończenie i zamyka uchwyty.
     */
    void stop();

    /**
     * Zwraca kopię ostatniej świecy i statusu, bezpieczną do odczytu w innym wątku.
     */
    StockPriceSnapshot snapshot() const;

    /**
     * Odtwarza dźwięk dla podanej liczby nowych sygnałów.
     * @param signalCount Liczba sygnałów zapisanych przez API.
     * @return true, jeśli wszystkie dźwięki odtworzono poprawnie.
     */
    bool playSavedSignalSounds(std::size_t signalCount);

private:
    /**
     * Uruchamia niezależną pętlę powiadomień dla wskazanego monitora.
     * @param parameter Wskaźnik na monitor.
     * @return Kod zakończenia wątku Windows.
     */
    static DWORD WINAPI notificationThreadEntryPoint(LPVOID parameter);

    /**
     * Pozostała pętla legacy Twelve Data; start() nie uruchamia jej.
     */
    void run();

    /**
     * Pobiera nowe powiadomienia i ponawia błędy z ograniczoną częstotliwością.
     */
    void runNotifications();

    /**
     * Zmienia widoczny stan powiadomień i informuje okno o zmianie.
     * @param status Opis błędu albo pusty tekst po odzyskaniu sprawności.
     */
    void setNotificationStatus(const std::string& status);

    /**
     * Odtwarza dźwięk zapisu sygnału bez przerywania innego dźwięku monitora.
     * @return true, jeśli odtwarzanie zakończyło się powodzeniem.
     */
    bool playSignalSavedAudio();

    /**
     * Obsługuje świecę EUR/USD w nieuruchamianej pętli legacy Twelve Data.
     */
    bool updateCandle();

    /**
     * Obsługuje inne instrumenty w nieuruchamianej pętli legacy Twelve Data.
     */
    void updateAdditionalProducts();

    HWND notificationWindow_ = nullptr;
    HANDLE stopEvent_ = nullptr;
    HANDLE notificationThread_ = nullptr;
    mutable SRWLOCK statusLock_ = SRWLOCK_INIT;
    SRWLOCK audioLock_ = SRWLOCK_INIT;
    std::vector<std::string> monitoredSymbols_;
    std::unordered_set<std::string> backfilledSymbols_;
    // Ogranicza ponawianie backfillu niezależnie dla każdego instrumentu.
    std::unordered_map<
        std::string, std::chrono::steady_clock::time_point> backfillRetryAfter_;
    CandleData oneMinuteCandle_;
    std::string status_ = "Oczekiwanie na świecę 1-minutową...";
    std::string backfillStatus_ = "Sprawdzanie brakujących świec...";
    std::string notificationStatus_;
};
