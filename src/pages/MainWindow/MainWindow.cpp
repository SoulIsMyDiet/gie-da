#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "DailySentencePicker.h"
#include "ErrorLogger.h"
#include "MainWindow.h"
#include "MainWindowDisplay.h"
#include "../WindowScale.h"

#include <algorithm>
#include <array>
#include <exception>
#include <iomanip>
#include <limits>
#include <sstream>
#include <string>
#include <system_error>
#include <utility>

namespace {

constexpr wchar_t windowClassName[] = L"SignalReportWindow";
constexpr UINT candleUpdateMessage = WM_APP + 1;
constexpr int reportsButtonId = 1001;
constexpr int candleGapsButtonId = 1002;
constexpr int signalsButtonId = 1003;
constexpr int transactionsButtonId = 1004;
constexpr int todoListButtonId = 1005;
constexpr int reportsButtonWidth = 140;
constexpr int reportsButtonHeight = 36;
constexpr int reportsButtonGap = 12;
constexpr int reportsButtonBottomMargin = 16;
constexpr int initialWindowWidth = 1600;
constexpr int initialWindowHeight = 900;
#ifdef ENABLE_IBKR_TWS_API
constexpr UINT ibkrStatusMessage = WM_APP + 2;
constexpr int connectPaperCommandId = 1501;
constexpr int connectLiveCommandId = 1502;
constexpr int disconnectIbkrCommandId = 1503;
constexpr int synchronizeIbkrHistoryCommandId = 1504;
constexpr int ibkrPaperPort = 4002;
constexpr int ibkrLivePort = 4001;
#endif

/**
 * Zapisuje błąd do pliku i pokazuje go użytkownikowi w standardowym oknie.
 */
void showErrorMessage(HWND owner, const wchar_t* message) {
    ErrorLogger::error(message);
    MessageBoxW(owner, message, L"Błąd", MB_ICONERROR);
}

/**
 * Konwertuje nazwę widoku UTF-8 do tekstu Unicode przycisku Windows.
 */
bool convertUtf8ToWide(
    const std::string& text, std::wstring& convertedText) {
    if (text.empty()) {
        convertedText.clear();
        return true;
    }
    if (text.size() > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
        return false;
    }
    const int requiredCharacters = MultiByteToWideChar(
        CP_UTF8, MB_ERR_INVALID_CHARS, text.data(),
        static_cast<int>(text.size()), nullptr, 0);
    if (requiredCharacters == 0) {
        return false;
    }
    convertedText.resize(static_cast<std::size_t>(requiredCharacters));
    return MultiByteToWideChar(
               CP_UTF8, MB_ERR_INVALID_CHARS, text.data(),
               static_cast<int>(text.size()), convertedText.data(),
               requiredCharacters) == requiredCharacters;
}

} // namespace

/**
 * Tworzy widok To-do i harmonogram zależne od wspólnego managera.
 */
MainWindow::MainWindow()
    : todoView_(todoManager_),
      todoScheduler_(todoManager_, viewRegistry_) {}

MainWindow::~MainWindow() {
    if (navigationButtonFont_ != nullptr) {
        DeleteObject(navigationButtonFont_);
    }
#ifdef ENABLE_IBKR_TWS_API
    ibkrClient_.setStatusChangedCallback({});
    ibkrClient_.disconnect();
    if (ibkrMenu_ != nullptr) {
        DestroyMenu(ibkrMenu_);
    }
#endif
}

/**
 * Rejestruje klasę i tworzy okno powiększone o 25% względem poprzedniego rozmiaru.
 */
bool MainWindow::create(HINSTANCE instance, int showCommand) {
    instance_ = instance;
    showCommand_ = showCommand;

    WNDCLASSW windowClass{};
    windowClass.lpfnWndProc = windowProcedure;
    windowClass.hInstance = instance_;
    windowClass.lpszClassName = windowClassName;
    windowClass.hCursor = LoadCursor(nullptr, IDC_ARROW);
    windowClass.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);

    if (!RegisterClassW(&windowClass)) {
        showErrorMessage(
            nullptr, L"Nie udało się zarejestrować klasy okna.");
        return false;
    }

    handle_ = CreateWindowExW(
        0,
        windowClassName,
        L"Raport sygnałów",
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT,
        initialWindowWidth, initialWindowHeight,
        nullptr, nullptr, instance_, this);
    if (handle_ == nullptr) {
        showErrorMessage(nullptr, L"Nie udało się utworzyć okna.");
        return false;
    }

    ShowWindow(handle_, showCommand_);
    UpdateWindow(handle_);
    return true;
}

/**
 * Obsługuje komunikaty systemu i zwraca kod zakończenia aplikacji.
 */
int MainWindow::run() {
    MSG message{};
    int result = 0;
    while ((result = GetMessageA(&message, nullptr, 0, 0)) > 0) {
        TranslateMessage(&message);
        DispatchMessageA(&message);
    }

    if (result == -1) {
        ErrorLogger::error(
            "Nie udało się pobrać komunikatu okna. Kod Win32: " +
            std::to_string(GetLastError()) + ".");
        return 1;
    }
    return static_cast<int>(message.wParam);
}

/**
 * Łączy okno z obiektem MainWindow i bezpiecznie przekazuje mu komunikaty.
 */
LRESULT CALLBACK MainWindow::windowProcedure(
    HWND window, UINT message, WPARAM wParam, LPARAM lParam) {
    MainWindow* mainWindow = reinterpret_cast<MainWindow*>(
        GetWindowLongPtrW(window, GWLP_USERDATA));

    if (message == WM_NCCREATE) {
        const auto* createData = reinterpret_cast<const CREATESTRUCTW*>(lParam);
        mainWindow = static_cast<MainWindow*>(createData->lpCreateParams);
        mainWindow->handle_ = window;
        SetWindowLongPtrW(window, GWLP_USERDATA,
                          reinterpret_cast<LONG_PTR>(mainWindow));
    }

    if (mainWindow == nullptr) {
        return DefWindowProcW(window, message, wParam, lParam);
    }

    LRESULT result = 0;
    try {
        result = mainWindow->handleMessage(message, wParam, lParam);
    } catch (const std::exception& error) {
        ErrorLogger::error(
            "Nieobsłużony wyjątek podczas obsługi komunikatu okna: " +
            std::string(error.what()));
        result = message == WM_CREATE
            ? -1
            : DefWindowProcW(window, message, wParam, lParam);
    } catch (...) {
        ErrorLogger::error(
            "Nieznany wyjątek podczas obsługi komunikatu okna.");
        result = message == WM_CREATE
            ? -1
            : DefWindowProcW(window, message, wParam, lParam);
    }
    if (message == WM_NCDESTROY) {
        SetWindowLongPtrW(window, GWLP_USERDATA, 0);
    }
    return result;
}

/**
 * Przekazuje komunikaty okna, zegara, rysowania i aktualizacji świecy do obsługi.
 */
LRESULT MainWindow::handleMessage(UINT message, WPARAM wParam, LPARAM lParam) {
    switch (message) {
        case WM_CREATE:
            return onCreate() ? 0 : -1;
        case WM_SIZE:
            positionNavigationButtons();
            reportsPage_.positionControls();
            candleGapsPage_.positionControls();
            signalsPage_.positionControls();
            transactionsPage_.positionControls();
            todoView_.positionControls();
            return 0;
        case WM_COMMAND:
#ifdef ENABLE_IBKR_TWS_API
            if (HIWORD(wParam) == 0) {
                if (LOWORD(wParam) == connectPaperCommandId) {
                    connectToIbkr(ibkrPaperPort);
                    return 0;
                }
                if (LOWORD(wParam) == connectLiveCommandId) {
                    connectToIbkr(ibkrLivePort);
                    return 0;
                }
                if (LOWORD(wParam) == disconnectIbkrCommandId) {
                    ibkrClient_.disconnect();
                    updateIbkrStatus();
                    return 0;
                }
                if (LOWORD(wParam) == synchronizeIbkrHistoryCommandId) {
                    std::string error;
                    if (!ibkrClient_.requestHistorySync(error)) {
                        std::wstring wideError;
                        if (!convertUtf8ToWide(error, wideError)) {
                            wideError =
                                L"Nie udało się rozpocząć synchronizacji "
                                L"historii IBKR.";
                        }
                        showErrorMessage(handle_, wideError.c_str());
                    }
                    updateIbkrStatus();
                    return 0;
                }
            }
#endif
            if (reportsPage_.handleCommand(
                    LOWORD(wParam), HIWORD(wParam))) {
                return 0;
            }
            if (candleGapsPage_.handleCommand(
                    LOWORD(wParam), HIWORD(wParam))) {
                return 0;
            }
            if (signalsPage_.handleCommand(
                    LOWORD(wParam), HIWORD(wParam))) {
                return 0;
            }
            if (transactionsPage_.handleCommand(
                    LOWORD(wParam), HIWORD(wParam))) {
                return 0;
            }
            if (todoView_.handleCommand(
                    LOWORD(wParam), HIWORD(wParam))) {
                return 0;
            }
            if (HIWORD(wParam) == BN_CLICKED &&
                viewRegistry_.findByCommandId(LOWORD(wParam)) != nullptr) {
                onNavigationClicked(LOWORD(wParam));
                return 0;
            }
            break;
        case WM_VSCROLL:
            if (todoView_.handleScroll(wParam)) {
                return 0;
            }
            break;
        case WM_MOUSEWHEEL:
            if (todoView_.handleMouseWheel(
                    GET_WHEEL_DELTA_WPARAM(wParam))) {
                return 0;
            }
            break;
        case WM_SETCURSOR:
            if (todoView_.handleSetCursor(
                    reinterpret_cast<HWND>(wParam))) {
                return TRUE;
            }
            break;
        case WM_CTLCOLORSTATIC: {
            HBRUSH backgroundBrush = todoView_.handleControlColor(
                reinterpret_cast<HDC>(wParam),
                reinterpret_cast<HWND>(lParam));
            if (backgroundBrush != nullptr) {
                return reinterpret_cast<LRESULT>(backgroundBrush);
            }
            break;
        }
        case WM_DRAWITEM:
            if (todoView_.handleDrawItem(
                    reinterpret_cast<const DRAWITEMSTRUCT*>(lParam))) {
                return TRUE;
            }
            break;
        case candleUpdateMessage:
            InvalidateRect(handle_, nullptr, TRUE);
            return 0;
#ifdef ENABLE_IBKR_TWS_API
        case ibkrStatusMessage:
            updateIbkrStatus();
            return 0;
#endif
        case WM_PAINT:
            onPaint();
            return 0;
        case WM_DESTROY:
            onDestroy();
            return 0;
        default:
            return DefWindowProcW(handle_, message, wParam, lParam);
    }
    return DefWindowProcW(handle_, message, wParam, lParam);
}

/**
 * Tworzy widoki i uruchamia monitor, sentencję oraz harmonogram To-do.
 */
bool MainWindow::onCreate() {
#ifdef ENABLE_IBKR_TWS_API
    ibkrClient_.setStatusChangedCallback([this]() {
        if (handle_ != nullptr &&
            !PostMessageW(handle_, ibkrStatusMessage, 0, 0)) {
            ErrorLogger::warning(
                "Nie udało się odświeżyć statusu połączenia IBKR. Kod Win32: " +
                std::to_string(GetLastError()) + ".");
        }
    });
    if (!createIbkrMenu()) {
        showErrorMessage(
            handle_, L"Nie udało się utworzyć menu połączenia IB Gateway.");
        return false;
    }
#endif
    if (!createNavigationButtons()) {
        showErrorMessage(
            handle_, L"Nie udało się utworzyć przycisków nawigacji.");
        return false;
    }
    if (!reportsPage_.create(instance_, handle_)) {
        showErrorMessage(
            handle_, L"Nie udało się utworzyć przycisków raportu.");
        return false;
    }
    if (!candleGapsPage_.create(instance_, handle_)) {
        showErrorMessage(
            handle_, L"Nie udało się utworzyć widoku brakujących świec.");
        return false;
    }
    if (!signalsPage_.create(instance_, handle_)) {
        showErrorMessage(
            handle_, L"Nie udało się utworzyć widoku sygnałów.");
        return false;
    }
    if (!transactionsPage_.create(instance_, handle_)) {
        showErrorMessage(
            handle_, L"Nie udało się utworzyć widoku transakcji.");
        return false;
    }
    if (!todoView_.create(instance_, handle_)) {
        showErrorMessage(
            handle_, L"Nie udało się utworzyć widoku To-do List.");
        return false;
    }

    positionNavigationButtons();
    reportsPage_.positionControls();
    candleGapsPage_.positionControls();
    signalsPage_.positionControls();
    transactionsPage_.positionControls();
    todoView_.positionControls();

    if (!candleMonitor_.start(handle_)) {
        showErrorMessage(
            handle_, L"Nie udało się uruchomić pobierania świecy.");
        return false;
    }

    try {
        startDailySentenceRequest();
    } catch (const std::system_error& error) {
        ErrorLogger::error(
            "Nie udało się uruchomić pobierania sentencji: " +
            std::string(error.what()));
        candleMonitor_.stop();
        showErrorMessage(
            handle_, L"Nie udało się uruchomić pobierania sentencji.");
        return false;
    }

    std::string schedulerError;
    if (!todoScheduler_.start(schedulerError)) {
        ErrorLogger::error(schedulerError);
        candleMonitor_.stop();
        showErrorMessage(
            handle_, L"Nie udało się uruchomić harmonogramu zadań To-do.");
        return false;
    }
    return true;
}

/**
 * Pobiera sentencję w tle i odświeża okno po otrzymaniu wyniku.
 */
void MainWindow::startDailySentenceRequest() {
    dailySentenceThread_ = std::thread([this]() {
        std::string sentence;
        std::string error;
        bool succeeded = false;
        try {
            succeeded =
                dailySentencePicker_.fetchRandomSentence(sentence, error);
        } catch (const std::exception& exception) {
            error = "Nieobsłużony wyjątek podczas pobierania sentencji: " +
                std::string(exception.what());
        } catch (...) {
            error = "Nieznany wyjątek podczas pobierania sentencji.";
        }
        if (!succeeded) {
            ErrorLogger::error(error);
        }
        {
            const std::lock_guard<std::mutex> lock(dailySentenceMutex_);
            dailySentence_ = succeeded
                ? std::move(sentence)
                : "Nie udało się pobrać sentencji: " + error;
        }
        InvalidateRect(handle_, nullptr, TRUE);
    });
}

// mix
/**
 * Rejestruje wszystkie samodzielne widoki i tworzy ich przyciski nawigacji.
 */
bool MainWindow::createNavigationButtons() {
    const std::array<ViewDescriptor, 5> availableViews{{
        {reportsButtonId, "Raporty"},
        {candleGapsButtonId, "Luki świec"},
        {signalsButtonId, "Sygnały"},
        {transactionsButtonId, "Transakcje"},
        {todoListButtonId, "To-do List"},
    }};
    std::string error;
    for (const ViewDescriptor& view : availableViews) {
        if (!viewRegistry_.registerView(
                view.commandId, view.name, error)) {
            ErrorLogger::error(error);
            return false;
        }
    }

    navigationButtons_.reserve(viewRegistry_.views().size());
    for (const ViewDescriptor& view : viewRegistry_.views()) {
        std::wstring title;
        if (!convertUtf8ToWide(view.name, title)) {
            ErrorLogger::error(
                "Nie udało się przekonwertować nazwy widoku: " + view.name);
            return false;
        }
        HWND button = CreateWindowExW(
            0, L"BUTTON", title.c_str(),
            WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
            0, 0, reportsButtonWidth, reportsButtonHeight,
            handle_,
            reinterpret_cast<HMENU>(
                static_cast<INT_PTR>(view.commandId)),
            instance_, nullptr);
        if (button == nullptr) {
            return false;
        }
        navigationButtons_.push_back(button);
    }
    positionNavigationButtons();
    return true;
}

/**
 * Ustawia przyciski nawigacji; w To-do zostawia tylko powrót na stronę główną.
 */
void MainWindow::positionNavigationButtons() {
    const int allButtonCount =
        static_cast<int>(navigationButtons_.size());
    if (allButtonCount == 0) {
        return;
    }

    const std::vector<ViewDescriptor>& views = viewRegistry_.views();
    const bool showOnlyTodoHomeButton =
        currentViewId() == todoListButtonId;
    const int buttonCount = showOnlyTodoHomeButton ? 1 : allButtonCount;

    RECT clientArea{};
    GetClientRect(handle_, &clientArea);
    const int clientWidth = clientArea.right - clientArea.left;
    const int clientHeight = clientArea.bottom - clientArea.top;
    const int buttonGap = WindowScale::size(handle_, reportsButtonGap);
    const int buttonHeight = WindowScale::size(handle_, reportsButtonHeight);
    const int bottomMargin =
        WindowScale::size(handle_, reportsButtonBottomMargin);
    const int buttonBaseWidth = WindowScale::size(handle_, reportsButtonWidth);
    const int availableButtonWidth = std::max(
        1, (clientWidth - buttonGap * (buttonCount - 1)) / buttonCount);
    const int buttonWidth = std::min(buttonBaseWidth, availableButtonWidth);
    const int buttonsWidth =
        buttonWidth * buttonCount + buttonGap * (buttonCount - 1);
    const int visibleButtonHeight = std::min(buttonHeight, clientHeight);
    const int firstButtonX = std::max(0, (clientWidth - buttonsWidth) / 2);
    const int buttonY = std::max(
        0, clientHeight - visibleButtonHeight - bottomMargin);
    WindowScale::updateFont(
        handle_, navigationButtonFont_, navigationButtonFontHeight_, 14,
        navigationButtons_);

    int visibleButtonIndex = 0;
    for (int index = 0; index < allButtonCount; ++index) {
        const bool shouldShowButton =
            !showOnlyTodoHomeButton
            || (static_cast<std::size_t>(index) < views.size()
                && views[static_cast<std::size_t>(index)].commandId
                    == todoListButtonId);
        HWND button = navigationButtons_[static_cast<std::size_t>(index)];
        ShowWindow(button, shouldShowButton ? SW_SHOW : SW_HIDE);
        if (!shouldShowButton) {
            continue;
        }
        MoveWindow(
            button,
            firstButtonX + (buttonWidth + buttonGap) * visibleButtonIndex,
            buttonY, buttonWidth, visibleButtonHeight, TRUE);
        ++visibleButtonIndex;
    }
}

/**
 * Przełącza wybrany widok; ponowne kliknięcie wraca do strony głównej.
 */
void MainWindow::onNavigationClicked(int commandId) {
    const int activeViewId = currentViewId();
    if (activeViewId == commandId) {
        closeAllViews();
        updateNavigationButtons(0);
    } else {
        closeAllViews();
        if (!openView(commandId)) {
            showErrorMessage(
                handle_, L"Nie udało się otworzyć wybranego widoku.");
            return;
        }
        updateNavigationButtons(commandId);
    }
    positionNavigationButtons();
    InvalidateRect(handle_, nullptr, TRUE);
}

/**
 * Zamyka widoki, aby jednocześnie widoczna była najwyżej jedna strona.
 */
void MainWindow::closeAllViews() {
    reportsPage_.close();
    candleGapsPage_.close();
    signalsPage_.close();
    transactionsPage_.close();
    todoView_.close();
}

/**
 * Otwiera stronę odpowiadającą identyfikatorowi wspólnego rejestru.
 */
bool MainWindow::openView(int commandId) {
    if (commandId == reportsButtonId) {
        reportsPage_.open();
    } else if (commandId == candleGapsButtonId) {
        candleGapsPage_.open();
    } else if (commandId == signalsButtonId) {
        signalsPage_.open();
    } else if (commandId == transactionsButtonId) {
        transactionsPage_.open();
    } else if (commandId == todoListButtonId) {
        todoView_.open();
    } else {
        return false;
    }
    return true;
}

/**
 * Zwraca identyfikator aktualnie widocznego widoku lub zero dla pulpitu.
 */
int MainWindow::currentViewId() const {
    if (reportsPage_.isVisible()) {
        return reportsButtonId;
    }
    if (candleGapsPage_.isVisible()) {
        return candleGapsButtonId;
    }
    if (signalsPage_.isVisible()) {
        return signalsButtonId;
    }
    if (transactionsPage_.isVisible()) {
        return transactionsButtonId;
    }
    if (todoView_.isVisible()) {
        return todoListButtonId;
    }
    return 0;
}

/**
 * Ustawia nazwę aktywnego przycisku na „Strona główna”.
 */
void MainWindow::updateNavigationButtons(int activeCommandId) {
    const std::vector<ViewDescriptor>& views = viewRegistry_.views();
    const std::size_t count = std::min(
        views.size(), navigationButtons_.size());
    for (std::size_t index = 0; index < count; ++index) {
        if (views[index].commandId == activeCommandId) {
            SetWindowTextW(
                navigationButtons_[index], L"Strona główna");
            continue;
        }
        std::wstring title;
        if (!convertUtf8ToWide(views[index].name, title)) {
            ErrorLogger::error(
                "Nie udało się wyświetlić nazwy widoku: " +
                views[index].name);
            continue;
        }
        SetWindowTextW(navigationButtons_[index], title.c_str());
    }
}

/**
 * Rysuje zawartość okna z bieżącymi danymi aplikacji.
 */
void MainWindow::onPaint() {
    PAINTSTRUCT paint{};
    HDC deviceContext = BeginPaint(handle_, &paint);
    RECT clientArea{};
    GetClientRect(handle_, &clientArea);
    clientArea.bottom = std::max(
        clientArea.top,
        clientArea.bottom - WindowScale::size(
            handle_, reportsButtonHeight + reportsButtonBottomMargin * 2));
    if (reportsPage_.isVisible()) {
        reportsPage_.draw(deviceContext, clientArea);
    } else if (candleGapsPage_.isVisible()) {
        candleGapsPage_.draw(deviceContext, clientArea);
    } else if (signalsPage_.isVisible()) {
        signalsPage_.draw(deviceContext, clientArea);
    } else if (transactionsPage_.isVisible()) {
        transactionsPage_.draw(deviceContext, clientArea);
    } else if (todoView_.isVisible()) {
        todoView_.draw(deviceContext, clientArea);
    } else {
        std::string dailySentence;
        {
            const std::lock_guard<std::mutex> lock(dailySentenceMutex_);
            dailySentence = dailySentence_;
        }
        std::string oneMinuteCandle;
#ifdef ENABLE_IBKR_TWS_API
        const IBGatewayOneMinuteCandle candle =
            ibkrClient_.candleSnapshot();
        const IBGatewayQuoteSnapshot quote =
            ibkrClient_.quoteSnapshot();
        std::ostringstream marketText;
        if (candle.available) {
            marketText << "EUR/USD - świeca 1-minutowa | "
                       << candle.timestamp << "\n"
                       << "O: " << std::fixed << std::setprecision(5)
                       << candle.open << "  H: " << candle.high
                       << "  L: " << candle.low
                       << "  C: " << candle.close;
            if (candle.message !=
                    "Aktualizacja świecy EUR/USD 1-minutowej z IBKR." &&
                candle.message !=
                    "Ostatnia świeca EUR/USD 1-minutowa z IBKR.") {
                marketText << "\n" << candle.message;
            }
        } else {
            marketText << candle.message;
        }
        marketText << "\nBid: ";
        if (quote.bidAvailable) {
            marketText << std::fixed << std::setprecision(5) << quote.bid;
        } else {
            marketText << "oczekiwanie";
        }
        marketText << "  Ask: ";
        if (quote.askAvailable) {
            marketText << std::fixed << std::setprecision(5) << quote.ask;
        } else {
            marketText << "oczekiwanie";
        }
        if (!quote.bidAvailable || !quote.askAvailable || quote.delayed) {
            marketText << "\n" << quote.message;
        }
        oneMinuteCandle = marketText.str();
#else
        oneMinuteCandle =
            "Włącz ENABLE_IBKR_TWS_API, aby pobierać świece z IBKR.";
#endif
        MainWindowDisplay::draw(
            deviceContext, handle_, clientArea, dailySentence,
            oneMinuteCandle);
    }
    EndPaint(handle_, &paint);
}

/**
 * Zatrzymuje zadania w tle i kończy pętlę komunikatów Windows.
 */
void MainWindow::onDestroy() {
    todoScheduler_.stop();
    candleMonitor_.stop();
#ifdef ENABLE_IBKR_TWS_API
    ibkrClient_.setStatusChangedCallback({});
    ibkrClient_.disconnect();
    if (ibkrMenu_ != nullptr) {
        if (!SetMenu(handle_, nullptr)) {
            ErrorLogger::warning(
                "Nie udało się odłączyć menu IB Gateway od okna.");
        }
        DestroyMenu(ibkrMenu_);
        ibkrMenu_ = nullptr;
    }
#endif
    if (dailySentenceThread_.joinable()) {
        dailySentenceThread_.join();
    }
    PostQuitMessage(0);
}

#ifdef ENABLE_IBKR_TWS_API
// Front
/**
 * Tworzy menu z bezpiecznymi, standardowymi portami IB Gateway.
 */
bool MainWindow::createIbkrMenu() {
    HMENU mainMenu = CreateMenu();
    if (mainMenu == nullptr) {
        return false;
    }
    HMENU connectionMenu = CreatePopupMenu();
    HMENU toolsMenu = CreatePopupMenu();
    if (connectionMenu == nullptr || toolsMenu == nullptr) {
        if (connectionMenu != nullptr) {
            DestroyMenu(connectionMenu);
        }
        if (toolsMenu != nullptr) {
            DestroyMenu(toolsMenu);
        }
        DestroyMenu(mainMenu);
        return false;
    }

    const bool commandsCreated =
        AppendMenuW(
            connectionMenu, MF_STRING,
            connectPaperCommandId,
            L"Połącz - Paper (konto testowe, port 4002)") &&
        AppendMenuW(
            connectionMenu, MF_STRING,
            connectLiveCommandId,
            L"Połącz - Live (konto rzeczywiste, port 4001)") &&
        AppendMenuW(connectionMenu, MF_SEPARATOR, 0, nullptr) &&
        AppendMenuW(
            connectionMenu, MF_STRING,
            disconnectIbkrCommandId, L"Rozłącz");
    if (!commandsCreated ||
        !AppendMenuW(
            toolsMenu, MF_STRING,
            synchronizeIbkrHistoryCommandId, L"Synchronizuj")) {
        DestroyMenu(connectionMenu);
        DestroyMenu(toolsMenu);
        DestroyMenu(mainMenu);
        return false;
    }
    if (!AppendMenuW(
            mainMenu, MF_POPUP,
            reinterpret_cast<UINT_PTR>(connectionMenu), L"IB Gateway")) {
        DestroyMenu(connectionMenu);
        DestroyMenu(toolsMenu);
        DestroyMenu(mainMenu);
        return false;
    }
    if (!AppendMenuW(
            mainMenu, MF_POPUP,
            reinterpret_cast<UINT_PTR>(toolsMenu), L"Narzędzia")) {
        DestroyMenu(toolsMenu);
        DestroyMenu(mainMenu);
        return false;
    }
    if (!SetMenu(handle_, mainMenu)) {
        DestroyMenu(mainMenu);
        return false;
    }

    ibkrMenu_ = mainMenu;
    DrawMenuBar(handle_);
    updateIbkrMenu();
    updateIbkrStatus();
    return true;
}

/**
 * Rozpoczyna połączenie lokalne z portem Paper lub Live wybranym w menu.
 */
void MainWindow::connectToIbkr(int port) {
    std::string error;
    if (!ibkrClient_.connect("127.0.0.1", port, 1, error)) {
        const IBGatewayConnectionStatus connectionStatus = ibkrClient_.status();
        if (connectionStatus.state != IBGatewayConnectionState::Error ||
            connectionStatus.message != error) {
            std::wstring wideError;
            if (!convertUtf8ToWide(error, wideError)) {
                wideError =
                    L"Nie udało się rozpocząć połączenia z IB Gateway.";
            }
            showErrorMessage(handle_, wideError.c_str());
        }
    }
    updateIbkrStatus();
}

/**
 * Pokazuje status API w tytule okna i prezentuje błędy po stronie użytkownika.
 */
void MainWindow::updateIbkrStatus() {
    const IBGatewayConnectionStatus connectionStatus = ibkrClient_.status();
    std::wstring wideMessage;
    if (!convertUtf8ToWide(connectionStatus.message, wideMessage)) {
        wideMessage = L"Nie udało się odczytać statusu IB Gateway.";
    }
    const std::wstring title =
        std::wstring(L"Raport sygnałów - IB Gateway: ") + wideMessage;
    SetWindowTextW(handle_, title.c_str());
    InvalidateRect(handle_, nullptr, TRUE);
    updateIbkrMenu();

    if (connectionStatus.state == IBGatewayConnectionState::Error &&
        lastIbkrErrorShown_ != connectionStatus.message) {
        lastIbkrErrorShown_ = connectionStatus.message;
        MessageBoxW(
            handle_, wideMessage.c_str(),
            L"Błąd połączenia IB Gateway", MB_OK | MB_ICONERROR);
    } else if (connectionStatus.state != IBGatewayConnectionState::Error) {
        lastIbkrErrorShown_.clear();
    }
}

/**
 * Blokuje ponowne łączenie podczas aktywnej sesji i pozwala ją przerwać.
 */
void MainWindow::updateIbkrMenu() {
    if (ibkrMenu_ == nullptr) {
        return;
    }
    const IBGatewayConnectionState state = ibkrClient_.status().state;
    const bool canConnect =
        state == IBGatewayConnectionState::Disconnected ||
        state == IBGatewayConnectionState::Error;
    const bool canDisconnect =
        state == IBGatewayConnectionState::Connecting ||
        state == IBGatewayConnectionState::Connected ||
        state == IBGatewayConnectionState::Error;
    HMENU connectionMenu = GetSubMenu(ibkrMenu_, 0);
    if (connectionMenu == nullptr) {
        ErrorLogger::warning(
            "Nie można odnaleźć menu połączenia IB Gateway.");
        return;
    }
    EnableMenuItem(
        connectionMenu, connectPaperCommandId,
        MF_BYCOMMAND | (canConnect ? MF_ENABLED : MF_GRAYED));
    EnableMenuItem(
        connectionMenu, connectLiveCommandId,
        MF_BYCOMMAND | (canConnect ? MF_ENABLED : MF_GRAYED));
    EnableMenuItem(
        connectionMenu, disconnectIbkrCommandId,
        MF_BYCOMMAND | (canDisconnect ? MF_ENABLED : MF_GRAYED));

    HMENU toolsMenu = GetSubMenu(ibkrMenu_, 1);
    if (toolsMenu == nullptr) {
        ErrorLogger::warning(
            "Nie można odnaleźć menu Narzędzia.");
        return;
    }
    const bool canSynchronize =
        state == IBGatewayConnectionState::Connected &&
        !ibkrClient_.historySyncInProgress();
    EnableMenuItem(
        toolsMenu, synchronizeIbkrHistoryCommandId,
        MF_BYCOMMAND |
            (canSynchronize ? MF_ENABLED : MF_GRAYED));
}
#endif
