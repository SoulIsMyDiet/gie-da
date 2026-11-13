#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "SignalsPage.h"

#include <algorithm>
#include <string>

namespace {

constexpr int previousPageButtonId = 1301;
constexpr int nextPageButtonId = 1302;
constexpr int detailsButtonIdBase = 1400;
constexpr int backButtonId = 1415;
constexpr int pageButtonWidth = 112;
constexpr int pageButtonHeight = 36;
constexpr int detailsButtonHeight = 22;
constexpr int pageContentBottomMargin = pageButtonHeight + 32;
constexpr int pageLabelHeight = 20;
constexpr int signalColumnWidths[] = {22, 18, 14, 30, 16};

struct StyledText {
    std::wstring value;
    bool bold = false;
    COLORREF color = RGB(0, 0, 0);
};

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

/**
 * Rysuje zawijany akapit z fragmentami o osobnym kolorze i grubości czcionki.
 */
LONG drawWrappedParagraph(
    HDC deviceContext,
    RECT bounds,
    LONG top,
    const std::vector<StyledText>& fragments,
    HFONT regularFont,
    HFONT boldFont) {
    TEXTMETRICW textMetrics{};
    SelectObject(deviceContext, regularFont);
    GetTextMetricsW(deviceContext, &textMetrics);
    const LONG lineHeight = textMetrics.tmHeight + 6;
    LONG x = bounds.left;
    LONG y = top;

    for (const StyledText& fragment : fragments) {
        SelectObject(deviceContext, fragment.bold ? boldFont : regularFont);
        SetTextColor(deviceContext, fragment.color);

        std::size_t wordStart = 0;
        while (wordStart < fragment.value.size()) {
            while (wordStart < fragment.value.size() &&
                   fragment.value[wordStart] == L' ') {
                ++wordStart;
            }
            if (wordStart == fragment.value.size()) {
                break;
            }
            std::size_t wordEnd = fragment.value.find(L' ', wordStart);
            if (wordEnd == std::wstring::npos) {
                wordEnd = fragment.value.size();
            }
            const std::wstring word =
                fragment.value.substr(wordStart, wordEnd - wordStart);
            SIZE wordSize{};
            GetTextExtentPoint32W(
                deviceContext, word.c_str(),
                static_cast<int>(word.size()), &wordSize);

            SIZE spaceSize{};
            const wchar_t firstCharacter = word.front();
            const bool startsWithPunctuation =
                firstCharacter == L';' || firstCharacter == L',' ||
                firstCharacter == L'.' || firstCharacter == L':' ||
                firstCharacter == L'!' || firstCharacter == L'?';
            const bool needsSpace =
                x != bounds.left && !startsWithPunctuation;
            if (needsSpace) {
                GetTextExtentPoint32W(deviceContext, L" ", 1, &spaceSize);
            }
            if (needsSpace &&
                x + spaceSize.cx + wordSize.cx > bounds.right) {
                x = bounds.left;
                y += lineHeight;
            } else if (needsSpace) {
                TextOutW(deviceContext, x, y, L" ", 1);
                x += spaceSize.cx;
            }

            TextOutW(
                deviceContext, x, y, word.c_str(),
                static_cast<int>(word.size()));
            x += wordSize.cx;
            wordStart = wordEnd + 1;
        }
    }

    return y + lineHeight;
}

/**
 * Określa znak liczby dziesiętnej zapisanej jako tekst bez konwersji zmiennoprzecinkowej.
 */
int decimalSign(const std::string& value) {
    bool isNegative = false;
    bool hasDigit = false;
    bool hasNonZeroDigit = false;
    bool hasDecimalPoint = false;
    for (std::size_t index = 0; index < value.size(); ++index) {
        const char character = value[index];
        if (index == 0 && (character == '-' || character == '+')) {
            isNegative = character == '-';
        } else if (character >= '0' && character <= '9') {
            hasDigit = true;
            hasNonZeroDigit = hasNonZeroDigit || character != '0';
        } else if (character == '.' && !hasDecimalPoint) {
            hasDecimalPoint = true;
        } else {
            return 2;
        }
    }
    if (!hasDigit) {
        return 2;
    }
    if (!hasNonZeroDigit) {
        return 0;
    }
    return isNegative ? -1 : 1;
}

} // namespace

/**
 * Tworzy przyciski paginacji widoku sygnałów.
 */
bool SignalsPage::create(HINSTANCE instance, HWND parent) {
    instance_ = instance;
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

    backButton_ = CreateWindowExW(
        0, L"BUTTON", L"Powrót",
        WS_CHILD | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, pageButtonWidth, pageButtonHeight,
        parent_, reinterpret_cast<HMENU>(
            static_cast<INT_PTR>(backButtonId)),
        instance_, nullptr);
    if (backButton_ == nullptr) {
        DestroyWindow(previousPageButton_);
        DestroyWindow(nextPageButton_);
        previousPageButton_ = nullptr;
        nextPageButton_ = nullptr;
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
    detailsVisible_ = false;
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

    if (controlId == backButtonId) {
        detailsVisible_ = false;
        detailsStatus_.clear();
        updateControls();
        InvalidateRect(parent_, nullptr, TRUE);
        return true;
    }
    if (controlId >= detailsButtonIdBase &&
        controlId < detailsButtonIdBase + static_cast<int>(signals_.size())) {
        openDetails(static_cast<std::size_t>(controlId - detailsButtonIdBase));
        updateControls();
        InvalidateRect(parent_, nullptr, TRUE);
        return true;
    }
    if (detailsVisible_) {
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

    if (backButton_ != nullptr) {
        MoveWindow(
            backButton_, std::max(0, (clientWidth - pageButtonWidth) / 2),
            buttonY, pageButtonWidth, pageButtonHeight, TRUE);
    }
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

    const LONG contentBottom = std::max(
        0, clientHeight - pageContentBottomMargin - pageLabelHeight);
    const LONG rowHeight = std::max<LONG>(
        1, contentBottom / static_cast<LONG>(signals_.size() + 1));
    const LONG buttonHeight =
        std::min<LONG>(detailsButtonHeight, std::max<LONG>(1, rowHeight - 2));
    LONG buttonX = 0;
    for (std::size_t column = 0; column < 4; ++column) {
        buttonX += clientWidth * signalColumnWidths[column] / 100;
    }
    buttonX += 4;
    const LONG buttonWidth =
        std::max<LONG>(
            1, clientWidth * signalColumnWidths[4] / 100 - 8);
    for (std::size_t index = 0; index < detailsButtons_.size(); ++index) {
        const LONG rowTop = rowHeight * static_cast<LONG>(index + 1);
        const LONG buttonTop = rowTop + (rowHeight - buttonHeight) / 2;
        MoveWindow(
            detailsButtons_[index], buttonX, buttonTop,
            buttonWidth, buttonHeight, TRUE);
    }
}

/**
 * Rysuje czas, produkt, cenę i algorytm; przyciski szczegółów są osobnymi kontrolkami.
 */
void SignalsPage::draw(HDC deviceContext, RECT& clientArea) const {
    if (detailsVisible_) {
        drawDetails(deviceContext, clientArea);
        return;
    }

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
    const wchar_t* columnNames[] = {
        L"Czas (Polska)", L"Produkt", L"Cena", L"Algorytm", L""
    };
    const UINT textFormat =
        DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS;

    SetTextColor(deviceContext, RGB(0, 0, 0));
    SetBkMode(deviceContext, TRANSPARENT);
    LONG rowTop = tableArea.top;
    LONG columnLeft = tableArea.left;
    const LONG totalWidth = tableArea.right - tableArea.left;
    for (std::size_t column = 0; column < 5; ++column) {
        const LONG columnWidth =
            totalWidth * signalColumnWidths[column] / 100;
        RECT cell{columnLeft + 2, rowTop, columnLeft + columnWidth - 2,
                  rowTop + rowHeight};
        DrawTextW(deviceContext, columnNames[column], -1, &cell, textFormat);
        columnLeft += columnWidth;
    }
    rowTop += rowHeight;

    for (const SignalReportEntry& signal : signals_) {
        const std::string values[] = {
            signal.datetime,
            signal.product,
            signal.price,
            signal.algorithmName,
        };
        columnLeft = tableArea.left;
        for (std::size_t column = 0; column < 4; ++column) {
            std::wstring wideValue;
            if (!convertUtf8ToWide(values[column], wideValue)) {
                wideValue = L"Nieprawidłowy tekst";
            }
            const LONG columnWidth =
                totalWidth * signalColumnWidths[column] / 100;
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
        createDetailsButtons();
        positionControls();
        return;
    }

    signals_ = std::move(receivedSignals);
    hasNextPage_ = receivedHasNextPage;
    status_.clear();
    if (!createDetailsButtons()) {
        status_ = "Nie udało się utworzyć przycisków szczegółów.";
    }
    positionControls();
}

/**
 * Pokazuje lub ukrywa przyciski paginacji.
 */
void SignalsPage::updateControls() {
    if (previousPageButton_ != nullptr) {
        ShowWindow(
            previousPageButton_,
            visible_ && !detailsVisible_ ? SW_SHOW : SW_HIDE);
        EnableWindow(previousPageButton_, currentPage_ > 0 && !detailsVisible_);
    }
    if (nextPageButton_ != nullptr) {
        ShowWindow(
            nextPageButton_,
            visible_ && !detailsVisible_ ? SW_SHOW : SW_HIDE);
        EnableWindow(nextPageButton_, hasNextPage_ && !detailsVisible_);
    }
    if (backButton_ != nullptr) {
        ShowWindow(
            backButton_,
            visible_ && detailsVisible_ ? SW_SHOW : SW_HIDE);
    }
    for (HWND button : detailsButtons_) {
        ShowWindow(
            button,
            visible_ && !detailsVisible_ ? SW_SHOW : SW_HIDE);
    }
}

/**
 * Tworzy przyciski szczegółów, zachowując po jednym dla każdego wiersza tabeli.
 */
bool SignalsPage::createDetailsButtons() {
    for (HWND button : detailsButtons_) {
        DestroyWindow(button);
    }
    detailsButtons_.clear();
    detailsButtons_.reserve(signals_.size());
    for (std::size_t index = 0; index < signals_.size(); ++index) {
        HWND button = CreateWindowExW(
            0, L"BUTTON", L"Szczegóły",
            WS_CHILD | WS_TABSTOP | BS_PUSHBUTTON,
            0, 0, 1, 1, parent_,
            reinterpret_cast<HMENU>(static_cast<INT_PTR>(
                detailsButtonIdBase + static_cast<int>(index))),
            instance_, nullptr);
        if (button == nullptr) {
            for (HWND createdButton : detailsButtons_) {
                DestroyWindow(createdButton);
            }
            detailsButtons_.clear();
            return false;
        }
        detailsButtons_.push_back(button);
    }
    updateControls();
    return true;
}

/**
 * Pobiera wyniki sygnału wybranego z tabeli i przełącza widok na szczegóły.
 */
void SignalsPage::openDetails(std::size_t signalIndex) {
    if (signalIndex >= signals_.size()) {
        detailsStatus_ = "Nie można odnaleźć wybranego sygnału.";
        details_ = {};
        detailsVisible_ = true;
        return;
    }

    SignalPotentialDetails receivedDetails;
    std::string error;
    if (!CandleHistoryClient::fetchSignalPotential(
            signals_[signalIndex].id, receivedDetails, error)) {
        details_ = {};
        detailsStatus_ = std::move(error);
    } else {
        details_ = std::move(receivedDetails);
        detailsStatus_.clear();
    }
    detailsVisible_ = true;
}

/**
 * Rysuje opis wyniku sygnału, kolorując i pogrubiając wskazane wartości.
 */
void SignalsPage::drawDetails(
    HDC deviceContext, RECT& clientArea) const {
    if (!detailsStatus_.empty()) {
        std::wstring message;
        if (!convertUtf8ToWide(detailsStatus_, message)) {
            message = L"Nie udało się pobrać szczegółów sygnału.";
        }
        DrawTextW(
            deviceContext, message.c_str(), static_cast<int>(message.size()),
            &clientArea, DT_CENTER | DT_VCENTER | DT_WORDBREAK);
        return;
    }

    const int sign = decimalSign(details_.deal100Pln);
    if (details_.deal100Pln.empty() || details_.saldo20.empty() ||
        details_.maxCandle.empty() || details_.spread.empty()) {
        DrawTextW(
            deviceContext,
            L"Wynik będzie dostępny po zebraniu danych z 20 kolejnych świec.",
            -1, &clientArea, DT_CENTER | DT_VCENTER | DT_WORDBREAK);
        return;
    }
    if (sign == 2) {
        DrawTextW(
            deviceContext, L"Wartość wyniku sygnału jest nieprawidłowa.", -1,
            &clientArea, DT_CENTER | DT_VCENTER | DT_WORDBREAK);
        return;
    }

    HFONT regularFont = CreateFontW(
        -14, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
        OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
        DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
    HFONT boldFont = CreateFontW(
        -14, 0, 0, 0, FW_BOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
        OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
        DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
    if (regularFont == nullptr || boldFont == nullptr) {
        if (regularFont != nullptr) {
            DeleteObject(regularFont);
        }
        if (boldFont != nullptr) {
            DeleteObject(boldFont);
        }
        DrawTextW(
            deviceContext, L"Nie można wyświetlić szczegółów sygnału.", -1,
            &clientArea, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        return;
    }

    const HGDIOBJ previousFont = SelectObject(deviceContext, boldFont);
    SetTextColor(deviceContext, RGB(0, 0, 0));
    RECT titleBounds = clientArea;
    titleBounds.bottom = titleBounds.top + 44;
    DrawTextW(
        deviceContext, L"Szczegóły sygnału", -1, &titleBounds,
        DT_CENTER | DT_VCENTER | DT_SINGLELINE);

    const COLORREF resultColor = sign > 0
        ? RGB(0, 128, 0)
        : (sign < 0 ? RGB(200, 0, 0) : RGB(0, 0, 0));
    std::wstring saldo;
    std::wstring minute;
    std::wstring deal;
    std::wstring spread;
    if (!convertUtf8ToWide(details_.saldo20, saldo)) {
        saldo = L"--";
    }
    if (!convertUtf8ToWide(details_.maxCandle, minute)) {
        minute = L"--";
    }
    if (!convertUtf8ToWide(details_.deal100Pln, deal)) {
        deal = L"--";
    }
    if (!convertUtf8ToWide(details_.spread, spread)) {
        spread = L"--";
    }

    RECT textBounds = clientArea;
    textBounds.left += 24;
    textBounds.right -= 24;
    LONG nextLine = titleBounds.bottom + 20;
    if (sign > 0) {
        nextLine = drawWrappedParagraph(
            deviceContext, textBounds, nextLine,
            {{L"Największe odchylenie dla sygnału w ciągu ostatnich 20 minut wyniosło", false},
             {saldo, true, resultColor},
             {L"; wystąpiło w", false},
             {minute + L".", true},
             {L"minucie.", false}},
            regularFont, boldFont);
        nextLine = drawWrappedParagraph(
            deviceContext, textBounds, nextLine + 12,
            {{L"Przy inwestycji 100 zł zysk wyniósł", false},
             {deal, true, resultColor},
             {L"przy spreadzie wynoszącym", false},
             {spread, true},
             {L".", false}},
            regularFont, boldFont);
    } else if (sign < 0) {
        nextLine = drawWrappedParagraph(
            deviceContext, textBounds, nextLine,
            {{L"Najmniejsze odchylenie dla sygnału w ciągu ostatnich 20 minut wyniosło", false},
             {saldo, true, resultColor},
             {L"; wystąpiło w", false},
             {minute + L".", true},
             {L"minucie.", false}},
            regularFont, boldFont);
        drawWrappedParagraph(
            deviceContext, textBounds, nextLine + 12,
            {{L"Przy inwestycji 100 zł strata wyniosła", false},
             {deal, true, resultColor},
             {L"przy spreadzie wynoszącym", false},
             {spread, true},
             {L".", false}},
            regularFont, boldFont);
    } else {
        drawWrappedParagraph(
            deviceContext, textBounds, nextLine,
            {{L"Sygnał nie odnotował ani zysku, ani straty w najlepszym możliwym przypadku w ciągu 20 najbliższych minut przy spreadzie wynoszącym", false},
             {spread, true},
             {L".", false}},
            regularFont, boldFont);
    }

    SelectObject(deviceContext, previousFont);
    DeleteObject(regularFont);
    DeleteObject(boldFont);
}
