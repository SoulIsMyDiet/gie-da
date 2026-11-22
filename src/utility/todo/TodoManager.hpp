#pragma once

#include "TodoRepository.hpp"

#include <chrono>
#include <cstdint>
#include <string>
#include <vector>

/**
 * Sprawdza dane wejściowe i przekazuje operacje zadań do repozytorium API.
 */
class TodoManager {
public:
    /**
     * Pobiera zadania z repozytorium.
     */
    bool loadTasks(std::vector<TodoTask>& tasks, std::string& error) const;

    /**
     * Dodaje zadanie główne o niepustej nazwie.
     */
    bool addMainTask(const std::string& title, std::string& error) const;

    /**
     * Dodaje podzadanie do wskazanego zadania głównego.
     */
    bool addSubtask(
        std::uint64_t mainTaskId,
        const std::string& title,
        std::string& error) const;

    /**
     * Zmienia stan ukończenia wskazanego podzadania.
     */
    bool setSubtaskCompleted(
        std::uint64_t subtaskId, bool completed, std::string& error) const;

    /**
     * Pobiera prompt przypisany do podzadania.
     */
    bool loadPrompt(
        std::uint64_t subtaskId,
        std::string& prompt,
        std::string& error) const;

    /**
     * Zapisuje prompt przypisany do podzadania.
     */
    bool savePrompt(
        std::uint64_t subtaskId,
        const std::string& prompt,
        std::string& error) const;

    /**
     * Pobiera opis techniczny przypisany do podzadania.
     */
    bool loadDescription(
        std::uint64_t subtaskId,
        std::string& description,
        std::string& error) const;

    /**
     * Zapisuje opis techniczny przypisany do podzadania.
     */
    bool saveDescription(
        std::uint64_t subtaskId,
        const std::string& description,
        std::string& error) const;

    /**
     * Trwale usuwa podzadanie.
     */
    bool removeSubtask(std::uint64_t subtaskId, std::string& error) const;

    /**
     * Trwale usuwa zadanie główne i wszystkie podzadania.
     */
    bool removeMainTask(std::uint64_t mainTaskId, std::string& error) const;

    /**
     * Zleca API utworzenie cotygodniowego zadania dla rejestru widoków.
     */
    bool ensureWeeklyReview(
        const std::vector<std::string>& viewNames,
        std::chrono::seconds& nextCheckDelay,
        std::string& error) const;

private:
    /**
     * Odrzuca identyfikatory zerowe i teksty zawierające tylko spacje.
     */
    static bool validateTaskId(
        std::uint64_t id,
        const char* taskType,
        std::string& error);

    /**
     * Odrzuca nazwę złożoną wyłącznie z białych znaków.
     */
    static bool validateTitle(const std::string& title, std::string& error);

    TodoRepository repository_;
};
