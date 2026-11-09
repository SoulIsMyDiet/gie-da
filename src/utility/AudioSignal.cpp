#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mmsystem.h>

#include "AudioSignal.h"

#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>

namespace {

// Audio format and signal timing used to construct the in-memory WAV data.
constexpr DWORD sampleRate = 44100;
constexpr DWORD signalDurationMs = 350;
constexpr DWORD sampleCount = sampleRate * signalDurationMs / 1000;
constexpr DWORD signalGapMs = 150;
constexpr DWORD gapSampleCount = sampleRate * signalGapMs / 1000;

#pragma pack(push, 1)
struct WaveHeader {
    char riffId[4];
    DWORD riffSize;
    char waveId[4];
    char formatId[4];
    DWORD formatSize;
    WORD audioFormat;
    WORD channelCount;
    DWORD samplesPerSecond;
    DWORD bytesPerSecond;
    WORD bytesPerSample;
    WORD bitsPerSample;
    char dataId[4];
    DWORD dataSize;
};
#pragma pack(pop)

static_assert(sizeof(WaveHeader) == 44);

/**
 * Buduje i odtwarza wskazaną liczbę krótkich piknięć.
 */
bool playBeeps(DWORD beepCount) {
    constexpr double pi = 3.14159265358979323846;
    constexpr double frequency = 440.0;
    const DWORD sequenceSampleCount =
        sampleCount * beepCount + gapSampleCount * (beepCount - 1);
    std::vector<std::int16_t> samples(sequenceSampleCount, 0);
    for (DWORD beep = 0; beep < beepCount; ++beep) {
        const DWORD start = beep * (sampleCount + gapSampleCount);
        for (DWORD index = 0; index < sampleCount; ++index) {
            const double phase = 2.0 * pi * frequency * index / sampleRate;
            samples[start + index] =
                static_cast<std::int16_t>(12000.0 * std::sin(phase));
        }
    }

    WaveHeader header{
        {'R', 'I', 'F', 'F'},
        static_cast<DWORD>(
            sizeof(WaveHeader) + samples.size() * sizeof(std::int16_t) - 8),
        {'W', 'A', 'V', 'E'},
        {'f', 'm', 't', ' '},
        16,
        1,
        1,
        sampleRate,
        sampleRate * sizeof(std::int16_t),
        sizeof(std::int16_t),
        16,
        {'d', 'a', 't', 'a'},
        static_cast<DWORD>(samples.size() * sizeof(std::int16_t))
    };

    std::vector<std::uint8_t> wave(
        sizeof(header) + samples.size() * sizeof(std::int16_t));
    std::memcpy(wave.data(), &header, sizeof(header));
    std::memcpy(
        wave.data() + sizeof(header), samples.data(),
        samples.size() * sizeof(std::int16_t));
    return PlaySoundA(
        reinterpret_cast<LPCSTR>(wave.data()), nullptr, SND_MEMORY | SND_SYNC);
}

} // namespace

/**
 * Odtwarza trzy piknięcia używane jako powiadomienie o brakujących świecach.
 */
bool AudioSignal::play() {
    return playBeeps(3);
}

/**
 * Odtwarza dwa piknięcia po potwierdzonym zapisie sygnału do bazy.
 */
bool AudioSignal::playSignalSaved() {
    return playBeeps(2);
}
