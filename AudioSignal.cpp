#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mmsystem.h>

#include "AudioSignal.h"

#include <cmath>
#include <cstdint>

namespace {

constexpr DWORD sampleRate = 44100;
constexpr DWORD signalDurationMs = 350;
constexpr DWORD sampleCount = sampleRate * signalDurationMs / 1000;
constexpr DWORD signalGapMs = 150;
constexpr DWORD gapSampleCount = sampleRate * signalGapMs / 1000;
constexpr DWORD sequenceSampleCount = sampleCount * 3 + gapSampleCount * 2;

#pragma pack(push, 1)
struct WaveData {
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
    std::int16_t samples[sequenceSampleCount];
};
#pragma pack(pop)

static_assert(sizeof(WaveData) == 44 + sequenceSampleCount * sizeof(std::int16_t));

} // namespace

bool AudioSignal::playForMinute(unsigned int minute) {
    constexpr double pi = 3.14159265358979323846;
    const double frequency = 440.0 + (minute / 5) * 50.0;
    WaveData wave{
        {'R', 'I', 'F', 'F'},
        static_cast<DWORD>(sizeof(WaveData) - 8),
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
        sequenceSampleCount * sizeof(std::int16_t),
        {}
    };

    for (DWORD signal = 0; signal < 3; ++signal) {
        const DWORD start = signal * (sampleCount + gapSampleCount);
        for (DWORD i = 0; i < sampleCount; ++i) {
            const double phase = 2.0 * pi * frequency * i / sampleRate;
            wave.samples[start + i] = static_cast<std::int16_t>(12000.0 * std::sin(phase));
        }
    }

    return PlaySoundA(reinterpret_cast<LPCSTR>(&wave), nullptr, SND_MEMORY | SND_SYNC);
}
