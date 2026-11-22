#include "ViewRegistry.hpp"

#include <algorithm>
#include <utility>

// Backend
/**
 * Rejestruje widok po sprawdzeniu identyfikatora i nazwy.
 */
bool ViewRegistry::registerView(
    int commandId, std::string name, std::string& error) {
    error.clear();
    if (commandId < 1 || commandId > 65535) {
        error = "Identyfikator widoku musi mieścić się w zakresie 1-65535.";
        return false;
    }
    if (name.empty()) {
        error = "Nazwa widoku nie może być pusta.";
        return false;
    }
    if (findByCommandId(commandId) != nullptr ||
        std::any_of(
            views_.begin(), views_.end(),
            [&name](const ViewDescriptor& view) {
                return view.name == name;
            })) {
        error = "Identyfikator i nazwa widoku muszą być unikalne.";
        return false;
    }

    views_.push_back({commandId, std::move(name)});
    return true;
}

/**
 * Zwraca listę widoków używaną przy tworzeniu nawigacji.
 */
const std::vector<ViewDescriptor>& ViewRegistry::views() const {
    return views_;
}

/**
 * Wyszukuje widok po identyfikatorze przycisku.
 */
const ViewDescriptor* ViewRegistry::findByCommandId(int commandId) const {
    const auto found = std::find_if(
        views_.begin(), views_.end(),
        [commandId](const ViewDescriptor& view) {
            return view.commandId == commandId;
        });
    return found == views_.end() ? nullptr : &*found;
}

/**
 * Kopiuje nazwy zarejestrowanych widoków do zadania przeglądu.
 */
std::vector<std::string> ViewRegistry::viewNames() const {
    std::vector<std::string> names;
    names.reserve(views_.size());
    for (const ViewDescriptor& view : views_) {
        names.push_back(view.name);
    }
    return names;
}
