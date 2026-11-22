#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include "TodoView.hpp"

#include "ErrorLogger.h"
#include "../WindowScale.h"

#include <algorithm>
#include <climits>
#include <cstdlib>
#include <limits>
#include <string>
#include <utility>

namespace {

// Front
constexpr int addMainTaskButtonId = 1900;
constexpr int navigationButtonHeight = 36;
constexpr int navigationBottomMargin = 16;
constexpr wchar_t textDialogClassName[] = L"GieDaTodoTextDialog";
constexpr int dialogSaveButtonId = 1;
constexpr int dialogCancelButtonId = 2;

/**
 * Przechowuje kontrolki i wynik modalnego okna edycji tekstu.
 */
struct TextDialogState {
    HWND edit = nullptr;
    HINSTANCE instance = nullptr;
    std::wstring initialText;
    std::wstring result;
    bool multiline = false;
    bool accepted = false;
    bool done = false;
};

/**
 * Konwertuje UTF-8 z API do tekstu Unicode używanego przez kontrolki Windows.
 */
bool convertUtf8ToWide(
    const std::string& text, std::wstring& convertedText) {
    if (text.empty()) {
        convertedText.clear();
        return true;
    }
    if (text.size() > static_cast<std::size_t>(INT_MAX)) {
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
 * Konwertuje tekst Unicode z okna dialogowego do UTF-8 dla lokalnego API.
 */
bool convertWideToUtf8(
    const std::wstring& text, std::string& convertedText) {
    if (text.empty()) {
        convertedText.clear();
        return true;
    }
    if (text.size() > static_cast<std::size_t>(INT_MAX)) {
        return false;
    }

    const int requiredBytes = WideCharToMultiByte(
        CP_UTF8, WC_ERR_INVALID_CHARS, text.data(),
        static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
    if (requiredBytes == 0) {
        return false;
    }
    convertedText.resize(static_cast<std::size_t>(requiredBytes));
    return WideCharToMultiByte(
               CP_UTF8, WC_ERR_INVALID_CHARS, text.data(),
               static_cast<int>(text.size()), convertedText.data(),
               requiredBytes, nullptr, nullptr) == requiredBytes;
}

/**
 * Tworzy edytor dialogowy i jego przyciski zatwierdzenia oraz anulowania.
 */
LRESULT CALLBACK textDialogProcedure(
    HWND window, UINT message, WPARAM wParam, LPARAM lParam) {
    auto* state = reinterpret_cast<TextDialogState*>(
        GetWindowLongPtrW(window, GWLP_USERDATA));
    if (message == WM_NCCREATE) {
        const auto* createData =
            reinterpret_cast<const CREATESTRUCTW*>(lParam);
        state = static_cast<TextDialogState*>(createData->lpCreateParams);
        SetWindowLongPtrW(
            window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(state));
    }
    if (state == nullptr) {
        return DefWindowProcW(window, message, wParam, lParam);
    }

    if (message == WM_CREATE) {
        const int margin = WindowScale::size(window, 16);
        const int labelHeight = WindowScale::size(window, 24);
        const int buttonHeight = WindowScale::size(window, 34);
        RECT clientArea{};
        GetClientRect(window, &clientArea);
        const int clientWidth = clientArea.right - clientArea.left;
        const int clientHeight = clientArea.bottom - clientArea.top;
        CreateWindowExW(
            0, L"STATIC",
            state->multiline ? L"Treść:" : L"Nazwa zadania:",
            WS_CHILD | WS_VISIBLE | SS_LEFT,
            margin, margin,
            clientWidth - 2 * margin, labelHeight,
            window, nullptr, state->instance, nullptr);

        DWORD editStyle = WS_CHILD | WS_VISIBLE | WS_TABSTOP | WS_BORDER;
        editStyle |= state->multiline
            ? ES_MULTILINE | ES_AUTOVSCROLL | ES_WANTRETURN | WS_VSCROLL
            : ES_AUTOHSCROLL;
        state->edit = CreateWindowExW(
            WS_EX_CLIENTEDGE, L"EDIT", L"",
            editStyle,
            margin, margin + labelHeight,
            clientWidth - 2 * margin,
            clientHeight - 3 * margin - labelHeight - buttonHeight,
            window, nullptr, state->instance, nullptr);
        HWND saveButton = CreateWindowExW(
            0, L"BUTTON", L"Zapisz",
            WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_DEFPUSHBUTTON,
            clientWidth - 2 * margin - 2 * WindowScale::size(window, 110),
            clientHeight - margin - buttonHeight,
            WindowScale::size(window, 110), buttonHeight,
            window,
            reinterpret_cast<HMENU>(
                static_cast<INT_PTR>(dialogSaveButtonId)),
            state->instance, nullptr);
        HWND cancelButton = CreateWindowExW(
            0, L"BUTTON", L"Anuluj",
            WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_PUSHBUTTON,
            clientWidth - margin - WindowScale::size(window, 110),
            clientHeight - margin - buttonHeight,
            WindowScale::size(window, 110), buttonHeight,
            window,
            reinterpret_cast<HMENU>(
                static_cast<INT_PTR>(dialogCancelButtonId)),
            state->instance, nullptr);
        if (state->edit == nullptr || saveButton == nullptr ||
            cancelButton == nullptr) {
            state->done = true;
            return -1;
        }

        SendMessageW(
            state->edit, EM_SETLIMITTEXT,
            static_cast<WPARAM>(INT_MAX), 0);
        SetWindowTextW(state->edit, state->initialText.c_str());
        SendMessageW(
            state->edit, WM_SETFONT,
            reinterpret_cast<WPARAM>(GetStockObject(DEFAULT_GUI_FONT)), TRUE);
        SendMessageW(
            saveButton, WM_SETFONT,
            reinterpret_cast<WPARAM>(GetStockObject(DEFAULT_GUI_FONT)), TRUE);
        SendMessageW(
            cancelButton, WM_SETFONT,
            reinterpret_cast<WPARAM>(GetStockObject(DEFAULT_GUI_FONT)), TRUE);
        SetFocus(state->edit);
        return 0;
    }

    if (message == WM_COMMAND) {
        const int controlId = LOWORD(wParam);
        if (controlId == dialogSaveButtonId) {
            const int textLength = GetWindowTextLengthW(state->edit);
            std::wstring text(static_cast<std::size_t>(textLength) + 1, L'\0');
            const int copied = GetWindowTextW(
                state->edit, text.data(), textLength + 1);
            text.resize(static_cast<std::size_t>(std::max(0, copied)));
            state->result = std::move(text);
            state->accepted = true;
            state->done = true;
            return 0;
        }
        if (controlId == dialogCancelButtonId) {
            state->done = true;
            return 0;
        }
    }
    if (message == WM_CLOSE) {
        state->done = true;
        return 0;
    }
    return DefWindowProcW(window, message, wParam, lParam);
}

/**
 * Rejestruje klasę własnego okna edycji przed jego pierwszym utworzeniem.
 */
bool registerTextDialogClass(HINSTANCE instance) {
    static bool registrationAttempted = false;
    static bool registered = false;
    if (registrationAttempted) {
        return registered;
    }
    registrationAttempted = true;

    WNDCLASSEXW windowClass{};
    windowClass.cbSize = sizeof(windowClass);
    windowClass.lpfnWndProc = textDialogProcedure;
    windowClass.hInstance = instance;
    windowClass.hCursor = LoadCursor(nullptr, IDC_IBEAM);
    windowClass.hbrBackground =
        reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
    windowClass.lpszClassName = textDialogClassName;
    const ATOM atom = RegisterClassExW(&windowClass);
    registered = atom != 0 || GetLastError() == ERROR_CLASS_ALREADY_EXISTS;
    return registered;
}

/**
 * Otwiera modalne okno tekstowe; false oznacza anulowanie lub błąd.
 */
bool showTextInputDialog(
    HINSTANCE instance,
    HWND owner,
    const std::wstring& title,
    const std::wstring& initialText,
    bool multiline,
    std::wstring& result,
    std::string& error) {
    result.clear();
    error.clear();
    if (!registerTextDialogClass(instance)) {
        error = "Nie udało się zarejestrować okna edycji tekstu.";
        return false;
    }

    TextDialogState state;
    state.instance = instance;
    state.initialText = initialText;
    state.multiline = multiline;

    RECT ownerBounds{};
    GetWindowRect(owner, &ownerBounds);
    RECT workArea{0, 0, GetSystemMetrics(SM_CXSCREEN),
                  GetSystemMetrics(SM_CYSCREEN)};
    MONITORINFO monitorInfo{};
    monitorInfo.cbSize = sizeof(monitorInfo);
    const HMONITOR monitor =
        MonitorFromWindow(owner, MONITOR_DEFAULTTONEAREST);
    if (GetMonitorInfoW(monitor, &monitorInfo)) {
        workArea = monitorInfo.rcWork;
    }
    const int desiredWidth = WindowScale::size(owner, 560);
    const int desiredHeight =
        WindowScale::size(owner, multiline ? 420 : 190);
    const int dialogWidth = std::min(
        desiredWidth,
        static_cast<int>(std::max<LONG>(
            1, workArea.right - workArea.left - 32)));
    const int dialogHeight = std::min(
        desiredHeight,
        static_cast<int>(std::max<LONG>(
            1, workArea.bottom - workArea.top - 32)));
    const LONG left = ownerBounds.left +
        ((ownerBounds.right - ownerBounds.left) - dialogWidth) / 2;
    const LONG top = ownerBounds.top +
        ((ownerBounds.bottom - ownerBounds.top) - dialogHeight) / 2;
    HWND dialog = CreateWindowExW(
        WS_EX_DLGMODALFRAME | WS_EX_CONTROLPARENT,
        textDialogClassName, title.c_str(),
        WS_POPUP | WS_CAPTION | WS_SYSMENU,
        static_cast<int>(std::clamp<LONG>(
            left, workArea.left, workArea.right - dialogWidth)),
        static_cast<int>(std::clamp<LONG>(
            top, workArea.top, workArea.bottom - dialogHeight)),
        dialogWidth, dialogHeight,
        owner, nullptr, instance, &state);
    if (dialog == nullptr) {
        error = "Nie udało się utworzyć okna edycji tekstu.";
        return false;
    }

    EnableWindow(owner, FALSE);
    ShowWindow(dialog, SW_SHOW);
    UpdateWindow(dialog);

    int quitCode = 0;
    bool quitReceived = false;
    while (!state.done) {
        MSG message{};
        const BOOL resultCode = GetMessageW(&message, nullptr, 0, 0);
        if (resultCode <= 0) {
            if (resultCode == 0) {
                quitCode = static_cast<int>(message.wParam);
                quitReceived = true;
            } else {
                error = "Nie udało się pobrać komunikatu okna edycji.";
            }
            state.done = true;
            break;
        }
        if (!IsDialogMessageW(dialog, &message)) {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
    }

    DestroyWindow(dialog);
    EnableWindow(owner, TRUE);
    SetActiveWindow(owner);
    if (quitReceived) {
        PostQuitMessage(quitCode);
    }
    if (!error.empty() || !state.accepted) {
        return false;
    }
    result = std::move(state.result);
    return true;
}

} // namespace

/**
 * Powiązuje widok z warstwą zarządzającą zadaniami.
 */
TodoView::TodoView(TodoManager& manager) : manager_(manager) {}

/**
 * Usuwa utworzone wiersze przed zwolnieniem obiektu widoku.
 */
TodoView::~TodoView() {
    destroyTaskControls();
}

/**
 * Tworzy stały przycisk dodawania zadania głównego.
 */
bool TodoView::create(HINSTANCE instance, HWND parent) {
    instance_ = instance;
    parent_ = parent;
    addMainTaskButton_ = CreateWindowExW(
        0, L"BUTTON", L"+ Dodaj zadanie główne",
        WS_CHILD | WS_TABSTOP | BS_PUSHBUTTON,
        0, 0, 1, 1, parent_,
        reinterpret_cast<HMENU>(
            static_cast<INT_PTR>(addMainTaskButtonId)),
        instance_, nullptr);
    if (addMainTaskButton_ == nullptr) {
        return false;
    }
    scrollBar_ = CreateWindowExW(
        0, L"SCROLLBAR", nullptr,
        WS_CHILD | SBS_VERT,
        0, 0, 1, 1, parent_,
        reinterpret_cast<HMENU>(static_cast<INT_PTR>(1901)),
        instance_, nullptr);
    if (scrollBar_ == nullptr) {
        DestroyWindow(addMainTaskButton_);
        addMainTaskButton_ = nullptr;
        return false;
    }
    SendMessageW(
        addMainTaskButton_, WM_SETFONT,
        reinterpret_cast<WPARAM>(GetStockObject(DEFAULT_GUI_FONT)), TRUE);
    ShowWindow(scrollBar_, SW_HIDE);
    positionControls();
    return true;
}

/**
 * Pokazuje widok i odczytuje stan z lokalnej bazy przez API.
 */
void TodoView::open() {
    visible_ = true;
    scrollPosition_ = 0;
    ShowWindow(addMainTaskButton_, SW_SHOW);
    refresh();
}

/**
 * Ukrywa widok i jego pasek przewijania bez usuwania zapisanych danych.
 */
void TodoView::close() {
    visible_ = false;
    scrollPosition_ = 0;
    ShowWindow(addMainTaskButton_, SW_HIDE);
    for (HWND control : taskControls_) {
        ShowWindow(control, SW_HIDE);
    }
    ShowWindow(scrollBar_, SW_HIDE);
}

/**
 * Zwraca aktualną widoczność widoku.
 */
bool TodoView::isVisible() const {
    return visible_;
}

/**
 * Obsługuje kliknięcia przycisku dodawania i wierszy zadań.
 */
bool TodoView::handleCommand(int controlId, int notificationCode) {
    if (notificationCode != BN_CLICKED) {
        return false;
    }
    if (controlId == addMainTaskButtonId) {
        addMainTask();
        return true;
    }
    const auto action = controlActions_.find(controlId);
    if (action == controlActions_.end()) {
        return false;
    }
    performAction(action->second, controlId);
    return true;
}

/**
 * Przewija listę i aktualizuje pozycję widocznych kontrolek.
 */
bool TodoView::handleScroll(WPARAM scrollCommand) {
    if (!visible_ || contentHeight_ <= 0) {
        return false;
    }

    SCROLLINFO scrollInfo{};
    scrollInfo.cbSize = sizeof(scrollInfo);
    scrollInfo.fMask = SIF_ALL;
    GetScrollInfo(scrollBar_, SB_CTL, &scrollInfo);

    int nextPosition = scrollPosition_;
    const int lineHeight = WindowScale::size(parent_, 44);
    const int pageHeight = std::max(
        1, static_cast<int>(scrollInfo.nPage) - lineHeight);
    switch (LOWORD(scrollCommand)) {
        case SB_LINEUP:
            nextPosition -= lineHeight;
            break;
        case SB_LINEDOWN:
            nextPosition += lineHeight;
            break;
        case SB_PAGEUP:
            nextPosition -= pageHeight;
            break;
        case SB_PAGEDOWN:
            nextPosition += pageHeight;
            break;
        case SB_THUMBTRACK:
        case SB_THUMBPOSITION:
            nextPosition = scrollInfo.nTrackPos;
            break;
        case SB_TOP:
            nextPosition = 0;
            break;
        case SB_BOTTOM:
            nextPosition = std::numeric_limits<int>::max();
            break;
        default:
            return false;
    }

    const int maximumPosition = std::max(
        0, scrollInfo.nMax - static_cast<int>(scrollInfo.nPage) + 1);
    scrollPosition_ = std::clamp(nextPosition, 0, maximumPosition);
    applyScrollPosition();
    scrollInfo.fMask = SIF_POS;
    scrollInfo.nPos = scrollPosition_;
    SetScrollInfo(scrollBar_, SB_CTL, &scrollInfo, TRUE);
    return true;
}

/**
 * Przewija listę w odpowiedzi na obrót kółka myszy.
 */
bool TodoView::handleMouseWheel(short wheelDelta) {
    if (!visible_ || wheelDelta == 0) {
        return false;
    }
    const int lines = std::max(
        1, std::abs(static_cast<int>(wheelDelta)) / WHEEL_DELTA);
    const int direction = wheelDelta > 0 ? -1 : 1;
    const WPARAM command = direction < 0
        ? SB_LINEUP
        : SB_LINEDOWN;
    for (int line = 0; line < lines; ++line) {
        handleScroll(command);
    }
    return true;
}

/**
 * Rysuje nagłówek, pusty stan lub komunikat błędu widoku.
 */
// Front
void TodoView::draw(HDC deviceContext, RECT& clientArea) const {
    RECT titleBounds = clientArea;
    titleBounds.left += WindowScale::size(parent_, 24);
    titleBounds.top += WindowScale::size(parent_, 20);
    titleBounds.bottom = titleBounds.top + WindowScale::size(parent_, 32);
    WindowScale::drawText(
        deviceContext, parent_, L"To-do List", titleBounds,
        DT_LEFT | DT_VCENTER | DT_SINGLELINE, 22);

    if (!status_.empty()) {
        std::wstring wideStatus;
        if (convertUtf8ToWide(status_, wideStatus)) {
            RECT statusBounds = clientArea;
            statusBounds.left += WindowScale::size(parent_, 24);
            statusBounds.top += WindowScale::size(parent_, 72);
            statusBounds.bottom = statusBounds.top +
                WindowScale::size(parent_, 48);
            WindowScale::drawText(
                deviceContext, parent_, wideStatus, statusBounds,
                DT_LEFT | DT_WORDBREAK, 14);
        }
        return;
    }
    if (tasks_.empty()) {
        RECT emptyBounds = clientArea;
        emptyBounds.left += WindowScale::size(parent_, 24);
        emptyBounds.top += WindowScale::size(parent_, 118);
        emptyBounds.bottom = emptyBounds.top + WindowScale::size(parent_, 48);
        WindowScale::drawText(
            deviceContext, parent_, L"Brak zadań. Dodaj pierwsze zadanie.",
            emptyBounds, DT_LEFT | DT_WORDBREAK, 15);
    }
}

/**
 * Rysuje checkboxy główne bez obsługi kliknięć oraz czerwone przyciski X.
 */
bool TodoView::handleDrawItem(const DRAWITEMSTRUCT* drawItem) const {
    if (drawItem == nullptr || drawItem->CtlType != ODT_BUTTON) {
        return false;
    }

    const auto mainCheckbox = mainCheckboxes_.find(drawItem->CtlID);
    if (mainCheckbox != mainCheckboxes_.end()) {
        RECT bounds = drawItem->rcItem;
        FillRect(
            drawItem->hDC, &bounds,
            reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1));
        RECT checkboxBounds = bounds;
        const int checkboxSize = WindowScale::size(parent_, 18);
        checkboxBounds.right = checkboxBounds.left + checkboxSize;
        checkboxBounds.top += (bounds.bottom - bounds.top - checkboxSize) / 2;
        checkboxBounds.bottom = checkboxBounds.top + checkboxSize;
        UINT checkboxState = DFCS_BUTTONCHECK;
        if (mainCheckbox->second.completed) {
            checkboxState |= DFCS_CHECKED;
        }
        DrawFrameControl(
            drawItem->hDC, &checkboxBounds, DFC_BUTTON, checkboxState);

        RECT textBounds = bounds;
        textBounds.left = checkboxBounds.right + WindowScale::size(parent_, 8);
        HFONT taskFont = CreateFontW(
            -WindowScale::size(parent_, 16), 0, 0, 0, FW_BOLD,
            FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
            CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
            DEFAULT_PITCH | FF_DONTCARE, L"Segoe UI");
        HGDIOBJ previousFont = taskFont == nullptr
            ? nullptr
            : SelectObject(drawItem->hDC, taskFont);
        DrawTextW(
            drawItem->hDC,
            mainCheckbox->second.title.c_str(),
            static_cast<int>(mainCheckbox->second.title.size()),
            &textBounds,
            DT_LEFT | DT_VCENTER | DT_WORDBREAK | DT_NOPREFIX);
        if (taskFont != nullptr) {
            SelectObject(drawItem->hDC, previousFont);
            DeleteObject(taskFont);
        }
        return true;
    }

    const auto action = controlActions_.find(drawItem->CtlID);
    if (action == controlActions_.end() ||
        (action->second.kind != ActionKind::deleteMainTask &&
         action->second.kind != ActionKind::deleteSubtask)) {
        return false;
    }

    HBRUSH buttonBrush = CreateSolidBrush(
        (drawItem->itemState & ODS_SELECTED) != 0
            ? RGB(145, 24, 24)
            : RGB(190, 36, 36));
    FillRect(drawItem->hDC, &drawItem->rcItem, buttonBrush);
    DeleteObject(buttonBrush);
    FrameRect(
        drawItem->hDC, &drawItem->rcItem,
        reinterpret_cast<HBRUSH>(GetStockObject(BLACK_BRUSH)));
    SetBkMode(drawItem->hDC, TRANSPARENT);
    SetTextColor(drawItem->hDC, RGB(255, 255, 255));
    RECT textBounds = drawItem->rcItem;
    DrawTextW(
        drawItem->hDC, L"X", 1, &textBounds,
        DT_CENTER | DT_VCENTER | DT_SINGLELINE);
    return true;
}

/**
 * Ustawia stały przycisk dodawania i przebudowuje listę po zmianie rozmiaru.
 */
void TodoView::positionControls() {
    if (parent_ == nullptr || addMainTaskButton_ == nullptr) {
        return;
    }

    RECT clientArea{};
    GetClientRect(parent_, &clientArea);
    const int clientWidth = clientArea.right - clientArea.left;
    const int buttonX = WindowScale::size(parent_, 24);
    const int buttonY = WindowScale::size(parent_, 58);
    const int buttonWidth = std::min(
        WindowScale::size(parent_, 230),
        std::max(1, clientWidth - 2 * buttonX));
    const int buttonHeight = WindowScale::size(parent_, 36);
    MoveWindow(
        addMainTaskButton_, buttonX, buttonY,
        buttonWidth, buttonHeight, TRUE);
    const int scrollBarWidth = GetSystemMetrics(SM_CXVSCROLL);
    const int contentTop = WindowScale::size(parent_, 108);
    const int contentBottom = clientArea.bottom - WindowScale::size(
        parent_, navigationButtonHeight + navigationBottomMargin * 2);
    const int scrollBarX = std::max(
        0, static_cast<int>(clientArea.right) - scrollBarWidth);
    MoveWindow(
        scrollBar_, scrollBarX,
        contentTop, scrollBarWidth,
        std::max(1, contentBottom - contentTop), TRUE);
    if (visible_) {
        rebuildTaskControls();
    }
}

/**
 * Pobiera aktualne dane i nie zastępuje poprawnego widoku pustą listą po błędzie.
 */
// mix
void TodoView::refresh() {
    std::vector<TodoTask> loadedTasks;
    std::string error;
    if (!manager_.loadTasks(loadedTasks, error)) {
        status_ = error;
        ErrorLogger::error("Nie udało się odświeżyć To-do List: " + error);
        MessageBoxW(
            parent_, L"Nie udało się pobrać zadań z bazy danych.",
            L"Błąd To-do List", MB_ICONERROR | MB_OK);
        InvalidateRect(parent_, nullptr, TRUE);
        return;
    }
    tasks_ = std::move(loadedTasks);
    status_.clear();
    rebuildTaskControls();
    InvalidateRect(parent_, nullptr, TRUE);
}

/**
 * Buduje wiersze zadań, zachowując odpowiednie wcięcia i przewijanie.
 */
void TodoView::rebuildTaskControls() {
    destroyTaskControls();
    if (!visible_ || parent_ == nullptr) {
        return;
    }

    RECT clientArea{};
    GetClientRect(parent_, &clientArea);
    const int clientWidth = clientArea.right - clientArea.left;
    const int taskAreaWidth = std::max(
        1, clientWidth - GetSystemMetrics(SM_CXVSCROLL));
    const int clientHeight = clientArea.bottom - clientArea.top;
    const int horizontalPadding = WindowScale::size(parent_, 24);
    const int rowGap = WindowScale::size(parent_, 10);
    const int buttonHeight = WindowScale::size(parent_, 30);
    const int moveButtonWidth = WindowScale::size(parent_, 32);
    const int hideButtonWidth = WindowScale::size(parent_, 64);
    const int deleteWidth = WindowScale::size(parent_, 34);
    const int actionGap = WindowScale::size(parent_, 6);
    const int contentTop = WindowScale::size(parent_, 108);
    const int contentBottom = clientHeight - WindowScale::size(
        parent_, navigationButtonHeight + navigationBottomMargin * 2);
    const int viewportHeight = std::max(1, contentBottom - contentTop);
    int rowTop = contentTop;

    for (std::size_t taskIndex = 0; taskIndex < tasks_.size(); ++taskIndex) {
        const TodoTask& task = tasks_[taskIndex];
        std::wstring wideTitle;
        if (!convertUtf8ToWide(task.title, wideTitle)) {
            status_ = "Nie można wyświetlić nazwy zadania z API.";
            continue;
        }
        const int mainActionsWidth =
            moveButtonWidth * 2 + hideButtonWidth + deleteWidth +
            actionGap * 3;
        const int mainActionsX =
            taskAreaWidth - horizontalPadding - mainActionsWidth;
        const int mainCheckboxWidth = std::max(
            1, mainActionsX - horizontalPadding - rowGap);
        const int mainRowHeight =
            measureTextHeight(wideTitle, mainCheckboxWidth);
        const int mainCheckboxId = allocateControlId();
        if (mainCheckboxId != 0) {
            HWND mainCheckbox = CreateWindowExW(
                0, L"BUTTON", L"",
                    WS_CHILD | (visible_ ? WS_VISIBLE : 0) |
                        WS_DISABLED | BS_OWNERDRAW,
                horizontalPadding, rowTop - scrollPosition_,
                mainCheckboxWidth, mainRowHeight,
                parent_,
                reinterpret_cast<HMENU>(
                    static_cast<INT_PTR>(mainCheckboxId)),
                instance_, nullptr);
            if (mainCheckbox != nullptr) {
                taskControls_.push_back(mainCheckbox);
                controlPositions_.push_back(
                    {mainCheckbox, horizontalPadding, rowTop,
                     mainCheckboxWidth, mainRowHeight});
                mainCheckboxes_.emplace(
                    mainCheckboxId,
                    MainCheckboxInfo{wideTitle, task.isComplete()});
            } else {
                status_ = "Nie udało się utworzyć checkboxa zadania głównego.";
            }
        }

        int mainActionX = mainActionsX;
        HWND moveUpButton = createActionButton(
            L"↑", mainActionX, rowTop - scrollPosition_,
            moveButtonWidth, buttonHeight, ActionKind::moveMainUp, task.id);
        addPositionedControl(
            moveUpButton, mainActionX, rowTop,
            moveButtonWidth, buttonHeight);
        if (moveUpButton != nullptr) {
            EnableWindow(moveUpButton, taskIndex > 0);
        }
        mainActionX += moveButtonWidth + actionGap;

        HWND moveDownButton = createActionButton(
            L"↓", mainActionX, rowTop - scrollPosition_,
            moveButtonWidth, buttonHeight, ActionKind::moveMainDown, task.id);
        addPositionedControl(
            moveDownButton, mainActionX, rowTop,
            moveButtonWidth, buttonHeight);
        if (moveDownButton != nullptr) {
            EnableWindow(moveDownButton, taskIndex + 1 < tasks_.size());
        }
        mainActionX += moveButtonWidth + actionGap;

        HWND hideMainButton = createActionButton(
            L"Ukryj", mainActionX, rowTop - scrollPosition_,
            hideButtonWidth, buttonHeight, ActionKind::hideMainTask, task.id);
        addPositionedControl(
            hideMainButton, mainActionX, rowTop,
            hideButtonWidth, buttonHeight);
        mainActionX += hideButtonWidth + actionGap;

        HWND mainDeleteButton = createActionButton(
            L"X", mainActionX, rowTop - scrollPosition_,
            deleteWidth, buttonHeight, ActionKind::deleteMainTask,
            task.id, true);
        addPositionedControl(
            mainDeleteButton, mainActionX, rowTop,
            deleteWidth, buttonHeight);

        rowTop += mainRowHeight + WindowScale::size(parent_, 4);
        const int addSubtaskX =
            horizontalPadding + WindowScale::size(parent_, 24);
        const int addSubtaskWidth = std::min(
            WindowScale::size(parent_, 210),
            std::max(1, taskAreaWidth - addSubtaskX - horizontalPadding));
        HWND addSubtaskButton = createActionButton(
            L"+ Dodaj zadanie podrzędne",
            addSubtaskX, rowTop - scrollPosition_,
            addSubtaskWidth, buttonHeight,
            ActionKind::addSubtask, task.id);
        addPositionedControl(
            addSubtaskButton, addSubtaskX, rowTop,
            addSubtaskWidth, buttonHeight);
        rowTop += buttonHeight + WindowScale::size(parent_, 4);

        const std::size_t visibleSubtaskCount = static_cast<std::size_t>(
            std::count_if(
                task.subtasks.begin(), task.subtasks.end(),
                [](const TodoSubtask& subtask) { return !subtask.hidden; }));
        std::size_t visibleSubtaskIndex = 0;
        for (const TodoSubtask& subtask : task.subtasks) {
            if (subtask.hidden) {
                continue;
            }
            std::wstring wideSubtaskTitle;
            if (!convertUtf8ToWide(subtask.title, wideSubtaskTitle)) {
                status_ = "Nie można wyświetlić nazwy podzadania z API.";
                continue;
            }

            const int subtaskX =
                horizontalPadding + WindowScale::size(parent_, 34);
            const int desiredPromptWidth = WindowScale::size(parent_, 132);
            const int desiredDescriptionWidth = WindowScale::size(parent_, 132);
            const int actionRowWidth = std::max(
                1, taskAreaWidth - subtaskX - horizontalPadding);
            const int fixedActionWidth =
                moveButtonWidth * 2 + hideButtonWidth + deleteWidth +
                actionGap * 5;
            const int labelWidth = std::max(
                2, actionRowWidth - fixedActionWidth);
            const int promptWidth = std::min(
                desiredPromptWidth, std::max(1, labelWidth / 2));
            const int descriptionWidth = std::min(
                desiredDescriptionWidth,
                std::max(1, labelWidth - promptWidth));
            const int actionsWidth =
                promptWidth + descriptionWidth + fixedActionWidth;
            const int availableWidth =
                taskAreaWidth - horizontalPadding - subtaskX -
                horizontalPadding - actionsWidth - rowGap;
            const bool stackActions =
                availableWidth < WindowScale::size(parent_, 140);
            const int checkboxWidth = stackActions
                ? actionRowWidth
                : availableWidth;
            const int subtaskTextHeight =
                measureTextHeight(wideSubtaskTitle, checkboxWidth);
            const int checkboxId = allocateControlId();
            if (checkboxId != 0) {
                HWND checkbox = CreateWindowExW(
                    0, L"BUTTON", wideSubtaskTitle.c_str(),
                    WS_CHILD | (visible_ ? WS_VISIBLE : 0) |
                        WS_TABSTOP | BS_AUTOCHECKBOX |
                        BS_MULTILINE | BS_LEFT,
                    subtaskX, rowTop - scrollPosition_,
                    checkboxWidth, subtaskTextHeight,
                    parent_,
                    reinterpret_cast<HMENU>(
                        static_cast<INT_PTR>(checkboxId)),
                    instance_, nullptr);
                if (checkbox != nullptr) {
                    SendMessageW(
                        checkbox, BM_SETCHECK,
                        subtask.completed ? BST_CHECKED : BST_UNCHECKED, 0);
                    taskControls_.push_back(checkbox);
                    controlPositions_.push_back(
                        {checkbox, subtaskX, rowTop,
                         checkboxWidth, subtaskTextHeight});
                    controlActions_.emplace(
                        checkboxId,
                        ControlAction{ActionKind::toggleSubtask, subtask.id});
                } else {
                    status_ =
                        "Nie udało się utworzyć checkboxa zadania podrzędnego.";
                }
            }

            int actionsTop = rowTop;
            if (stackActions) {
                actionsTop += subtaskTextHeight + WindowScale::size(parent_, 4);
            } else {
                actionsTop += std::max(
                    0, (subtaskTextHeight - buttonHeight) / 2);
            }
            int promptX = stackActions
                ? subtaskX
                : taskAreaWidth - horizontalPadding - actionsWidth;
            const int promptButtonWidth = stackActions
                ? std::min(promptWidth, std::max(1, checkboxWidth))
                : promptWidth;
            HWND promptButton = createActionButton(
                L"Dodaj / edytuj prompt",
                promptX, actionsTop - scrollPosition_,
                promptButtonWidth, buttonHeight,
                ActionKind::editPrompt, subtask.id);
            addPositionedControl(
                promptButton, promptX, actionsTop,
                promptButtonWidth, buttonHeight);

            const int descriptionX = stackActions
                ? promptX + promptButtonWidth + actionGap
                : promptX + promptWidth + actionGap;
            const int descriptionButtonWidth = stackActions
                ? std::min(descriptionWidth, std::max(1, checkboxWidth -
                    promptButtonWidth - actionGap -
                    moveButtonWidth * 2 - hideButtonWidth - deleteWidth -
                    actionGap * 4))
                : descriptionWidth;
            HWND descriptionButton = createActionButton(
                L"Dodaj / edytuj opis",
                descriptionX, actionsTop - scrollPosition_,
                descriptionButtonWidth, buttonHeight,
                ActionKind::editDescription, subtask.id);
            addPositionedControl(
                descriptionButton, descriptionX, actionsTop,
                descriptionButtonWidth, buttonHeight);

            const int moveUpX =
                descriptionX + descriptionButtonWidth + actionGap;
            HWND moveSubtaskUpButton = createActionButton(
                L"↑", moveUpX, actionsTop - scrollPosition_,
                moveButtonWidth, buttonHeight,
                ActionKind::moveSubtaskUp, subtask.id);
            addPositionedControl(
                moveSubtaskUpButton, moveUpX, actionsTop,
                moveButtonWidth, buttonHeight);
            if (moveSubtaskUpButton != nullptr) {
                EnableWindow(
                    moveSubtaskUpButton, visibleSubtaskIndex > 0);
            }

            const int moveDownX = moveUpX + moveButtonWidth + actionGap;
            HWND moveSubtaskDownButton = createActionButton(
                L"↓", moveDownX, actionsTop - scrollPosition_,
                moveButtonWidth, buttonHeight,
                ActionKind::moveSubtaskDown, subtask.id);
            addPositionedControl(
                moveSubtaskDownButton, moveDownX, actionsTop,
                moveButtonWidth, buttonHeight);
            if (moveSubtaskDownButton != nullptr) {
                EnableWindow(
                    moveSubtaskDownButton,
                    visibleSubtaskIndex + 1 < visibleSubtaskCount);
            }

            const int hideSubtaskX =
                moveDownX + moveButtonWidth + actionGap;
            HWND hideSubtaskButton = createActionButton(
                L"Ukryj", hideSubtaskX, actionsTop - scrollPosition_,
                hideButtonWidth, buttonHeight,
                ActionKind::hideSubtask, subtask.id);
            addPositionedControl(
                hideSubtaskButton, hideSubtaskX, actionsTop,
                hideButtonWidth, buttonHeight);

            const int subtaskDeleteX = hideSubtaskX + hideButtonWidth +
                actionGap;
            HWND subtaskDeleteButton = createActionButton(
                L"X", subtaskDeleteX, actionsTop - scrollPosition_,
                deleteWidth, buttonHeight,
                ActionKind::deleteSubtask, subtask.id, true);
            addPositionedControl(
                subtaskDeleteButton, subtaskDeleteX, actionsTop,
                deleteWidth, buttonHeight);

            rowTop += (stackActions
                    ? subtaskTextHeight + buttonHeight +
                        WindowScale::size(parent_, 4)
                    : std::max(subtaskTextHeight, buttonHeight)) +
                rowGap;
            ++visibleSubtaskIndex;
        }
        rowTop += rowGap;
    }

    contentHeight_ = std::max(0, rowTop - contentTop);
    const int maximumPosition =
        std::max(0, contentHeight_ - viewportHeight);
    scrollPosition_ = std::clamp(scrollPosition_, 0, maximumPosition);
    SCROLLINFO scrollInfo{};
    scrollInfo.cbSize = sizeof(scrollInfo);
    scrollInfo.fMask = SIF_RANGE | SIF_PAGE | SIF_POS;
    scrollInfo.nMin = 0;
    scrollInfo.nMax = std::max(0, contentHeight_ - 1);
    scrollInfo.nPage = static_cast<UINT>(viewportHeight);
    scrollInfo.nPos = scrollPosition_;
    SetScrollInfo(scrollBar_, SB_CTL, &scrollInfo, TRUE);
    ShowWindow(
        scrollBar_,
        contentHeight_ > viewportHeight ? SW_SHOW : SW_HIDE);
    InvalidateRect(parent_, nullptr, TRUE);
}

/**
 * Przesuwa kontrolki listy, nie zmieniając ich danych ani uchwytów.
 */
void TodoView::applyScrollPosition() {
    for (const ControlPosition& position : controlPositions_) {
        MoveWindow(
            position.handle, position.x, position.y - scrollPosition_,
            position.width, position.height, TRUE);
    }
}

/**
 * Otwiera dialog nazwy i dodaje nowe zadanie główne do bazy.
 */
void TodoView::addMainTask() {
    std::wstring wideTitle;
    std::string dialogError;
    if (!showTextInputDialog(
            instance_, parent_, L"Nowe zadanie główne", L"", false,
            wideTitle, dialogError)) {
        if (!dialogError.empty()) {
            showError(dialogError);
        }
        return;
    }
    std::string title;
    if (!convertWideToUtf8(wideTitle, title)) {
        showError("Nie udało się przekonwertować nazwy zadania.");
        return;
    }
    std::string error;
    if (!manager_.addMainTask(title, error)) {
        showError(error);
        return;
    }
    scrollPosition_ = std::numeric_limits<int>::max();
    refresh();
}

/**
 * Wykonuje operację odpowiadającą klikniętej kontrolce wiersza.
 */
void TodoView::performAction(
    const ControlAction& action, int controlId) {
    std::string error;
    switch (action.kind) {
        case ActionKind::addSubtask:
            addSubtask(action.taskId);
            return;
        case ActionKind::toggleSubtask: {
            HWND checkbox = GetDlgItem(parent_, controlId);
            const bool completed =
                SendMessageW(checkbox, BM_GETCHECK, 0, 0) == BST_CHECKED;
            if (!manager_.setSubtaskCompleted(
                    action.taskId, completed, error)) {
                showError(error);
                refresh();
                return;
            }
            refresh();
            return;
        }
        case ActionKind::moveMainUp:
        case ActionKind::moveMainDown: {
            const bool moveUp = action.kind == ActionKind::moveMainUp;
            if (!manager_.moveMainTask(action.taskId, moveUp, error)) {
                showError(error);
                return;
            }
            refresh();
            return;
        }
        case ActionKind::moveSubtaskUp:
        case ActionKind::moveSubtaskDown: {
            const bool moveUp = action.kind == ActionKind::moveSubtaskUp;
            if (!manager_.moveSubtask(action.taskId, moveUp, error)) {
                showError(error);
                return;
            }
            refresh();
            return;
        }
        case ActionKind::hideMainTask:
            if (!manager_.hideMainTask(action.taskId, error)) {
                showError(error);
                return;
            }
            refresh();
            return;
        case ActionKind::hideSubtask:
            if (!manager_.hideSubtask(action.taskId, error)) {
                showError(error);
                return;
            }
            refresh();
            return;
        case ActionKind::editPrompt:
            editSubtaskText(action.taskId, true);
            return;
        case ActionKind::editDescription:
            editSubtaskText(action.taskId, false);
            return;
        case ActionKind::deleteMainTask:
        case ActionKind::deleteSubtask:
            confirmAndDelete(action);
            return;
    }
}

/**
 * Dodaje podzadanie do wybranego rodzica i natychmiast odświeża listę.
 */
void TodoView::addSubtask(std::uint64_t mainTaskId) {
    std::wstring wideTitle;
    std::string dialogError;
    if (!showTextInputDialog(
            instance_, parent_, L"Nowe zadanie podrzędne", L"", false,
            wideTitle, dialogError)) {
        if (!dialogError.empty()) {
            showError(dialogError);
        }
        return;
    }
    std::string title;
    if (!convertWideToUtf8(wideTitle, title)) {
        showError("Nie udało się przekonwertować nazwy podzadania.");
        return;
    }
    std::string error;
    if (!manager_.addSubtask(mainTaskId, title, error)) {
        showError(error);
        return;
    }
    refresh();
}

/**
 * Ładuje lub zapisuje prompt albo opis po kliknięciu jego przycisku.
 */
void TodoView::editSubtaskText(
    std::uint64_t subtaskId, bool isPrompt) {
    std::string currentText;
    std::string error;
    const bool loaded = isPrompt
        ? manager_.loadPrompt(subtaskId, currentText, error)
        : manager_.loadDescription(subtaskId, currentText, error);
    if (!loaded) {
        showError(error);
        return;
    }

    std::wstring wideCurrentText;
    if (!convertUtf8ToWide(currentText, wideCurrentText)) {
        showError("Zapisana treść nie jest poprawnym tekstem UTF-8.");
        return;
    }
    std::wstring wideEditedText;
    std::string dialogError;
    const wchar_t* dialogTitle = isPrompt
        ? L"Edytuj prompt zadania"
        : L"Edytuj opis zadania";
    if (!showTextInputDialog(
            instance_, parent_, dialogTitle, wideCurrentText, true,
            wideEditedText, dialogError)) {
        if (!dialogError.empty()) {
            showError(dialogError);
        }
        return;
    }

    std::string editedText;
    if (!convertWideToUtf8(wideEditedText, editedText)) {
        showError("Nie udało się przekonwertować zapisanej treści.");
        return;
    }
    const bool saved = isPrompt
        ? manager_.savePrompt(subtaskId, editedText, error)
        : manager_.saveDescription(subtaskId, editedText, error);
    if (!saved) {
        showError(error);
        return;
    }
    refresh();
}

/**
 * Pyta o potwierdzenie i usuwa wskazany rekord w transakcji API.
 */
void TodoView::confirmAndDelete(const ControlAction& action) {
    const bool isMainTask =
        action.kind == ActionKind::deleteMainTask;
    const wchar_t* confirmation = isMainTask
        ? L"Czy na pewno chcesz usunąć to zadanie główne?"
        : L"Czy na pewno chcesz usunąć to zadanie podrzędne?";
    if (MessageBoxW(
            parent_, confirmation, L"Potwierdź usunięcie",
            MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2) != IDYES) {
        return;
    }

    std::string error;
    const bool deleted = isMainTask
        ? manager_.removeMainTask(action.taskId, error)
        : manager_.removeSubtask(action.taskId, error);
    if (!deleted) {
        showError(error);
        return;
    }
    refresh();
}

/**
 * Tworzy kontrolkę przypisaną do konkretnego zadania i rodzaju akcji.
 */
HWND TodoView::createActionButton(
    const std::wstring& title,
    int x,
    int y,
    int width,
    int height,
    ActionKind kind,
    std::uint64_t taskId,
    bool ownerDraw) {
    const int controlId = allocateControlId();
    if (controlId == 0) {
        status_ = "Lista zawiera zbyt wiele kontrolek do obsługi Windows.";
        return nullptr;
    }
    const DWORD style = WS_CHILD | (visible_ ? WS_VISIBLE : 0) | WS_TABSTOP |
        (ownerDraw ? BS_OWNERDRAW : BS_PUSHBUTTON);
    HWND control = CreateWindowExW(
        0, L"BUTTON", title.c_str(), style,
        x, y, width, height, parent_,
        reinterpret_cast<HMENU>(static_cast<INT_PTR>(controlId)),
        instance_, nullptr);
    if (control == nullptr) {
        status_ = "Nie udało się utworzyć kontrolki zadania.";
        return nullptr;
    }
    SendMessageW(
        control, WM_SETFONT,
        reinterpret_cast<WPARAM>(GetStockObject(DEFAULT_GUI_FONT)), TRUE);
    controlActions_.emplace(
        controlId, ControlAction{kind, taskId});
    taskControls_.push_back(control);
    return control;
}

/**
 * Zapisuje pozycję logiczną kontrolki przewijanej.
 */
void TodoView::addPositionedControl(
    HWND control, int x, int y, int width, int height) {
    if (control == nullptr) {
        return;
    }
    controlPositions_.push_back({control, x, y, width, height});
}

/**
 * Zwalnia kontrolki wierszy i przygotowuje mapy do kolejnego odczytu.
 */
void TodoView::destroyTaskControls() {
    for (HWND control : taskControls_) {
        if (control != nullptr) {
            DestroyWindow(control);
        }
    }
    taskControls_.clear();
    controlPositions_.clear();
    controlActions_.clear();
    mainCheckboxes_.clear();
    nextControlId_ = 2000;
}

/**
 * Przydziela identyfikator rozpoznawany w 16-bitowym polu WM_COMMAND.
 */
int TodoView::allocateControlId() {
    if (nextControlId_ > 65535) {
        status_ = "Lista zawiera zbyt wiele kontrolek do obsługi Windows.";
        return 0;
    }
    return nextControlId_++;
}

/**
 * Oblicza wysokość wielowierszowego tekstu przy bieżącej szerokości wiersza.
 */
int TodoView::measureTextHeight(
    const std::wstring& text, int width) const {
    HDC deviceContext = GetDC(parent_);
    if (deviceContext == nullptr) {
        return WindowScale::size(parent_, 42);
    }
    RECT bounds{0, 0, std::max(1, width), 0};
    DrawTextW(
        deviceContext, text.c_str(), static_cast<int>(text.size()),
        &bounds, DT_CALCRECT | DT_WORDBREAK | DT_NOPREFIX);
    ReleaseDC(parent_, deviceContext);
    return std::max(
        WindowScale::size(parent_, 38),
        static_cast<int>(bounds.bottom) + WindowScale::size(parent_, 12));
}

/**
 * Zapisuje błąd operacji i pokazuje użytkownikowi czytelny komunikat.
 */
void TodoView::showError(const std::string& error) {
    status_ = error;
    ErrorLogger::error("Operacja To-do List nie powiodła się: " + error);
    std::wstring wideError;
    const wchar_t* message = L"Operacja To-do List nie powiodła się.";
    if (convertUtf8ToWide(error, wideError)) {
        message = wideError.c_str();
    }
    MessageBoxW(
        parent_, message, L"Błąd To-do List", MB_ICONERROR | MB_OK);
    InvalidateRect(parent_, nullptr, TRUE);
}
