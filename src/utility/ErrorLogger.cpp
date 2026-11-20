#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "ErrorLogger.h"

#include <array>
#include <chrono>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <system_error>

namespace {

std::mutex logMutex;

/**
 * Zwraca katalog pliku wykonywalnego albo bieżący katalog, gdy nie jest dostępny.
 */
std::filesystem::path executableDirectory() {
    std::array<wchar_t, 32768> executablePath{};
    const DWORD pathLength = GetModuleFileNameW(
        nullptr, executablePath.data(),
        static_cast<DWORD>(executablePath.size()));
    if (pathLength > 0 && pathLength < executablePath.size()) {
        return std::filesystem::path(
            std::wstring(executablePath.data(), pathLength)).parent_path();
    }

    std::error_code error;
    const std::filesystem::path workingDirectory =
        std::filesystem::current_path(error);
    return error ? std::filesystem::path{} : workingDirectory;
}

/**
 * Szuka katalogu głównego projektu, aby logi trafiały do wspólnego folderu.
 */
std::filesystem::path logDirectory() {
    std::error_code error;
    std::array<std::filesystem::path, 2> startingDirectories{
        executableDirectory(),
        std::filesystem::current_path(error)
    };

    for (const std::filesystem::path& startingDirectory : startingDirectories) {
        if (startingDirectory.empty()) {
            continue;
        }

        std::filesystem::path candidate = startingDirectory;
        while (!candidate.empty()) {
            error.clear();
            if (std::filesystem::exists(candidate / ".git", error)) {
                return candidate / "logs";
            }
            error.clear();
            if (std::filesystem::is_directory(candidate / "logs", error)) {
                return candidate / "logs";
            }

            const std::filesystem::path parent = candidate.parent_path();
            if (parent == candidate) {
                break;
            }
            candidate = parent;
        }
    }

    const std::filesystem::path fallback = executableDirectory();
    return fallback.empty() ? std::filesystem::path("logs")
                            : fallback / "logs";
}

/**
 * Formatuje bieżącą datę i godzinę oraz nazwę pliku dla lokalnej strefy czasu.
 */
std::filesystem::path currentLogFile(std::string& timestamp) {
    const std::time_t currentTime = std::chrono::system_clock::to_time_t(
        std::chrono::system_clock::now());
    std::tm localTime{};
    if (localtime_s(&localTime, &currentTime) != 0) {
        throw std::runtime_error("Nie można odczytać lokalnego czasu systemu.");
    }

    std::ostringstream formattedTimestamp;
    formattedTimestamp << std::put_time(&localTime, "%Y-%m-%d %H:%M:%S");
    timestamp = formattedTimestamp.str();

    std::ostringstream fileName;
    fileName << "LogError-" << std::setw(2) << std::setfill('0')
             << localTime.tm_mon + 1 << "." << localTime.tm_year + 1900;
    return logDirectory() / fileName.str();
}

/**
 * Zastępuje znaki końca wiersza spacjami, aby każdy wpis zajmował jedną linię.
 */
std::string makeSingleLine(std::string_view message) {
    std::string singleLine(message);
    for (char& character : singleLine) {
        if (character == '\r' || character == '\n' || character == '\t') {
            character = ' ';
        }
    }
    return singleLine;
}

/**
 * Konwertuje tekst UTF-16 na UTF-8 bez zmiany jego treści.
 */
std::string convertToUtf8(std::wstring_view message) {
    if (message.empty()) {
        return {};
    }

    const int sourceLength = static_cast<int>(message.size());
    const int requiredSize = WideCharToMultiByte(
        CP_UTF8, WC_ERR_INVALID_CHARS, message.data(), sourceLength,
        nullptr, 0, nullptr, nullptr);
    if (requiredSize <= 0) {
        return "Nie można przekonwertować komunikatu UTF-16 na UTF-8.";
    }

    std::string converted(static_cast<std::size_t>(requiredSize), '\0');
    const int convertedSize = WideCharToMultiByte(
        CP_UTF8, WC_ERR_INVALID_CHARS, message.data(), sourceLength,
        converted.data(), requiredSize, nullptr, nullptr);
    if (convertedSize != requiredSize) {
        return "Nie można przekonwertować komunikatu UTF-16 na UTF-8.";
    }
    return converted;
}

/**
 * Zapisuje komunikat awaryjnie do debuggera, gdy nie można zapisać go do pliku.
 */
void writeDebugFallback(std::string_view message) noexcept {
    try {
        const std::string fallback = std::string(message) + "\r\n";
        OutputDebugStringA(fallback.c_str());
    } catch (...) {
        OutputDebugStringA(
            "[Error] Nie udało się przygotować awaryjnego komunikatu logu.\r\n");
    }
}

} // namespace

void ErrorLogger::initialize() noexcept {
    try {
        std::string timestamp;
        const std::filesystem::path path = currentLogFile(timestamp);
        std::filesystem::create_directories(path.parent_path());
        std::ofstream file(path, std::ios::binary | std::ios::app);
        if (!file) {
            writeDebugFallback(
                "[Error] Nie można utworzyć pliku dziennika aplikacji.");
        }
    } catch (...) {
        writeDebugFallback(
            "[Error] Nie można przygotować pliku dziennika aplikacji.");
    }
}

void ErrorLogger::warning(std::string_view message) noexcept {
    write("Warning", message);
}

void ErrorLogger::warning(std::wstring_view message) noexcept {
    const std::string converted = convertToUtf8(message);
    write("Warning", converted);
}

void ErrorLogger::error(std::string_view message) noexcept {
    write("Error", message);
}

void ErrorLogger::error(std::wstring_view message) noexcept {
    const std::string converted = convertToUtf8(message);
    write("Error", converted);
}

void ErrorLogger::write(
    std::string_view severity, std::string_view message) noexcept {
    try {
        std::string timestamp;
        const std::filesystem::path path = currentLogFile(timestamp);
        const std::string singleLine = makeSingleLine(message);
        const std::string line = "[" + std::string(severity) + "] " +
            timestamp + " | C++ | " + singleLine + "\r\n";

        const std::lock_guard<std::mutex> lock(logMutex);
        std::filesystem::create_directories(path.parent_path());
        std::ofstream file(path, std::ios::binary | std::ios::app);
        if (!file) {
            writeDebugFallback(line);
            return;
        }
        file.write(line.data(), static_cast<std::streamsize>(line.size()));
        file.flush();
        if (!file) {
            writeDebugFallback(line);
        }
    } catch (...) {
        writeDebugFallback(
            "[Error] Nie udało się dopisać wpisu do pliku dziennika.");
    }
}
