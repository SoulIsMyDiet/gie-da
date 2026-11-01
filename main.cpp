#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "AudioSignal.h"
#include "MainWindowDisplay.h"
#include "TwelveDataClient.h"

#include <string>

namespace {

constexpr char windowClassName[] = "HelloWorldWindow";
constexpr UINT_PTR clockTimerId = 1;
constexpr UINT forexUpdateMessage = WM_APP + 1;
constexpr DWORD forexRefreshIntervalMs = 30000;

SRWLOCK forexStatusLock = SRWLOCK_INIT;
std::string forexStatus = "Connecting to Twelve Data...";
HANDLE forexStopEvent = nullptr;
HANDLE forexWorkerThread = nullptr;

class ForexStatusLock {
public:
    ForexStatusLock() {
        AcquireSRWLockExclusive(&forexStatusLock);
    }

    ~ForexStatusLock() {
        ReleaseSRWLockExclusive(&forexStatusLock);
    }

    ForexStatusLock(const ForexStatusLock&) = delete;
    ForexStatusLock& operator=(const ForexStatusLock&) = delete;
};

DWORD WINAPI updateForexPrice(LPVOID parameter) {
    const HWND window = static_cast<HWND>(parameter);
    while (true) {
        std::string price;
        std::string error;
        const bool succeeded = TwelveDataClient::fetchEurUsdPrice(price, error);
        {
            const ForexStatusLock lock;
            forexStatus = succeeded ? "EUR/USD: " + price : error;
        }
        if (!PostMessageA(window, forexUpdateMessage, 0, 0)) {
            break;
        }
        if (WaitForSingleObject(forexStopEvent, forexRefreshIntervalMs) != WAIT_TIMEOUT) {
            break;
        }
    }
    return 0;
}

LRESULT CALLBACK WindowProcedure(HWND window, UINT message, WPARAM wParam, LPARAM lParam) {
    switch (message) {
        case forexUpdateMessage:
            InvalidateRect(window, nullptr, TRUE);
            return 0;
        case WM_TIMER:
            if (wParam == clockTimerId) {
                static bool hasPreviousTime = false;
                static WORD previousHour = 0;
                static WORD previousMinute = 0;
                SYSTEMTIME localTime{};
                GetLocalTime(&localTime);
                if (!hasPreviousTime) {
                    previousHour = localTime.wHour;
                    previousMinute = localTime.wMinute;
                    hasPreviousTime = true;
                } else if (localTime.wHour != previousHour ||
                           localTime.wMinute != previousMinute) {
                    previousHour = localTime.wHour;
                    previousMinute = localTime.wMinute;
                    InvalidateRect(window, nullptr, TRUE);
                    if (localTime.wMinute % 5 == 0) {
                        if (!AudioSignal::play()) {
                            MessageBoxA(window, "Windows could not play the audio signal.",
                                        "Audio error", MB_ICONERROR);
                        }
                    }
                }
            }
            return 0;
        case WM_PAINT: {
            PAINTSTRUCT paint{};
            HDC deviceContext = BeginPaint(window, &paint);
            RECT clientArea{};
            GetClientRect(window, &clientArea);
            std::string currentForexStatus;
            {
                const ForexStatusLock lock;
                currentForexStatus = forexStatus;
            }
            MainWindowDisplay::draw(deviceContext, clientArea, currentForexStatus);
            EndPaint(window, &paint);
            return 0;
        }
        case WM_DESTROY:
            if (forexStopEvent != nullptr) {
                SetEvent(forexStopEvent);
            }
            if (forexWorkerThread != nullptr) {
                WaitForSingleObject(forexWorkerThread, INFINITE);
                CloseHandle(forexWorkerThread);
                forexWorkerThread = nullptr;
            }
            if (forexStopEvent != nullptr) {
                CloseHandle(forexStopEvent);
                forexStopEvent = nullptr;
            }
            PostQuitMessage(0);
            return 0;
        default:
            return DefWindowProcA(window, message, wParam, lParam);
    }
}

} // namespace

int WINAPI WinMain(HINSTANCE instance, HINSTANCE, LPSTR, int showCommand) {
    WNDCLASSA windowClass{};
    windowClass.lpfnWndProc = WindowProcedure;
    windowClass.hInstance = instance;
    windowClass.lpszClassName = windowClassName;
    windowClass.hCursor = LoadCursor(nullptr, IDC_ARROW);
    windowClass.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);

    if (!RegisterClassA(&windowClass)) {
        MessageBoxA(nullptr, "Could not register the window class.", "Error", MB_ICONERROR);
        return 1;
    }

    HWND window = CreateWindowExA(
        0,
        windowClassName,
        "Hello World",
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT, 640, 360,
        nullptr, nullptr, instance, nullptr
    );

    if (window == nullptr) {
        MessageBoxA(nullptr, "Could not create the window.", "Error", MB_ICONERROR);
        return 1;
    }

    forexStopEvent = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (forexStopEvent == nullptr) {
        MessageBoxA(window, "Could not start the Forex update service.",
                    "Error", MB_ICONERROR);
        DestroyWindow(window);
        return 1;
    }
    forexWorkerThread = CreateThread(
        nullptr, 0, updateForexPrice, window, 0, nullptr);
    if (forexWorkerThread == nullptr) {
        MessageBoxA(window, "Could not start the Forex update service.",
                    "Error", MB_ICONERROR);
        DestroyWindow(window);
        return 1;
    }

    if (SetTimer(window, clockTimerId, 1000, nullptr) == 0) {
        MessageBoxA(nullptr, "Could not start the clock timer.", "Error", MB_ICONERROR);
        DestroyWindow(window);
        return 1;
    }

    ShowWindow(window, showCommand);
    UpdateWindow(window);

    MSG message{};
    while (GetMessageA(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageA(&message);
    }

    return static_cast<int>(message.wParam);
}