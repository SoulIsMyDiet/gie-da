#include "MainWindowDisplay.h"

void MainWindowDisplay::draw(HDC deviceContext, RECT& clientArea) {
    SetTextColor(deviceContext, RGB(255, 0, 0));

    SYSTEMTIME localTime{};
    GetLocalTime(&localTime);
    char displayText[32]{};
    wsprintfA(displayText, "hello world\r\n%02u:%02u",
              localTime.wHour, localTime.wMinute);
    DrawTextA(deviceContext, displayText, -1, &clientArea, DT_CENTER | DT_VCENTER);
}
