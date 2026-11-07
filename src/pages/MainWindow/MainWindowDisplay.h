#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include <string>
#include <vector>

#include "StockPriceMonitor.h"
#include "PriceHistoryClient.h"

class MainWindowDisplay {
public:
    /**
     * Rysuje sentencję dnia, zegar, status i świecę 1-minutową.
     * @param deviceContext Kontekst rysowania głównego okna.
     * @param clientArea Obszar dostępny dla strony głównej.
     * @param snapshot Bieżąca świeca 1-minutowa i status jej pobierania.
     * @param dailySentence Sentencja lub informacja o błędzie jej pobierania.
     */
    static void draw(
        HDC deviceContext, RECT& clientArea, const StockPriceSnapshot& snapshot,
        const std::string& dailySentence);

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
