#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string>
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
     * Uruchamia wątek pobierający świece i powiadamiający okno o aktualizacji.
     * @param notificationWindow Okno, które ma otrzymywać komunikaty o zmianach.
     * @return true, jeśli uruchomiono wątek; w przeciwnym razie false.
     */
    bool start(HWND notificationWindow);

    /**
     * Zatrzymuje wątek pobierający świece i zamyka jego uchwyty.
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
     * Pobiera nową świecę 1-minutową co minutę.
     */
    void run();

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
    mutable SRWLOCK statusLock_ = SRWLOCK_INIT;
    std::vector<std::string> monitoredSymbols_;
    CandleData oneMinuteCandle_;
    std::string status_ = "Oczekiwanie na świecę 1-minutową...";
    std::string backfillStatus_ = "Sprawdzanie brakujących świec...";
};
