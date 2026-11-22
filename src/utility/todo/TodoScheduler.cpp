#include "TodoScheduler.hpp"

#include "ErrorLogger.h"

#include <chrono>
#include <exception>
#include <system_error>

// Backend
/**
 * Łączy harmonogram z usługą zadań i centralnym rejestrem widoków.
 */
TodoScheduler::TodoScheduler(
    TodoManager& manager, const ViewRegistry& viewRegistry)
    : manager_(manager), viewRegistry_(viewRegistry) {}

/**
 * Zatrzymuje wątek, jeśli obiekt jest niszczony przed zamknięciem okna.
 */
TodoScheduler::~TodoScheduler() {
    stop();
}

/**
 * Uruchamia pojedynczy wątek sprawdzający harmonogram.
 */
bool TodoScheduler::start(std::string& error) {
    error.clear();
    std::lock_guard<std::mutex> lock(mutex_);
    if (worker_.joinable()) {
        return true;
    }

    stopRequested_ = false;
    try {
        worker_ = std::thread(&TodoScheduler::run, this);
    } catch (const std::system_error& exception) {
        error = "Nie udało się uruchomić harmonogramu To-do: ";
        error += exception.what();
        return false;
    }
    return true;
}

/**
 * Sygnalizuje zakończenie pracy i czeka na zwolnienie wątku.
 */
void TodoScheduler::stop() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        stopRequested_ = true;
    }
    wakeCondition_.notify_all();
    if (worker_.joinable()) {
        worker_.join();
    }
}

/**
 * Ponawia wywołanie API, gdy aplikacja lub serwer były czasowo niedostępne.
 */
void TodoScheduler::run() {
    while (true) {
        std::chrono::seconds nextCheckDelay = std::chrono::minutes(1);
        try {
            std::string error;
            if (!manager_.ensureWeeklyReview(
                    viewRegistry_.viewNames(), nextCheckDelay, error)) {
                ErrorLogger::error(
                    "Nie udało się sprawdzić tygodniowego zadania To-do: " +
                    error);
            }
        } catch (const std::exception& exception) {
            ErrorLogger::error(
                "Nieobsłużony wyjątek harmonogramu To-do: " +
                std::string(exception.what()));
        } catch (...) {
            ErrorLogger::error(
                "Nieznany wyjątek harmonogramu To-do.");
        }

        std::unique_lock<std::mutex> lock(mutex_);
        if (wakeCondition_.wait_for(
                lock, nextCheckDelay,
                [this] { return stopRequested_; })) {
            return;
        }
    }
}
