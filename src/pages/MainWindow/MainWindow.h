#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <mutex>
#include <string>
#include <thread>

#include "DailySentenceClient.h"
#include "CandleGapsPage.h"
#include "ReportsPage.h"
#include "SignalsPage.h"
#include "StockPriceMonitor.h"

class MainWindow {
public:
    /**
     * Registers and creates the main window, then displays it.
     */
    bool create(HINSTANCE instance, int showCommand);

    /**
     * Processes Windows messages until the application is closed.
     */
    int run();

private:
    /**
     * Routes Windows messages to the MainWindow instance stored on the window.
     */
    static LRESULT CALLBACK windowProcedure(
        HWND window, UINT message, WPARAM wParam, LPARAM lParam);

    /**
     * Handles a message after the window procedure has located this instance.
     */
    LRESULT handleMessage(UINT message, WPARAM wParam, LPARAM lParam);

    /**
     * Uruchamia monitor świecy oraz pobieranie sentencji w tle.
     * @return true, jeśli wszystkie usługi wystartowały; w przeciwnym razie false.
     */
    bool onCreate();

    /**
     * Tworzy przycisk „Raporty” i ustawia go na dole głównego okna.
     * @return true, jeśli przycisk został utworzony; w przeciwnym razie false.
     */
    bool createReportsButton();

    /**
     * Tworzy przycisk „Luki świec” obok przycisku raportów.
     * @return true, jeśli przycisk został utworzony; w przeciwnym razie false.
     */
    bool createCandleGapsButton();

    /**
     * Tworzy przycisk „Sygnały” obok pozostałych widoków tabel.
     * @return true, jeśli przycisk został utworzony; w przeciwnym razie false.
     */
    bool createSignalsButton();

    /**
     * Dopasowuje położenie przycisków widoków do aktualnego rozmiaru okna.
     */
    void positionReportsButton();

    /**
     * Przełącza główną część okna na stronę raportów.
     */
    void onReportsClicked();

    /**
     * Przełącza główną część okna na tabelę brakujących świec.
     */
    void onCandleGapsClicked();

    /**
     * Przełącza główną część okna na tabelę signals.
     */
    void onSignalsClicked();

    /**
     * Pobiera sentencję w osobnym wątku, aby nie blokować okna aplikacji.
     * @throws std::system_error, jeśli system nie może utworzyć nowego wątku.
     */
    void startDailySentenceRequest();

    /**
     * Rysuje stronę główną lub raporty na podstawie bieżącego stanu aplikacji.
     */
    void onPaint();

    /**
     * Zatrzymuje zadania w tle i kończy pętlę komunikatów Windows.
     */
    void onDestroy();

    HINSTANCE instance_ = nullptr;
    HWND handle_ = nullptr;
    int showCommand_ = SW_SHOW;
    HWND reportsButton_ = nullptr;
    HWND candleGapsButton_ = nullptr;
    HWND signalsButton_ = nullptr;
    std::mutex dailySentenceMutex_;
    std::string dailySentence_ = "Pobieranie sentencji dnia...";
    std::thread dailySentenceThread_;
    StockPriceMonitor candleMonitor_;
    ReportsPage reportsPage_;
    CandleGapsPage candleGapsPage_;
    SignalsPage signalsPage_;
};
