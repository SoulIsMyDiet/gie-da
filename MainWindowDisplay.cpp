#include "MainWindowDisplay.h"

#include <algorithm>
#include <cstdio>
#include <string>

namespace {

constexpr COLORREF firstPriceColor = RGB(128, 128, 128);
constexpr COLORREF lowerPriceColor = RGB(255, 0, 0);
constexpr COLORREF higherPriceColor = RGB(0, 160, 0);
constexpr char productName[] = "  EUR/USD: ";

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
 * Rysuje pojedynczą komórkę tabeli z przycięciem zbyt długiej zawartości.
 */
void drawTableCell(
    HDC deviceContext, const std::wstring& text, RECT bounds, UINT format) {
    DrawTextW(deviceContext, text.c_str(), static_cast<int>(text.size()),
              &bounds, format);
}

LONG measureText(HDC deviceContext, const std::string& text) {
    SIZE textSize{};
    GetTextExtentPoint32A(
        deviceContext, text.c_str(), static_cast<int>(text.size()), &textSize);
    return textSize.cx;
}

void drawCenteredText(
    HDC deviceContext, const std::string& text, RECT bounds, UINT format) {
    DrawTextA(deviceContext, text.c_str(), -1, &bounds, format);
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

std::string currentTimeText() {
    SYSTEMTIME localTime{};
    GetLocalTime(&localTime);
    char clockText[8]{};
    std::snprintf(clockText, sizeof(clockText), "%02u:%02u",
                  static_cast<unsigned>(localTime.wHour),
                  static_cast<unsigned>(localTime.wMinute));
    return clockText;
}

void drawClock(
    HDC deviceContext, const std::string& clockText, RECT clientArea,
    LONG top, LONG height) {
    RECT bounds = clientArea;
    bounds.top = top;
    bounds.bottom = top + height;
    drawCenteredText(deviceContext, clockText, bounds, DT_CENTER | DT_SINGLELINE);
}

void drawStatus(
    HDC deviceContext, const std::string& status, RECT clientArea,
    LONG top, LONG height) {
    RECT bounds = clientArea;
    bounds.top = top;
    bounds.bottom = top + height;
    drawCenteredText(deviceContext, status, bounds, DT_CENTER | DT_WORDBREAK);
}

COLORREF rowColor(ForexRowColor color) {
    switch (color) {
        case ForexRowColor::red:
            return lowerPriceColor;
        case ForexRowColor::green:
            return higherPriceColor;
        case ForexRowColor::gray:
            return firstPriceColor;
    }
    return firstPriceColor;
}

LONG drawProductTimestamp(
    HDC deviceContext, const std::string& timestamp, LONG x, LONG y) {
    TextOutA(deviceContext, x, y, timestamp.c_str(),
             static_cast<int>(timestamp.size()));
    return measureText(deviceContext, timestamp);
}

LONG drawProductName(HDC deviceContext, LONG x, LONG y) {
    TextOutA(deviceContext, x, y, productName,
             static_cast<int>(sizeof(productName) - 1));
    return measureText(deviceContext, productName);
}

LONG drawProductValue(
    HDC deviceContext, const std::string& value, LONG x, LONG y) {
    TextOutA(deviceContext, x, y, value.c_str(), static_cast<int>(value.size()));
    return measureText(deviceContext, value);
}

void drawProductRow(
    HDC deviceContext, RECT clientArea, LONG top, LONG rowHeight,
    const ForexPriceEntry& entry, COLORREF color) {
    const LONG timestampWidth = measureText(deviceContext, entry.timestamp);
    const LONG nameWidth = measureText(deviceContext, productName);
    const LONG valueWidth = measureText(deviceContext, entry.price);
    const LONG totalWidth = timestampWidth + nameWidth + valueWidth;
    LONG x = clientArea.left + ((clientArea.right - clientArea.left) - totalWidth) / 2;

    TEXTMETRICA textMetrics{};
    GetTextMetricsA(deviceContext, &textMetrics);
    const LONG y = top + (rowHeight - textMetrics.tmHeight) / 2;
    SetTextColor(deviceContext, color);
    x += drawProductTimestamp(deviceContext, entry.timestamp, x, y);
    x += drawProductName(deviceContext, x, y);
    drawProductValue(deviceContext, entry.price, x, y);
}

} // namespace

// Front
/**
 * Rysuje sentencję dnia nad zegarem oraz pozostałe informacje strony głównej.
 */
void MainWindowDisplay::draw(
    HDC deviceContext, RECT& clientArea, const ForexPriceSnapshot& snapshot,
    const std::string& dailySentence) {
    const std::string clockText = currentTimeText();
    RECT headerBounds = clientArea;
    DrawTextA(deviceContext, clockText.c_str(), -1, &headerBounds,
              DT_CENTER | DT_SINGLELINE | DT_CALCRECT);
    const LONG headerHeight = headerBounds.bottom - headerBounds.top;

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
    const LONG sentenceSpacing = 8;

    TEXTMETRICA textMetrics{};
    GetTextMetricsA(deviceContext, &textMetrics);
    const LONG rowHeight = textMetrics.tmHeight + textMetrics.tmExternalLeading;

    LONG statusHeight = 0;
    if (!snapshot.status.empty()) {
        RECT statusBounds = clientArea;
        DrawTextA(deviceContext, snapshot.status.c_str(), -1, &statusBounds,
                  DT_CENTER | DT_WORDBREAK | DT_CALCRECT);
        statusHeight = statusBounds.bottom - statusBounds.top;
    }

    const LONG historyHeight = rowHeight * static_cast<LONG>(snapshot.prices.size());
    const LONG totalHeight =
        sentenceHeight + sentenceSpacing + headerHeight + statusHeight + historyHeight;
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
    top += sentenceHeight + sentenceSpacing;

    SetTextColor(deviceContext, RGB(255, 0, 0));
    drawClock(deviceContext, clockText, clientArea, top, headerHeight);
    top += headerHeight;

    if (!snapshot.status.empty()) {
        drawStatus(deviceContext, snapshot.status, clientArea, top, statusHeight);
        top += statusHeight;
    }

    for (std::size_t index = 0; index < snapshot.prices.size(); ++index) {
        drawProductRow(deviceContext, clientArea, top, rowHeight,
                       snapshot.prices[index], rowColor(snapshot.prices[index].color));
        top += rowHeight;
    }
}

/**
 * Rysuje wyśrodkowany tekst na stronie otwieranej przyciskiem „Raporty”.
 */
void MainWindowDisplay::drawReportsPage(
    HDC deviceContext, RECT& clientArea,
    const std::vector<PriceReportEntry>& entries, const std::string& status) {
    if (!status.empty() || entries.empty()) {
        const std::string message = status.empty() ? "Brak wpisow do wyswietlenia." : status;
        std::wstring wideMessage;
        if (!convertUtf8ToWide(message, wideMessage)) {
            wideMessage = L"Nie można wyświetlić raportu.";
        }
        RECT messageBounds = clientArea;
        DrawTextW(
            deviceContext, wideMessage.c_str(), static_cast<int>(wideMessage.size()),
            &messageBounds, DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
        return;
    }

    HFONT tableFont = CreateFontW(
        -12, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
        OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
        DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
    if (tableFont == nullptr) {
        DrawTextW(
            deviceContext, L"Nie można utworzyć tabeli.", -1,
            &clientArea, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        return;
    }

    const HGDIOBJ previousFont = SelectObject(deviceContext, tableFont);
    const LONG rowHeight = std::max<LONG>(
        1, (clientArea.bottom - clientArea.top) /
               static_cast<LONG>(entries.size() + 1));
    const int columnWidths[] = {
        10, 25, 16, 20, 29
    };
    const wchar_t* columnNames[] = {
        L"ID", L"Czas", L"Symbol", L"Cena", L"Złote rady"
    };
    const UINT textFormat = DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS;

    SetTextColor(deviceContext, RGB(0, 0, 0));
    SetBkMode(deviceContext, TRANSPARENT);
    LONG rowTop = clientArea.top;
    LONG columnLeft = clientArea.left;
    const LONG totalWidth = clientArea.right - clientArea.left;
    for (std::size_t column = 0; column < 5; ++column) {
        const LONG columnWidth = totalWidth * columnWidths[column] / 100;
        RECT cell{columnLeft + 4, rowTop, columnLeft + columnWidth - 4,
                  rowTop + rowHeight};
        drawTableCell(deviceContext, columnNames[column], cell, textFormat);
        columnLeft += columnWidth;
    }
    rowTop += rowHeight;

    for (const PriceReportEntry& entry : entries) {
        std::wstring values[5];
        const std::string* sourceValues[] = {
            &entry.id, &entry.recordedAt, &entry.symbol,
            &entry.price, &entry.goldenAdvice
        };
        bool validRow = true;
        for (std::size_t column = 0; column < 5; ++column) {
            if (!convertUtf8ToWide(*sourceValues[column], values[column])) {
                validRow = false;
                break;
            }
        }
        if (!validRow) {
            values[0] = L"";
            values[1] = L"";
            values[2] = L"";
            values[3] = L"";
            values[4] = L"Nieprawidłowy tekst";
        }

        columnLeft = clientArea.left;
        for (std::size_t column = 0; column < 5; ++column) {
            const LONG columnWidth = totalWidth * columnWidths[column] / 100;
            RECT cell{columnLeft + 4, rowTop, columnLeft + columnWidth - 4,
                      rowTop + rowHeight};
            drawTableCell(deviceContext, values[column], cell, textFormat);
            columnLeft += columnWidth;
        }
        rowTop += rowHeight;
    }

    SelectObject(deviceContext, previousFont);
    DeleteObject(tableFont);
}
