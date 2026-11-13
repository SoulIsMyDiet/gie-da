#include "MainWindowDisplay.h"

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
    HDC deviceContext, RECT& clientArea, const std::string& dailySentence) {
    std::wstring wideDailySentence;
    if (!convertUtf8ToWide(dailySentence, wideDailySentence)) {
        wideDailySentence = L"Nie można wyświetlić sentencji.";
    }
    RECT sentenceBounds = clientArea;
    DrawTextW(
        deviceContext, wideDailySentence.c_str(),
        static_cast<int>(wideDailySentence.size()), &sentenceBounds,
        DT_CENTER | DT_WORDBREAK | DT_CALCRECT);
    const LONG sentenceHeight = sentenceBounds.bottom - sentenceBounds.top;
    const LONG totalHeight = sentenceHeight;
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
}
