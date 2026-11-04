#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "DailySentenceClient.h"
#include "ForexPriceMonitor.h"
#include "PriceHistoryClient.h"

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
     * Uruchamia monitor cen, zegar oraz pobieranie sentencji w tle.
     * @return true, jeśli wszystkie usługi wystartowały; w przeciwnym razie false.
     */
    bool onCreate();

    /**
     * Tworzy przycisk „Raporty” i ustawia go na dole głównego okna.
     * @return true, jeśli przycisk został utworzony; w przeciwnym razie false.
     */
    bool createReportsButton();

    /**
     * Dopasowuje położenie przycisku „Raporty” do aktualnego rozmiaru okna.
     */
    void positionReportsButton();

    /**
     * Przełącza główną część okna na stronę raportów.
     */
    void onReportsClicked();

    /**
     * Odświeża zegar, gdy zmieni się wyświetlana minuta.
     */
    void onClockTimer();

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
    bool hasPreviousTime_ = false;
    bool reportsPageVisible_ = false;
    WORD previousHour_ = 0;
    WORD previousMinute_ = 0;
    HWND reportsButton_ = nullptr;
    std::vector<PriceReportEntry> reportEntries_;
    std::string reportsStatus_;
    std::mutex dailySentenceMutex_;
    std::string dailySentence_ = "Pobieranie sentencji dnia...";
    std::thread dailySentenceThread_;
    ForexPriceMonitor forexMonitor_;
};
