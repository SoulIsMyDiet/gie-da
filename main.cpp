#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "AudioSignal.h"
#include "MainWindowDisplay.h"

namespace {

constexpr char windowClassName[] = "HelloWorldWindow";
constexpr UINT_PTR clockTimerId = 1;

LRESULT CALLBACK WindowProcedure(HWND window, UINT message, WPARAM wParam, LPARAM lParam) {
    switch (message) {
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
                        if (!AudioSignal::playForMinute(localTime.wMinute)) {
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
            MainWindowDisplay::draw(deviceContext, clientArea);
            EndPaint(window, &paint);
            return 0;
        }
        case WM_DESTROY:
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