#include "MainWindowDisplay.h"

#include <cstdio>

namespace {

constexpr COLORREF firstPriceColor = RGB(128, 128, 128);
constexpr COLORREF lowerPriceColor = RGB(255, 0, 0);
constexpr COLORREF higherPriceColor = RGB(0, 160, 0);
constexpr char productName[] = "  EUR/USD: ";

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

/**
 * Renders the clock, status, and color-coded Forex price history.
 */
void MainWindowDisplay::draw(
    HDC deviceContext, RECT& clientArea, const ForexPriceSnapshot& snapshot) {
    const std::string clockText = currentTimeText();
    RECT headerBounds = clientArea;
    DrawTextA(deviceContext, clockText.c_str(), -1, &headerBounds,
              DT_CENTER | DT_SINGLELINE | DT_CALCRECT);
    const LONG headerHeight = headerBounds.bottom - headerBounds.top;

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
    const LONG totalHeight = headerHeight + statusHeight + historyHeight;
    LONG top = clientArea.top + ((clientArea.bottom - clientArea.top) - totalHeight) / 2;
    if (top < clientArea.top) {
        top = clientArea.top;
    }

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
