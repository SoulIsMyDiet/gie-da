#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string>
#include <vector>

#include "CandleHistoryClient.h"

class ReportsPage {
public:
    /**
     * Tworzy przyciski paginacji należące do strony raportów.
     * @param instance Instancja aplikacji Windows.
     * @param parent Okno, w którym zostaną utworzone przyciski.
     * @return true, jeśli oba przyciski utworzono poprawnie.
     */
    bool create(HINSTANCE instance, HWND parent);

    /**
     * Otwiera stronę i pobiera pierwszą, najnowszą stronę świec.
     */
    void open();

    /**
     * Zamyka stronę i ukrywa kontrolki paginacji.
     */
    void close();

    /**
     * Informuje, czy strona raportów jest aktualnie widoczna.
     */
    bool isVisible() const;

    /**
     * Obsługuje kliknięcia przycisków poprzedniej i następnej strony.
     * @param controlId Identyfikator klikniętej kontrolki.
     * @param notificationCode Kod powiadomienia Windows.
     * @return true, jeśli komunikat należał do tej strony.
     */
    bool handleCommand(int controlId, int notificationCode);

    /**
     * Ustawia przyciski na dole okna, dostosowując ich pozycję do rozmiaru.
     */
    void positionControls();

    /**
     * Rysuje tabelę świec, komunikat stanu i numer aktualnej strony.
     */
    void draw(HDC deviceContext, RECT& clientArea) const;

private:
    /**
     * Pobiera dane aktualnej strony i aktualizuje stan widoku.
     */
    void loadCurrentPage();

    /**
     * Pokazuje lub ukrywa kontrolki paginacji zgodnie ze stanem strony.
     */
    void updateControls();

    HWND parent_ = nullptr;
    HWND previousPageButton_ = nullptr;
    HWND nextPageButton_ = nullptr;
    bool visible_ = false;
    bool hasNextPage_ = false;
    std::size_t currentPage_ = 0;
    std::vector<CandleReportEntry> candles_;
    std::string status_;
};
