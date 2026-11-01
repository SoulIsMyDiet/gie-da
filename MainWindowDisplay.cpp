#include "MainWindowDisplay.h"

#include <cstdio>

/**
 * Renders the current local time and timestamped Forex prices as centered red text.
 */
void MainWindowDisplay::draw(
    HDC deviceContext, RECT& clientArea, const std::string& forexStatus) {
    SetTextColor(deviceContext, RGB(255, 0, 0));

    SYSTEMTIME localTime{};
    GetLocalTime(&localTime);
    char clockText[8]{};
    std::snprintf(clockText, sizeof(clockText), "%02u:%02u",
                  static_cast<unsigned>(localTime.wHour),
                  static_cast<unsigned>(localTime.wMinute));
    const std::string displayText =
        "hello world\r\n" + std::string(clockText) + "\r\n" + forexStatus;
    DrawTextA(deviceContext, displayText.c_str(), -1, &clientArea,
              DT_CENTER | DT_VCENTER | DT_WORDBREAK);
}
