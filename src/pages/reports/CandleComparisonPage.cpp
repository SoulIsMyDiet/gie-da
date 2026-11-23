#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "CandleComparisonPage.h"
#include "ErrorLogger.h"
#include "../WindowScale.h"

#include <algorithm>
#include <array>
#include <limits>
#include <optional>
#include <string_view>
#include <utility>

namespace {

constexpr int previousPageButtonId = 1401;
constexpr int nextPageButtonId = 1402;
constexpr int pageButtonWidth = 112;
constexpr int pageButtonHeight = 36;

struct DecimalParts {
    bool negative = false;
    std::string_view integer;
    std::string_view fraction;
};

/**
 * Sprawdza i normalizuje tekst liczby dziesiętnej z odpowiedzi API.
 */
std::optional<DecimalParts> parseDecimal(std::string_view value) {
    if (value.empty()) {
        return std::nullopt;
    }

    DecimalParts parts;
    if (value.front() == '-' || value.front() == '+') {
        parts.negative = value.front() == '-';
        value.remove_prefix(1);
    }
    if (value.empty()) {
        return std::nullopt;
    }

    const std::size_t decimalPoint = value.find('.');
    parts.integer = value.substr(0, decimalPoint);
    parts.fraction = decimalPoint == std::string_view::npos
        ? std::string_view{}
        : value.substr(decimalPoint + 1);
    if (parts.integer.empty()) {
        return std::nullopt;
    }

    const auto containsOnlyDigits = [](std::string_view digits) {
        return std::all_of(
            digits.begin(), digits.end(),
            [](char character) {
                return character >= '0' && character <= '9';
            });
    };
    if (!containsOnlyDigits(parts.integer) ||
        !containsOnlyDigits(parts.fraction) ||
        (decimalPoint != std::string_view::npos &&
         value.find('.', decimalPoint + 1) != std::string_view::npos)) {
        return std::nullopt;
    }

    const std::size_t firstNonZero = parts.integer.find_first_not_of('0');
    parts.integer = firstNonZero == std::string_view::npos
        ? std::string_view("0")
        : parts.integer.substr(firstNonZero);
    const std::size_t lastNonZero =
        parts.fraction.find_last_not_of('0');
    parts.fraction = lastNonZero == std::string_view::npos
        ? std::string_view{}
        : parts.fraction.substr(0, lastNonZero + 1);
    if (parts.integer == "0" && parts.fraction.empty()) {
        parts.negative = false;
    }
    return parts;
}

/**
 * Porównuje dwie liczby dziesiętne dokładnie, bez zaokrąglania float.
 */
std::optional<int> compareDecimalValues(
    std::string_view first, std::string_view second) {
    const std::optional<DecimalParts> firstParts = parseDecimal(first);
    const std::optional<DecimalParts> secondParts = parseDecimal(second);
    if (!firstParts || !secondParts) {
        return std::nullopt;
    }
    if (firstParts->negative != secondParts->negative) {
        return firstParts->negative ? -1 : 1;
    }

    int comparison = 0;
    if (firstParts->integer.size() != secondParts->integer.size()) {
        comparison = firstParts->integer.size() < secondParts->integer.size()
            ? -1
            : 1;
    } else if (firstParts->integer != secondParts->integer) {
        comparison = firstParts->integer < secondParts->integer ? -1 : 1;
    } else {
        const std::size_t fractionLength = std::max(
            firstParts->fraction.size(), secondParts->fraction.size());
        for (std::size_t index = 0; index < fractionLength; ++index) {
            const char firstDigit = index < firstParts->fraction.size()
                ? firstParts->fraction[index]
                : '0';
            const char secondDigit = index < secondParts->fraction.size()
                ? secondParts->fraction[index]
                : '0';
            if (firstDigit != secondDigit) {
                comparison = firstDigit < secondDigit ? -1 : 1;
                break;
            }
        }
    }
    return firstParts->negative ? -comparison : comparison;
}

/**
 * Konwertuje tekst UTF-8 z API do tekstu Unicode używanego przez Windows.
 */
bool convertUtf8ToWide(
    const std::string& text, std::wstring& convertedText) {
    if (text.empty()) {
        convertedText.clear();
        return true;
    }
    if (text.size() > static_cast<std::size_t>(
                          std::numeric_limits<int>::max())) {
        return false;
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
 * Zwalnia czcionkę przypisaną przyciskom paginacji.
 */
CandleComparisonPage::~CandleComparisonPage() {
    if (buttonFont_ != nullptr) {
        DeleteObject(buttonFont_);
    }
}

/**
 * Tworzy przyciski paginacji należące do zestawienia.
 */
bool CandleComparisonPage::create(HINSTANCE instance, HWND parent) {
    parent_ = parent;
    previousPageButton_ = CreateWindowExW(
        0, L"BUTTON", L"Poprzednia",
        WS_CHILD | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, WindowScale::size(parent_, pageButtonWidth),
        WindowScale::size(parent_, pageButtonHeight),
        parent_, reinterpret_cast<HMENU>(
            static_cast<INT_PTR>(previousPageButtonId)),
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
 * Otwiera widok i pobiera pierwszą, najnowszą stronę danych.
 */
void CandleComparisonPage::open() {
    visible_ = true;
    currentPage_ = 0;
    loadCurrentPage();
    updateControls();
}

/**
 * Zamyka widok i ukrywa kontrolki paginacji.
 */
void CandleComparisonPage::close() {
    visible_ = false;
    updateControls();
}

/**
 * Informuje, czy zestawienie jest aktualnie widoczne.
 */
bool CandleComparisonPage::isVisible() const {
    return visible_;
}

/**
 * Obsługuje kliknięcia przycisków poprzedniej i następnej strony.
 */
bool CandleComparisonPage::handleCommand(
    int controlId, int notificationCode) {
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
 * Ustawia przyciski paginacji odpowiednio do rozmiaru okna.
 */
void CandleComparisonPage::positionControls() {
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

    MoveWindow(
        previousPageButton_, previousX, buttonY,
        buttonWidth, buttonHeight, TRUE);
    MoveWindow(
        nextPageButton_, nextX, buttonY,
        buttonWidth, buttonHeight, TRUE);
}

/**
 * Rysuje dziewięciokolumnową tabelę wspólnych świec.
 */
void CandleComparisonPage::draw(
    HDC deviceContext, RECT& clientArea) const {
    if (!status_.empty() || candles_.empty()) {
        const std::string message = status_.empty()
            ? "Brak wspólnych świec z tabel candles i candles_IBKR."
            : status_;
        std::wstring wideMessage;
        if (!convertUtf8ToWide(message, wideMessage)) {
            wideMessage = L"Nie można wyświetlić zestawienia świec.";
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
    constexpr std::array<int, 9> columnWidths{{
        220, 100, 100, 100, 100, 95, 95, 95, 95
    }};
    constexpr std::array<const wchar_t*, 9> columnNames{{
        L"Symbol / godzina",
        L"Open candles", L"Low candles", L"High candles", L"Close candles",
        L"Open IBKR", L"Low IBKR", L"High IBKR", L"Close IBKR"
    }};
    const UINT textFormat =
        DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS;
    const LONG totalWidth = tableArea.right - tableArea.left;
    const LONG padding = WindowScale::size(parent_, 3);

    SetTextColor(deviceContext, RGB(0, 0, 0));
    SetBkMode(deviceContext, TRANSPARENT);
    LONG rowTop = tableArea.top;
    LONG columnLeft = tableArea.left;
    for (std::size_t column = 0; column < columnNames.size(); ++column) {
        const LONG columnWidth = totalWidth * columnWidths[column] / 1000;
        RECT cell{
            columnLeft + padding, rowTop,
            columnLeft + columnWidth - padding, rowTop + rowHeight
        };
        DrawTextW(
            deviceContext, columnNames[column], -1, &cell, textFormat);
        columnLeft += columnWidth;
    }
    rowTop += rowHeight;

    for (const CandleComparisonEntry& candle : candles_) {
        std::array<std::string, 9> values{};
        values[0] = candle.symbolAndDatetime;
        for (std::size_t index = 0; index < candle.prices.size(); ++index) {
            values[index + 1] = candle.prices[index];
        }

        columnLeft = tableArea.left;
        for (std::size_t column = 0; column < values.size(); ++column) {
            COLORREF textColor = RGB(0, 0, 0);
            if (column >= 5) {
                const std::size_t ibkrPriceIndex = column - 1;
                const std::size_t candlesPriceIndex = column - 5;
                const int comparison = *compareDecimalValues(
                    candle.prices[ibkrPriceIndex],
                    candle.prices[candlesPriceIndex]);
                textColor = comparison < 0
                    ? RGB(200, 0, 0)
                    : comparison > 0
                        ? RGB(0, 128, 0)
                        : RGB(0, 0, 0);
            }

            std::wstring wideValue;
            if (!convertUtf8ToWide(values[column], wideValue)) {
                wideValue = L"Nieprawidłowy tekst";
            }
            const LONG columnWidth = totalWidth * columnWidths[column] / 1000;
            RECT cell{
                columnLeft + padding, rowTop,
                columnLeft + columnWidth - padding, rowTop + rowHeight
            };
            SetTextColor(deviceContext, textColor);
            drawCell(deviceContext, wideValue, cell, textFormat);
            columnLeft += columnWidth;
        }
        rowTop += rowHeight;
    }

    SetTextColor(deviceContext, RGB(0, 0, 0));
    const std::wstring pageLabel =
        L"Strona " + std::to_wstring(currentPage_ + 1);
    RECT pageBounds = clientArea;
    pageBounds.top = tableArea.bottom;
    DrawTextW(
        deviceContext, pageLabel.c_str(), -1, &pageBounds,
        DT_CENTER | DT_VCENTER | DT_SINGLELINE);

    SelectObject(deviceContext, previousFont);
    DeleteObject(tableFont);
}

/**
 * Pobiera stronę i sprawdza poprawność cen przed ich porównaniem.
 */
void CandleComparisonPage::loadCurrentPage() {
    std::vector<CandleComparisonEntry> receivedCandles;
    bool receivedHasNextPage = false;
    std::string error;
    if (!CandleHistoryClient::fetchCandleComparisonPage(
            currentPage_, receivedCandles, receivedHasNextPage, error)) {
        candles_.clear();
        hasNextPage_ = false;
        ErrorLogger::error(error);
        status_ = std::move(error);
        return;
    }

    for (const CandleComparisonEntry& candle : receivedCandles) {
        for (const std::string& price : candle.prices) {
            if (!compareDecimalValues(price, price)) {
                error =
                    "Lokalne API zestawienia zwróciło nieprawidłową cenę.";
                candles_.clear();
                hasNextPage_ = false;
                status_ = error;
                ErrorLogger::error(error);
                return;
            }
        }
    }

    candles_ = std::move(receivedCandles);
    hasNextPage_ = receivedHasNextPage;
    status_.clear();
}

/**
 * Pokazuje lub ukrywa przyciski zgodnie ze stanem paginacji.
 */
void CandleComparisonPage::updateControls() {
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
