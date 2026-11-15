#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string>
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
     * Przygotowuje monitor do pobierania świec 1-minutowych.
     */
    StockPriceMonitor();

    /**
     * Zatrzymuje pobieranie w tle i zwalnia uchwyty Windows.
     */
    ~StockPriceMonitor();

    StockPriceMonitor(const StockPriceMonitor&) = delete;
    StockPriceMonitor& operator=(const StockPriceMonitor&) = delete;

    /**
     * Uruchamia niezależne wątki świec i powiadomień dźwiękowych.
     * @param notificationWindow Okno, które ma otrzymywać komunikaty o zmianach.
     * @return true, jeśli uruchomiono oba wątki; w przeciwnym razie false.
     */
    bool start(HWND notificationWindow);

    /**
     * Zatrzymuje oba wątki, czeka na ich zakończenie i zamyka uchwyty.
     */
    void stop();

    /**
     * Zwraca kopię ostatniej świecy i statusu, bezpieczną do odczytu w innym wątku.
     */
    StockPriceSnapshot snapshot() const;

private:
    /**
     * Uruchamia pętlę dla obiektu monitora przekazanego przez Windows.
     */
    static DWORD WINAPI threadEntryPoint(LPVOID parameter);

    /**
     * Uruchamia niezależną pętlę powiadomień dla wskazanego monitora.
     * @param parameter Wskaźnik na monitor.
     * @return Kod zakończenia wątku Windows.
     */
    static DWORD WINAPI notificationThreadEntryPoint(LPVOID parameter);

    /**
     * Pobiera nową świecę 1-minutową co minutę.
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
     * Odtwarza dźwięk bez przerywania innego dźwięku tego monitora.
     * @param signalSaved Czy odtworzyć dźwięk zapisu zamiast dźwięku luki.
     * @return true, jeśli odtwarzanie zakończyło się powodzeniem.
     */
    bool playAudio(bool signalSaved);

    /**
     * Pobiera świecę EURUSD i uruchamia przypisane jej algorytmy sygnałów.
     */
    bool updateCandle();

    /**
     * Pobiera i zapisuje świece aktywnych produktów innych niż EURUSD.
     */
    void updateAdditionalProducts();

    HWND notificationWindow_ = nullptr;
    HANDLE stopEvent_ = nullptr;
    HANDLE workerThread_ = nullptr;
    HANDLE notificationThread_ = nullptr;
    mutable SRWLOCK statusLock_ = SRWLOCK_INIT;
    SRWLOCK audioLock_ = SRWLOCK_INIT;
    std::vector<std::string> monitoredSymbols_;
    std::unordered_set<std::string> backfilledSymbols_;
    CandleData oneMinuteCandle_;
    std::string status_ = "Oczekiwanie na świecę 1-minutową...";
    std::string backfillStatus_ = "Sprawdzanie brakujących świec...";
    std::string notificationStatus_;
};
