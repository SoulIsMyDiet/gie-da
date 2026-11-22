#pragma once

#include "TodoManager.hpp"
#include "ViewRegistry.hpp"

#include <condition_variable>
#include <mutex>
#include <string>
#include <thread>

/**
 * Sprawdza cyklicznie API i bezpiecznie ponawia próbę po restarcie aplikacji.
 */
class TodoScheduler {
public:
    /**
     * Powiązuje harmonogram z managerem zadań i rejestrem nawigacji.
     */
    TodoScheduler(TodoManager& manager, const ViewRegistry& viewRegistry);

    /**
     * Zatrzymuje wątek harmonogramu przed zwolnieniem jego zależności.
     */
    ~TodoScheduler();

    /**
     * Uruchamia natychmiastową próbę, a potem sprawdza API co minutę.
     * @param error Otrzymuje błąd utworzenia wątku.
     * @return true, jeśli harmonogram już działa lub został uruchomiony.
     */
    bool start(std::string& error);

    /**
     * Budzi i dołącza wątek, aby zamknięcie aplikacji nie czekało minuty.
     */
    void stop();

private:
    /**
     * Wykonuje żądanie początkowe i cykliczne do lokalnego API.
     */
    void run();

    TodoManager& manager_;
    const ViewRegistry& viewRegistry_;
    std::mutex mutex_;
    std::condition_variable wakeCondition_;
    bool stopRequested_ = false;
    std::thread worker_;
};
