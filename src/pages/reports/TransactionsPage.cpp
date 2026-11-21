#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commctrl.h>

#include "TransactionsPage.h"
#include "../WindowScale.h"

#include <algorithm>
#include <initializer_list>

namespace {

constexpr int formTop = 90;
constexpr int formMargin = 24;
constexpr int formGap = 24;
constexpr int fieldWidth = 190;
constexpr int labelHeight = 24;
constexpr int pickerHeight = 30;

} // namespace

/**
 * Tworzy kontrolki daty z natywnym kalendarzem rozwijanym.
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
        nullptr, instance, nullptr);
    fromDatePicker_ = CreateWindowExW(
        0, DATETIMEPICK_CLASSW, L"",
        WS_CHILD | WS_TABSTOP | DTS_SHORTDATECENTURYFORMAT,
        0, 0, 0, 0, parent_,
        nullptr, instance, nullptr);
    toDateLabel_ = CreateWindowExW(
        0, L"STATIC", L"Data do",
        WS_CHILD | SS_LEFT,
        0, 0, 0, 0, parent_,
        nullptr, instance, nullptr);
    toDatePicker_ = CreateWindowExW(
        0, DATETIMEPICK_CLASSW, L"",
        WS_CHILD | WS_TABSTOP | DTS_SHORTDATECENTURYFORMAT,
        0, 0, 0, 0, parent_,
        nullptr, instance, nullptr);

    if (fromDateLabel_ == nullptr || fromDatePicker_ == nullptr ||
        toDateLabel_ == nullptr || toDatePicker_ == nullptr) {
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
 * Otwiera widok, nie resetując dat wybranych wcześniej przez użytkownika.
 */
void TransactionsPage::open() {
    visible_ = true;
    updateControls();
}

/**
 * Ukrywa widok, pozostawiając daty w kontrolkach na czas kolejnego otwarcia.
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
 * Rozmieszcza etykiety i pola daty w dwóch kolumnach.
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
    const int scaledFieldWidth = std::min(
        WindowScale::size(parent_, fieldWidth),
        std::max(1, (clientWidth - scaledMargin * 2 - scaledGap) / 2));
    const int totalWidth = scaledFieldWidth * 2 + scaledGap;
    const int firstX = std::max(0, (clientWidth - totalWidth) / 2);
    const int secondX = firstX + scaledFieldWidth + scaledGap;
    const int labelY = WindowScale::size(parent_, formTop);
    const int pickerY = labelY + WindowScale::size(parent_, labelHeight);
    const int labelHeightScaled = WindowScale::size(parent_, labelHeight);
    const int pickerHeightScaled = WindowScale::size(parent_, pickerHeight);

    WindowScale::updateFont(
        parent_, controlFont_, controlFontHeight_, 14,
        {fromDateLabel_, fromDatePicker_, toDateLabel_, toDatePicker_});

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
}

/**
 * Wyświetla nagłówek nad polami zakresu dat.
 */
void TransactionsPage::draw(HDC deviceContext, RECT& clientArea) const {
    RECT headingArea = clientArea;
    headingArea.top += WindowScale::size(parent_, 28);
    headingArea.bottom = headingArea.top + WindowScale::size(parent_, 38);
    WindowScale::drawText(
        deviceContext, parent_, L"Zakres dat transakcji", headingArea,
        DT_CENTER | DT_VCENTER | DT_SINGLELINE, 22);
}

/**
 * Pokazuje albo ukrywa kontrolki zgodnie ze stanem widoku.
 */
void TransactionsPage::updateControls() {
    const int visibility = visible_ ? SW_SHOW : SW_HIDE;
    for (HWND control : {
             fromDateLabel_, fromDatePicker_, toDateLabel_, toDatePicker_}) {
        if (control != nullptr) {
            ShowWindow(control, visibility);
        }
    }
}

/**
 * Zwalnia czcionkę przypisaną kontrolkom daty.
 */
TransactionsPage::~TransactionsPage() {
    if (controlFont_ != nullptr) {
        DeleteObject(controlFont_);
    }
}
