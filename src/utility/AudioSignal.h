#pragma once

class AudioSignal {
public:
    /**
     * Odtwarza dwa piknięcia po zapisie sygnału, o ile dźwięki nie są wyciszone.
     * @return true, jeśli dźwięk odtworzono lub celowo wyciszono; false po błędzie odtwarzania.
     */
    static bool playSignalSaved();
};
