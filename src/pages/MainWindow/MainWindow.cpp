#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "DailySentencePicker.h"
#include "ErrorLogger.h"
#include "MainWindow.h"
#include "MainWindowDisplay.h"
#include "../WindowScale.h"

#include <algorithm>
#include <exception>
#include <string>
#include <system_error>
#include <utility>

namespace {

constexpr wchar_t windowClassName[] = L"SignalReportWindow";
constexpr UINT candleUpdateMessage = WM_APP + 1;
constexpr int reportsButtonId = 1001;
constexpr int candleGapsButtonId = 1002;
constexpr int signalsButtonId = 1003;
constexpr int reportsButtonWidth = 140;
constexpr int reportsButtonHeight = 36;
constexpr int reportsButtonGap = 12;
constexpr int reportsButtonBottomMargin = 16;
constexpr int initialWindowWidth = 1280;
constexpr int initialWindowHeight = 720;

/**
 * Zapisuje błąd do pliku i pokazuje go użytkownikowi w standardowym oknie.
 */
void showErrorMessage(HWND owner, const wchar_t* message) {
    ErrorLogger::error(message);
    MessageBoxW(owner, message, L"Błąd", MB_ICONERROR);
}

} // namespace

MainWindow::~MainWindow() {
    if (navigationButtonFont_ != nullptr) {
        DeleteObject(navigationButtonFont_);
    }
}

/**
 * Rejestruje klasę, tworzy główne okno w podwójnym rozmiarze i je wyświetla.
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
            return DefWindowProcW(handle_, message, wParam, lParam);
    }
    return DefWindowProcW(handle_, message, wParam, lParam);
}

/**
 * Uruchamia monitor świecy oraz pobieranie sentencji dnia.
 */
bool MainWindow::onCreate() {
    if (!createReportsButton()) {
        showErrorMessage(handle_, L"Nie udało się utworzyć przycisku raportów.");
        return false;
    }
    if (!createCandleGapsButton()) {
        showErrorMessage(
            handle_, L"Nie udało się utworzyć przycisku brakujących świec.");
        return false;
    }
    if (!createSignalsButton()) {
        showErrorMessage(
            handle_, L"Nie udało się utworzyć przycisku sygnałów.");
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

    positionReportsButton();
    reportsPage_.positionControls();
    candleGapsPage_.positionControls();
    signalsPage_.positionControls();

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
    const int buttonGap = WindowScale::size(handle_, reportsButtonGap);
    const int buttonHeight = WindowScale::size(handle_, reportsButtonHeight);
    const int bottomMargin =
        WindowScale::size(handle_, reportsButtonBottomMargin);
    const int buttonBaseWidth = WindowScale::size(handle_, reportsButtonWidth);
    const int availableButtonWidth =
        std::max(1, (clientWidth - buttonGap * 2) / 3);
    const int buttonWidth = std::min(buttonBaseWidth, availableButtonWidth);
    const int buttonsWidth = buttonWidth * 3 + buttonGap * 2;
    const int visibleButtonHeight = std::min(buttonHeight, clientHeight);
    const int firstButtonX = std::max(0, (clientWidth - buttonsWidth) / 2);
    const int buttonY = std::max(
        0, clientHeight - visibleButtonHeight - bottomMargin);
    WindowScale::updateFont(
        handle_, navigationButtonFont_, navigationButtonFontHeight_, 14,
        {reportsButton_, candleGapsButton_, signalsButton_});

    if (reportsButton_ != nullptr) {
        MoveWindow(
            reportsButton_, firstButtonX, buttonY, buttonWidth,
            visibleButtonHeight, TRUE);
    }
    if (candleGapsButton_ != nullptr) {
        MoveWindow(
            candleGapsButton_,
            firstButtonX + buttonWidth + buttonGap,
            buttonY, buttonWidth, visibleButtonHeight, TRUE);
    }
    if (signalsButton_ != nullptr) {
        MoveWindow(
            signalsButton_,
            firstButtonX + (buttonWidth + buttonGap) * 2,
            buttonY, buttonWidth, visibleButtonHeight, TRUE);
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
    candleMonitor_.stop();
    if (dailySentenceThread_.joinable()) {
        dailySentenceThread_.join();
    }
    PostQuitMessage(0);
}
