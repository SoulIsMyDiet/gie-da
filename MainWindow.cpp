#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "AudioSignal.h"
#include "MainWindow.h"
#include "MainWindowDisplay.h"
#include "PriceHistoryClient.h"

#include <algorithm>

namespace {

constexpr char windowClassName[] = "HelloWorldWindow";
constexpr UINT_PTR clockTimerId = 1;
constexpr UINT forexUpdateMessage = WM_APP + 1;
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
 * Routes window, timer, paint, and Forex update messages to their handlers.
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
            return 0;
        case WM_COMMAND:
            if (LOWORD(wParam) == reportsButtonId &&
                HIWORD(wParam) == BN_CLICKED) {
                onReportsClicked();
                return 0;
            }
            break;
        case forexUpdateMessage:
            InvalidateRect(handle_, nullptr, TRUE);
            if (wParam != 0 && !AudioSignal::play()) {
                MessageBoxA(handle_, "Windows could not play the audio signal.",
                            "Audio error", MB_ICONERROR);
            }
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
 * Starts background price updates and the one-second clock timer.
 */
bool MainWindow::onCreate() {
    if (!createReportsButton()) {
        MessageBoxA(handle_, "Could not create the Reports button.",
                    "Error", MB_ICONERROR);
        return false;
    }

    if (!forexMonitor_.start(handle_)) {
        MessageBoxA(handle_, "Could not start the Forex update service.",
                    "Error", MB_ICONERROR);
        return false;
    }

    if (SetTimer(handle_, clockTimerId, 1000, nullptr) == 0) {
        MessageBoxA(handle_, "Could not start the clock timer.",
                    "Error", MB_ICONERROR);
        forexMonitor_.stop();
        return false;
    }
    return true;
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
 * Otwiera stronę raportów i odświeża zawartość głównego okna.
 */
void MainWindow::onReportsClicked() {
    reportsPageVisible_ = true;
    FILETIME clickTime{};
    GetSystemTimeAsFileTime(&clickTime);
    ULARGE_INTEGER fileTimeValue{};
    fileTimeValue.LowPart = clickTime.dwLowDateTime;
    fileTimeValue.HighPart = clickTime.dwHighDateTime;
    constexpr unsigned long long windowsToUnixEpochMicroseconds =
        11644473600000000ULL;

    if (fileTimeValue.QuadPart / 10 < windowsToUnixEpochMicroseconds) {
        reportsStatus_ = "Nie można ustalić czasu kliknięcia.";
        reportEntries_.clear();
        InvalidateRect(handle_, nullptr, TRUE);
        return;
    }

    const unsigned long long clickTimeMicroseconds =
        fileTimeValue.QuadPart / 10 - windowsToUnixEpochMicroseconds;
    std::vector<PriceReportEntry> receivedEntries;
    std::string error;
    if (!PriceHistoryClient::fetchRecentPrices(
            clickTimeMicroseconds, receivedEntries, error)) {
        reportsStatus_ = std::move(error);
        reportEntries_.clear();
    } else {
        reportEntries_ = std::move(receivedEntries);
        reportsStatus_.clear();
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
 * Draws the window contents with the latest status from the Forex monitor.
 */
void MainWindow::onPaint() {
    PAINTSTRUCT paint{};
    HDC deviceContext = BeginPaint(handle_, &paint);
    RECT clientArea{};
    GetClientRect(handle_, &clientArea);
    clientArea.bottom = std::max(
        clientArea.top,
        clientArea.bottom - reportsButtonHeight - reportsButtonBottomMargin * 2);
    if (reportsPageVisible_) {
        MainWindowDisplay::drawReportsPage(
            deviceContext, clientArea, reportEntries_, reportsStatus_);
    } else {
        MainWindowDisplay::draw(deviceContext, clientArea, forexMonitor_.snapshot());
    }
    EndPaint(handle_, &paint);
}

/**
 * Stops the Forex worker, releases the clock timer, and exits the message loop.
 */
void MainWindow::onDestroy() {
    KillTimer(handle_, clockTimerId);
    forexMonitor_.stop();
    PostQuitMessage(0);
}
