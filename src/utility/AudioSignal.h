#pragma once

class AudioSignal {
public:
    /**
     * Odtwarza dwa piknięcia potwierdzające zapis sygnału inwestycyjnego.
     * @return true, jeśli system Windows odtworzył dźwięk; w przeciwnym razie false.
     */
    static bool playSignalSaved();
};
