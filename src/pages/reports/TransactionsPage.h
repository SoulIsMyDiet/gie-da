#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string>
#include <vector>

#include "CandleHistoryClient.h"

class TransactionsPage {
public:
    ~TransactionsPage();

    /**
     * Tworzy kompaktowe filtry daty, tabelę świec i przyciski widoku.
     * @param instance Instancja aplikacji Windows.
     * @param parent Okno nadrzędne dla utworzonych kontrolek.
     * @return true, jeśli wszystkie kontrolki utworzono poprawnie.
     */
    bool create(HINSTANCE instance, HWND parent);

    /**
     * Pokazuje pola zakresu dat widoku transakcji.
     */
    void open();

    /**
     * Ukrywa pola zakresu dat, zachowując wybrane wartości.
     */
    void close();

    /**
     * Sprawdza, czy widok transakcji jest aktualnie otwarty.
     * @return true, jeśli widok jest widoczny.
     */
    bool isVisible() const;

    /**
     * Obsługuje generowanie raportu i zmianę strony tabeli.
     * @param controlId Identyfikator klikniętej kontrolki.
     * @param notificationCode Kod powiadomienia Windows.
     * @return true, jeśli komunikat należał do tego widoku.
     */
    bool handleCommand(int controlId, int notificationCode);

    /**
     * Dopasowuje położenie i czcionkę kontrolek do rozmiaru okna.
     */
    void positionControls();

    /**
     * Rysuje nagłówek widoku transakcji.
     * @param deviceContext Kontekst używany do rysowania.
     * @param clientArea Obszar dostępny dla widoku.
     */
    void draw(HDC deviceContext, RECT& clientArea) const;

private:
    /**
     * Pobiera wybraną stronę tabeli z zapisanym zakresem dat.
     */
    void loadCurrentPage();

    /**
     * Odczytuje zakres z pól daty i generuje pierwszą stronę tabeli.
     */
    void generateReport();

    /**
     * Pokazuje lub ukrywa kontrolki i ustawia ich aktywność.
     */
    void updateControls();

    HWND parent_ = nullptr;
    HWND fromDateLabel_ = nullptr;
    HWND fromDatePicker_ = nullptr;
    HWND toDateLabel_ = nullptr;
    HWND toDatePicker_ = nullptr;
    HWND previousPageButton_ = nullptr;
    HWND nextPageButton_ = nullptr;
    HWND generateButton_ = nullptr;
    bool visible_ = false;
    bool generated_ = false;
    bool hasNextPage_ = false;
    std::size_t currentPage_ = 0;
    std::string appliedStartDate_;
    std::string appliedEndDate_;
    std::vector<CandleReportEntry> candles_;
    std::string status_;
    HFONT controlFont_ = nullptr;
    int controlFontHeight_ = 0;
    HFONT buttonFont_ = nullptr;
    int buttonFontHeight_ = 0;
};
