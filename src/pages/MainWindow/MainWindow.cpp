#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "DailySentencePicker.h"
#include "ErrorLogger.h"
#include "MainWindow.h"
#include "MainWindowDisplay.h"
#include <array>
#include <cstddef>
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
constexpr int ibkrCandlesButtonId = 1006;
constexpr int candleComparisonButtonId = 1007;
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
#ifdef ENABLE_IBKR_TWS_API
    ibkrClient_.setStatusChangedCallback({});
    ibkrClient_.setSignalSavedCallback({});
    ibkrClient_.disconnect();
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
            reportsPage_.positionControls();
            ibkrCandlesPage_.positionControls();
            candleComparisonPage_.positionControls();
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
            if (ibkrCandlesPage_.handleCommand(
                    LOWORD(wParam), HIWORD(wParam))) {
                return 0;
            }
            if (candleComparisonPage_.handleCommand(
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
            if ((HIWORD(wParam) == 0 || HIWORD(wParam) == BN_CLICKED) &&
                (viewRegistry_.findByCommandId(LOWORD(wParam)) != nullptr ||
                 LOWORD(wParam) == ibkrCandlesButtonId ||
                 LOWORD(wParam) == candleComparisonButtonId)) {
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
            if (currentViewId() == 0) {
                InvalidateRect(handle_, nullptr, TRUE);
            }
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
    ibkrClient_.setSignalSavedCallback([this](std::size_t signalCount) {
        return candleMonitor_.playSavedSignalSounds(signalCount);
    });
#endif
    if (!createMainMenu()) {
        showErrorMessage(
            handle_, L"Nie udało się utworzyć menu aplikacji.");
        return false;
    }
    if (!reportsPage_.create(instance_, handle_)) {
        showErrorMessage(
            handle_, L"Nie udało się utworzyć przycisków raportu.");
        return false;
    }
    if (!ibkrCandlesPage_.create(instance_, handle_)) {
        showErrorMessage(
            handle_, L"Nie udało się utworzyć widoku świec IBKR.");
        return false;
    }
    if (!candleComparisonPage_.create(instance_, handle_)) {
        showErrorMessage(
            handle_, L"Nie udało się utworzyć widoku zestawienia świec.");
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

    reportsPage_.positionControls();
    ibkrCandlesPage_.positionControls();
    candleComparisonPage_.positionControls();
    candleGapsPage_.positionControls();
    signalsPage_.positionControls();
    transactionsPage_.positionControls();
    todoView_.positionControls();

    if (!candleMonitor_.start(handle_)) {
        showErrorMessage(
            handle_, L"Nie udało się uruchomić powiadomień sygnałów.");
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

/**
 * Rejestruje dotychczasowe widoki i tworzy menu z ich pozycjami.
 */
bool MainWindow::createMainMenu() {
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

    HMENU applicationMenu = CreateMenu();
    if (applicationMenu == nullptr) {
        return false;
    }

#ifdef ENABLE_IBKR_TWS_API
    HMENU connectionMenu = CreatePopupMenu();
    HMENU toolsMenu = CreatePopupMenu();
    if (connectionMenu == nullptr || toolsMenu == nullptr) {
        if (connectionMenu != nullptr) {
            DestroyMenu(connectionMenu);
        }
        if (toolsMenu != nullptr) {
            DestroyMenu(toolsMenu);
        }
        DestroyMenu(applicationMenu);
        return false;
    }

    const bool ibkrCommandsCreated =
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
            disconnectIbkrCommandId, L"Rozłącz") &&
        AppendMenuW(
            toolsMenu, MF_STRING,
            synchronizeIbkrHistoryCommandId, L"Synchronizuj");
    if (!ibkrCommandsCreated ||
        !AppendMenuW(
            applicationMenu, MF_POPUP,
            reinterpret_cast<UINT_PTR>(connectionMenu), L"IB Gateway")) {
        DestroyMenu(connectionMenu);
        DestroyMenu(toolsMenu);
        DestroyMenu(applicationMenu);
        return false;
    }
    if (!AppendMenuW(
            applicationMenu, MF_POPUP,
            reinterpret_cast<UINT_PTR>(toolsMenu), L"Narzędzia")) {
        DestroyMenu(toolsMenu);
        DestroyMenu(applicationMenu);
        return false;
    }
#endif

    HMENU viewsMenu = CreatePopupMenu();
    if (viewsMenu == nullptr) {
        DestroyMenu(applicationMenu);
        return false;
    }
    const std::array<std::pair<int, const wchar_t*>, 7> menuItems{{
        {reportsButtonId, L"Raporty"},
        {candleGapsButtonId, L"Luki świec"},
        {signalsButtonId, L"Sygnały"},
        {transactionsButtonId, L"Transakcje"},
        {todoListButtonId, L"Todo lista"},
        {ibkrCandlesButtonId, L"Świece"},
        {candleComparisonButtonId, L"Zestawienie świec"},
    }};
    for (const auto& [commandId, label] : menuItems) {
        if (!AppendMenuW(viewsMenu, MF_STRING, commandId, label)) {
            DestroyMenu(viewsMenu);
            DestroyMenu(applicationMenu);
            return false;
        }
    }
    if (!AppendMenuW(
            applicationMenu, MF_POPUP,
            reinterpret_cast<UINT_PTR>(viewsMenu), L"Widoki")) {
        DestroyMenu(viewsMenu);
        DestroyMenu(applicationMenu);
        return false;
    }
    if (!SetMenu(handle_, applicationMenu)) {
        DestroyMenu(applicationMenu);
        return false;
    }

    mainMenu_ = applicationMenu;
    DrawMenuBar(handle_);
#ifdef ENABLE_IBKR_TWS_API
    updateIbkrMenu();
    updateIbkrStatus();
#endif
    return true;
}

/**
 * Przełącza wybrany widok; ponowne kliknięcie wraca do strony głównej.
 */
void MainWindow::onNavigationClicked(int commandId) {
    const int activeViewId = currentViewId();
    if (activeViewId == commandId) {
        closeAllViews();
    } else {
        closeAllViews();
        if (!openView(commandId)) {
            showErrorMessage(
                handle_, L"Nie udało się otworzyć wybranego widoku.");
            return;
        }
    }
    InvalidateRect(handle_, nullptr, TRUE);
}

/**
 * Zamyka widoki, aby jednocześnie widoczna była najwyżej jedna strona.
 */
void MainWindow::closeAllViews() {
    reportsPage_.close();
    ibkrCandlesPage_.close();
    candleComparisonPage_.close();
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
    } else if (commandId == ibkrCandlesButtonId) {
        ibkrCandlesPage_.open();
    } else if (commandId == candleComparisonButtonId) {
        candleComparisonPage_.open();
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
    if (ibkrCandlesPage_.isVisible()) {
        return ibkrCandlesButtonId;
    }
    if (candleComparisonPage_.isVisible()) {
        return candleComparisonButtonId;
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
 * Rysuje zawartość okna z bieżącymi danymi aplikacji.
 */
void MainWindow::onPaint() {
    PAINTSTRUCT paint{};
    HDC deviceContext = BeginPaint(handle_, &paint);
    RECT clientArea{};
    GetClientRect(handle_, &clientArea);
    if (reportsPage_.isVisible()) {
        reportsPage_.draw(deviceContext, clientArea);
    } else if (ibkrCandlesPage_.isVisible()) {
        ibkrCandlesPage_.draw(deviceContext, clientArea);
    } else if (candleComparisonPage_.isVisible()) {
        candleComparisonPage_.draw(deviceContext, clientArea);
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
#ifdef ENABLE_IBKR_TWS_API
    ibkrClient_.setStatusChangedCallback({});
    ibkrClient_.setSignalSavedCallback({});
    ibkrClient_.disconnect();
#endif
    candleMonitor_.stop();
    if (mainMenu_ != nullptr) {
        if (!SetMenu(handle_, nullptr)) {
            ErrorLogger::warning(
                "Nie udało się odłączyć menu aplikacji od okna.");
        }
        DestroyMenu(mainMenu_);
        mainMenu_ = nullptr;
    }
    if (dailySentenceThread_.joinable()) {
        dailySentenceThread_.join();
    }
    PostQuitMessage(0);
}

#ifdef ENABLE_IBKR_TWS_API
// Front
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
 * Pokazuje status API w tytule i odświeża pulpit tylko, gdy jest widoczny.
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
    if (currentViewId() == 0) {
        InvalidateRect(handle_, nullptr, TRUE);
    }
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
    if (mainMenu_ == nullptr) {
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
    HMENU connectionMenu = GetSubMenu(mainMenu_, 0);
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

    HMENU toolsMenu = GetSubMenu(mainMenu_, 1);
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
