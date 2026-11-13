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
     * Rysuje tabelę sygnałów albo opis szczegółów wybranego sygnału.
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

    /**
     * Tworzy przycisk szczegółów dla każdego sygnału na bieżącej stronie.
     * @return true, jeśli utworzono wszystkie przyciski.
     */
    bool createDetailsButtons();

    /**
     * Pobiera wyniki dla wybranego sygnału i otwiera jego widok szczegółów.
     */
    void openDetails(std::size_t signalIndex);

    /**
     * Rysuje opis wyniku inwestycji z wyróżnieniem salda, zysku i spreadu.
     */
    void drawDetails(HDC deviceContext, RECT& clientArea) const;

    HINSTANCE instance_ = nullptr;
    HWND parent_ = nullptr;
    HWND previousPageButton_ = nullptr;
    HWND nextPageButton_ = nullptr;
    HWND backButton_ = nullptr;
    std::vector<HWND> detailsButtons_;
    bool visible_ = false;
    bool detailsVisible_ = false;
    bool hasNextPage_ = false;
    std::size_t currentPage_ = 0;
    std::vector<SignalReportEntry> signals_;
    SignalPotentialDetails details_;
    std::string detailsStatus_;
    std::string status_;
};
