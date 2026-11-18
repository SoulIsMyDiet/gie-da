#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include <string>

class MainWindowDisplay {
public:
    /**
     * Rysuje sentencję dnia na stronie głównej.
     * @param deviceContext Kontekst rysowania głównego okna.
     * @param window Okno wyznaczające skalę tekstu.
     * @param clientArea Obszar dostępny dla strony głównej.
     * @param dailySentence Sentencja lub informacja o błędzie jej pobierania.
     */
    static void draw(
        HDC deviceContext,
        HWND window,
        RECT& clientArea,
        const std::string& dailySentence);
};
