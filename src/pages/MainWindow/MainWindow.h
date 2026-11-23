#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "DailySentencePicker.h"
#include "CandleGapsPage.h"
#include "ReportsPage.h"
#include "SignalsPage.h"
#include "TransactionsPage.h"
#include "StockPriceMonitor.h"
#include "TodoManager.hpp"
#include "TodoScheduler.hpp"
#include "TodoView.hpp"
#include "ViewRegistry.hpp"

#ifdef ENABLE_IBKR_TWS_API
#include "IBGatewayClient.h"
#endif

class MainWindow {
public:
    /**
     * Łączy widok To-do i harmonogram ze wspólnym managerem oraz rejestrem.
     */
    MainWindow();

    /**
     * Zwalnia czcionkę używaną przez przyciski głównego okna.
     */
    ~MainWindow();

    /**
     * Rejestruje i tworzy główne okno, a następnie je wyświetla.
     */
    bool create(HINSTANCE instance, int showCommand);

    /**
     * Obsługuje komunikaty Windows aż do zamknięcia aplikacji.
     */
    int run();

private:
    /**
     * Przekazuje komunikaty do obiektu MainWindow przypisanego do okna.
     */
    static LRESULT CALLBACK windowProcedure(
        HWND window, UINT message, WPARAM wParam, LPARAM lParam);

    /**
     * Obsługuje komunikat po odnalezieniu obiektu przez procedurę okna.
     */
    LRESULT handleMessage(UINT message, WPARAM wParam, LPARAM lParam);

    /**
     * Tworzy widoki i uruchamia istniejące oraz nowe zadania w tle.
     * @return true, jeśli wszystkie usługi wystartowały; w przeciwnym razie false.
     */
    bool onCreate();

    /**
     * Rejestruje widoki oraz tworzy ich przyciski z jednej wspólnej listy.
     * @return true, jeśli wszystkie przyciski powstały poprawnie.
     */
    bool createNavigationButtons();

    /**
     * Dopasowuje położenie przycisków widoków do aktualnego rozmiaru okna.
     */
    void positionNavigationButtons();

    /**
     * Przełącza aplikację na wybrany widok lub wraca do strony głównej.
     */
    void onNavigationClicked(int commandId);

    /**
     * Zamyka wszystkie strony przed otwarciem kolejnej.
     */
    void closeAllViews();

    /**
     * Otwiera stronę przypisaną do identyfikatora rejestru.
     */
    bool openView(int commandId);

    /**
     * Zwraca identyfikator aktualnie otwartego widoku lub zero.
     */
    int currentViewId() const;

    /**
     * Ustawia etykiety nawigacji zgodnie z aktualnie otwartym widokiem.
     */
    void updateNavigationButtons(int activeCommandId);

    /**
     * Pobiera sentencję w osobnym wątku, aby nie blokować okna aplikacji.
     * @throws std::system_error, jeśli system nie może utworzyć nowego wątku.
     */
    void startDailySentenceRequest();

    /**
     * Rysuje stronę główną lub raporty na podstawie bieżącego stanu aplikacji.
     */
    void onPaint();

    /**
     * Zatrzymuje zadania w tle i kończy pętlę komunikatów Windows.
     */
    void onDestroy();

#ifdef ENABLE_IBKR_TWS_API
    /**
     * Tworzy menu z osobnymi pozycjami dla środowisk Paper i Live.
     * @return true, jeśli menu utworzono poprawnie.
     */
    bool createIbkrMenu();

    /**
     * Rozpoczyna połączenie z wybranym portem standardowym Gateway.
     * @param port Port środowiska Paper albo Live.
     */
    void connectToIbkr(int port);

    /**
     * Aktualizuje tytuł okna i dostępność poleceń menu na podstawie statusu.
     */
    void updateIbkrStatus();

    /**
     * Włącza lub wyłącza polecenia menu zgodnie z bieżącym stanem połączenia.
     */
    void updateIbkrMenu();
#endif

    HINSTANCE instance_ = nullptr;
    HWND handle_ = nullptr;
    int showCommand_ = SW_SHOW;
    std::vector<HWND> navigationButtons_;
    HFONT navigationButtonFont_ = nullptr;
    int navigationButtonFontHeight_ = 0;
    std::mutex dailySentenceMutex_;
    std::string dailySentence_ = "Pobieranie sentencji dnia...";
    std::thread dailySentenceThread_;
    DailySentencePicker dailySentencePicker_;
    StockPriceMonitor candleMonitor_;
    TodoManager todoManager_;
    ViewRegistry viewRegistry_;
    TodoView todoView_;
    TodoScheduler todoScheduler_;
    ReportsPage reportsPage_;
    CandleGapsPage candleGapsPage_;
    SignalsPage signalsPage_;
    TransactionsPage transactionsPage_;
#ifdef ENABLE_IBKR_TWS_API
    HMENU ibkrMenu_ = nullptr;
    std::string lastIbkrErrorShown_;
    IBGatewayClient ibkrClient_;
#endif
};
