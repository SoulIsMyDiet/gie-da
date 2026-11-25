#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstddef>
#include <map>
#include <string>
#include <vector>

#include "CandleHistoryClient.h"

/**
 * Zarządza filtrami i dynamiczną tabelą raportu transakcji.
 */
class TransactionsPage {
public:
    ~TransactionsPage();

    /**
     * Tworzy filtry daty, przyciski kolumn, listy wyboru i nawigację.
     * @param instance Instancja aplikacji Windows.
     * @param parent Okno nadrzędne dla utworzonych kontrolek.
     * @return true, jeśli wszystkie kontrolki utworzono poprawnie.
     * @throws std::bad_alloc Gdy nie można przydzielić pamięci na stan filtrów.
     */
    bool create(HINSTANCE instance, HWND parent);

    /**
     * Otwiera widok i pokazuje listy opcji dla zaznaczonych kolumn.
     * @return Brak (funkcja zmienia widoczność kontrolek).
     * @throws std::bad_alloc Gdy nie można wczytać lub wyświetlić opcji filtrów.
     */
    void open();

    /**
     * Ukrywa widok i zamyka wszystkie otwarte listy filtrów.
     * @return Brak (funkcja zmienia widoczność kontrolek).
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
     * @throws std::bad_alloc Gdy aktualizacja listy wymaga pamięci, której brakuje.
     */
    bool handleCommand(int controlId, int notificationCode);

    /**
     * Dopasowuje układ i czcionki kontrolek do rozmiaru okna.
     * @return Brak (funkcja zmienia położenie kontrolek).
     */
    void positionControls();

    /**
     * Rysuje wybrane kolumny, rekordy i wiersz podsumowania.
     * @param deviceContext Kontekst używany do rysowania.
     * @param clientArea Obszar dostępny dla widoku.
     * @return Brak (funkcja rysuje zawartość widoku).
     * @throws std::bad_alloc Gdy nie można utworzyć tekstu wyświetlanego w tabeli.
     */
    void draw(HDC deviceContext, RECT& clientArea) const;

private:
    /**
     * Pobiera wybraną stronę raportu z aktywnym zestawem kolumn i filtrów.
     * @return Brak (wynik zapisuje w stanie widoku).
     * @throws std::bad_alloc Gdy nie można przydzielić pamięci na wynik.
     */
    void loadCurrentPage();

    /**
     * Odczytuje formularz i generuje pierwszą stronę nowego raportu.
     * @return Brak (wynik zapisuje w stanie widoku).
     * @throws std::bad_alloc Gdy nie można przydzielić pamięci na filtry.
     */
    void generateReport();

    /**
     * Pobiera aktualne opcje i pokazuje listę dla wskazanego filtra.
     * @param filterIndex Indeks pola w konfiguracji przycisków raportu.
     * @return true, jeśli pobrano i pokazano listę opcji.
     * @throws std::bad_alloc Gdy nie można przydzielić pamięci na opcje.
     */
    bool showFilterOptions(std::size_t filterIndex);

    /**
     * Zapisuje zaznaczone wartości wskazanej listy po każdej zmianie.
     * @param filterIndex Indeks filtra, którego lista zmieniła zaznaczenie.
     * @return Brak (funkcja uaktualnia stan filtrów).
     * @throws std::bad_alloc Gdy nie można przydzielić pamięci na wartości filtra.
     */
    void storeFilterSelection(std::size_t filterIndex);

    /**
     * Zwraca klucze wybranych kolumn w kolejności widocznej w formularzu.
     * @return Lista kluczy aktywnych kolumn.
     * @throws std::bad_alloc Gdy nie można przydzielić pamięci na listę.
     */
    std::vector<std::string> selectedColumnKeys() const;

    /**
     * Zwraca wybrane wartości filtrów aktywnych kolumn.
     * @return Mapa kluczy kolumn na wybrane wartości.
     * @throws std::bad_alloc Gdy nie można przydzielić pamięci na mapę.
     */
    std::map<std::string, std::vector<std::string>> selectedFilterValues() const;

    /**
     * Pokazuje lub ukrywa kontrolki i ustawia ich aktywność.
     * @return Brak (funkcja aktualizuje właściwości kontrolek).
     */
    void updateControls();

    HWND parent_ = nullptr;
    HWND fromDateLabel_ = nullptr;
    HWND fromDatePicker_ = nullptr;
    HWND toDateLabel_ = nullptr;
    HWND toDatePicker_ = nullptr;
    HWND generateButton_ = nullptr;
    HWND previousPageButton_ = nullptr;
    HWND nextPageButton_ = nullptr;
    std::vector<HWND> filterButtons_;
    std::vector<HWND> filterLists_;
    std::vector<bool> openFilterLists_;
    std::vector<bool> filterSelected_;
    std::vector<std::vector<std::string>> filterOptionValues_;
    std::vector<std::vector<std::string>> selectedFilterValues_;
    std::map<std::string, std::vector<TransactionReportFilterOption>>
        filterOptions_;
    bool filterOptionsLoaded_ = false;
    bool visible_ = false;
    bool generated_ = false;
    bool hasNextPage_ = false;
    std::size_t currentPage_ = 0;
    std::string appliedStartDate_;
    std::string appliedEndDate_;
    std::vector<std::string> appliedColumns_;
    std::map<std::string, std::vector<std::string>> appliedFilters_;
    TransactionReportPage report_;
    std::string status_;
    HFONT controlFont_ = nullptr;
    int controlFontHeight_ = 0;
    HFONT buttonFont_ = nullptr;
    int buttonFontHeight_ = 0;
    HFONT filterButtonFont_ = nullptr;
    int filterButtonFontHeight_ = 0;
};
