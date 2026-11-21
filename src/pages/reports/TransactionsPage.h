#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

class TransactionsPage {
public:
    ~TransactionsPage();

    /**
     * Tworzy etykiety i pola wyboru zakresu dat dla widoku transakcji.
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
     * Ustawia widoczność etykiet i pól wyboru daty.
     */
    void updateControls();

    HWND parent_ = nullptr;
    HWND fromDateLabel_ = nullptr;
    HWND fromDatePicker_ = nullptr;
    HWND toDateLabel_ = nullptr;
    HWND toDatePicker_ = nullptr;
    bool visible_ = false;
    HFONT controlFont_ = nullptr;
    int controlFontHeight_ = 0;
};
