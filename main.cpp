#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mmsystem.h>

#include <cmath>
#include <cstdint>

namespace {

constexpr char windowClassName[] = "HelloWorldWindow";
constexpr UINT_PTR clockTimerId = 1;
constexpr DWORD sampleRate = 44100;
constexpr DWORD signalDurationMs = 350;
constexpr DWORD sampleCount = sampleRate * signalDurationMs / 1000;
constexpr DWORD signalGapMs = 150;
constexpr DWORD gapSampleCount = sampleRate * signalGapMs / 1000;
constexpr DWORD sequenceSampleCount = sampleCount * 3 + gapSampleCount * 2;

#pragma pack(push, 1)
struct WaveData {
    char riffId[4];
    DWORD riffSize;
    char waveId[4];
    char formatId[4];
    DWORD formatSize;
    WORD audioFormat;
    WORD channelCount;
    DWORD samplesPerSecond;
    DWORD bytesPerSecond;
    WORD bytesPerSample;
    WORD bitsPerSample;
    char dataId[4];
    DWORD dataSize;
    std::int16_t samples[sequenceSampleCount];
};
#pragma pack(pop)

static_assert(sizeof(WaveData) == 44 + sequenceSampleCount * sizeof(std::int16_t));

bool PlayMinuteSignal(WORD minute) {
    constexpr double pi = 3.14159265358979323846;
    const double frequency = 440.0 + (minute / 5) * 50.0;
    WaveData wave{
        {'R', 'I', 'F', 'F'},
        static_cast<DWORD>(sizeof(WaveData) - 8),
        {'W', 'A', 'V', 'E'},
        {'f', 'm', 't', ' '},
        16,
        1,
        1,
        sampleRate,
        sampleRate * sizeof(std::int16_t),
        sizeof(std::int16_t),
        16,
        {'d', 'a', 't', 'a'},
        sequenceSampleCount * sizeof(std::int16_t),
        {}
    };

    for (DWORD signal = 0; signal < 3; ++signal) {
        const DWORD start = signal * (sampleCount + gapSampleCount);
        for (DWORD i = 0; i < sampleCount; ++i) {
            const double phase = 2.0 * pi * frequency * i / sampleRate;
            wave.samples[start + i] = static_cast<std::int16_t>(12000.0 * std::sin(phase));
        }
    }

    return PlaySoundA(reinterpret_cast<LPCSTR>(&wave), nullptr, SND_MEMORY | SND_SYNC);
}

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
                        if (!PlayMinuteSignal(localTime.wMinute)) {
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