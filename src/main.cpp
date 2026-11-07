#include "MainWindow.h"

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

/**
 * Creates the application window and runs its Windows message loop.
 */
int WINAPI WinMain(HINSTANCE instance, HINSTANCE, LPSTR, int showCommand) {
    MainWindow window;
    if (!window.create(instance, showCommand)) {
        return 1;
    }

    return window.run();
}
