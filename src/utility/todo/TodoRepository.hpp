#pragma once

#include "TodoTask.hpp"

#include <chrono>
#include <cstdint>
#include <string>
#include <vector>

/**
 * Komunikuje się z istniejącym lokalnym API zadań, bez bezpośredniego SQL.
 */
class TodoRepository {
public:
    /**
     * Pobiera zadania główne i podrzędne z lokalnego API.
     * @param tasks Otrzymuje listę zadań po poprawnym odczycie.
     * @param error Otrzymuje opis błędu transportu lub niepoprawnej odpowiedzi.
     * @return true, jeśli API zwróciło poprawne dane.
     */
    bool fetchTasks(std::vector<TodoTask>& tasks, std::string& error) const;

    /**
     * Zapisuje nowe zadanie główne.
     */
    bool createMainTask(const std::string& title, std::string& error) const;

    /**
     * Zapisuje podzadanie powiązane z zadaniem głównym.
     */
    bool createSubtask(
        std::uint64_t mainTaskId,
        const std::string& title,
        std::string& error) const;

    /**
     * Ustawia stan ukończenia podzadania.
     */
    bool setSubtaskCompleted(
        std::uint64_t subtaskId, bool completed, std::string& error) const;

    /**
     * Trwale zapamiętuje ukrycie zadania bez usuwania go z bazy.
     */
    bool hideMainTask(std::uint64_t mainTaskId, std::string& error) const;

    /**
     * Trwale zapamiętuje ukrycie podzadania bez usuwania go z bazy.
     */
    bool hideSubtask(std::uint64_t subtaskId, std::string& error) const;

    /**
     * Przesuwa zadanie główne w górę lub w dół listy.
     */
    bool moveMainTask(
        std::uint64_t mainTaskId, bool moveUp, std::string& error) const;

    /**
     * Przesuwa podzadanie w obrębie jego zadania głównego.
     */
    bool moveSubtask(
        std::uint64_t subtaskId, bool moveUp, std::string& error) const;

    /**
     * Pobiera prompt podzadania na żądanie użytkownika.
     */
    bool fetchPrompt(
        std::uint64_t subtaskId,
        std::string& prompt,
        std::string& error) const;

    /**
     * Zapisuje prompt bez limitu narzuconego przez aplikację.
     */
    bool savePrompt(
        std::uint64_t subtaskId,
        const std::string& prompt,
        std::string& error) const;

    /**
     * Pobiera opis techniczny podzadania na żądanie użytkownika.
     */
    bool fetchDescription(
        std::uint64_t subtaskId,
        std::string& description,
        std::string& error) const;

    /**
     * Zapisuje opis bez limitu narzuconego przez aplikację.
     */
    bool saveDescription(
        std::uint64_t subtaskId,
        const std::string& description,
        std::string& error) const;

    /**
     * Trwale usuwa podzadanie.
     */
    bool deleteSubtask(std::uint64_t subtaskId, std::string& error) const;

    /**
     * Trwale usuwa zadanie główne i jego podzadania.
     */
    bool deleteMainTask(std::uint64_t mainTaskId, std::string& error) const;

    /**
     * Prosi API o idempotentne utworzenie zadania dla bieżącego tygodnia.
     */
    bool createWeeklyReview(
        const std::vector<std::string>& viewNames,
        std::chrono::seconds& nextCheckDelay,
        std::string& error) const;
};
