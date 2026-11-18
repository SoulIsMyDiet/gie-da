#pragma once

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <algorithm>
#include <cmath>
#include <string>
#include <vector>

namespace WindowScale {

constexpr int referenceWindowWidth = 640;
constexpr int referenceWindowHeight = 360;

/**
 * Oblicza jednolitą skalę elementów względem pierwotnego rozmiaru okna.
 * @param window Okno, którego aktualny rozmiar wyznacza skalę.
 * @return Współczynnik używany do zachowania proporcji elementów.
 */
inline double factor(HWND window) {
    RECT bounds{};
    if (window == nullptr || !GetWindowRect(window, &bounds)) {
        return 1.0;
    }

    const double widthScale =
        static_cast<double>(bounds.right - bounds.left) /
        referenceWindowWidth;
    const double heightScale =
        static_cast<double>(bounds.bottom - bounds.top) /
        referenceWindowHeight;
    const double uniformScale =
        widthScale < heightScale ? widthScale : heightScale;
    return uniformScale < 0.5 ? 0.5 : uniformScale;
}

/**
 * Zwraca rozmiar elementu przeliczony według skali okna.
 * @param window Okno, którego rozmiar wyznacza skalę.
 * @param baseSize Rozmiar elementu w pierwotnym układzie.
 * @return Przeskalowany rozmiar, nie mniejszy niż jeden piksel.
 */
inline int size(HWND window, int baseSize) {
    return std::max(
        1, static_cast<int>(std::lround(baseSize * factor(window))));
}

/**
 * Rysuje tekst w czcionce dopasowanej do aktualnego rozmiaru okna.
 * @param deviceContext Kontekst używany do rysowania tekstu.
 * @param window Okno wyznaczające rozmiar czcionki.
 * @param text Tekst do wyświetlenia.
 * @param bounds Obszar, w którym tekst ma zostać narysowany.
 * @param format Flagi określające wyrównanie i zawijanie tekstu.
 * @param baseHeight Wysokość czcionki w pierwotnym układzie.
 */
inline void drawText(
    HDC deviceContext,
    HWND window,
    const std::wstring& text,
    RECT bounds,
    UINT format,
    int baseHeight = 16) {
    HFONT font = CreateFontW(
        -size(window, baseHeight), 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
    if (font == nullptr) {
        DrawTextW(
            deviceContext, text.c_str(), static_cast<int>(text.size()),
            &bounds, format);
        return;
    }

    const HGDIOBJ previousFont = SelectObject(deviceContext, font);
    DrawTextW(
        deviceContext, text.c_str(), static_cast<int>(text.size()),
        &bounds, format);
    SelectObject(deviceContext, previousFont);
    DeleteObject(font);
}

/**
 * Aktualizuje czcionkę kontrolek i zwalnia poprzedni uchwyt po zmianie skali.
 * @param window Okno wyznaczające rozmiar czcionki.
 * @param font Uchwyt aktualnie używanej czcionki.
 * @param currentHeight Wysokość poprzedniej czcionki w pikselach.
 * @param baseHeight Wysokość czcionki w pierwotnym układzie.
 * @param controls Kontrolki, którym należy przypisać nową czcionkę.
 * @return true, jeśli utworzono i przypisano nową czcionkę.
 */
inline bool updateFont(
    HWND window,
    HFONT& font,
    int& currentHeight,
    int baseHeight,
    const std::vector<HWND>& controls) {
    const int nextHeight = size(window, baseHeight);
    if (font != nullptr && currentHeight == nextHeight) {
        for (HWND control : controls) {
            if (control != nullptr) {
                SendMessageW(
                    control, WM_SETFONT, reinterpret_cast<WPARAM>(font), TRUE);
            }
        }
        return false;
    }

    HFONT nextFont = CreateFontW(
        -nextHeight, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
    if (nextFont == nullptr) {
        for (HWND control : controls) {
            if (control != nullptr && font != nullptr) {
                SendMessageW(
                    control, WM_SETFONT, reinterpret_cast<WPARAM>(font), TRUE);
            }
        }
        return false;
    }

    for (HWND control : controls) {
        if (control != nullptr) {
            SendMessageW(
                control, WM_SETFONT, reinterpret_cast<WPARAM>(nextFont), TRUE);
        }
    }
    if (font != nullptr) {
        DeleteObject(font);
    }
    font = nextFont;
    currentHeight = nextHeight;
    return true;
}

} // namespace WindowScale
