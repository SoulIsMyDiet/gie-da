#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "ErrorLogger.h"
#include "ReportsPage.h"
#include "../WindowScale.h"

#include <algorithm>
#include <string>

namespace {

constexpr int previousPageButtonId = 1101;
constexpr int nextPageButtonId = 1102;
constexpr int previousIbkrPageButtonId = 1111;
constexpr int nextIbkrPageButtonId = 1112;
constexpr int pageButtonWidth = 112;
constexpr int pageButtonHeight = 36;

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
 * Rysuje tekst w komórce i przycina zbyt długie wartości.
 */
void drawCell(
    HDC deviceContext, const std::wstring& text, RECT bounds, UINT format) {
    DrawTextW(
        deviceContext, text.c_str(), static_cast<int>(text.size()),
        &bounds, format);
}

} // namespace

/**
 * Wybiera źródło, z którego widok pobiera strony świec.
 */
ReportsPage::ReportsPage(CandleReportSource source)
    : source_(source) {}

/**
 * Zwalnia czcionkę przypisaną kontrolkom strony raportów.
 */
ReportsPage::~ReportsPage() {
    if (buttonFont_ != nullptr) {
        DeleteObject(buttonFont_);
    }
}

/**
 * Tworzy przyciski paginacji należące do strony raportów.
 */
bool ReportsPage::create(HINSTANCE instance, HWND parent) {
    parent_ = parent;
    const int previousButtonId =
        source_ == CandleReportSource::Ibkr
        ? previousIbkrPageButtonId
        : previousPageButtonId;
    const int nextButtonId =
        source_ == CandleReportSource::Ibkr
        ? nextIbkrPageButtonId
        : nextPageButtonId;
    previousPageButton_ = CreateWindowExW(
        0, L"BUTTON", L"Poprzednia",
        WS_CHILD | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, WindowScale::size(parent_, pageButtonWidth),
        WindowScale::size(parent_, pageButtonHeight),
        parent_, reinterpret_cast<HMENU>(
            static_cast<INT_PTR>(previousButtonId)),
        instance, nullptr);
    if (previousPageButton_ == nullptr) {
        return false;
    }

    nextPageButton_ = CreateWindowExW(
        0, L"BUTTON", L"Następna",
        WS_CHILD | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, WindowScale::size(parent_, pageButtonWidth),
        WindowScale::size(parent_, pageButtonHeight),
        parent_, reinterpret_cast<HMENU>(
            static_cast<INT_PTR>(nextButtonId)),
        instance, nullptr);
    if (nextPageButton_ == nullptr) {
        DestroyWindow(previousPageButton_);
        previousPageButton_ = nullptr;
        return false;
    }

    updateControls();
    return true;
}

/**
 * Otwiera stronę i pobiera pierwszą, najnowszą stronę świec.
 */
void ReportsPage::open() {
    visible_ = true;
    currentPage_ = 0;
    loadCurrentPage();
    updateControls();
}

/**
 * Zamyka stronę i ukrywa kontrolki paginacji.
 */
void ReportsPage::close() {
    visible_ = false;
    updateControls();
}

/**
 * Informuje, czy strona raportów jest aktualnie widoczna.
 */
bool ReportsPage::isVisible() const {
    return visible_;
}

/**
 * Obsługuje kliknięcia przycisków paginacji.
 */
bool ReportsPage::handleCommand(int controlId, int notificationCode) {
    if (notificationCode != BN_CLICKED) {
        return false;
    }

    const int previousButtonId =
        source_ == CandleReportSource::Ibkr
        ? previousIbkrPageButtonId
        : previousPageButtonId;
    const int nextButtonId =
        source_ == CandleReportSource::Ibkr
        ? nextIbkrPageButtonId
        : nextPageButtonId;
    if (controlId == previousButtonId && currentPage_ > 0) {
        --currentPage_;
    } else if (controlId == nextButtonId && hasNextPage_) {
        ++currentPage_;
    } else {
        return controlId == previousButtonId || controlId == nextButtonId;
    }

    loadCurrentPage();
    updateControls();
    InvalidateRect(parent_, nullptr, TRUE);
    return true;
}

/**
 * Ustawia przyciski na dole okna, dostosowując ich pozycję do rozmiaru.
 */
void ReportsPage::positionControls() {
    if (parent_ == nullptr) {
        return;
    }

    RECT clientArea{};
    GetClientRect(parent_, &clientArea);
    const int clientWidth = clientArea.right - clientArea.left;
    const int clientHeight = clientArea.bottom - clientArea.top;
    const int buttonWidth = WindowScale::size(parent_, pageButtonWidth);
    const int buttonHeight = WindowScale::size(parent_, pageButtonHeight);
    const int sideMargin = WindowScale::size(parent_, 8);
    const int bottomMargin = WindowScale::size(parent_, 16);
    const int previousX = sideMargin;
    const int nextX = std::max(
        sideMargin, clientWidth - buttonWidth - sideMargin);
    const int buttonY = std::max(
        0, clientHeight - buttonHeight - bottomMargin);
    WindowScale::updateFont(
        parent_, buttonFont_, buttonFontHeight_, 14,
        {previousPageButton_, nextPageButton_});

    if (previousPageButton_ != nullptr) {
        MoveWindow(previousPageButton_, previousX, buttonY,
                   buttonWidth, buttonHeight, TRUE);
    }
    if (nextPageButton_ != nullptr) {
        MoveWindow(nextPageButton_, nextX, buttonY,
                   buttonWidth, buttonHeight, TRUE);
    }
}

/**
 * Rysuje tabelę świec, komunikat stanu i numer aktualnej strony.
 */
void ReportsPage::draw(HDC deviceContext, RECT& clientArea) const {
    if (!status_.empty() || candles_.empty()) {
        const std::string message = status_.empty()
            ? "Brak świec do wyświetlenia."
            : status_;
        std::wstring wideMessage;
        if (!convertUtf8ToWide(message, wideMessage)) {
            wideMessage = L"Nie można wyświetlić raportu świec.";
        }
        WindowScale::drawText(
            deviceContext, parent_, wideMessage, clientArea,
            DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
        return;
    }

    HFONT tableFont = CreateFontW(
        -WindowScale::size(parent_, 12), 0, 0, 0, FW_NORMAL,
        FALSE, FALSE, FALSE, DEFAULT_CHARSET,
        OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
        DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
    if (tableFont == nullptr) {
        DrawTextW(
            deviceContext, L"Nie można utworzyć tabeli świec.", -1,
            &clientArea, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        return;
    }

    const HGDIOBJ previousFont = SelectObject(deviceContext, tableFont);
    RECT tableArea = clientArea;
    tableArea.bottom = std::max(
        tableArea.top, tableArea.bottom - WindowScale::size(parent_, 20));
    const LONG rowHeight = std::max<LONG>(
        1, (tableArea.bottom - tableArea.top) /
               static_cast<LONG>(candles_.size() + 1));
    const int columnWidths[] = {14, 24, 11, 11, 11, 11, 18};
    const wchar_t* columnNames[] = {
        L"Symbol", L"Czas (Warszawa)", L"Open", L"High",
        L"Low", L"Close", L"Volume"
    };
    const UINT textFormat =
        DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS;

    SetTextColor(deviceContext, RGB(0, 0, 0));
    SetBkMode(deviceContext, TRANSPARENT);
    LONG rowTop = tableArea.top;
    LONG columnLeft = tableArea.left;
    const LONG totalWidth = tableArea.right - tableArea.left;
    for (std::size_t column = 0; column < 7; ++column) {
        const LONG columnWidth = totalWidth * columnWidths[column] / 100;
        const LONG padding = WindowScale::size(parent_, 4);
        RECT cell{columnLeft + padding, rowTop, columnLeft + columnWidth - padding,
                  rowTop + rowHeight};
        drawCell(deviceContext, columnNames[column], cell, textFormat);
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
            const LONG columnWidth = totalWidth * columnWidths[column] / 100;
            const LONG padding = WindowScale::size(parent_, 4);
            RECT cell{columnLeft + padding, rowTop, columnLeft + columnWidth - padding,
                      rowTop + rowHeight};
            drawCell(deviceContext, wideValue, cell, textFormat);
            columnLeft += columnWidth;
        }
        rowTop += rowHeight;
    }

    const std::wstring pageLabel =
        L"Strona " + std::to_wstring(currentPage_ + 1);
    RECT pageBounds = clientArea;
    pageBounds.top = tableArea.bottom;
    pageBounds.bottom = clientArea.bottom;
    DrawTextW(
        deviceContext, pageLabel.c_str(), -1, &pageBounds,
        DT_CENTER | DT_VCENTER | DT_SINGLELINE);

    SelectObject(deviceContext, previousFont);
    DeleteObject(tableFont);
}

/**
 * Pobiera dane aktualnej strony i aktualizuje stan widoku.
 */
void ReportsPage::loadCurrentPage() {
    std::vector<CandleReportEntry> receivedCandles;
    bool receivedHasNextPage = false;
    std::string error;
    const bool receivedPage =
        source_ == CandleReportSource::Ibkr
        ? CandleHistoryClient::fetchIbkrCandlePage(
              currentPage_, receivedCandles, receivedHasNextPage, error)
        : CandleHistoryClient::fetchCandlePage(
              currentPage_, receivedCandles, receivedHasNextPage, error);
    if (!receivedPage) {
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
 * Pokazuje lub ukrywa kontrolki paginacji zgodnie ze stanem strony.
 */
void ReportsPage::updateControls() {
    if (previousPageButton_ != nullptr) {
        ShowWindow(
            previousPageButton_, visible_ ? SW_SHOW : SW_HIDE);
        EnableWindow(previousPageButton_, currentPage_ > 0);
    }
    if (nextPageButton_ != nullptr) {
        ShowWindow(nextPageButton_, visible_ ? SW_SHOW : SW_HIDE);
        EnableWindow(nextPageButton_, hasNextPage_);
    }
}
