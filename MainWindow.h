#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "ForexPriceMonitor.h"

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
     * Refreshes the clock and plays the five-minute audio reminder when due.
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
    WORD previousHour_ = 0;
    WORD previousMinute_ = 0;
    ForexPriceMonitor forexMonitor_;
};
