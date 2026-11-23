#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string>
#include <vector>

#include "CandleHistoryClient.h"

/**
 * Wyświetla wspólne świece z Twelve Data i IBKR w jednej tabeli.
 */
class CandleComparisonPage {
public:
    ~CandleComparisonPage();

    /**
     * Tworzy przyciski paginacji należące do zestawienia.
     * @param instance Instancja aplikacji Windows.
     * @param parent Okno, w którym zostaną utworzone przyciski.
     * @return true, jeśli oba przyciski utworzono poprawnie.
     */
    bool create(HINSTANCE instance, HWND parent);

    /**
     * Otwiera widok i pobiera pierwszą, najnowszą stronę danych.
     */
    void open();

    /**
     * Zamyka widok i ukrywa kontrolki paginacji.
     */
    void close();

    /**
     * Informuje, czy zestawienie jest aktualnie widoczne.
     */
    bool isVisible() const;

    /**
     * Obsługuje kliknięcia przycisków poprzedniej i następnej strony.
     * @param controlId Identyfikator klikniętej kontrolki.
     * @param notificationCode Kod powiadomienia Windows.
     * @return true, jeśli komunikat należał do tego widoku.
     */
    bool handleCommand(int controlId, int notificationCode);

    /**
     * Ustawia przyciski paginacji odpowiednio do rozmiaru okna.
     */
    void positionControls();

    /**
     * Rysuje dziewięciokolumnową tabelę wspólnych świec.
     */
    void draw(HDC deviceContext, RECT& clientArea) const;

private:
    /**
     * Pobiera aktualną stronę i sprawdza poprawność wartości dziesiętnych.
     */
    void loadCurrentPage();

    /**
     * Pokazuje lub ukrywa przyciski zgodnie ze stanem paginacji.
     */
    void updateControls();

    HWND parent_ = nullptr;
    HWND previousPageButton_ = nullptr;
    HWND nextPageButton_ = nullptr;
    bool visible_ = false;
    bool hasNextPage_ = false;
    std::size_t currentPage_ = 0;
    std::vector<CandleComparisonEntry> candles_;
    std::string status_;
    HFONT buttonFont_ = nullptr;
    int buttonFontHeight_ = 0;
};
