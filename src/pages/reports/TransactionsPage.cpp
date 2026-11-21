#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commctrl.h>

#include "TransactionsPage.h"
#include "ErrorLogger.h"
#include "../WindowScale.h"

#include <algorithm>
#include <cstdio>
#include <initializer_list>
#include <string>

namespace {

constexpr int fromDateLabelId = 1501;
constexpr int toDateLabelId = 1502;
constexpr int previousPageButtonId = 1503;
constexpr int nextPageButtonId = 1504;
constexpr int generateButtonId = 1505;
constexpr int formTop = 12;
constexpr int formMargin = 12;
constexpr int formGap = 8;
constexpr int fieldWidth = 48;
constexpr int labelHeight = 8;
constexpr int pickerHeight = 8;
constexpr int tableTop = 44;
constexpr int tableGap = 8;
constexpr int navigationReservedHeight = 68;
constexpr int footerMargin = 8;
constexpr int footerGap = 8;
constexpr int pageLabelHeight = 16;
constexpr int pageButtonWidth = 44;
constexpr int pageButtonHeight = 22;
constexpr int generateButtonWidth = 96;
constexpr int generateButtonHeight = 28;

/**
 * Konwertuje tekst UTF-8 z API do tekstu Unicode używanego przez Windows.
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
 * Zamienia datę z kontrolki kalendarza na format YYYY-MM-DD dla lokalnego API.
 */
std::string formatDate(const SYSTEMTIME& date) {
    char formattedDate[11]{};
    std::snprintf(
        formattedDate, sizeof(formattedDate), "%04u-%02u-%02u",
        static_cast<unsigned int>(date.wYear),
        static_cast<unsigned int>(date.wMonth),
        static_cast<unsigned int>(date.wDay));
    return formattedDate;
}

/**
 * Rysuje tekst w komórce tabeli z przycięciem dłuższych wartości.
 */
void drawCell(HDC deviceContext, const std::wstring& text, RECT bounds) {
    DrawTextW(
        deviceContext, text.c_str(), static_cast<int>(text.size()),
        &bounds, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
}

} // namespace

/**
 * Zwalnia czcionki przypisane kontrolkom widoku transakcji.
 */
TransactionsPage::~TransactionsPage() {
    if (controlFont_ != nullptr) {
        DeleteObject(controlFont_);
    }
    if (buttonFont_ != nullptr) {
        DeleteObject(buttonFont_);
    }
}

/**
 * Tworzy kompaktowe filtry daty, przyciski tabeli i natywne kalendarze.
 */
bool TransactionsPage::create(HINSTANCE instance, HWND parent) {
    parent_ = parent;

    INITCOMMONCONTROLSEX commonControls{};
    commonControls.dwSize = sizeof(commonControls);
    commonControls.dwICC = ICC_DATE_CLASSES;
    if (!InitCommonControlsEx(&commonControls)) {
        return false;
    }

    fromDateLabel_ = CreateWindowExW(
        0, L"STATIC", L"Data od",
        WS_CHILD | SS_LEFT,
        0, 0, 0, 0, parent_,
        reinterpret_cast<HMENU>(static_cast<INT_PTR>(fromDateLabelId)),
        instance, nullptr);
    fromDatePicker_ = CreateWindowExW(
        0, DATETIMEPICK_CLASSW, L"",
        WS_CHILD | WS_TABSTOP | DTS_SHORTDATECENTURYFORMAT,
        0, 0, 0, 0, parent_, nullptr, instance, nullptr);
    toDateLabel_ = CreateWindowExW(
        0, L"STATIC", L"Data do",
        WS_CHILD | SS_LEFT,
        0, 0, 0, 0, parent_,
        reinterpret_cast<HMENU>(static_cast<INT_PTR>(toDateLabelId)),
        instance, nullptr);
    toDatePicker_ = CreateWindowExW(
        0, DATETIMEPICK_CLASSW, L"",
        WS_CHILD | WS_TABSTOP | DTS_SHORTDATECENTURYFORMAT,
        0, 0, 0, 0, parent_, nullptr, instance, nullptr);
    previousPageButton_ = CreateWindowExW(
        0, L"BUTTON", L"←",
        WS_CHILD | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, 0, 0, parent_,
        reinterpret_cast<HMENU>(
            static_cast<INT_PTR>(previousPageButtonId)),
        instance, nullptr);
    nextPageButton_ = CreateWindowExW(
        0, L"BUTTON", L"→",
        WS_CHILD | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, 0, 0, parent_,
        reinterpret_cast<HMENU>(static_cast<INT_PTR>(nextPageButtonId)),
        instance, nullptr);
    generateButton_ = CreateWindowExW(
        0, L"BUTTON", L"Generuj",
        WS_CHILD | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, 0, 0, parent_,
        reinterpret_cast<HMENU>(static_cast<INT_PTR>(generateButtonId)),
        instance, nullptr);

    if (fromDateLabel_ == nullptr || fromDatePicker_ == nullptr ||
        toDateLabel_ == nullptr || toDatePicker_ == nullptr ||
        previousPageButton_ == nullptr || nextPageButton_ == nullptr ||
        generateButton_ == nullptr) {
        return false;
    }

    constexpr wchar_t dateFormat[] = L"dd.MM.yyyy";
    if (!SendMessageW(
            fromDatePicker_, DTM_SETFORMATW, 0,
            reinterpret_cast<LPARAM>(dateFormat)) ||
        !SendMessageW(
            toDatePicker_, DTM_SETFORMATW, 0,
            reinterpret_cast<LPARAM>(dateFormat))) {
        return false;
    }

    positionControls();
    updateControls();
    return true;
}

/**
 * Otwiera widok, zachowując daty i wyniki wybrane wcześniej.
 */
void TransactionsPage::open() {
    visible_ = true;
    updateControls();
}

/**
 * Ukrywa widok, pozostawiając daty i wyniki do następnego otwarcia.
 */
void TransactionsPage::close() {
    visible_ = false;
    updateControls();
}

/**
 * Informuje, czy widok transakcji jest aktualnie widoczny.
 */
bool TransactionsPage::isVisible() const {
    return visible_;
}

/**
 * Obsługuje generowanie danych i zmianę strony bez utraty aktywnego filtra.
 */
bool TransactionsPage::handleCommand(
    int controlId, int notificationCode) {
    if (notificationCode != BN_CLICKED) {
        return false;
    }

    if (controlId == generateButtonId) {
        generateReport();
        return true;
    }
    if (controlId == previousPageButtonId && currentPage_ > 0) {
        --currentPage_;
    } else if (controlId == nextPageButtonId && hasNextPage_) {
        ++currentPage_;
    } else if (controlId == previousPageButtonId ||
               controlId == nextPageButtonId) {
        return true;
    } else {
        return false;
    }

    loadCurrentPage();
    updateControls();
    InvalidateRect(parent_, nullptr, TRUE);
    return true;
}

/**
 * Rozmieszcza małe pola daty w lewym górnym rogu i przyciski u dołu.
 */
void TransactionsPage::positionControls() {
    if (parent_ == nullptr) {
        return;
    }

    RECT clientArea{};
    GetClientRect(parent_, &clientArea);
    const int clientWidth = clientArea.right - clientArea.left;
    const int clientHeight = clientArea.bottom - clientArea.top;
    const int scaledMargin = WindowScale::size(parent_, formMargin);
    const int scaledGap = WindowScale::size(parent_, formGap);
    const int scaledFieldWidth = std::min(
        WindowScale::size(parent_, fieldWidth),
        std::max(1, (clientWidth - scaledMargin * 2 - scaledGap) / 2));
    const int firstX = scaledMargin;
    const int secondX = firstX + scaledFieldWidth + scaledGap;
    const int labelY = WindowScale::size(parent_, formTop);
    const int labelHeightScaled = WindowScale::size(parent_, labelHeight);
    const int pickerY = labelY + labelHeightScaled;
    const int pickerHeightScaled = WindowScale::size(parent_, pickerHeight);
    const int contentBottom = std::max(
        0, clientHeight -
               WindowScale::size(parent_, navigationReservedHeight));
    const int generateWidth =
        WindowScale::size(parent_, generateButtonWidth);
    const int generateHeight =
        WindowScale::size(parent_, generateButtonHeight);
    const int pageButtonWidthScaled =
        WindowScale::size(parent_, pageButtonWidth);
    const int pageButtonHeightScaled =
        WindowScale::size(parent_, pageButtonHeight);
    const int margin = WindowScale::size(parent_, footerMargin);
    const int gap = WindowScale::size(parent_, footerGap);
    const int generateY = std::max(
        0, contentBottom - generateHeight - margin);
    const int pageY = std::max(
        0, generateY - pageButtonHeightScaled - gap);
    const int pageButtonsWidth = pageButtonWidthScaled * 2 + gap;
    const int pageButtonsX = std::max(
        0, (clientWidth - pageButtonsWidth) / 2);

    WindowScale::updateFont(
        parent_, controlFont_, controlFontHeight_, 4,
        {fromDateLabel_, fromDatePicker_, toDateLabel_, toDatePicker_});
    WindowScale::updateFont(
        parent_, buttonFont_, buttonFontHeight_, 14,
        {previousPageButton_, nextPageButton_, generateButton_});

    if (fromDateLabel_ != nullptr) {
        MoveWindow(fromDateLabel_, firstX, labelY, scaledFieldWidth,
                   labelHeightScaled, TRUE);
    }
    if (fromDatePicker_ != nullptr) {
        MoveWindow(fromDatePicker_, firstX, pickerY, scaledFieldWidth,
                   pickerHeightScaled, TRUE);
    }
    if (toDateLabel_ != nullptr) {
        MoveWindow(toDateLabel_, secondX, labelY, scaledFieldWidth,
                   labelHeightScaled, TRUE);
    }
    if (toDatePicker_ != nullptr) {
        MoveWindow(toDatePicker_, secondX, pickerY, scaledFieldWidth,
                   pickerHeightScaled, TRUE);
    }
    if (previousPageButton_ != nullptr) {
        MoveWindow(
            previousPageButton_, pageButtonsX, pageY,
            pageButtonWidthScaled, pageButtonHeightScaled, TRUE);
    }
    if (nextPageButton_ != nullptr) {
        MoveWindow(
            nextPageButton_, pageButtonsX + pageButtonWidthScaled + gap,
            pageY, pageButtonWidthScaled, pageButtonHeightScaled, TRUE);
    }
    if (generateButton_ != nullptr) {
        MoveWindow(
            generateButton_, std::max(0, (clientWidth - generateWidth) / 2),
            generateY, generateWidth, generateHeight, TRUE);
    }
}

/**
 * Rysuje tabelę świec, komunikat stanu i numer strony.
 */
void TransactionsPage::draw(HDC deviceContext, RECT& clientArea) const {
    const int pageLabelHeightScaled =
        WindowScale::size(parent_, pageLabelHeight);
    const int margin = WindowScale::size(parent_, footerMargin);
    const int gap = WindowScale::size(parent_, footerGap);
    const int pageButtonHeightScaled =
        WindowScale::size(parent_, pageButtonHeight);
    const int generateButtonHeightScaled =
        WindowScale::size(parent_, generateButtonHeight);
    const int generateY = clientArea.bottom -
        generateButtonHeightScaled - margin;
    const int pageY = generateY - pageButtonHeightScaled - gap;

    RECT tableArea = clientArea;
    tableArea.top += WindowScale::size(parent_, tableTop);
    tableArea.bottom = std::max(
        tableArea.top,
        static_cast<LONG>(
            pageY - pageLabelHeightScaled - margin - gap));

    if (!generated_ || candles_.empty()) {
        const std::string message = !status_.empty()
            ? status_
            : generated_
                ? "Brak świec w wybranym zakresie dat."
                : "Wybierz zakres dat i kliknij „Generuj”.";
        std::wstring wideMessage;
        if (!convertUtf8ToWide(message, wideMessage)) {
            wideMessage = L"Nie można wyświetlić tabeli świec.";
        }
        WindowScale::drawText(
            deviceContext, parent_, wideMessage, tableArea,
            DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
    } else {
        HFONT tableFont = CreateFontW(
            -WindowScale::size(parent_, 10), 0, 0, 0, FW_NORMAL,
            FALSE, FALSE, FALSE, DEFAULT_CHARSET,
            OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
            DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
        if (tableFont == nullptr) {
            DrawTextW(
                deviceContext, L"Nie można utworzyć tabeli świec.", -1,
                &tableArea, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
            return;
        }

        const HGDIOBJ previousFont = SelectObject(deviceContext, tableFont);
        const LONG rowHeight = std::max<LONG>(
            1, (tableArea.bottom - tableArea.top) /
                   static_cast<LONG>(candles_.size() + 1));
        const int columnWidths[] = {14, 22, 10, 10, 10, 10, 24};
        const wchar_t* columnNames[] = {
            L"Symbol", L"Czas", L"Open", L"High", L"Low", L"Close", L"Volume"
        };
        const UINT textFormat =
            DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS;
        const LONG totalWidth = tableArea.right - tableArea.left;
        const LONG padding = WindowScale::size(parent_, 4);
        LONG rowTop = tableArea.top;
        LONG columnLeft = tableArea.left;

        SetTextColor(deviceContext, RGB(0, 0, 0));
        SetBkMode(deviceContext, TRANSPARENT);
        for (std::size_t column = 0; column < 7; ++column) {
            const LONG columnWidth =
                totalWidth * columnWidths[column] / 100;
            RECT cell{
                columnLeft + padding, rowTop,
                columnLeft + columnWidth - padding, rowTop + rowHeight};
            drawCell(deviceContext, columnNames[column], cell);
            columnLeft += columnWidth;
        }
        rowTop += rowHeight;

        for (const CandleReportEntry& candle : candles_) {
            const std::string values[] = {
                candle.symbol,
                candle.datetime,
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume.empty() ? "--" : candle.volume
            };
            columnLeft = tableArea.left;
            for (std::size_t column = 0; column < 7; ++column) {
                std::wstring wideValue;
                if (!convertUtf8ToWide(values[column], wideValue)) {
                    wideValue = L"Nieprawidłowy tekst";
                }
                const LONG columnWidth =
                    totalWidth * columnWidths[column] / 100;
                RECT cell{
                    columnLeft + padding, rowTop,
                    columnLeft + columnWidth - padding, rowTop + rowHeight};
                drawCell(deviceContext, wideValue, cell);
                columnLeft += columnWidth;
            }
            rowTop += rowHeight;
        }
        SelectObject(deviceContext, previousFont);
        DeleteObject(tableFont);
    }

    if (generated_) {
        const std::wstring pageLabel =
            L"Strona " + std::to_wstring(currentPage_ + 1);
        RECT pageBounds = clientArea;
        pageBounds.top = pageY - pageLabelHeightScaled - margin;
        pageBounds.bottom = pageY - margin;
        WindowScale::drawText(
            deviceContext, parent_, pageLabel, pageBounds,
            DT_CENTER | DT_VCENTER | DT_SINGLELINE);
    }
}

/**
 * Pobiera wybraną stronę tabeli z zapisanym zakresem dat.
 */
void TransactionsPage::loadCurrentPage() {
    std::vector<CandleReportEntry> receivedCandles;
    bool receivedHasNextPage = false;
    std::string error;
    if (!CandleHistoryClient::fetchFilteredCandlePage(
            appliedStartDate_, appliedEndDate_, currentPage_,
            receivedCandles, receivedHasNextPage, error)) {
        candles_.clear();
        hasNextPage_ = false;
        ErrorLogger::error(error);
        status_ = std::move(error);
        return;
    }

    candles_ = std::move(receivedCandles);
    hasNextPage_ = receivedHasNextPage;
    status_.clear();
}

/**
 * Zapisuje bieżący zakres dat i pobiera jego pierwszą stronę.
 */
void TransactionsPage::generateReport() {
    generated_ = false;
    currentPage_ = 0;
    hasNextPage_ = false;
    candles_.clear();
    status_.clear();

    SYSTEMTIME startDate{};
    SYSTEMTIME endDate{};
    if (SendMessageW(
            fromDatePicker_, DTM_GETSYSTEMTIME, 0,
            reinterpret_cast<LPARAM>(&startDate)) != GDT_VALID ||
        SendMessageW(
            toDatePicker_, DTM_GETSYSTEMTIME, 0,
            reinterpret_cast<LPARAM>(&endDate)) != GDT_VALID) {
        status_ = "Nie udało się odczytać wybranego zakresu dat.";
    } else {
        appliedStartDate_ = formatDate(startDate);
        appliedEndDate_ = formatDate(endDate);
        if (appliedEndDate_ < appliedStartDate_) {
            status_ = "Data „do” nie może być wcześniejsza niż data „od”.";
        } else {
            generated_ = true;
            loadCurrentPage();
        }
    }

    updateControls();
    InvalidateRect(parent_, nullptr, TRUE);
}

/**
 * Pokazuje lub ukrywa kontrolki i ustawia ich aktywność.
 */
void TransactionsPage::updateControls() {
    const int visibility = visible_ ? SW_SHOW : SW_HIDE;
    for (HWND control : {
             fromDateLabel_, fromDatePicker_, toDateLabel_, toDatePicker_,
             generateButton_}) {
        if (control != nullptr) {
            ShowWindow(control, visibility);
        }
    }

    const int paginationVisibility =
        visible_ && generated_ ? SW_SHOW : SW_HIDE;
    if (previousPageButton_ != nullptr) {
        ShowWindow(previousPageButton_, paginationVisibility);
        EnableWindow(previousPageButton_, currentPage_ > 0);
    }
    if (nextPageButton_ != nullptr) {
        ShowWindow(nextPageButton_, paginationVisibility);
        EnableWindow(nextPageButton_, hasNextPage_);
    }
}
