#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include <string>

#include "StockPriceMonitor.h"

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
};
