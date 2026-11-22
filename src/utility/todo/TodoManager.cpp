#include "TodoManager.hpp"

#include <algorithm>
#include <cctype>

// Backend
/**
 * Sprawdza, czy identyfikator zadania jest dodatni.
 */
bool TodoManager::validateTaskId(
    std::uint64_t id, const char* taskType, std::string& error) {
    error.clear();
    if (id == 0) {
        error = "Identyfikator zadania " + std::string(taskType) +
            " musi być dodatni.";
        return false;
    }
    return true;
}

/**
 * Sprawdza, czy nazwa zawiera co najmniej jeden widoczny znak.
 */
bool TodoManager::validateTitle(
    const std::string& title, std::string& error) {
    error.clear();
    const bool titleHasVisibleCharacter = std::any_of(
        title.begin(), title.end(), [](unsigned char character) {
            return std::isspace(character) == 0;
        });
    if (!titleHasVisibleCharacter) {
        error = "Nazwa zadania nie może być pusta.";
        return false;
    }
    return true;
}

/**
 * Pobiera zadania z repozytorium lokalnego API.
 */
bool TodoManager::loadTasks(
    std::vector<TodoTask>& tasks, std::string& error) const {
    return repository_.fetchTasks(tasks, error);
}

/**
 * Dodaje zadanie główne po sprawdzeniu jego nazwy.
 */
bool TodoManager::addMainTask(
    const std::string& title, std::string& error) const {
    return validateTitle(title, error) &&
        repository_.createMainTask(title, error);
}

/**
 * Dodaje podzadanie po sprawdzeniu identyfikatora rodzica i nazwy.
 */
bool TodoManager::addSubtask(
    std::uint64_t mainTaskId,
    const std::string& title,
    std::string& error) const {
    return validateTaskId(mainTaskId, "głównego", error) &&
        validateTitle(title, error) &&
        repository_.createSubtask(mainTaskId, title, error);
}

/**
 * Zapisuje nowy stan podzadania po sprawdzeniu identyfikatora.
 */
bool TodoManager::setSubtaskCompleted(
    std::uint64_t subtaskId, bool completed, std::string& error) const {
    if (!validateTaskId(subtaskId, "podrzędnego", error)) {
        return false;
    }
    return repository_.setSubtaskCompleted(subtaskId, completed, error);
}

/**
 * Pobiera prompt po sprawdzeniu identyfikatora podzadania.
 */
bool TodoManager::loadPrompt(
    std::uint64_t subtaskId,
    std::string& prompt,
    std::string& error) const {
    if (!validateTaskId(subtaskId, "podrzędnego", error)) {
        return false;
    }
    return repository_.fetchPrompt(subtaskId, prompt, error);
}

/**
 * Zapisuje prompt bez ograniczania jego długości.
 */
bool TodoManager::savePrompt(
    std::uint64_t subtaskId,
    const std::string& prompt,
    std::string& error) const {
    if (!validateTaskId(subtaskId, "podrzędnego", error)) {
        return false;
    }
    return repository_.savePrompt(subtaskId, prompt, error);
}

/**
 * Pobiera opis po sprawdzeniu identyfikatora podzadania.
 */
bool TodoManager::loadDescription(
    std::uint64_t subtaskId,
    std::string& description,
    std::string& error) const {
    if (!validateTaskId(subtaskId, "podrzędnego", error)) {
        return false;
    }
    return repository_.fetchDescription(subtaskId, description, error);
}

/**
 * Zapisuje opis techniczny bez ograniczania jego długości.
 */
bool TodoManager::saveDescription(
    std::uint64_t subtaskId,
    const std::string& description,
    std::string& error) const {
    if (!validateTaskId(subtaskId, "podrzędnego", error)) {
        return false;
    }
    return repository_.saveDescription(subtaskId, description, error);
}

/**
 * Trwale usuwa wskazane podzadanie.
 */
bool TodoManager::removeSubtask(
    std::uint64_t subtaskId, std::string& error) const {
    if (!validateTaskId(subtaskId, "podrzędnego", error)) {
        return false;
    }
    return repository_.deleteSubtask(subtaskId, error);
}

/**
 * Trwale usuwa wskazane zadanie główne.
 */
bool TodoManager::removeMainTask(
    std::uint64_t mainTaskId, std::string& error) const {
    if (!validateTaskId(mainTaskId, "głównego", error)) {
        return false;
    }
    return repository_.deleteMainTask(mainTaskId, error);
}

/**
 * Przekazuje bieżący rejestr widoków do endpointu idempotentnego.
 */
bool TodoManager::ensureWeeklyReview(
    const std::vector<std::string>& viewNames,
    std::chrono::seconds& nextCheckDelay,
    std::string& error) const {
    error.clear();
    if (viewNames.empty()) {
        error = "Nie można utworzyć przeglądu bez widoków aplikacji.";
        return false;
    }
    return repository_.createWeeklyReview(
        viewNames, nextCheckDelay, error);
}
