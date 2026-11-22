#include "TodoTask.hpp"
#include "ViewRegistry.hpp"

#include <string>

/**
 * Sprawdza wyliczanie statusu rodzica i wspólny rejestr widoków.
 */
int main() {
    TodoTask task;
    task.id = 1;
    task.title = "Przegląd";
    if (task.isComplete()) {
        return 1;
    }

    task.subtasks = {
        {1, "Raporty", true, false, false, false, "", ""},
        {2, "Sygnały", true, true, true, false, "", ""},
    };
    if (!task.isComplete()) {
        return 2;
    }
    task.subtasks[1].completed = false;
    if (task.isComplete()) {
        return 3;
    }
    task.subtasks.clear();
    if (task.isComplete()) {
        return 4;
    }

    ViewRegistry registry;
    std::string error;
    if (!registry.registerView(1001, "Raporty", error) ||
        !registry.registerView(1002, "To-do List", error)) {
        return 5;
    }
    if (registry.viewNames() !=
        std::vector<std::string>{"Raporty", "To-do List"}) {
        return 6;
    }
    if (registry.findByCommandId(1002) == nullptr ||
        registry.findByCommandId(1002)->name != "To-do List") {
        return 7;
    }
    if (registry.registerView(1002, "Inny widok", error) ||
        error.empty()) {
        return 8;
    }
    return 0;
}
