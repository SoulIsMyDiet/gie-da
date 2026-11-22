#include "TodoTask.hpp"

#include <algorithm>

// Backend
/**
 * Wylicza status zadania głównego bez przechowywania go osobno.
 */
bool TodoTask::isComplete() const {
    return !subtasks.empty() &&
        std::all_of(
            subtasks.begin(), subtasks.end(),
            [](const TodoSubtask& subtask) { return subtask.completed; });
}
