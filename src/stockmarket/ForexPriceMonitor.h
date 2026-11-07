#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string>

#include "CandlesDataClient.h"

struct ForexPriceSnapshot {
    CandleData oneMinuteCandle;
    std::string status;
};

class ForexPriceMonitor {
public:
    /**
     * Przygotowuje monitor do pobierania świec 1-minutowych.
     */
    ForexPriceMonitor();

    /**
     * Zatrzymuje pobieranie w tle i zwalnia uchwyty Windows.
     */
    ~ForexPriceMonitor();

    ForexPriceMonitor(const ForexPriceMonitor&) = delete;
    ForexPriceMonitor& operator=(const ForexPriceMonitor&) = delete;

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
    ForexPriceSnapshot snapshot() const;

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
     * Pobiera świecę i aktualizuje wspólny stan monitora.
     * Przy błędzie zachowuje ostatnią poprawną świecę i zapisuje opis błędu.
     */
    void updateCandle();

    HWND notificationWindow_ = nullptr;
    HANDLE stopEvent_ = nullptr;
    HANDLE workerThread_ = nullptr;
    mutable SRWLOCK statusLock_ = SRWLOCK_INIT;
    CandleData oneMinuteCandle_;
    std::string status_ = "Oczekiwanie na świecę 1-minutową...";
};
