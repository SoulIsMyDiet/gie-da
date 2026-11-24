#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commctrl.h>

#include "TransactionsPage.h"
#include "ErrorLogger.h"
#include "../WindowScale.h"

#include <algorithm>
#include <array>
#include <cstdio>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

constexpr int fromDateLabelId = 1501;
constexpr int toDateLabelId = 1502;
constexpr int previousPageButtonId = 1503;
constexpr int nextPageButtonId = 1504;
constexpr int generateButtonId = 1505;
constexpr int firstFilterButtonId = 1510;
constexpr int firstFilterListId = 1600;
constexpr int closeFilterButtonId = 1540;
constexpr int filterButtonColumns = 6;
constexpr int formTop = 12;
constexpr int formMargin = 12;
constexpr int formGap = 6;
constexpr int labelHeight = 16;
constexpr int pickerHeight = 24;
constexpr int filterButtonHeight = 30;
constexpr int filterButtonGap = 4;
constexpr int filterListHeight = 78;
constexpr int tableGap = 8;
constexpr int navigationReservedHeight = 52;
constexpr int footerMargin = 8;
constexpr int pageLabelHeight = 16;
constexpr int pageButtonWidth = 44;
constexpr int pageButtonHeight = 22;
constexpr int generateButtonWidth = 78;

struct ReportColumnDefinition {
    const char* key;
    const wchar_t* label;
    int widthWeight;
    bool selectedByDefault;
};

// Front
const std::array<ReportColumnDefinition, 20> reportColumns{{
    {"product", L"Instrument", 1, true},
    {"algorithm_name", L"Algorytm", 1, false},
    {"signal_status", L"Status sygnału", 1, true},
    {"saldo_20_aggr_candles", L"Najw. wart. 20 min", 1, false},
    {"best_candle_20_minute", L"Która minuta z 20", 1, false},
    {"deal_20", L"Wartość 20 min - 100 zł", 1, true},
    {"saldo_60_aggr_candles", L"Najw. wart. 60 min", 1, false},
    {"best_candle_60_minute", L"Która minuta z 60", 1, false},
    {"deal_60", L"Wartość 60 min - 100 zł", 1, false},
    {"ai_name", L"Nazwa AI wer.", 1, true},
    {"mode", L"Mode", 1, true},
    {"ai_decision_status", L"Status wg AI", 1, false},
    {"ai_pts", L"Punkty AI", 1, false},
    {"ai_decision", L"Decyzja AI", 1, true},
    {"ai_decision_reason_msg", L"Argumenty AI", 2, false},
    {"transaction_saldo", L"Saldo transakcji", 1, false},
    {"transaction_100zl_deal", L"Wartość transakcji - 100 zł", 1, true},
    {"time_to_open", L"Czas do otwarcia", 1, false},
    {"time_from_open_to_close", L"Czas od otwarcia do zam", 1, false},
    {"transaction_status", L"Status transakcji", 1, false},
}};

constexpr std::size_t reportColumnCount = reportColumns.size();
constexpr std::size_t legacyButtonIndex = reportColumnCount;
constexpr std::size_t filterButtonCount = reportColumnCount + 1;
constexpr int dropdownReservedHeight = filterListHeight;

/**
 * Konwertuje tekst UTF-8 z API do tekstu Unicode używanego przez Windows.
 * @param text Tekst źródłowy w UTF-8.
 * @param convertedText Otrzymuje tekst Unicode.
 * @return true, jeśli tekst wejściowy był poprawnym UTF-8.
 * @throws std::bad_alloc Gdy nie można przydzielić pamięci na tekst wynikowy.
 */
bool convertUtf8ToWide(
    const std::string& text, std::wstring& convertedText) {
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
 * @param date Data pobrana z natywnej kontrolki Windows.
 * @return Data w formacie akceptowanym przez endpoint raportu.
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
 * @param deviceContext Kontekst używany do rysowania tekstu.
 * @param text Tekst UTF-16 wyświetlany w komórce.
 * @param bounds Położenie i rozmiar komórki.
 * @return Brak.
 */
void drawCell(HDC deviceContext, const std::wstring& text, RECT bounds) {
    DrawTextW(
        deviceContext, text.c_str(), static_cast<int>(text.size()),
        &bounds, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
}

/**
 * Zwraca pozycję początku tabeli, pozostawiając miejsce na filtry.
 * @param parent Okno, którego skalowanie określa położenie tabeli.
 * @return Wysokość górnego obszaru filtrów w pikselach.
 */
int calculateTableTop(HWND parent) {
    const int top = WindowScale::size(parent, formTop);
    const int margin = WindowScale::size(parent, formMargin);
    const int gap = WindowScale::size(parent, formGap);
    const int labelHeightScaled = WindowScale::size(parent, labelHeight);
    const int pickerHeightScaled = WindowScale::size(parent, pickerHeight);
    const int buttonHeight = WindowScale::size(parent, filterButtonHeight);
    const int buttonGap = WindowScale::size(parent, filterButtonGap);
    const int gridTop =
        top + labelHeightScaled + pickerHeightScaled + gap;
    const int rowCount = static_cast<int>(
        (filterButtonCount + filterButtonColumns - 1) /
        filterButtonColumns);
    const int buttonGridBottom =
        gridTop + rowCount * (buttonHeight + buttonGap) - buttonGap;
    return buttonGridBottom + gap +
           WindowScale::size(parent, dropdownReservedHeight) +
           WindowScale::size(parent, tableGap) + margin;
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
 * Tworzy filtry daty, przyciski kolumn, listy wielokrotnego wyboru i nawigację.
 * @param instance Instancja aplikacji Windows.
 * @param parent Okno nadrzędne dla utworzonych kontrolek.
 * @return true, jeśli wszystkie kontrolki utworzono poprawnie.
 */
bool TransactionsPage::create(HINSTANCE instance, HWND parent) {
    parent_ = parent;
    filterButtons_.resize(filterButtonCount, nullptr);
    filterLists_.resize(reportColumnCount, nullptr);
    filterOptionValues_.resize(reportColumnCount);
    selectedFilterValues_.resize(reportColumnCount);
    filterSelected_.resize(filterButtonCount, false);

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
    closeFilterButton_ = CreateWindowExW(
        0, L"BUTTON", L"Gotowe",
        WS_CHILD | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, 0, 0, parent_,
        reinterpret_cast<HMENU>(
            static_cast<INT_PTR>(closeFilterButtonId)),
        instance, nullptr);

    if (fromDateLabel_ == nullptr || fromDatePicker_ == nullptr ||
        toDateLabel_ == nullptr || toDatePicker_ == nullptr ||
        previousPageButton_ == nullptr || nextPageButton_ == nullptr ||
        generateButton_ == nullptr || closeFilterButton_ == nullptr) {
        return false;
    }

    for (std::size_t index = 0; index < filterButtonCount; ++index) {
        const bool isLegacy = index == legacyButtonIndex;
        const wchar_t* label = isLegacy
            ? L"Legacy"
            : reportColumns[index].label;
        const DWORD buttonStyle = isLegacy
            ? BS_PUSHBUTTON | BS_MULTILINE
            : BS_AUTOCHECKBOX | BS_PUSHLIKE | BS_MULTILINE;
        filterButtons_[index] = CreateWindowExW(
            0, L"BUTTON", label,
            WS_CHILD | WS_TABSTOP | buttonStyle,
            0, 0, 0, 0, parent_,
            reinterpret_cast<HMENU>(static_cast<INT_PTR>(
                firstFilterButtonId + static_cast<int>(index))),
            instance, nullptr);
        if (filterButtons_[index] == nullptr) {
            return false;
        }

        if (isLegacy) {
            EnableWindow(filterButtons_[index], FALSE);
            continue;
        }

        filterSelected_[index] =
            reportColumns[index].selectedByDefault;
        SendMessageW(
            filterButtons_[index], BM_SETCHECK,
            filterSelected_[index] ? BST_CHECKED : BST_UNCHECKED, 0);
        filterLists_[index] = CreateWindowExW(
            0, L"LISTBOX", L"",
            WS_CHILD | WS_VSCROLL | WS_HSCROLL | WS_BORDER | LBS_MULTIPLESEL |
                LBS_NOTIFY | LBS_HASSTRINGS,
            0, 0, 0, 0, parent_,
            reinterpret_cast<HMENU>(static_cast<INT_PTR>(
                firstFilterListId + static_cast<int>(index))),
            instance, nullptr);
        if (filterLists_[index] == nullptr) {
            return false;
        }
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
 * Otwiera widok, zachowując daty, kolumny i wybrane opcje filtrów.
 */
void TransactionsPage::open() {
    visible_ = true;
    activeFilterIndex_ = -1;
    filterOptionsLoaded_ = false;
    updateControls();
}

/**
 * Ukrywa widok, pozostawiając stan raportu do następnego otwarcia.
 */
void TransactionsPage::close() {
    storeActiveFilterSelection();
    visible_ = false;
    activeFilterIndex_ = -1;
    filterOptionsLoaded_ = false;
    updateControls();
}

/**
 * Informuje, czy widok transakcji jest aktualnie widoczny.
 */
bool TransactionsPage::isVisible() const {
    return visible_;
}

/**
 * Obsługuje przyciski raportu, wybór opcji i zmianę strony tabeli.
 * @param controlId Identyfikator klikniętej kontrolki.
 * @param notificationCode Kod powiadomienia Windows.
 * @return true, jeśli komunikat należał do tego widoku.
 */
bool TransactionsPage::handleCommand(
    int controlId, int notificationCode) {
    if (notificationCode == LBN_SELCHANGE) {
        const int filterIndex = controlId - firstFilterListId;
        if (filterIndex >= 0 &&
            static_cast<std::size_t>(filterIndex) < reportColumnCount) {
            storeActiveFilterSelection();
            return true;
        }
    }

    if (notificationCode != BN_CLICKED) {
        return false;
    }

    if (controlId == generateButtonId) {
        generateReport();
        return true;
    }
    if (controlId == closeFilterButtonId) {
        storeActiveFilterSelection();
        activeFilterIndex_ = -1;
        status_.clear();
        updateControls();
        InvalidateRect(parent_, nullptr, TRUE);
        return true;
    }
    if (controlId == previousPageButtonId && currentPage_ > 0) {
        --currentPage_;
    } else if (controlId == nextPageButtonId && hasNextPage_) {
        ++currentPage_;
    } else if (controlId == previousPageButtonId ||
               controlId == nextPageButtonId) {
        return true;
    } else if (controlId >= firstFilterButtonId &&
               controlId <
                   firstFilterButtonId +
                       static_cast<int>(filterButtonCount)) {
        const std::size_t filterIndex =
            static_cast<std::size_t>(controlId - firstFilterButtonId);
        if (filterIndex == legacyButtonIndex) {
            return true;
        }

        const bool isChecked =
            SendMessageW(filterButtons_[filterIndex], BM_GETCHECK, 0, 0) ==
            BST_CHECKED;
        if (isChecked && !filterSelected_[filterIndex]) {
            int selectedWeight = 0;
            for (std::size_t index = 0; index < reportColumnCount; ++index) {
                if (filterSelected_[index]) {
                    selectedWeight += reportColumns[index].widthWeight;
                }
            }
            if (selectedWeight + reportColumns[filterIndex].widthWeight > 8) {
                SendMessageW(
                    filterButtons_[filterIndex], BM_SETCHECK,
                    BST_UNCHECKED, 0);
                status_ = "Można wybrać najwyżej 8 jednostek kolumn.";
                InvalidateRect(parent_, nullptr, TRUE);
                return true;
            }
            filterSelected_[filterIndex] = true;
        } else if (!isChecked && filterSelected_[filterIndex]) {
            if (activeFilterIndex_ == static_cast<int>(filterIndex)) {
                storeActiveFilterSelection();
                activeFilterIndex_ = -1;
                filterSelected_[filterIndex] = false;
            } else {
                SendMessageW(
                    filterButtons_[filterIndex], BM_SETCHECK,
                    BST_CHECKED, 0);
            }
        }

        status_.clear();
        if (filterSelected_[filterIndex]) {
            showFilterOptions(filterIndex);
        } else {
            updateControls();
            InvalidateRect(parent_, nullptr, TRUE);
        }
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
 * Rozmieszcza daty, siatkę filtrów, listę opcji i przyciski nawigacji.
 */
void TransactionsPage::positionControls() {
    if (parent_ == nullptr) {
        return;
    }

    RECT clientArea{};
    GetClientRect(parent_, &clientArea);
    const int clientWidth = clientArea.right - clientArea.left;
    const int scaledMargin = WindowScale::size(parent_, formMargin);
    const int scaledGap = WindowScale::size(parent_, formGap);
    const int generateWidth =
        WindowScale::size(parent_, generateButtonWidth);
    const int fieldWidth = std::min(
        WindowScale::size(parent_, 145),
        std::max(
            1, (clientWidth - scaledMargin * 2 - generateWidth -
                scaledGap * 3) /
                   2));
    const int firstX = scaledMargin;
    const int secondX = firstX + fieldWidth + scaledGap;
    const int labelY = WindowScale::size(parent_, formTop);
    const int labelHeightScaled = WindowScale::size(parent_, labelHeight);
    const int pickerY = labelY + labelHeightScaled;
    const int pickerHeightScaled =
        WindowScale::size(parent_, pickerHeight);
    const int filterTop =
        pickerY + pickerHeightScaled + scaledGap;
    const int buttonHeight =
        WindowScale::size(parent_, filterButtonHeight);
    const int buttonGap =
        WindowScale::size(parent_, filterButtonGap);
    const int filterListHeightScaled =
        WindowScale::size(parent_, filterListHeight);
    const int contentWidth =
        std::max(1, clientWidth - scaledMargin * 2);
    const int buttonWidth = std::max(
        1, (contentWidth -
            buttonGap * (filterButtonColumns - 1)) /
               filterButtonColumns);
    const int activeFilterRow = activeFilterIndex_ >= 0
        ? activeFilterIndex_ / filterButtonColumns
        : -1;
    const int rowsBelowActiveFilter = WindowScale::size(parent_, formGap) +
                                      filterListHeightScaled;
    const int contentBottom = static_cast<int>(std::max<LONG>(
        0, clientArea.bottom -
               WindowScale::size(parent_, navigationReservedHeight)));
    const int pageButtonWidthScaled =
        WindowScale::size(parent_, pageButtonWidth);
    const int pageButtonHeightScaled =
        WindowScale::size(parent_, pageButtonHeight);
    const int footer = WindowScale::size(parent_, footerMargin);
    const int pageY = std::max(
        0, contentBottom - pageButtonHeightScaled - footer);
    const int pageButtonsWidth =
        pageButtonWidthScaled * 2 + scaledGap;
    const int pageButtonsX = std::max(
        0, (clientWidth - pageButtonsWidth) / 2);

    WindowScale::updateFont(
        parent_, controlFont_, controlFontHeight_, 4,
        {fromDateLabel_, fromDatePicker_, toDateLabel_, toDatePicker_});
    WindowScale::updateFont(
        parent_, buttonFont_, buttonFontHeight_, 14,
        {previousPageButton_, nextPageButton_, generateButton_,
         closeFilterButton_});
    for (HWND button : filterButtons_) {
        if (button != nullptr) {
            SendMessageW(
                button, WM_SETFONT,
                reinterpret_cast<WPARAM>(buttonFont_), TRUE);
        }
    }
    for (HWND list : filterLists_) {
        if (list != nullptr) {
            SendMessageW(
                list, WM_SETFONT,
                reinterpret_cast<WPARAM>(controlFont_), TRUE);
        }
    }

    MoveWindow(
        fromDateLabel_, firstX, labelY, fieldWidth, labelHeightScaled, TRUE);
    MoveWindow(
        fromDatePicker_, firstX, pickerY, fieldWidth,
        pickerHeightScaled, TRUE);
    MoveWindow(
        toDateLabel_, secondX, labelY, fieldWidth, labelHeightScaled, TRUE);
    MoveWindow(
        toDatePicker_, secondX, pickerY, fieldWidth,
        pickerHeightScaled, TRUE);
    MoveWindow(
        generateButton_, std::max(scaledMargin, clientWidth -
            scaledMargin - generateWidth), pickerY, generateWidth,
        pickerHeightScaled, TRUE);

    for (std::size_t index = 0; index < filterButtonCount; ++index) {
        const int column = static_cast<int>(index % filterButtonColumns);
        const int row = static_cast<int>(index / filterButtonColumns);
        const int x = scaledMargin + column * (buttonWidth + buttonGap);
        const int y = filterTop + row * (buttonHeight + buttonGap) +
                      (activeFilterRow >= 0 && row > activeFilterRow
                           ? rowsBelowActiveFilter
                           : 0);
        MoveWindow(filterButtons_[index], x, y, buttonWidth, buttonHeight, TRUE);
    }

    if (activeFilterIndex_ >= 0 &&
        static_cast<std::size_t>(activeFilterIndex_) < reportColumnCount) {
        const int activeColumn = activeFilterIndex_ % filterButtonColumns;
        const int buttonX =
            scaledMargin + activeColumn * (buttonWidth + buttonGap);
        const int closeWidth = WindowScale::size(parent_, 60);
        const int closeGap = buttonGap;
        const int listWidth = std::min(
            std::max(1, contentWidth - closeWidth - closeGap),
            buttonWidth * 2 + buttonGap);
        const int maximumListX = scaledMargin + std::max(
            0, contentWidth - listWidth - closeGap - closeWidth);
        const int listX = std::min(
            buttonX, maximumListX);
        const int activeButtonTop =
            filterTop + activeFilterRow * (buttonHeight + buttonGap);
        const int listTop = activeButtonTop + buttonHeight + buttonGap;
        MoveWindow(
            filterLists_[static_cast<std::size_t>(activeFilterIndex_)],
            listX, listTop, listWidth, filterListHeightScaled, TRUE);
        MoveWindow(
            closeFilterButton_, listX + listWidth + closeGap, listTop,
            closeWidth, WindowScale::size(parent_, 24), TRUE);
    }

    MoveWindow(
        previousPageButton_, pageButtonsX, pageY,
        pageButtonWidthScaled, pageButtonHeightScaled, TRUE);
    MoveWindow(
        nextPageButton_,
        pageButtonsX + pageButtonWidthScaled + scaledGap, pageY,
        pageButtonWidthScaled, pageButtonHeightScaled, TRUE);
}

/**
 * Rysuje wybrane kolumny, maksymalnie dziesięć rekordów i wiersz podsumowania.
 * @param deviceContext Kontekst używany do rysowania.
 * @param clientArea Obszar dostępny dla widoku.
 */
void TransactionsPage::draw(HDC deviceContext, RECT& clientArea) const {
    RECT tableArea = clientArea;
    tableArea.top += calculateTableTop(parent_);
    tableArea.bottom = std::max(
        tableArea.top,
        static_cast<LONG>(
            clientArea.bottom -
            WindowScale::size(parent_, navigationReservedHeight)));

    if (!generated_) {
        const std::string message = !status_.empty()
            ? status_
            : "Ustaw daty, wybierz kolumny i kliknij „Generuj”.";
        std::wstring wideMessage;
        if (!convertUtf8ToWide(message, wideMessage)) {
            wideMessage = L"Nie można wyświetlić instrukcji raportu.";
        }
        WindowScale::drawText(
            deviceContext, parent_, wideMessage, tableArea,
            DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
        return;
    }

    if (!status_.empty()) {
        std::wstring wideStatus;
        if (!convertUtf8ToWide(status_, wideStatus)) {
            wideStatus = L"Nie można wyświetlić tabeli transakcji.";
        }
        WindowScale::drawText(
            deviceContext, parent_, wideStatus, tableArea,
            DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
        return;
    }
    if (appliedColumns_.empty()) {
        return;
    }

    HFONT tableFont = CreateFontW(
        -WindowScale::size(parent_, 10), 0, 0, 0, FW_NORMAL,
        FALSE, FALSE, FALSE, DEFAULT_CHARSET,
        OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
        DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
    if (tableFont == nullptr) {
        DrawTextW(
            deviceContext, L"Nie można utworzyć tabeli transakcji.", -1,
            &tableArea, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
        return;
    }

    const HGDIOBJ previousFont = SelectObject(deviceContext, tableFont);
    const LONG totalWidth = tableArea.right - tableArea.left;
    const LONG padding = WindowScale::size(parent_, 4);
    const LONG rowHeight = std::max<LONG>(
        1, (tableArea.bottom - tableArea.top) / 12);
    const int headerHeight = static_cast<int>(rowHeight);
    RECT headerBackground{
        tableArea.left, tableArea.top,
        tableArea.right, tableArea.top + headerHeight};
    HBRUSH headerBrush = CreateSolidBrush(RGB(230, 235, 242));
    HBRUSH summaryBrush = CreateSolidBrush(RGB(240, 240, 240));
    if (headerBrush != nullptr) {
        FillRect(deviceContext, &headerBackground, headerBrush);
        DeleteObject(headerBrush);
    }

    std::vector<int> columnWidths;
    columnWidths.reserve(appliedColumns_.size());
    int totalWeight = 0;
    for (const std::string& key : appliedColumns_) {
        const auto definition = std::find_if(
            reportColumns.begin(), reportColumns.end(),
            [&key](const ReportColumnDefinition& column) {
                return key == column.key;
            });
        totalWeight += definition == reportColumns.end()
            ? 1
            : definition->widthWeight;
    }
    LONG assignedWidth = 0;
    for (std::size_t index = 0; index < appliedColumns_.size(); ++index) {
        const auto definition = std::find_if(
            reportColumns.begin(), reportColumns.end(),
            [this, index](const ReportColumnDefinition& column) {
                return appliedColumns_[index] == column.key;
            });
        const int weight = definition == reportColumns.end()
            ? 1
            : definition->widthWeight;
        const LONG width = index + 1 == appliedColumns_.size()
            ? totalWidth - assignedWidth
            : totalWidth * weight / totalWeight;
        columnWidths.push_back(static_cast<int>(width));
        assignedWidth += width;
    }

    LONG rowTop = tableArea.top;
    LONG columnLeft = tableArea.left;
    SetTextColor(deviceContext, RGB(0, 0, 0));
    SetBkMode(deviceContext, TRANSPARENT);

    for (std::size_t columnIndex = 0;
         columnIndex < appliedColumns_.size(); ++columnIndex) {
        const auto definition = std::find_if(
            reportColumns.begin(), reportColumns.end(),
            [this, columnIndex](const ReportColumnDefinition& column) {
                return appliedColumns_[columnIndex] == column.key;
            });
        std::wstring title;
        if (definition != reportColumns.end()) {
            title = definition->label;
        }
        RECT cell{
            columnLeft + padding, rowTop,
            columnLeft + columnWidths[columnIndex] - padding,
            rowTop + rowHeight};
        drawCell(deviceContext, title, cell);
        columnLeft += columnWidths[columnIndex];
    }

    const LONG firstDataRowTop = tableArea.top + rowHeight;
    if (report_.rows.empty()) {
        RECT emptyBounds{
            tableArea.left + padding, firstDataRowTop,
            tableArea.right - padding, firstDataRowTop + rowHeight};
        DrawTextW(
            deviceContext, L"Brak transakcji spełniających filtry.", -1,
            &emptyBounds,
            DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
    }

    for (std::size_t rowIndex = 0; rowIndex < 10; ++rowIndex) {
        rowTop = firstDataRowTop +
                 static_cast<LONG>(rowIndex) * rowHeight;
        columnLeft = tableArea.left;
        for (std::size_t columnIndex = 0;
             columnIndex < appliedColumns_.size(); ++columnIndex) {
            std::string value;
            if (rowIndex < report_.rows.size() &&
                columnIndex < report_.rows[rowIndex].size()) {
                value = report_.rows[rowIndex][columnIndex];
            }
            std::wstring wideValue;
            if (!convertUtf8ToWide(value, wideValue)) {
                wideValue = L"Nieprawidłowy tekst";
            }
            RECT cell{
                columnLeft + padding, rowTop,
                columnLeft + columnWidths[columnIndex] - padding,
                rowTop + rowHeight};
            drawCell(deviceContext, wideValue, cell);
            columnLeft += columnWidths[columnIndex];
        }
    }

    const LONG summaryTop = firstDataRowTop + rowHeight * 10;
    RECT summaryBackground{
        tableArea.left, summaryTop,
        tableArea.right, summaryTop + rowHeight};
    if (summaryBrush != nullptr) {
        FillRect(deviceContext, &summaryBackground, summaryBrush);
        DeleteObject(summaryBrush);
    }
    columnLeft = tableArea.left;
    for (std::size_t columnIndex = 0;
         columnIndex < appliedColumns_.size(); ++columnIndex) {
        const std::string value =
            columnIndex < report_.summary.size()
                ? report_.summary[columnIndex]
                : "";
        std::wstring wideValue;
        if (!convertUtf8ToWide(value, wideValue)) {
            wideValue = L"Nieprawidłowy tekst";
        }
        RECT cell{
            columnLeft + padding, summaryTop,
            columnLeft + columnWidths[columnIndex] - padding,
            summaryTop + rowHeight};
        drawCell(deviceContext, wideValue, cell);
        columnLeft += columnWidths[columnIndex];
    }

    SelectObject(deviceContext, previousFont);
    DeleteObject(tableFont);

    const int pageLabelHeightScaled =
        WindowScale::size(parent_, pageLabelHeight);
    const int footer = WindowScale::size(parent_, footerMargin);
    const int pageButtonHeightScaled =
        WindowScale::size(parent_, pageButtonHeight);
    const int contentBottom = clientArea.bottom -
        WindowScale::size(parent_, navigationReservedHeight);
    const int pageY = contentBottom - pageButtonHeightScaled - footer;
    RECT pageBounds = clientArea;
    pageBounds.top = pageY - pageLabelHeightScaled - footer;
    pageBounds.bottom = pageY - footer;
    const std::wstring pageLabel =
        L"Strona " + std::to_wstring(currentPage_ + 1);
    WindowScale::drawText(
        deviceContext, parent_, pageLabel, pageBounds,
        DT_CENTER | DT_VCENTER | DT_SINGLELINE);
}

/**
 * Pobiera bieżącą stronę raportu z zapamiętanym zakresem dat i filtrami.
 */
void TransactionsPage::loadCurrentPage() {
    TransactionReportPage receivedReport;
    std::string error;
    if (!CandleHistoryClient::fetchTransactionReportPage(
            appliedStartDate_, appliedEndDate_, currentPage_,
            appliedColumns_, appliedFilters_, receivedReport, error)) {
        report_ = {};
        hasNextPage_ = false;
        ErrorLogger::error(error);
        status_ = std::move(error);
        return;
    }

    report_ = std::move(receivedReport);
    hasNextPage_ = report_.hasNextPage;
    status_.clear();
}

/**
 * Odczytuje zakres, wybrane kolumny i filtry oraz generuje nowy raport.
 */
void TransactionsPage::generateReport() {
    generated_ = false;
    currentPage_ = 0;
    hasNextPage_ = false;
    report_ = {};
    status_.clear();
    storeActiveFilterSelection();

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
            appliedColumns_ = selectedColumnKeys();
            if (appliedColumns_.empty()) {
                status_ = "Wybierz przynajmniej jedną kolumnę raportu.";
            } else {
                appliedFilters_ = selectedFilterValues();
                generated_ = true;
                activeFilterIndex_ = -1;
                loadCurrentPage();
            }
        }
    }

    updateControls();
    InvalidateRect(parent_, nullptr, TRUE);
}

/**
 * Pobiera świeże opcje z API i pokazuje listę dla wskazanego przycisku.
 * @param filterIndex Indeks pola w konfiguracji przycisków raportu.
 * @return true, jeśli pobrano i pokazano listę opcji.
 * @throws std::bad_alloc Gdy nie można przydzielić pamięci na opcje.
 */
bool TransactionsPage::showFilterOptions(std::size_t filterIndex) {
    if (filterIndex >= reportColumnCount) {
        return false;
    }

    if (!filterOptionsLoaded_) {
        std::map<std::string, std::vector<TransactionReportFilterOption>>
            receivedOptions;
        std::string error;
        if (!CandleHistoryClient::fetchTransactionReportOptions(
                receivedOptions, error)) {
            ErrorLogger::error(error);
            status_ = std::move(error);
            updateControls();
            InvalidateRect(parent_, nullptr, TRUE);
            return false;
        }
        filterOptions_ = std::move(receivedOptions);
        filterOptionsLoaded_ = true;
    }

    const auto foundOptions =
        filterOptions_.find(reportColumns[filterIndex].key);
    const std::vector<TransactionReportFilterOption> emptyOptions;
    const std::vector<TransactionReportFilterOption>& filterOptions =
        foundOptions == filterOptions_.end()
            ? emptyOptions
            : foundOptions->second;
    std::vector<std::string> previousValues =
        std::move(selectedFilterValues_[filterIndex]);
    std::vector<std::string> optionValues;
    optionValues.reserve(filterOptions.size());

    SendMessageW(
        filterLists_[filterIndex], LB_RESETCONTENT, 0, 0);
    for (const TransactionReportFilterOption& option : filterOptions) {
        std::wstring wideLabel;
        if (!convertUtf8ToWide(option.label, wideLabel)) {
            status_ = "API zwróciło nieprawidłowy tekst opcji filtra.";
            ErrorLogger::error(status_);
            selectedFilterValues_[filterIndex] =
                std::move(previousValues);
            updateControls();
            InvalidateRect(parent_, nullptr, TRUE);
            return false;
        }
        const LRESULT itemIndex = SendMessageW(
            filterLists_[filterIndex], LB_ADDSTRING, 0,
            reinterpret_cast<LPARAM>(wideLabel.c_str()));
        if (itemIndex == LB_ERR || itemIndex == LB_ERRSPACE) {
            status_ = "Nie udało się wypełnić listy opcji filtra.";
            ErrorLogger::error(status_);
            selectedFilterValues_[filterIndex] =
                std::move(previousValues);
            updateControls();
            InvalidateRect(parent_, nullptr, TRUE);
            return false;
        }
        optionValues.push_back(option.value);
        if (std::find(
                previousValues.begin(), previousValues.end(),
                option.value) != previousValues.end()) {
            SendMessageW(
                filterLists_[filterIndex], LB_SETSEL, TRUE, itemIndex);
        }
    }
    SendMessageW(
        filterLists_[filterIndex], LB_SETHORIZONTALEXTENT,
        static_cast<WPARAM>(WindowScale::size(parent_, 1200)), 0);

    filterOptionValues_[filterIndex] = std::move(optionValues);
    selectedFilterValues_[filterIndex].clear();
    for (const std::string& value : previousValues) {
        if (std::find(
                filterOptionValues_[filterIndex].begin(),
                filterOptionValues_[filterIndex].end(), value) !=
            filterOptionValues_[filterIndex].end()) {
            selectedFilterValues_[filterIndex].push_back(value);
        }
    }
    activeFilterIndex_ = static_cast<int>(filterIndex);
    status_.clear();
    updateControls();
    InvalidateRect(parent_, nullptr, TRUE);
    return true;
}

/**
 * Zapisuje zaznaczone wartości aktualnie widocznej listy.
 */
void TransactionsPage::storeActiveFilterSelection() {
    if (activeFilterIndex_ < 0 ||
        static_cast<std::size_t>(activeFilterIndex_) >= reportColumnCount) {
        return;
    }

    const std::size_t filterIndex =
        static_cast<std::size_t>(activeFilterIndex_);
    const LRESULT selectedCount = SendMessageW(
        filterLists_[filterIndex], LB_GETSELCOUNT, 0, 0);
    if (selectedCount == LB_ERR || selectedCount <= 0) {
        selectedFilterValues_[filterIndex].clear();
        return;
    }

    std::vector<int> selectedItems(
        static_cast<std::size_t>(selectedCount));
    const LRESULT receivedCount = SendMessageW(
        filterLists_[filterIndex], LB_GETSELITEMS,
        static_cast<WPARAM>(selectedItems.size()),
        reinterpret_cast<LPARAM>(selectedItems.data()));
    if (receivedCount == LB_ERR ||
        static_cast<std::size_t>(receivedCount) != selectedItems.size()) {
        status_ = "Nie udało się odczytać wybranych opcji filtra.";
        ErrorLogger::error(status_);
        return;
    }

    std::vector<std::string> selectedValues;
    selectedValues.reserve(selectedItems.size());
    for (const int itemIndex : selectedItems) {
        if (itemIndex < 0 ||
            static_cast<std::size_t>(itemIndex) >=
                filterOptionValues_[filterIndex].size()) {
            status_ = "Lista filtra zawiera nieprawidłowy indeks.";
            ErrorLogger::error(status_);
            return;
        }
        selectedValues.push_back(
            filterOptionValues_[filterIndex][
                static_cast<std::size_t>(itemIndex)]);
    }
    selectedFilterValues_[filterIndex] = std::move(selectedValues);
}

/**
 * Zwraca klucze wybranych kolumn w kolejności widocznej w formularzu.
 */
std::vector<std::string> TransactionsPage::selectedColumnKeys() const {
    std::vector<std::string> columns;
    for (std::size_t index = 0; index < reportColumnCount; ++index) {
        if (filterSelected_[index]) {
            columns.emplace_back(reportColumns[index].key);
        }
    }
    return columns;
}

/**
 * Zwraca wybrane wartości filtrów aktywnych kolumn.
 */
std::map<std::string, std::vector<std::string>>
TransactionsPage::selectedFilterValues() const {
    std::map<std::string, std::vector<std::string>> filters;
    for (std::size_t index = 0; index < reportColumnCount; ++index) {
        if (filterSelected_[index] &&
            !selectedFilterValues_[index].empty()) {
            filters.emplace(
                reportColumns[index].key, selectedFilterValues_[index]);
        }
    }
    return filters;
}

/**
 * Pokazuje lub ukrywa kontrolki i ustawia ich aktywność.
 */
void TransactionsPage::updateControls() {
    const int visibility = visible_ ? SW_SHOW : SW_HIDE;
    for (HWND control : {
             fromDateLabel_, fromDatePicker_, toDateLabel_, toDatePicker_,
             generateButton_, closeFilterButton_}) {
        if (control != nullptr) {
            ShowWindow(control, visibility);
        }
    }
    for (HWND button : filterButtons_) {
        if (button != nullptr) {
            ShowWindow(button, visibility);
        }
    }
    for (std::size_t index = 0; index < filterLists_.size(); ++index) {
        const bool showList =
            visible_ && activeFilterIndex_ == static_cast<int>(index);
        ShowWindow(filterLists_[index], showList ? SW_SHOW : SW_HIDE);
    }
    ShowWindow(
        closeFilterButton_,
        visible_ && activeFilterIndex_ >= 0 ? SW_SHOW : SW_HIDE);

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
