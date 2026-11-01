#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include <string>
#include <vector>

#include "ForexPriceMonitor.h"

class MainWindowDisplay {
public:
    /**
     * Draws the clock, status, and color-coded Forex price history.
     * @param deviceContext Windows drawing context for the target window.
     * @param clientArea Window client rectangle to draw within.
     * @param snapshot Current status and price history to display.
     */
    static void draw(
        HDC deviceContext, RECT& clientArea, const ForexPriceSnapshot& snapshot);
};
