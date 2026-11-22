#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "TodoManager.hpp"

#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

/**
 * Rysuje i obsługuje ekran To-do List w istniejącym oknie Win32.
 */
class TodoView {
public:
    /**
     * Używa managera do zapisu danych bez uzależniania logiki od kontrolek.
     */
    explicit TodoView(TodoManager& manager);

    /**
     * Zwalnia dynamiczne kontrolki widoku.
     */
    ~TodoView();

    /**
     * Tworzy przycisk nowego zadania głównego i pasek przewijania.
     * @param instance Instancja aplikacji Windows.
     * @param parent Główne okno, w którym widok tworzy swoje kontrolki.
     * @return true, jeśli kontrolka została utworzona.
     */
    bool create(HINSTANCE instance, HWND parent);

    /**
     * Otwiera widok, odświeża dane i pokazuje jego kontrolki.
     */
    void open();

    /**
     * Ukrywa kontrolki widoku i jego pasek przewijania.
     */
    void close();

    /**
     * Informuje, czy widok To-do jest aktualnie widoczny.
     */
    bool isVisible() const;

    /**
     * Obsługuje kliknięcia kontrolek należących do widoku.
     */
    bool handleCommand(int controlId, int notificationCode);

    /**
     * Obsługuje przewijanie listy zadań klawiaturą lub paskiem przewijania.
     */
    bool handleScroll(WPARAM scrollCommand);

    /**
     * Obsługuje przewijanie listy kółkiem myszy.
     */
    bool handleMouseWheel(short wheelDelta);

    /**
     * Rysuje nagłówek i komunikat pustej listy lub błędu.
     */
    void draw(HDC deviceContext, RECT& clientArea) const;

    /**
     * Rysuje checkbox zadania głównego i czerwone przyciski usuwania.
     */
    bool handleDrawItem(const DRAWITEMSTRUCT* drawItem) const;

    /**
     * Ustawia kolory zielonego przycisku dodawania zadania podrzędnego.
     * @param deviceContext Kontekst rysowania przekazany przez system Windows.
     * @param control Kontrolka, której kolor jest właśnie ustawiany.
     * @return Pędzel tła dla przycisku albo nullptr dla pozostałych kontrolek.
     */
    HBRUSH handleControlColor(HDC deviceContext, HWND control) const;

    /**
     * Ustawia kursor dłoni po najechaniu na przycisk dodawania podzadania.
     * @param control Kontrolka, nad którą znajduje się kursor.
     * @return true, jeśli widok ustawił kursor dłoni.
     */
    bool handleSetCursor(HWND control) const;

    /**
     * Dopasowuje kontrolki i obszar przewijania do rozmiaru okna.
     */
    void positionControls();

private:
    enum class ActionKind {
        addSubtask,
        toggleSubtask,
        editMainTaskTitle,
        editSubtaskTitle,
        moveMainUp,
        moveMainDown,
        moveSubtaskUp,
        moveSubtaskDown,
        hideMainTask,
        hideSubtask,
        editPrompt,
        editDescription,
        deleteMainTask,
        deleteSubtask
    };

    /**
     * Łączy rodzaj klikniętej akcji z identyfikatorem jej zadania.
     */
    struct ControlAction {
        ActionKind kind;
        std::uint64_t taskId;
    };

    /**
     * Zapamiętuje początkowe położenie kontrolki listy podczas przewijania.
     */
    struct ControlPosition {
        HWND handle;
        int x;
        int y;
        int width;
        int height;
    };

    /**
     * Przechowuje nazwę i wyliczony stan głównego checkboxa.
     */
    struct MainCheckboxInfo {
        std::wstring title;
        bool completed;
    };

    /**
     * Przechowuje tekst i stan checkboxa zadania podrzędnego.
     */
    struct SubtaskCheckboxInfo {
        std::wstring title;
        bool completed;
    };

    /**
     * Pobiera listę i zachowuje dotychczasowe dane, jeśli API zwróci błąd.
     */
    void refresh();

    /**
     * Usuwa poprzednie kontrolki wierszy i tworzy je ponownie z bieżących danych.
     */
    void rebuildTaskControls();

    /**
     * Zmienia pozycję istniejących kontrolek zgodnie z wartością przewinięcia.
     */
    void applyScrollPosition();

    /**
     * Obsługuje kliknięcie przycisku dodawania zadania głównego.
     */
    void addMainTask();

    /**
     * Obsługuje akcję przypisaną do pojedynczej kontrolki zadania.
     */
    void performAction(const ControlAction& action, int controlId);

    /**
     * Tworzy edytowalne zadanie podrzędne dla wskazanego zadania głównego.
     */
    void addSubtask(std::uint64_t mainTaskId);

    /**
     * Edytuje nazwę zadania głównego albo podrzędnego.
     */
    void editTaskTitle(std::uint64_t taskId, bool isMainTask);

    /**
     * Wyświetla prompt lub opis, a po zatwierdzeniu zapisuje nową treść.
     */
    void editSubtaskText(std::uint64_t subtaskId, bool isPrompt);

    /**
     * Pyta o potwierdzenie i trwale usuwa wskazane zadanie.
     */
    void confirmAndDelete(const ControlAction& action);

    /**
     * Tworzy przycisk akcji z identyfikatorem rozpoznawanym przez widok.
     */
    HWND createActionButton(
        const std::wstring& title,
        int x,
        int y,
        int width,
        int height,
        ActionKind kind,
        std::uint64_t taskId,
        bool ownerDraw = false);

    /**
     * Dodaje kontrolkę do listy, której pozycje zmienia pasek przewijania.
     */
    void addPositionedControl(
        HWND control, int x, int y, int width, int height);

    /**
     * Zwalnia kontrolki wierszy i mapy akcji przed ich ponownym utworzeniem.
     */
    void destroyTaskControls();

    /**
     * Zwraca identyfikator kontrolki mieszczący się w komunikacie WM_COMMAND.
     */
    int allocateControlId();

    /**
     * Mierzy wysokość zawijanego tekstu w aktualnej szerokości wiersza.
     */
    int measureTextHeight(
        const std::wstring& text, int width, bool mainTask) const;

    /**
     * Mierzy szerokość nazwy zadania w czcionce używanej przez widok.
     */
    int measureTextWidth(const std::wstring& text, bool mainTask) const;

    /**
     * Zapisuje błąd do logu i pokazuje jego treść użytkownikowi.
     */
    void showError(const std::string& error);

    TodoManager& manager_;
    HINSTANCE instance_ = nullptr;
    HWND parent_ = nullptr;
    HWND addMainTaskButton_ = nullptr;
    HWND scrollBar_ = nullptr;
    HWND toolTip_ = nullptr;
    HFONT addSubtaskFont_ = nullptr;
    bool visible_ = false;
    int scrollPosition_ = 0;
    int contentHeight_ = 0;
    int nextControlId_ = 2000;
    std::vector<TodoTask> tasks_;
    std::vector<HWND> taskControls_;
    std::vector<ControlPosition> controlPositions_;
    std::unordered_map<int, ControlAction> controlActions_;
    std::unordered_map<int, MainCheckboxInfo> mainCheckboxes_;
    std::unordered_map<int, SubtaskCheckboxInfo> subtaskCheckboxes_;
    std::vector<HWND> toolTipControls_;
    std::string status_;
};
