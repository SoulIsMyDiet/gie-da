#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "DailySentenceClient.h"
#include "MainWindow.h"
#include "MainWindowDisplay.h"

#include <algorithm>
#include <system_error>
#include <utility>

namespace {

constexpr char windowClassName[] = "HelloWorldWindow";
constexpr UINT_PTR clockTimerId = 1;
constexpr UINT candleUpdateMessage = WM_APP + 1;
constexpr int reportsButtonId = 1001;
constexpr int reportsButtonWidth = 128;
constexpr int reportsButtonHeight = 36;
constexpr int reportsButtonBottomMargin = 16;

} // namespace

/**
 * Registers the window class, creates the main window, and makes it visible.
 */
bool MainWindow::create(HINSTANCE instance, int showCommand) {
    instance_ = instance;
    showCommand_ = showCommand;

    WNDCLASSA windowClass{};
    windowClass.lpfnWndProc = windowProcedure;
    windowClass.hInstance = instance_;
    windowClass.lpszClassName = windowClassName;
    windowClass.hCursor = LoadCursor(nullptr, IDC_ARROW);
    windowClass.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);

    if (!RegisterClassA(&windowClass)) {
        MessageBoxA(nullptr, "Could not register the window class.",
                    "Error", MB_ICONERROR);
        return false;
    }

    handle_ = CreateWindowExA(
        0,
        windowClassName,
        "Hello World",
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT, 640, 360,
        nullptr, nullptr, instance_, this);
    if (handle_ == nullptr) {
        MessageBoxA(nullptr, "Could not create the window.", "Error", MB_ICONERROR);
        return false;
    }

    ShowWindow(handle_, showCommand_);
    UpdateWindow(handle_);
    return true;
}

/**
 * Dispatches queued messages and returns the application's exit code.
 */
int MainWindow::run() {
    MSG message{};
    int result = 0;
    while ((result = GetMessageA(&message, nullptr, 0, 0)) > 0) {
        TranslateMessage(&message);
        DispatchMessageA(&message);
    }

    return result == -1 ? 1 : static_cast<int>(message.wParam);
}

/**
 * Associates each created window with its MainWindow object and dispatches messages.
 */
LRESULT CALLBACK MainWindow::windowProcedure(
    HWND window, UINT message, WPARAM wParam, LPARAM lParam) {
    MainWindow* mainWindow = reinterpret_cast<MainWindow*>(
        GetWindowLongPtrA(window, GWLP_USERDATA));

    if (message == WM_NCCREATE) {
        const auto* createData = reinterpret_cast<const CREATESTRUCTA*>(lParam);
        mainWindow = static_cast<MainWindow*>(createData->lpCreateParams);
        mainWindow->handle_ = window;
        SetWindowLongPtrA(window, GWLP_USERDATA,
                          reinterpret_cast<LONG_PTR>(mainWindow));
    }

    if (mainWindow == nullptr) {
        return DefWindowProcA(window, message, wParam, lParam);
    }

    const LRESULT result = mainWindow->handleMessage(message, wParam, lParam);
    if (message == WM_NCDESTROY) {
        SetWindowLongPtrA(window, GWLP_USERDATA, 0);
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
        case WM_TIMER:
            if (wParam == clockTimerId) {
                onClockTimer();
            }
            return 0;
        case WM_SIZE:
            positionReportsButton();
            reportsPage_.positionControls();
            return 0;
        case WM_COMMAND:
            if (reportsPage_.handleCommand(
                    LOWORD(wParam), HIWORD(wParam))) {
                return 0;
            }
            if (LOWORD(wParam) == reportsButtonId &&
                HIWORD(wParam) == BN_CLICKED) {
                onReportsClicked();
                return 0;
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
            return DefWindowProcA(handle_, message, wParam, lParam);
    }
    return DefWindowProcA(handle_, message, wParam, lParam);
}

/**
 * Uruchamia monitor świecy, zegar oraz pobieranie sentencji dnia.
 */
bool MainWindow::onCreate() {
    if (!createReportsButton()) {
        MessageBoxA(handle_, "Could not create the Reports button.",
                    "Error", MB_ICONERROR);
        return false;
    }
    if (!reportsPage_.create(instance_, handle_)) {
        MessageBoxW(handle_, L"Nie udało się utworzyć przycisków raportu.",
                    L"Błąd", MB_ICONERROR);
        return false;
    }

    if (!candleMonitor_.start(handle_)) {
        MessageBoxW(handle_, L"Nie udało się uruchomić pobierania świecy.",
                    L"Błąd", MB_ICONERROR);
        return false;
    }

    if (SetTimer(handle_, clockTimerId, 1000, nullptr) == 0) {
        MessageBoxA(handle_, "Could not start the clock timer.",
                    "Error", MB_ICONERROR);
        candleMonitor_.stop();
        return false;
    }

    try {
        startDailySentenceRequest();
    } catch (const std::system_error&) {
        KillTimer(handle_, clockTimerId);
        candleMonitor_.stop();
        MessageBoxW(
            handle_, L"Nie udało się uruchomić pobierania sentencji.",
            L"Błąd", MB_ICONERROR);
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
        const bool succeeded =
            DailySentenceClient::fetchDailySentence(sentence, error);
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
 * Tworzy przycisk na dole okna; jego akcja zostanie podłączona później.
 */
bool MainWindow::createReportsButton() {
    reportsButton_ = CreateWindowExA(
        0,
        "BUTTON",
        "Raporty",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, reportsButtonWidth, reportsButtonHeight,
        handle_,
        reinterpret_cast<HMENU>(static_cast<INT_PTR>(reportsButtonId)),
        instance_,
        nullptr);
    if (reportsButton_ == nullptr) {
        return false;
    }

    positionReportsButton();
    return true;
}

/**
 * Ustawia przycisk pośrodku dolnej krawędzi i ogranicza go do okna.
 */
void MainWindow::positionReportsButton() {
    if (reportsButton_ == nullptr) {
        return;
    }

    RECT clientArea{};
    GetClientRect(handle_, &clientArea);
    const int clientWidth = clientArea.right - clientArea.left;
    const int clientHeight = clientArea.bottom - clientArea.top;
    const int buttonWidth = std::min(reportsButtonWidth, clientWidth);
    const int buttonHeight = std::min(reportsButtonHeight, clientHeight);
    const int buttonX = (clientWidth - buttonWidth) / 2;
    const int buttonY = std::max(
        0, clientHeight - buttonHeight - reportsButtonBottomMargin);

    MoveWindow(
        reportsButton_, buttonX, buttonY, buttonWidth, buttonHeight, TRUE);
}

/**
 * Przełącza stronę raportów i stronę główną.
 */
void MainWindow::onReportsClicked() {
    if (reportsPage_.isVisible()) {
        reportsPage_.close();
        SetWindowTextA(reportsButton_, "Raporty");
    } else {
        reportsPage_.open();
        SetWindowTextA(reportsButton_, "Strona główna");
    }
    InvalidateRect(handle_, nullptr, TRUE);
}

/**
 * Redraws the clock when its minute changes.
 */
void MainWindow::onClockTimer() {
    SYSTEMTIME localTime{};
    GetLocalTime(&localTime);
    if (!hasPreviousTime_) {
        previousHour_ = localTime.wHour;
        previousMinute_ = localTime.wMinute;
        hasPreviousTime_ = true;
        return;
    }

    if (localTime.wHour == previousHour_ && localTime.wMinute == previousMinute_) {
        return;
    }

    previousHour_ = localTime.wHour;
    previousMinute_ = localTime.wMinute;
    InvalidateRect(handle_, nullptr, TRUE);
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
        clientArea.bottom - reportsButtonHeight - reportsButtonBottomMargin * 2);
    if (reportsPage_.isVisible()) {
        reportsPage_.draw(deviceContext, clientArea);
    } else {
        std::string dailySentence;
        {
            const std::lock_guard<std::mutex> lock(dailySentenceMutex_);
            dailySentence = dailySentence_;
        }
        MainWindowDisplay::draw(
            deviceContext, clientArea, candleMonitor_.snapshot(), dailySentence);
    }
    EndPaint(handle_, &paint);
}

/**
 * Zatrzymuje zadania w tle, zwalnia zegar i kończy pętlę komunikatów.
 */
void MainWindow::onDestroy() {
    KillTimer(handle_, clockTimerId);
    candleMonitor_.stop();
    if (dailySentenceThread_.joinable()) {
        dailySentenceThread_.join();
    }
    PostQuitMessage(0);
}
