#include "MainWindowDisplay.h"

#include <algorithm>
#include <cstdio>
#include <cwchar>
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
 * Rysuje pojedynczą komórkę tabeli z przycięciem zbyt długiej zawartości.
 */
void drawTableCell(
    HDC deviceContext, const std::wstring& text, RECT bounds, UINT format) {
    DrawTextW(deviceContext, text.c_str(), static_cast<int>(text.size()),
              &bounds, format);
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

/**
 * Zamienia czas świecy UTC na czas Europe/Warsaw, uwzględniając zmianę czasu.
 */
std::string formatWarsawCandleTime(const std::string& utcText) {
    unsigned int year = 0;
    unsigned int month = 0;
    unsigned int day = 0;
    unsigned int hour = 0;
    unsigned int minute = 0;
    unsigned int second = 0;
    int consumedCharacters = 0;
    if (std::sscanf(
            utcText.c_str(), "%4u-%2u-%2u %2u:%2u:%2u%n",
            &year, &month, &day, &hour, &minute, &second,
            &consumedCharacters) != 6 ||
        consumedCharacters != 19 || year > 65535 || month > 65535 ||
        day > 65535 || hour > 65535 || minute > 65535 || second > 65535) {
        return utcText + " UTC";
    }

    SYSTEMTIME utcTime{};
    utcTime.wYear = static_cast<WORD>(year);
    utcTime.wMonth = static_cast<WORD>(month);
    utcTime.wDay = static_cast<WORD>(day);
    utcTime.wHour = static_cast<WORD>(hour);
    utcTime.wMinute = static_cast<WORD>(minute);
    utcTime.wSecond = static_cast<WORD>(second);
    FILETIME validatedTime{};
    if (!SystemTimeToFileTime(&utcTime, &validatedTime)) {
        return utcText + " UTC";
    }

    DYNAMIC_TIME_ZONE_INFORMATION currentZone{};
    if (GetDynamicTimeZoneInformation(&currentZone) == TIME_ZONE_ID_INVALID ||
        std::wcscmp(
            currentZone.TimeZoneKeyName,
            L"Central European Standard Time") != 0) {
        return utcText + " UTC";
    }
    TIME_ZONE_INFORMATION warsawTimeZone{};
    if (GetTimeZoneInformation(&warsawTimeZone) == TIME_ZONE_ID_INVALID) {
        return utcText + " UTC";
    }
    SYSTEMTIME warsawTime{};
    if (!SystemTimeToTzSpecificLocalTime(
            &warsawTimeZone, &utcTime, &warsawTime)) {
        return utcText + " UTC";
    }

    char formattedTime[64]{};
    std::snprintf(
        formattedTime, sizeof(formattedTime),
        "%04u-%02u-%02u %02u:%02u:%02u Europe/Warsaw",
        static_cast<unsigned>(warsawTime.wYear),
        static_cast<unsigned>(warsawTime.wMonth),
        static_cast<unsigned>(warsawTime.wDay),
        static_cast<unsigned>(warsawTime.wHour),
        static_cast<unsigned>(warsawTime.wMinute),
        static_cast<unsigned>(warsawTime.wSecond));
    return formattedTime;
}

/**
 * Składa wartości OHLC świecy w jeden czytelny wiersz.
 */
std::string createCandleText(
    const CandleData& candle, const std::string& status) {
    if (candle.datetime.empty()) {
        return "Swieca 1 min: " + status;
    }

    std::string text = "Swieca 1 min (" +
        formatWarsawCandleTime(candle.datetime) +
        ")  Open: " + candle.open + "  High: " + candle.high +
        "  Low: " + candle.low + "  Close: " + candle.close;
    if (!status.empty()) {
        text += "\n" + status;
    }
    return text;
}

} // namespace

// Front
/**
 * Rysuje sentencję dnia, zegar, status pobierania i świecę 1-minutową.
 */
void MainWindowDisplay::draw(
    HDC deviceContext, RECT& clientArea, const StockPriceSnapshot& snapshot,
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

    const std::string candleText =
        createCandleText(snapshot.oneMinuteCandle, snapshot.status);
    std::wstring wideCandleText;
    if (!convertUtf8ToWide(candleText, wideCandleText)) {
        wideCandleText = L"Nie można wyświetlić świecy 1-minutowej.";
    }
    RECT candleBounds = clientArea;
    DrawTextW(deviceContext, wideCandleText.c_str(), -1, &candleBounds,
              DT_CENTER | DT_WORDBREAK | DT_CALCRECT);
    const LONG candleHeight = candleBounds.bottom - candleBounds.top;
    const LONG totalHeight =
        sentenceHeight + sentenceSpacing + headerHeight + candleHeight;
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

    RECT candleDisplayBounds = clientArea;
    candleDisplayBounds.top = top;
    candleDisplayBounds.bottom = top + candleHeight;
    drawCenteredWideText(
        deviceContext, wideCandleText, candleDisplayBounds,
        DT_CENTER | DT_WORDBREAK);
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
