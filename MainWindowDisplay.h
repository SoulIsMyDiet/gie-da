#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include <string>
#include <vector>

#include "ForexPriceMonitor.h"
#include "PriceHistoryClient.h"

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

    /**
     * Wyświetla tabelę wpisów pobranych przed kliknięciem przycisku „Raporty”.
     * @param deviceContext Kontekst rysowania głównego okna.
     * @param clientArea Obszar dostępny dla zawartości strony.
     * @param entries Wiersze raportu pobrane z bazy.
     * @param status Informacja o błędzie lub braku wpisów.
     */
    static void drawReportsPage(
        HDC deviceContext, RECT& clientArea,
        const std::vector<PriceReportEntry>& entries, const std::string& status);
};
