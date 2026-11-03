#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string>
#include <vector>

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
     * Starts the Forex monitor and clock timer during window creation.
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
     * Refreshes the clock when the displayed minute changes.
     */
    void onClockTimer();

    /**
     * Paints the window using the current Forex monitor status.
     */
    void onPaint();

    /**
     * Stops background work and exits the Windows message loop.
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
    ForexPriceMonitor forexMonitor_;
};
