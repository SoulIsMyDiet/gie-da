#pragma once

class AudioSignal {
public:
    /**
     * Generates and plays a short three-tone audio signal.
     * @return true if Windows successfully played the signal; otherwise false.
     */
    static bool play();
};
