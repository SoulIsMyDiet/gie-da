#pragma once

#include <cstdint>
#include <string>
#include <vector>

/**
 * Przechowuje zadanie podrzędne wraz z jego stanem i metadanymi.
 */
struct TodoSubtask {
    std::uint64_t id = 0;
    std::string title;
    bool completed = false;
    bool hidden = false;
    bool promptExists = false;
    bool descriptionExists = false;
    std::string createdAt;
    std::string updatedAt;
};

/**
 * Przechowuje zadanie główne i jego podzadania; status jest zawsze wyliczany.
 */
struct TodoTask {
    std::uint64_t id = 0;
    std::string title;
    std::string createdAt;
    std::string updatedAt;
    std::vector<TodoSubtask> subtasks;

    /**
     * Zwraca true, gdy istnieje co najmniej jedno podzadanie i każde jest ukończone.
     * @return true, jeśli wszystkie podzadania są ukończone.
     */
    bool isComplete() const;
};
