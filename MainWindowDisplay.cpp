#include "MainWindowDisplay.h"

/**
 * Renders the current local time and Forex status as centered red text.
 */
void MainWindowDisplay::draw(
    HDC deviceContext, RECT& clientArea, const std::string& forexStatus) {
    SetTextColor(deviceContext, RGB(255, 0, 0));

    SYSTEMTIME localTime{};
    GetLocalTime(&localTime);
    char displayText[256]{};
    wsprintfA(displayText, "hello world\r\n%02u:%02u\r\n%s",
              localTime.wHour, localTime.wMinute, forexStatus.c_str());
    DrawTextA(deviceContext, displayText, -1, &clientArea,
              DT_CENTER | DT_VCENTER | DT_WORDBREAK);
}
