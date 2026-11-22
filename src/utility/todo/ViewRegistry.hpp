#pragma once

#include <string>
#include <vector>

/**
 * Opisuje samodzielny widok dostępny w nawigacji aplikacji.
 */
struct ViewDescriptor {
    int commandId = 0;
    std::string name;
};

/**
 * Przechowuje wspólną listę widoków dla nawigacji i cotygodniowego zadania.
 */
class ViewRegistry {
public:
    /**
     * Dodaje widok, odrzucając pustą nazwę i powtórzone identyfikatory.
     * @param commandId Identyfikator komunikatu przycisku nawigacji.
     * @param name Nazwa widoku w kodowaniu UTF-8.
     * @param error Otrzymuje wyjaśnienie błędu, jeśli rejestracja się nie uda.
     * @return true, jeśli widok został dodany.
     */
    bool registerView(int commandId, std::string name, std::string& error);

    /**
     * Zwraca zarejestrowane widoki w kolejności dodania.
     */
    const std::vector<ViewDescriptor>& views() const;

    /**
     * Wyszukuje widok przypisany do identyfikatora przycisku.
     * @param commandId Identyfikator komunikatu przycisku.
     * @return Wskaźnik do widoku albo nullptr, gdy go nie zarejestrowano.
     */
    const ViewDescriptor* findByCommandId(int commandId) const;

    /**
     * Zwraca nazwy widoków do umieszczenia w zadaniu tygodniowym.
     */
    std::vector<std::string> viewNames() const;

private:
    std::vector<ViewDescriptor> views_;
};
