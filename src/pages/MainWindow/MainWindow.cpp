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
constexpr int candleGapsButtonId = 1002;
constexpr int signalsButtonId = 1003;
constexpr int reportsButtonWidth = 140;
constexpr int reportsButtonHeight = 36;
constexpr int reportsButtonGap = 12;
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
            candleGapsPage_.positionControls();
            signalsPage_.positionControls();
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
            if (LOWORD(wParam) == reportsButtonId &&
                HIWORD(wParam) == BN_CLICKED) {
                onReportsClicked();
                return 0;
            }
            if (LOWORD(wParam) == candleGapsButtonId &&
                HIWORD(wParam) == BN_CLICKED) {
                onCandleGapsClicked();
                return 0;
            }
            if (LOWORD(wParam) == signalsButtonId &&
                HIWORD(wParam) == BN_CLICKED) {
                onSignalsClicked();
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
    if (!createCandleGapsButton()) {
        MessageBoxW(handle_, L"Nie udało się utworzyć przycisku brakujących świec.",
                    L"Błąd", MB_ICONERROR);
        return false;
    }
    if (!createSignalsButton()) {
        MessageBoxW(handle_, L"Nie udało się utworzyć przycisku sygnałów.",
                    L"Błąd", MB_ICONERROR);
        return false;
    }
    if (!reportsPage_.create(instance_, handle_)) {
        MessageBoxW(handle_, L"Nie udało się utworzyć przycisków raportu.",
                    L"Błąd", MB_ICONERROR);
        return false;
    }
    if (!candleGapsPage_.create(instance_, handle_)) {
        MessageBoxW(handle_, L"Nie udało się utworzyć widoku brakujących świec.",
                    L"Błąd", MB_ICONERROR);
        return false;
    }
    if (!signalsPage_.create(instance_, handle_)) {
        MessageBoxW(handle_, L"Nie udało się utworzyć widoku sygnałów.",
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
    reportsButton_ = CreateWindowExW(
        0,
        L"BUTTON",
        L"Raporty",
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
 * Tworzy przycisk „Luki świec” obok przycisku raportów.
 */
bool MainWindow::createCandleGapsButton() {
    candleGapsButton_ = CreateWindowExW(
        0,
        L"BUTTON",
        L"Luki świec",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, reportsButtonWidth, reportsButtonHeight,
        handle_,
        reinterpret_cast<HMENU>(static_cast<INT_PTR>(candleGapsButtonId)),
        instance_,
        nullptr);
    if (candleGapsButton_ == nullptr) {
        return false;
    }

    positionReportsButton();
    return true;
}

/**
 * Tworzy przycisk „Sygnały” obok pozostałych widoków tabel.
 */
bool MainWindow::createSignalsButton() {
    signalsButton_ = CreateWindowExW(
        0,
        L"BUTTON",
        L"Sygnały",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, reportsButtonWidth, reportsButtonHeight,
        handle_,
        reinterpret_cast<HMENU>(static_cast<INT_PTR>(signalsButtonId)),
        instance_,
        nullptr);
    if (signalsButton_ == nullptr) {
        return false;
    }

    positionReportsButton();
    return true;
}

/**
 * Ustawia trzy przyciski widoków obok siebie pośrodku dolnej krawędzi okna.
 */
void MainWindow::positionReportsButton() {
    if (reportsButton_ == nullptr && candleGapsButton_ == nullptr &&
        signalsButton_ == nullptr) {
        return;
    }

    RECT clientArea{};
    GetClientRect(handle_, &clientArea);
    const int clientWidth = clientArea.right - clientArea.left;
    const int clientHeight = clientArea.bottom - clientArea.top;
    const int availableButtonWidth =
        std::max(1, (clientWidth - reportsButtonGap * 2) / 3);
    const int buttonWidth = std::min(reportsButtonWidth, availableButtonWidth);
    const int buttonsWidth = buttonWidth * 3 + reportsButtonGap * 2;
    const int buttonHeight = std::min(reportsButtonHeight, clientHeight);
    const int firstButtonX = std::max(0, (clientWidth - buttonsWidth) / 2);
    const int buttonY = std::max(
        0, clientHeight - buttonHeight - reportsButtonBottomMargin);

    if (reportsButton_ != nullptr) {
        MoveWindow(
            reportsButton_, firstButtonX, buttonY,
            buttonWidth, buttonHeight, TRUE);
    }
    if (candleGapsButton_ != nullptr) {
        MoveWindow(
            candleGapsButton_,
            firstButtonX + buttonWidth + reportsButtonGap,
            buttonY, buttonWidth, buttonHeight, TRUE);
    }
    if (signalsButton_ != nullptr) {
        MoveWindow(
            signalsButton_,
            firstButtonX + (buttonWidth + reportsButtonGap) * 2,
            buttonY, buttonWidth, buttonHeight, TRUE);
    }
}

/**
 * Przełącza stronę raportów i stronę główną.
 */
void MainWindow::onReportsClicked() {
    if (reportsPage_.isVisible()) {
        reportsPage_.close();
        SetWindowTextW(reportsButton_, L"Raporty");
    } else {
        candleGapsPage_.close();
        signalsPage_.close();
        SetWindowTextW(candleGapsButton_, L"Luki świec");
        SetWindowTextW(signalsButton_, L"Sygnały");
        reportsPage_.open();
        SetWindowTextW(reportsButton_, L"Strona główna");
    }
    InvalidateRect(handle_, nullptr, TRUE);
}

/**
 * Przełącza główną część okna na tabelę brakujących świec.
 */
void MainWindow::onCandleGapsClicked() {
    if (candleGapsPage_.isVisible()) {
        candleGapsPage_.close();
        SetWindowTextW(candleGapsButton_, L"Luki świec");
    } else {
        reportsPage_.close();
        signalsPage_.close();
        SetWindowTextW(reportsButton_, L"Raporty");
        SetWindowTextW(signalsButton_, L"Sygnały");
        candleGapsPage_.open();
        SetWindowTextW(candleGapsButton_, L"Strona główna");
    }
    InvalidateRect(handle_, nullptr, TRUE);
}

/**
 * Przełącza główną część okna na tabelę signals.
 */
void MainWindow::onSignalsClicked() {
    if (signalsPage_.isVisible()) {
        signalsPage_.close();
        SetWindowTextW(signalsButton_, L"Sygnały");
    } else {
        reportsPage_.close();
        candleGapsPage_.close();
        SetWindowTextW(reportsButton_, L"Raporty");
        SetWindowTextW(candleGapsButton_, L"Luki świec");
        signalsPage_.open();
        SetWindowTextW(signalsButton_, L"Strona główna");
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
    } else if (candleGapsPage_.isVisible()) {
        candleGapsPage_.draw(deviceContext, clientArea);
    } else if (signalsPage_.isVisible()) {
        signalsPage_.draw(deviceContext, clientArea);
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
