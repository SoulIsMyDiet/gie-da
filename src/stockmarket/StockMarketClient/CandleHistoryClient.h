#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "CandlesDataClient.h"

/**
 * Przechowuje świecę wyświetlaną w raporcie.
 */
struct CandleReportEntry {
    std::string symbol;
    std::string datetime;
    std::string open;
    std::string high;
    std::string low;
    std::string close;
    std::string volume;
};

/**
 * Przechowuje stan jednej brakującej świecy i czasy jej obsługi.
 */
struct CandleGapReportEntry {
    std::string symbol;
    std::string candleDatetimeUtc;
    std::string status;
    std::string detectedAtUtc;
    std::string filledAtUtc;
};

/**
 * Przechowuje wszystkie kolumny jednego wpisu tabeli sygnałów.
 */
struct SignalReportEntry {
    std::string id;
    std::string datetime;
    std::string product;
    std::string price;
    std::string direction;
    std::string algorithmName;
    std::string aiDecision;
    std::string transactionId;
    std::string candlesCount;
    std::string signalParameters;
};

/**
 * Przechowuje cztery końcowe wyniki przypisane do sygnału.
 */
struct SignalPotentialDetails {
    std::string saldo20;
    std::string maxCandle;
    std::string deal100Pln;
    std::string spread;
};

/**
 * Przechowuje świecę pobraną z bazy do analizy algorytmem Grok-45.
 */
struct SignalCandleData {
    std::int64_t timestamp = 0;
    double open = 0.0;
    double high = 0.0;
    double low = 0.0;
    double close = 0.0;
    double volume = 0.0;
};

class CandleHistoryClient {
public:
    /**
     * Zapisuje świecę w lokalnym API historii rynkowej.
     * @param symbol Symbol instrumentu, którego dotyczy świeca.
     * @param candle Dane OHLCV i czas otwarcia świecy w UTC.
     * @param newlyDetectedGapCount Otrzymuje liczbę nowo wykrytych luk.
     * @param signalSaved Otrzymuje informację, czy sygnał został zapisany.
     * @param error Otrzymuje opis błędu, jeśli zapis się nie powiedzie.
     * @return true, jeśli API potwierdzi zapis; w przeciwnym razie false.
     */
    static bool saveCandle(
        const std::string& symbol,
        const CandleData& candle,
        std::size_t& newlyDetectedGapCount,
        bool& signalSaved,
        std::string& error);

    /**
     * Pobiera ostatnie świece EUR/USD do analizy wskaźników.
     * @param maximumCandles Maksymalna liczba świec do pobrania (1-100).
     * @param candles Otrzymuje świece uporządkowane chronologicznie.
     * @param error Otrzymuje opis błędu, jeśli odczyt się nie powiedzie.
     * @return true, jeśli API zwróciło poprawne dane świec.
     */
    static bool fetchLatestAnalysisCandles(
        std::size_t maximumCandles,
        std::vector<SignalCandleData>& candles,
        std::string& error);

    /**
     * Zapisuje wygenerowany sygnał Grok-45; API pomija duplikaty tej samej świecy.
     * @param signalJson Pełny dokument JSON zwrócony przez algorytm.
     * @param signalSaved Otrzymuje informację, czy utworzono nowy wpis.
     * @param error Otrzymuje opis błędu, jeśli zapis się nie powiedzie.
     * @return true, jeśli API poprawnie obsłużyło żądanie.
     */
    static bool saveGeneratedSignal(
        const std::string& signalJson,
        bool& signalSaved,
        std::string& error);

    /**
     * Pobiera z bazy do 1000 brakujących minut dla wskazanego symbolu.
     * @param symbol Symbol instrumentu, którego dotyczy historia.
     * @param hasExistingCandles Otrzymuje informację, czy baza zawiera już świece.
     * @param missingTimes Otrzymuje brakujące znaczniki czasu UTC.
     * @param totalMissingCount Otrzymuje łączną liczbę wykrytych luk.
     * @param error Otrzymuje opis błędu, jeśli odczyt się nie powiedzie.
     * @return true, jeśli lokalne API zwróciło poprawny plan uzupełnienia.
     */
    static bool fetchMissingCandleTimes(
        const std::string& symbol,
        bool& hasExistingCandles,
        std::vector<std::string>& missingTimes,
        std::size_t& totalMissingCount,
        std::string& error);

    /**
     * Zapisuje paczkę świec w jednym żądaniu do lokalnego API.
     * @param symbol Symbol instrumentu, którego dotyczą świece.
     * @param candles Świece zapisane w formacie czasu UTC.
     * @param savedCandleCount Otrzymuje liczbę świec przyjętych przez API.
     * @param latestSavedDatetime Otrzymuje czas UTC najnowszej zapisanej świecy.
     * @param error Otrzymuje opis błędu, jeśli zapis się nie powiedzie.
     * @return true, jeśli API potwierdzi zapis paczki.
     */
    static bool saveCandles(
        const std::string& symbol,
        const std::vector<CandleData>& candles,
        std::size_t& savedCandleCount,
        std::string& latestSavedDatetime,
        std::string& error);

    /**
     * Pobiera stronę zawierającą do 20 świec, od najnowszych.
     * @param page Numer strony liczony od zera.
     * @param candles Otrzymuje dane świec.
     * @param hasNextPage Otrzymuje informację, czy istnieje kolejna strona.
     * @param error Otrzymuje opis błędu, jeśli pobranie się nie powiedzie.
     * @return true, jeśli API zwróciło poprawną stronę.
     */
    static bool fetchCandlePage(
        std::size_t page,
        std::vector<CandleReportEntry>& candles,
        bool& hasNextPage,
        std::string& error);

    /**
     * Pobiera stronę 20 wpisów z historii brakujących świec.
     * @param page Numer strony liczony od zera.
     * @param gaps Otrzymuje wpisy luk świecowych.
     * @param hasNextPage Otrzymuje informację, czy istnieje kolejna strona.
     * @param error Otrzymuje opis błędu, jeśli pobranie się nie powiedzie.
     * @return true, jeśli lokalne API zwróciło poprawną stronę.
     */
    static bool fetchCandleGapPage(
        std::size_t page,
        std::vector<CandleGapReportEntry>& gaps,
        bool& hasNextPage,
        std::string& error);

    /**
     * Pobiera stronę 15 najnowszych wpisów tabeli signals.
     * @param page Numer strony liczony od zera.
     * @param signals Otrzymuje wpisy sygnałów z bazy.
     * @param hasNextPage Otrzymuje informację, czy istnieje kolejna strona.
     * @param error Otrzymuje opis błędu, jeśli pobranie się nie powiedzie.
     * @return true, jeśli lokalne API zwróciło poprawną stronę.
     */
    static bool fetchSignalPage(
        std::size_t page,
        std::vector<SignalReportEntry>& signals,
        bool& hasNextPage,
        std::string& error);

    /**
     * Pobiera saldo, numer najlepszej świecy, wynik dla 100 zł i spread sygnału.
     * @param signalId Liczbowy identyfikator sygnału zapisany jako tekst.
     * @param details Otrzymuje cztery wartości z tabeli signal_potention.
     * @param error Otrzymuje opis błędu, jeśli żądanie się nie powiedzie.
     * @return true, jeśli API zwróciło poprawne szczegóły sygnału.
     */
    static bool fetchSignalPotential(
        const std::string& signalId,
        SignalPotentialDetails& details,
        std::string& error);
};
