#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string>
#include <vector>

#include "CandleHistoryClient.h"

class SignalsPage {
public:
    /**
     * Tworzy przyciski paginacji widoku sygnałów.
     * @param instance Instancja aplikacji Windows.
     * @param parent Okno nadrzędne dla utworzonych kontrolek.
     * @return true, jeśli oba przyciski utworzono poprawnie.
     */
    bool create(HINSTANCE instance, HWND parent);

    /**
     * Otwiera widok i pobiera najnowszą stronę sygnałów.
     */
    void open();

    /**
     * Zamyka widok i ukrywa jego przyciski paginacji.
     */
    void close();

    /**
     * Informuje, czy widok sygnałów jest aktualnie widoczny.
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
     * Ustawia przyciski paginacji na dole okna.
     */
    void positionControls();

    /**
     * Rysuje kolumny tabeli sygnałów z danymi JSON.
     */
    void draw(HDC deviceContext, RECT& clientArea) const;

private:
    /**
     * Pobiera aktualną stronę sygnałów i aktualizuje stan widoku.
     */
    void loadCurrentPage();

    /**
     * Pokazuje lub ukrywa przyciski paginacji.
     */
    void updateControls();

    HWND parent_ = nullptr;
    HWND previousPageButton_ = nullptr;
    HWND nextPageButton_ = nullptr;
    bool visible_ = false;
    bool hasNextPage_ = false;
    std::size_t currentPage_ = 0;
    std::vector<SignalReportEntry> signals_;
    std::string status_;
};
