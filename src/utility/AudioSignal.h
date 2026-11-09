#pragma once

class AudioSignal {
public:
    /**
     * Odtwarza trzy piknięcia informujące o wykryciu luki w świecach.
     * @return true, jeśli system Windows odtworzył dźwięk; w przeciwnym razie false.
     */
    static bool play();

    /**
     * Odtwarza dwa piknięcia potwierdzające zapis sygnału inwestycyjnego.
     * @return true, jeśli system Windows odtworzył dźwięk; w przeciwnym razie false.
     */
    static bool playSignalSaved();
};
