#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "SignalsPage.h"

#include <algorithm>
#include <string>

namespace {

constexpr int previousPageButtonId = 1301;
constexpr int nextPageButtonId = 1302;
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
 * Rysuje tekst w komórce tabeli z przycięciem dłuższych wartości.
 */
void drawCell(HDC deviceContext, const std::wstring& text, RECT bounds) {
    DrawTextW(
        deviceContext, text.c_str(), static_cast<int>(text.size()),
        &bounds, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
}

} // namespace

/**
 * Tworzy przyciski paginacji widoku sygnałów.
 */
bool SignalsPage::create(HINSTANCE instance, HWND parent) {
    parent_ = parent;
    previousPageButton_ = CreateWindowExW(
        0, L"BUTTON", L"Poprzednia",
        WS_CHILD | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, pageButtonWidth, pageButtonHeight,
        parent_, reinterpret_cast<HMENU>(
            static_cast<INT_PTR>(previousPageButtonId)),
        instance, nullptr);
    if (previousPageButton_ == nullptr) {
        return false;
    }

    nextPageButton_ = CreateWindowExW(
        0, L"BUTTON", L"Następna",
        WS_CHILD | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, pageButtonWidth, pageButtonHeight,
        parent_, reinterpret_cast<HMENU>(
            static_cast<INT_PTR>(nextPageButtonId)),
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
 * Otwiera widok i pobiera najnowszą stronę sygnałów.
 */
void SignalsPage::open() {
    visible_ = true;
    currentPage_ = 0;
    loadCurrentPage();
    updateControls();
}

/**
 * Zamyka widok i ukrywa jego przyciski paginacji.
 */
void SignalsPage::close() {
    visible_ = false;
    updateControls();
}

/**
 * Informuje, czy widok sygnałów jest aktualnie widoczny.
 */
bool SignalsPage::isVisible() const {
    return visible_;
}

/**
 * Obsługuje kliknięcia przycisków poprzedniej i następnej strony.
 */
bool SignalsPage::handleCommand(int controlId, int notificationCode) {
    if (notificationCode != BN_CLICKED) {
        return false;
    }

    if (controlId == previousPageButtonId && currentPage_ > 0) {
        --currentPage_;
    } else if (controlId == nextPageButtonId && hasNextPage_) {
        ++currentPage_;
    } else {
        return controlId == previousPageButtonId ||
               controlId == nextPageButtonId;
    }

    loadCurrentPage();
    updateControls();
    InvalidateRect(parent_, nullptr, TRUE);
    return true;
}

/**
 * Ustawia przyciski paginacji na dole okna.
 */
void SignalsPage::positionControls() {
    if (parent_ == nullptr) {
        return;
    }

    RECT clientArea{};
    GetClientRect(parent_, &clientArea);
    const int clientWidth = clientArea.right - clientArea.left;
    const int clientHeight = clientArea.bottom - clientArea.top;
    const int sideMargin = 8;
    const int buttonY = std::max(
        0, clientHeight - pageButtonHeight - 16);

    if (previousPageButton_ != nullptr) {
        MoveWindow(
            previousPageButton_, sideMargin, buttonY,
            pageButtonWidth, pageButtonHeight, TRUE);
    }
    if (nextPageButton_ != nullptr) {
        MoveWindow(
            nextPageButton_,
            std::max(sideMargin, clientWidth - pageButtonWidth - sideMargin),
            buttonY, pageButtonWidth, pageButtonHeight, TRUE);
    }
}

/**
 * Rysuje kolumny tabeli sygnałów z danymi JSON.
 */
void SignalsPage::draw(HDC deviceContext, RECT& clientArea) const {
    if (!status_.empty() || signals_.empty()) {
        const std::string message = status_.empty()
            ? "Brak zapisanych sygnałów."
            : status_;
        std::wstring wideMessage;
        if (!convertUtf8ToWide(message, wideMessage)) {
            wideMessage = L"Nie można wyświetlić tabeli sygnałów.";
        }
        DrawTextW(
            deviceContext, wideMessage.c_str(),
            static_cast<int>(wideMessage.size()), &clientArea,
            DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
        return;
    }

    HFONT tableFont = CreateFontW(
        -11, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
        OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
        DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
    if (tableFont == nullptr) {
        DrawTextW(
            deviceContext, L"Nie można utworzyć tabeli sygnałów.", -1,
            &clientArea, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        return;
    }

    const HGDIOBJ previousFont = SelectObject(deviceContext, tableFont);
    RECT tableArea = clientArea;
    tableArea.bottom = std::max(tableArea.top, tableArea.bottom - 20);
    const LONG rowHeight = std::max<LONG>(
        1, (tableArea.bottom - tableArea.top) /
               static_cast<LONG>(signals_.size() + 1));
    const int columnWidths[] = {5, 14, 8, 8, 7, 16, 8, 8, 7, 19};
    const wchar_t* columnNames[] = {
        L"ID", L"Czas (Polska)", L"Produkt", L"Cena", L"Sygnał",
        L"Algorytm", L"AI", L"Transakcja", L"Świece", L"Parametry JSON"
    };
    const UINT textFormat =
        DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS;

    SetTextColor(deviceContext, RGB(0, 0, 0));
    SetBkMode(deviceContext, TRANSPARENT);
    LONG rowTop = tableArea.top;
    LONG columnLeft = tableArea.left;
    const LONG totalWidth = tableArea.right - tableArea.left;
    for (std::size_t column = 0; column < 10; ++column) {
        const LONG columnWidth = totalWidth * columnWidths[column] / 100;
        RECT cell{columnLeft + 2, rowTop, columnLeft + columnWidth - 2,
                  rowTop + rowHeight};
        DrawTextW(deviceContext, columnNames[column], -1, &cell, textFormat);
        columnLeft += columnWidth;
    }
    rowTop += rowHeight;

    for (const SignalReportEntry& signal : signals_) {
        const std::string values[] = {
            signal.id,
            signal.datetime,
            signal.product,
            signal.price,
            signal.direction == "long"
                ? "LONG"
                : (signal.direction == "short" ? "SHORT" : "NONE"),
            signal.algorithmName,
            signal.aiDecision,
            signal.transactionId,
            signal.candlesCount,
            signal.signalParameters
        };
        columnLeft = tableArea.left;
        for (std::size_t column = 0; column < 10; ++column) {
            std::wstring wideValue;
            if (!convertUtf8ToWide(values[column], wideValue)) {
                wideValue = L"Nieprawidłowy tekst";
            }
            const LONG columnWidth = totalWidth * columnWidths[column] / 100;
            RECT cell{columnLeft + 2, rowTop, columnLeft + columnWidth - 2,
                      rowTop + rowHeight};
            drawCell(deviceContext, wideValue, cell);
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
 * Pobiera aktualną stronę sygnałów i aktualizuje stan widoku.
 */
void SignalsPage::loadCurrentPage() {
    std::vector<SignalReportEntry> receivedSignals;
    bool receivedHasNextPage = false;
    std::string error;
    if (!CandleHistoryClient::fetchSignalPage(
            currentPage_, receivedSignals, receivedHasNextPage, error)) {
        signals_.clear();
        hasNextPage_ = false;
        status_ = std::move(error);
        return;
    }

    signals_ = std::move(receivedSignals);
    hasNextPage_ = receivedHasNextPage;
    status_.clear();
}

/**
 * Pokazuje lub ukrywa przyciski paginacji.
 */
void SignalsPage::updateControls() {
    if (previousPageButton_ != nullptr) {
        ShowWindow(previousPageButton_, visible_ ? SW_SHOW : SW_HIDE);
        EnableWindow(previousPageButton_, currentPage_ > 0);
    }
    if (nextPageButton_ != nullptr) {
        ShowWindow(nextPageButton_, visible_ ? SW_SHOW : SW_HIDE);
        EnableWindow(nextPageButton_, hasNextPage_);
    }
}
