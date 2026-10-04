#define WIN32_LEAN_AND_MEAN
#include <windows.h>

namespace {

constexpr char windowClassName[] = "HelloWorldWindow";
constexpr UINT_PTR clockTimerId = 1;

LRESULT CALLBACK WindowProcedure(HWND window, UINT message, WPARAM wParam, LPARAM lParam) {
    switch (message) {
        case WM_TIMER:
            if (wParam == clockTimerId) {
                static WORD lastHour = 24;
                static WORD lastMinute = 60;
                SYSTEMTIME localTime{};
                GetLocalTime(&localTime);
                if (localTime.wHour != lastHour || localTime.wMinute != lastMinute) {
                    lastHour = localTime.wHour;
                    lastMinute = localTime.wMinute;
                    InvalidateRect(window, nullptr, TRUE);
                }
            }
            return 0;
        case WM_PAINT: {
            PAINTSTRUCT paint{};
            HDC deviceContext = BeginPaint(window, &paint);
            RECT clientArea{};
            GetClientRect(window, &clientArea);
            SetTextColor(deviceContext, RGB(255, 0, 0));
            SYSTEMTIME localTime{};
            GetLocalTime(&localTime);
            char displayText[32]{};
            wsprintfA(displayText, "hello world\r\n%02u:%02u",
                      localTime.wHour, localTime.wMinute);
            DrawTextA(deviceContext, displayText, -1, &clientArea,
                      DT_CENTER | DT_VCENTER);
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