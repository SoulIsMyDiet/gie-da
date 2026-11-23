#include "MainWindowDisplay.h"
#include "../WindowScale.h"

#include <string>

namespace {

/**
 * Konwertuje tekst UTF-8 z API do formatu Unicode używanego przez Windows.
 */
bool convertUtf8ToWide(const std::string& text, std::wstring& convertedText) {
    if (text.empty()) {
        convertedText.clear();
        return true;
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

/**
 * Wyświetla tekst Unicode w wyznaczonym obszarze okna.
 * @param deviceContext Kontekst rysowania okna.
 * @param text Tekst do wyświetlenia.
 * @param bounds Obszar, w którym tekst ma zostać narysowany.
 * @param format Flagi określające wyrównanie i zawijanie tekstu.
 */
void drawCenteredWideText(
    HDC deviceContext, const std::wstring& text, RECT bounds, UINT format) {
    DrawTextW(
        deviceContext, text.c_str(), static_cast<int>(text.size()), &bounds, format);
}

} // namespace

// Front
/**
 * Rysuje sentencję dnia pośrodku obszaru strony głównej.
 */
void MainWindowDisplay::draw(
    HDC deviceContext,
    HWND window,
    RECT& clientArea,
    const std::string& dailySentence,
    const std::string& oneMinuteCandle) {
    std::wstring wideDailySentence;
    if (!convertUtf8ToWide(dailySentence, wideDailySentence)) {
        wideDailySentence = L"Nie można wyświetlić sentencji.";
    }
    std::wstring wideOneMinuteCandle;
    if (!convertUtf8ToWide(oneMinuteCandle, wideOneMinuteCandle)) {
        wideOneMinuteCandle = L"Nie można wyświetlić świecy IBKR.";
    }
    HFONT sentenceFont = CreateFontW(
        -WindowScale::size(window, 16), 0, 0, 0, FW_NORMAL, FALSE, FALSE,
        FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY, DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
    const HGDIOBJ previousFont = sentenceFont == nullptr
        ? nullptr
        : SelectObject(deviceContext, sentenceFont);

    RECT sentenceBounds = clientArea;
    DrawTextW(
        deviceContext, wideDailySentence.c_str(),
        static_cast<int>(wideDailySentence.size()), &sentenceBounds,
        DT_CENTER | DT_WORDBREAK | DT_CALCRECT);
    const LONG sentenceHeight = sentenceBounds.bottom - sentenceBounds.top;
    RECT candleBounds = clientArea;
    DrawTextW(
        deviceContext, wideOneMinuteCandle.c_str(),
        static_cast<int>(wideOneMinuteCandle.size()), &candleBounds,
        DT_CENTER | DT_WORDBREAK | DT_CALCRECT);
    const LONG candleHeight = candleBounds.bottom - candleBounds.top;
    const LONG sectionGap = WindowScale::size(window, 18);
    const LONG totalHeight = sentenceHeight + sectionGap + candleHeight;
    LONG top = clientArea.top + ((clientArea.bottom - clientArea.top) - totalHeight) / 2;
    if (top < clientArea.top) {
        top = clientArea.top;
    }

    SetTextColor(deviceContext, RGB(0, 0, 0));
    sentenceBounds = clientArea;
    sentenceBounds.top = top;
    sentenceBounds.bottom = top + sentenceHeight;
    drawCenteredWideText(
        deviceContext, wideDailySentence, sentenceBounds,
        DT_CENTER | DT_WORDBREAK);
    candleBounds = clientArea;
    candleBounds.top = top + sentenceHeight + sectionGap;
    candleBounds.bottom = candleBounds.top + candleHeight;
    drawCenteredWideText(
        deviceContext, wideOneMinuteCandle, candleBounds,
        DT_CENTER | DT_WORDBREAK);

    if (sentenceFont != nullptr) {
        SelectObject(deviceContext, previousFont);
        DeleteObject(sentenceFont);
    }
}
