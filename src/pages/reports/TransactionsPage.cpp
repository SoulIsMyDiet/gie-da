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
constexpr int previousPageButtonId = 1701;
constexpr int nextPageButtonId = 1702;
constexpr int generateButtonId = 1505;
constexpr int firstFilterButtonId = 1510;
constexpr int firstFilterListId = 1600;
constexpr char signalDatetimeColumnKey[] = "signal_datetime";
constexpr wchar_t signalDatetimeColumnLabel[] =
    L"Data utworzenia sygnału";
constexpr int filterButtonColumns = 12;
constexpr int referenceFilterButtonColumns = 6;
constexpr int filterButtonWidthDivisor = 2;
constexpr int filterButtonFontHeight = 7;
constexpr int datePickerSizeDivisor = 3;
constexpr int filterListHeightDivisor = 2;
constexpr int maximumSelectedFilterWeight = 7;
constexpr int formTop = 12;
constexpr int formMargin = 12;
constexpr int formGap = 6;
constexpr int labelHeight = 16;
constexpr int pickerHeight = 24;
constexpr int filterButtonHeight = 15;
constexpr int referenceFilterButtonGap = 4;
constexpr int filterButtonGap = 2;
constexpr int filterListHeight = 78;
constexpr int tableGap = 8;
constexpr int pageLabelHeight = 16;
constexpr int pageLabelWidth = 72;
constexpr int pageButtonWidth = 24;
constexpr int pageButtonHeight = 16;
constexpr int generateButtonWidth = 78;

struct ReportColumnDefinition {
    const char* key;
    const wchar_t* label;
    int widthWeight;
    bool selectedByDefault;
    bool hasMultiselect = true;
};

// Front
const std::array<ReportColumnDefinition, 22> reportColumns{{
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
    {"ai_decision_reason_msg", L"Argumenty AI", 2, false, false},
    {"transaction_saldo", L"Saldo transakcji", 1, false},
    {"transaction_100zl_deal", L"Wartość transakcji - 100 zł", 1, true},
    {"time_to_open", L"Czas do otwarcia", 1, false},
    {"time_from_open_to_close", L"Czas od otwarcia do zam", 1, false},
    {"transaction_status", L"Status transakcji", 1, false},
    {"signal_id", L"ID sygnału", 1, false, false},
    {"transaction_id", L"ID transakcji", 1, false, false},
}};

constexpr std::size_t reportColumnCount = reportColumns.size();
constexpr std::size_t legacyButtonIndex = reportColumnCount;
constexpr std::size_t filterButtonCount = reportColumnCount + 1;
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
 * Wyznacza początek tabeli z uwzględnieniem filtrów i nawigacji stron.
 * @param parent Okno, którego skalowanie określa położenie tabeli.
 * @return Wysokość górnego obszaru filtrów i nawigacji w pikselach.
 */
int calculateTableTop(
    HWND parent,
    const std::vector<bool>& openFilterLists) {
    const int top = WindowScale::size(parent, formTop);
    const int margin = WindowScale::size(parent, formMargin);
    const int gap = WindowScale::size(parent, formGap);
    const int labelHeightScaled =
        WindowScale::size(parent, labelHeight / datePickerSizeDivisor);
    const int pickerHeightScaled =
        WindowScale::size(parent, pickerHeight / datePickerSizeDivisor);
    const int generateButtonHeightScaled =
        WindowScale::size(parent, pickerHeight);
    const int buttonHeight = WindowScale::size(parent, filterButtonHeight);
    const int buttonGap = WindowScale::size(parent, filterButtonGap);
    const int filterListHeightScaled = WindowScale::size(
        parent, filterListHeight / filterListHeightDivisor);
    const int formHeight = std::max(
        generateButtonHeightScaled,
        labelHeightScaled + pickerHeightScaled);
    const int gridTop = top + formHeight + gap;
    const int rowCount = static_cast<int>(
        (filterButtonCount + filterButtonColumns - 1) /
        filterButtonColumns);
    const int buttonGridBottom =
        gridTop + rowCount * (buttonHeight + buttonGap) - buttonGap;
    int expandedRowCount = 0;
    for (std::size_t row = 0; row < static_cast<std::size_t>(rowCount); ++row) {
        const std::size_t firstIndex =
            row * static_cast<std::size_t>(filterButtonColumns);
        const std::size_t lastIndex = std::min(
            openFilterLists.size(),
            firstIndex + static_cast<std::size_t>(filterButtonColumns));
        if (std::any_of(
                openFilterLists.begin() + firstIndex,
                openFilterLists.begin() + lastIndex,
                [](bool isOpen) { return isOpen; })) {
            ++expandedRowCount;
        }
    }
    return buttonGridBottom + gap +
           expandedRowCount * (filterListHeightScaled + buttonGap) +
           WindowScale::size(parent, tableGap) + margin +
           std::max(
               WindowScale::size(parent, pageButtonHeight),
               WindowScale::size(parent, pageLabelHeight)) +
           gap;
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
    if (filterButtonFont_ != nullptr) {
        DeleteObject(filterButtonFont_);
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
    filterLists_.resize(filterButtonCount, nullptr);
    openFilterLists_.resize(filterButtonCount, false);
    filterOptionValues_.resize(filterButtonCount);
    selectedFilterValues_.resize(filterButtonCount);
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
    if (fromDateLabel_ == nullptr || fromDatePicker_ == nullptr ||
        toDateLabel_ == nullptr || toDatePicker_ == nullptr ||
        previousPageButton_ == nullptr || nextPageButton_ == nullptr ||
        generateButton_ == nullptr) {
        return false;
    }

    for (std::size_t index = 0; index < filterButtonCount; ++index) {
        const bool isLegacy = index == legacyButtonIndex;
        const wchar_t* label = isLegacy
            ? L"Legacy"
            : reportColumns[index].label;
        const DWORD buttonStyle =
            BS_AUTOCHECKBOX | BS_PUSHLIKE | BS_MULTILINE;
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
            filterSelected_[index] = false;
            SendMessageW(
                filterButtons_[index], BM_SETCHECK, BST_UNCHECKED, 0);
        } else {
            filterSelected_[index] =
                reportColumns[index].selectedByDefault;
            SendMessageW(
                filterButtons_[index], BM_SETCHECK,
                filterSelected_[index] ? BST_CHECKED : BST_UNCHECKED, 0);
        }

        if (!isLegacy && !reportColumns[index].hasMultiselect) {
            continue;
        }

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
 * Otwiera widok i pokazuje listy opcji dla zaznaczonych kolumn.
 */
void TransactionsPage::open() {
    visible_ = true;
    std::fill(openFilterLists_.begin(), openFilterLists_.end(), false);
    filterOptionsLoaded_ = false;
    positionControls();
    updateControls();
    for (std::size_t index = 0; index < reportColumnCount; ++index) {
        if (filterSelected_[index] &&
            reportColumns[index].hasMultiselect &&
            !showFilterOptions(index)) {
            break;
        }
    }
}

/**
 * Ukrywa widok, pozostawiając stan raportu do następnego otwarcia.
 */
void TransactionsPage::close() {
    visible_ = false;
    std::fill(openFilterLists_.begin(), openFilterLists_.end(), false);
    filterOptionsLoaded_ = false;
    positionControls();
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
            static_cast<std::size_t>(filterIndex) < filterButtonCount) {
            storeFilterSelection(static_cast<std::size_t>(filterIndex));
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
            const bool isChecked =
                SendMessageW(filterButtons_[filterIndex], BM_GETCHECK, 0, 0) ==
                BST_CHECKED;
            if (
                filterSelected_[filterIndex] &&
                !openFilterLists_[filterIndex] && !isChecked) {
                SendMessageW(
                    filterButtons_[filterIndex], BM_SETCHECK,
                    BST_CHECKED, 0);
                showFilterOptions(filterIndex);
            } else if (isChecked && !filterSelected_[filterIndex]) {
                filterSelected_[filterIndex] = true;
                showFilterOptions(filterIndex);
            } else if (!isChecked && filterSelected_[filterIndex]) {
                storeFilterSelection(filterIndex);
                filterSelected_[filterIndex] = false;
                openFilterLists_[filterIndex] = false;
                positionControls();
                status_.clear();
                updateControls();
                InvalidateRect(parent_, nullptr, TRUE);
            }
            return true;
        }

        const bool isChecked =
            SendMessageW(filterButtons_[filterIndex], BM_GETCHECK, 0, 0) ==
            BST_CHECKED;
        if (reportColumns[filterIndex].hasMultiselect &&
            filterSelected_[filterIndex] &&
            !openFilterLists_[filterIndex] && !isChecked) {
            SendMessageW(
                filterButtons_[filterIndex], BM_SETCHECK, BST_CHECKED, 0);
            showFilterOptions(filterIndex);
            return true;
        }
        if (isChecked && !filterSelected_[filterIndex]) {
            int selectedWeight = 0;
            for (std::size_t index = 0; index < reportColumnCount; ++index) {
                if (filterSelected_[index]) {
                    selectedWeight += reportColumns[index].widthWeight;
                }
            }
            if (selectedWeight + reportColumns[filterIndex].widthWeight >
                maximumSelectedFilterWeight) {
                SendMessageW(
                    filterButtons_[filterIndex], BM_SETCHECK,
                    BST_UNCHECKED, 0);
                status_ =
                    "Można wybrać najwyżej 7 jednostek kolumn; "
                    "data sygnału jest zawsze uwzględniona.";
                InvalidateRect(parent_, nullptr, TRUE);
                return true;
            }
            filterSelected_[filterIndex] = true;
        } else if (!isChecked && filterSelected_[filterIndex]) {
            if (reportColumns[filterIndex].hasMultiselect) {
                storeFilterSelection(filterIndex);
            }
            filterSelected_[filterIndex] = false;
            openFilterLists_[filterIndex] = false;
            positionControls();
        }

        status_.clear();
        if (filterSelected_[filterIndex] &&
            reportColumns[filterIndex].hasMultiselect) {
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
 * Rozmieszcza daty, siatkę filtrów, otwarte listy opcji i przyciski nawigacji.
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
    const int datePickerWidth =
        std::max(1, fieldWidth / datePickerSizeDivisor);
    const int secondX = firstX + datePickerWidth + scaledGap;
    const int generateX = secondX + datePickerWidth + scaledGap;
    const int labelY = WindowScale::size(parent_, formTop);
    const int labelHeightScaled =
        WindowScale::size(parent_, labelHeight / datePickerSizeDivisor);
    const int pickerHeightScaled =
        WindowScale::size(parent_, pickerHeight / datePickerSizeDivisor);
    const int generateButtonHeightScaled =
        WindowScale::size(parent_, pickerHeight);
    const int pickerY = labelY + labelHeightScaled;
    const int formHeight = std::max(
        generateButtonHeightScaled,
        labelHeightScaled + pickerHeightScaled);
    const int filterTop = labelY + formHeight + scaledGap;
    const int buttonHeight =
        WindowScale::size(parent_, filterButtonHeight);
    const int buttonGap =
        WindowScale::size(parent_, filterButtonGap);
    const int filterListHeightScaled =
        WindowScale::size(
            parent_, filterListHeight / filterListHeightDivisor);
    const int contentWidth =
        std::max(1, clientWidth - scaledMargin * 2);
    const int referenceButtonGap =
        WindowScale::size(parent_, referenceFilterButtonGap);
    const int referenceButtonWidth = std::max(
        1, (contentWidth -
            referenceButtonGap * (referenceFilterButtonColumns - 1)) /
               referenceFilterButtonColumns);
    const int buttonWidth = std::max(
        1, referenceButtonWidth / filterButtonWidthDivisor);
    const int buttonRowCount = static_cast<int>(
        (filterButtonCount + filterButtonColumns - 1) /
        filterButtonColumns);
    std::vector<bool> rowHasOpenList(
        static_cast<std::size_t>(buttonRowCount), false);
    for (std::size_t index = 0; index < openFilterLists_.size(); ++index) {
        if (openFilterLists_[index]) {
            const std::size_t row =
                index / static_cast<std::size_t>(filterButtonColumns);
            rowHasOpenList[row] = true;
        }
    }
    std::vector<int> expandedRowsBefore(
        static_cast<std::size_t>(buttonRowCount), 0);
    int expandedRowCount = 0;
    for (int row = 0; row < buttonRowCount; ++row) {
        expandedRowsBefore[static_cast<std::size_t>(row)] = expandedRowCount;
        if (rowHasOpenList[static_cast<std::size_t>(row)]) {
            ++expandedRowCount;
        }
    }
    const int pageButtonWidthScaled =
        WindowScale::size(parent_, pageButtonWidth);
    const int pageButtonHeightScaled =
        WindowScale::size(parent_, pageButtonHeight);
    const int pageLabelWidthScaled =
        WindowScale::size(parent_, pageLabelWidth);
    const int pageLabelHeightScaled =
        WindowScale::size(parent_, pageLabelHeight);
    const int paginationRowHeight =
        std::max(pageButtonHeightScaled, pageLabelHeightScaled);
    const int paginationGap = scaledGap;
    const int paginationGroupWidth =
        pageButtonWidthScaled * 2 + pageLabelWidthScaled +
        paginationGap * 2;
    const int paginationGroupX =
        std::max(0, (clientWidth - paginationGroupWidth) / 2);
    const int paginationY = std::max(
        0, calculateTableTop(parent_, openFilterLists_) -
               paginationRowHeight - paginationGap);

    WindowScale::updateFont(
        parent_, controlFont_, controlFontHeight_, 4,
        {fromDateLabel_, fromDatePicker_, toDateLabel_, toDatePicker_});
    WindowScale::updateFont(
        parent_, buttonFont_, buttonFontHeight_, 14,
        {previousPageButton_, nextPageButton_, generateButton_});
    WindowScale::updateFont(
        parent_, filterButtonFont_, filterButtonFontHeight_,
        filterButtonFontHeight, filterButtons_);
    for (HWND list : filterLists_) {
        if (list != nullptr) {
            SendMessageW(
                list, WM_SETFONT,
                reinterpret_cast<WPARAM>(controlFont_), TRUE);
        }
    }

    MoveWindow(
        fromDateLabel_, firstX, labelY, datePickerWidth, labelHeightScaled, TRUE);
    MoveWindow(
        fromDatePicker_, firstX, pickerY, datePickerWidth,
        pickerHeightScaled, TRUE);
    MoveWindow(
        toDateLabel_, secondX, labelY, datePickerWidth, labelHeightScaled, TRUE);
    MoveWindow(
        toDatePicker_, secondX, pickerY, datePickerWidth,
        pickerHeightScaled, TRUE);
    MoveWindow(
        generateButton_, generateX, labelY, generateWidth,
        generateButtonHeightScaled, TRUE);

    for (std::size_t index = 0; index < filterButtonCount; ++index) {
        const int column = static_cast<int>(index % filterButtonColumns);
        const int row = static_cast<int>(index / filterButtonColumns);
        const int x = scaledMargin + column * (buttonWidth + buttonGap);
        const int y =
            filterTop + row * (buttonHeight + buttonGap) +
            expandedRowsBefore[static_cast<std::size_t>(row)] *
                (filterListHeightScaled + buttonGap);
        MoveWindow(filterButtons_[index], x, y, buttonWidth, buttonHeight, TRUE);
        if (index < filterLists_.size() &&
            filterLists_[index] != nullptr && openFilterLists_[index]) {
            const int listY = y + buttonHeight + buttonGap;
            MoveWindow(
                filterLists_[index], x, listY, buttonWidth,
                filterListHeightScaled, TRUE);
        }
    }

    MoveWindow(
        previousPageButton_, paginationGroupX, paginationY,
        pageButtonWidthScaled, pageButtonHeightScaled, TRUE);
    const int pageLabelX =
        paginationGroupX + pageButtonWidthScaled + paginationGap;
    MoveWindow(
        nextPageButton_, pageLabelX + pageLabelWidthScaled + paginationGap,
        paginationY,
        pageButtonWidthScaled, pageButtonHeightScaled, TRUE);
}

/**
 * Rysuje wybrane kolumny, maksymalnie dziesięć rekordów i wiersz podsumowania.
 * @param deviceContext Kontekst używany do rysowania.
 * @param clientArea Obszar dostępny dla widoku.
 */
void TransactionsPage::draw(HDC deviceContext, RECT& clientArea) const {
    RECT tableArea = clientArea;
    tableArea.top += calculateTableTop(parent_, openFilterLists_);
    tableArea.bottom = std::max(
        tableArea.top,
        static_cast<LONG>(
            clientArea.bottom - WindowScale::size(parent_, formMargin)));

    if (generated_) {
        const int pageButtonWidthScaled =
            WindowScale::size(parent_, pageButtonWidth);
        const int pageLabelWidthScaled =
            WindowScale::size(parent_, pageLabelWidth);
        const int pageLabelHeightScaled =
            WindowScale::size(parent_, pageLabelHeight);
        const int paginationGap = WindowScale::size(parent_, formGap);
        const int paginationGroupWidth =
            pageButtonWidthScaled * 2 + pageLabelWidthScaled +
            paginationGap * 2;
        const int clientWidth =
            static_cast<int>(clientArea.right - clientArea.left);
        const int paginationGroupX = std::max(
            0, (clientWidth - paginationGroupWidth) / 2);
        const int paginationLabelX =
            paginationGroupX + pageButtonWidthScaled + paginationGap;
        RECT pageBounds{
            paginationLabelX,
            tableArea.top - paginationGap - pageLabelHeightScaled,
            paginationLabelX + pageLabelWidthScaled,
            tableArea.top - paginationGap};
        const std::wstring pageLabel =
            L"Strona " + std::to_wstring(currentPage_ + 1);
        WindowScale::drawText(
            deviceContext, parent_, pageLabel, pageBounds,
            DT_CENTER | DT_VCENTER | DT_SINGLELINE);
    }

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
        } else if (appliedColumns_[columnIndex] ==
                   signalDatetimeColumnKey) {
            title = signalDatetimeColumnLabel;
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
            appliedFilters_ = selectedFilterValues();
            generated_ = true;
            std::fill(
                openFilterLists_.begin(), openFilterLists_.end(), false);
            positionControls();
            loadCurrentPage();
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
    if (filterIndex >= filterButtonCount ||
        (filterIndex != legacyButtonIndex &&
         !reportColumns[filterIndex].hasMultiselect) ||
        filterLists_[filterIndex] == nullptr) {
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

    const std::string filterKey = filterIndex == legacyButtonIndex
        ? "signal_era"
        : reportColumns[filterIndex].key;
    const auto foundOptions = filterOptions_.find(filterKey);
    const std::vector<TransactionReportFilterOption> emptyOptions;
    const std::vector<TransactionReportFilterOption>& filterOptions =
        foundOptions == filterOptions_.end()
            ? emptyOptions
            : foundOptions->second;
    const std::vector<std::string>& previousValues =
        selectedFilterValues_[filterIndex];
    std::vector<std::string> optionValues;
    std::vector<std::string> validSelectedValues;
    optionValues.reserve(filterOptions.size());
    validSelectedValues.reserve(previousValues.size());

    SendMessageW(
        filterLists_[filterIndex], LB_RESETCONTENT, 0, 0);
    for (const TransactionReportFilterOption& option : filterOptions) {
        std::wstring wideLabel;
        if (!convertUtf8ToWide(option.label, wideLabel)) {
            status_ = "API zwróciło nieprawidłowy tekst opcji filtra.";
            ErrorLogger::error(status_);
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
            updateControls();
            InvalidateRect(parent_, nullptr, TRUE);
            return false;
        }
        optionValues.push_back(option.value);
        if (std::find(
                previousValues.begin(), previousValues.end(),
                option.value) != previousValues.end()) {
            validSelectedValues.push_back(option.value);
            SendMessageW(
                filterLists_[filterIndex], LB_SETSEL, TRUE, itemIndex);
        }
    }
    SendMessageW(
        filterLists_[filterIndex], LB_SETHORIZONTALEXTENT,
        static_cast<WPARAM>(WindowScale::size(parent_, 1200)), 0);

    filterOptionValues_[filterIndex] = std::move(optionValues);
    selectedFilterValues_[filterIndex] = std::move(validSelectedValues);
    openFilterLists_[filterIndex] = true;
    status_.clear();
    positionControls();
    updateControls();
    InvalidateRect(parent_, nullptr, TRUE);
    return true;
}

/**
 * Zapisuje zaznaczone wartości aktualnie widocznej listy.
 */
void TransactionsPage::storeFilterSelection(std::size_t filterIndex) {
    if (filterIndex >= filterButtonCount ||
        (filterIndex != legacyButtonIndex &&
         !reportColumns[filterIndex].hasMultiselect) ||
        filterLists_[filterIndex] == nullptr) {
        return;
    }

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
 * Zwraca datę sygnału, a następnie zaznaczone kolumny formularza.
 */
std::vector<std::string> TransactionsPage::selectedColumnKeys() const {
    std::vector<std::string> columns;
    columns.emplace_back(signalDatetimeColumnKey);
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
    if (
        filterSelected_[legacyButtonIndex] &&
        !selectedFilterValues_[legacyButtonIndex].empty()) {
        filters.emplace(
            "signal_era", selectedFilterValues_[legacyButtonIndex]);
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
             generateButton_}) {
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
        if (filterLists_[index] != nullptr) {
            const bool showList = visible_ && openFilterLists_[index];
            ShowWindow(filterLists_[index], showList ? SW_SHOW : SW_HIDE);
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
