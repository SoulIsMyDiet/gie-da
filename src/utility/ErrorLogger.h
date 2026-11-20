#pragma once

#include <string_view>

/**
 * Zapisuje ostrzeżenia i błędy aplikacji do miesięcznych plików w folderze logs.
 */
class ErrorLogger final {
public:
    /**
     * Tworzy folder logs i plik dla bieżącego miesiąca, jeśli jeszcze nie istnieją.
     */
    static void initialize() noexcept;

    /**
     * Zapisuje ostrzeżenie w jednej linii.
     * @param message Opis ostrzeżenia w UTF-8.
     */
    static void warning(std::string_view message) noexcept;

    /**
     * Zapisuje ostrzeżenie w jednej linii.
     * @param message Opis ostrzeżenia w UTF-16.
     */
    static void warning(std::wstring_view message) noexcept;

    /**
     * Zapisuje błąd w jednej linii.
     * @param message Opis błędu w UTF-8.
     */
    static void error(std::string_view message) noexcept;

    /**
     * Zapisuje błąd w jednej linii.
     * @param message Opis błędu w UTF-16.
     */
    static void error(std::wstring_view message) noexcept;

private:
    /**
     * Dodaje znacznik poziomu, datę, godzinę i opis do bieżącego pliku.
     * @param severity Poziom ważności wpisu.
     * @param message Opis wpisu w UTF-8.
     */
    static void write(
        std::string_view severity, std::string_view message) noexcept;
};
