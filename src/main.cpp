#include "MainWindow.h"
#include "utility/ErrorLogger.h"

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <exception>
#include <string>

/**
 * Przygotowuje logowanie, tworzy okno i uruchamia pętlę komunikatów Windows.
 */
int WINAPI WinMain(HINSTANCE instance, HINSTANCE, LPSTR, int showCommand) {
    ErrorLogger::initialize();
    try {
        MainWindow window;
        if (!window.create(instance, showCommand)) {
            return 1;
        }

        return window.run();
    } catch (const std::exception& error) {
        ErrorLogger::error(
            "Nieobsłużony wyjątek podczas uruchamiania: " +
            std::string(error.what()));
        return 1;
    } catch (...) {
        ErrorLogger::error(
            "Nieznany wyjątek podczas uruchamiania aplikacji.");
        return 1;
    }
}
