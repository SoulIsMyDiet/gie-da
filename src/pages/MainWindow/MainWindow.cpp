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
#include <limits>
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
 * Ustawia przyciski z rejestru pośrodku dolnej krawędzi okna.
 */
void MainWindow::positionNavigationButtons() {
    const int buttonCount =
        static_cast<int>(navigationButtons_.size());
    if (buttonCount == 0) {
        return;
    }

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

    for (int index = 0; index < buttonCount; ++index) {
        MoveWindow(
            navigationButtons_[static_cast<std::size_t>(index)],
            firstButtonX + (buttonWidth + buttonGap) * index,
            buttonY, buttonWidth, visibleButtonHeight, TRUE);
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
        MainWindowDisplay::draw(
            deviceContext, handle_, clientArea, dailySentence);
    }
    EndPaint(handle_, &paint);
}

/**
 * Zatrzymuje zadania w tle i kończy pętlę komunikatów Windows.
 */
void MainWindow::onDestroy() {
    todoScheduler_.stop();
    candleMonitor_.stop();
    if (dailySentenceThread_.joinable()) {
        dailySentenceThread_.join();
    }
    PostQuitMessage(0);
}
