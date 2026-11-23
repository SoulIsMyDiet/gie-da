#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "CandlesDataClient.h"

/**
 * Przechowuje zamkniętą świecę i kwotowania IBKR wyłącznie do podglądu.
 */
struct IBKRCandleData {
    std::string symbol;
    std::int64_t timestampUtc = 0;
    double open = 0.0;
    double high = 0.0;
    double low = 0.0;
    double close = 0.0;
    std::string volume;
    bool bidAvailable = false;
    double bid = 0.0;
    bool askAvailable = false;
    double ask = 0.0;
};

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
    std::string direction;
    std::string algorithmName;
    std::string aiDecision;
    std::string transactionId;
    std::string candlesCount;
    std::string signalParameters;
    std::string maxProfitAfter20Minutes;
};

/**
 * Przechowuje wyniki 20- i 60-minutowe przypisane do sygnału.
 */
struct SignalPotentialDetails {
    std::string saldo20;
    std::string bestCandle20Minute;
    std::string deal100Pln20;
    std::string averageSpread;
    std::string bestCandle60Datetime;
    std::string saldo60;
    std::string deal100Pln60;
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

/**
 * Przechowuje zweryfikowaną stronę identyfikatorów powiadomień Groka.
 */
struct GrokNotificationPage {
    std::vector<std::uint64_t> ids;
    std::uint64_t nextCursor = 0;
    bool hasMore = false;
};

class CandleHistoryClient {
public:
    /**
     * Pobiera bieżący kursor startowy, pomijający dotychczasowe sygnały Groka.
     * @param cursor Otrzymuje największy istniejący identyfikator.
     * @param error Otrzymuje opis błędu żądania lub odpowiedzi.
     * @return true, jeśli kursor został poprawnie odczytany.
     */
    static bool fetchGrokNotificationCursor(
        std::uint64_t& cursor, std::string& error);

    /**
     * Pobiera i w całości sprawdza stronę nowych powiadomień Groka.
     * @param afterId Ostatni obsłużony identyfikator.
     * @param page Otrzymuje poprawną stronę bez zmiany przy błędzie.
     * @param error Otrzymuje opis błędu żądania lub odpowiedzi.
     * @return true, jeśli odpowiedź jest poprawna i spójna z kursorem.
     */
    static bool fetchGrokNotifications(
        std::uint64_t afterId, GrokNotificationPage& page, std::string& error);

    /**
     * Pobiera identyfikatory oczekujących powiadomień strategii USD/MXN.
     * @param notificationIds Otrzymuje identyfikatory w kolejności odtworzenia.
     * @param error Otrzymuje opis błędu żądania lub odpowiedzi.
     * @return true, jeśli odpowiedź API zawiera poprawną listę identyfikatorów.
     */
    static bool fetchUsdmxnNotificationIds(
        std::vector<std::uint64_t>& notificationIds, std::string& error);

    /**
     * Potwierdza w API odtworzenie powiadomienia USD/MXN.
     * @param notificationId Identyfikator odtworzonego powiadomienia.
     * @param error Otrzymuje opis błędu żądania lub odpowiedzi.
     * @return true, jeśli powiadomienie zostało potwierdzone.
     */
    static bool acknowledgeUsdmxnNotification(
        std::uint64_t notificationId, std::string& error);

    /**
     * Pobiera identyfikatory oczekujących powiadomień strategii USD/JPY.
     * @param notificationIds Otrzymuje identyfikatory w kolejności odtworzenia.
     * @param error Otrzymuje opis błędu żądania lub odpowiedzi.
     * @return true, jeśli odpowiedź API zawiera poprawną listę identyfikatorów.
     */
    static bool fetchUsdjpyNotificationIds(
        std::vector<std::uint64_t>& notificationIds, std::string& error);

    /**
     * Oznacza powiadomienie USD/JPY jako obsłużone w API.
     * @param notificationId Identyfikator obsługiwanego powiadomienia.
     * @param error Otrzymuje opis błędu, jeśli potwierdzenie się nie powiedzie.
     * @return true, jeśli powiadomienie zostało potwierdzone.
     */
    static bool acknowledgeUsdjpyNotification(
        std::uint64_t notificationId, std::string& error);

    /**
     * Zapisuje świecę w lokalnym API historii rynkowej.
     * @param symbol Symbol instrumentu, którego dotyczy świeca.
     * @param candle Dane OHLCV i czas otwarcia świecy w UTC.
     * @param newlyDetectedGapCount Otrzymuje liczbę nowo wykrytych luk.
     * @param savedSignalCount Otrzymuje liczbę nowych rekordów sygnałów
     *        przekazywanych bezpośrednio w odpowiedzi API.
     * @param error Otrzymuje opis błędu, jeśli zapis się nie powiedzie.
     * @param candleSavedDespiteError Opcjonalnie otrzymuje true, gdy świeca
     *        została zapisana, ale dalsza analiza zakończyła się błędem.
     * @return true po pełnym zapisie; false również wtedy, gdy zapis świecy
     *         potwierdzono, ale dalsza analiza zwróciła poprawnie oznaczone 503.
     */
    static bool saveCandle(
        const std::string& symbol,
        const CandleData& candle,
        std::size_t& newlyDetectedGapCount,
        std::size_t& savedSignalCount,
        std::string& error,
        bool* candleSavedDespiteError = nullptr);

    /**
     * Zapisuje świecę IBKR do odrębnej tabeli podglądowej.
     * @param candle Świeca EUR/USD z czasem UTC oraz dostępnymi kwotowaniami.
     * @param error Otrzymuje opis błędu walidacji lub żądania do lokalnego API.
     * @return true, jeśli API potwierdziło zapis lub aktualizację świecy.
     * @throws std::bad_alloc, jeśli nie można przygotować żądania.
     */
    static bool saveIbkrCandle(
        const IBKRCandleData& candle, std::string& error);

    /**
     * Zapisuje paczkę świec IBKR bez uruchamiania algorytmów.
     * @param candles Świece jednego instrumentu, uporządkowane chronologicznie.
     * @param savedCandleCount Otrzymuje liczbę świec zaakceptowanych przez API.
     * @param error Otrzymuje opis błędu walidacji lub żądania.
     * @return true, jeśli API poprawnie obsłużyło całą paczkę.
     * @throws std::bad_alloc, jeśli nie można przygotować żądania.
     */
    static bool saveIbkrCandles(
        const std::vector<IBKRCandleData>& candles,
        std::size_t& savedCandleCount,
        std::string& error);

    /**
     * Pobiera listę instrumentów aktywnych w konfiguracji lokalnego API.
     * @param symbols Otrzymuje unikalne symbole w kolejności konfiguracji.
     * @param error Otrzymuje opis błędu, jeśli odpowiedź jest nieprawidłowa.
     * @return true, jeśli API zwróciło co najmniej jeden prawidłowy symbol.
     */
    static bool fetchMonitoredSymbols(
        std::vector<std::string>& symbols, std::string& error);

    /**
     * Pobiera symbole, których sesje są aktualnie otwarte.
     * @param symbols Otrzymuje unikalne symbole; lista może być pusta.
     * @param error Otrzymuje opis błędu, jeśli odpowiedź jest nieprawidłowa.
     * @return true, jeśli API zwróciło prawidłową listę.
     */
    static bool fetchOpenSymbols(
        std::vector<std::string>& symbols, std::string& error);

    /**
     * Pobiera wszystkie sentencje z lokalnej bazy do losowania po stronie C++.
     * @param sentences Otrzymuje sentencje w kolejności ich zapisania.
     * @param error Otrzymuje opis błędu żądania lub niepoprawnej odpowiedzi.
     * @return true, jeśli API zwróciło co najmniej jedną prawidłową sentencję.
     */
    static bool fetchDailySentences(
        std::vector<std::string>& sentences, std::string& error);

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
     * Pobiera historię M1 w TSV do budowania kontekstu prompta.
     * @param symbol Symbol instrumentu.
     * @param limit Maksymalna liczba zamkniętych świec (1-1000).
     * @param response Otrzymuje wiersze z czasem UTC, OHLC i flagą luki.
     * @param error Otrzymuje opis błędu żądania lub odpowiedzi.
     * @return true, jeśli lokalne API zwróciło poprawną odpowiedź.
     */
    static bool fetchPromptCandleHistory(
        const std::string& symbol,
        std::size_t limit,
        std::string& response,
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
     * Pobiera stronę do 10 świec z podanego zakresu dat.
     * @param startDate Początkowa data w formacie YYYY-MM-DD.
     * @param endDate Końcowa data w formacie YYYY-MM-DD, włącznie.
     * @param page Numer strony liczony od zera.
     * @param candles Otrzymuje świece z wybranego zakresu dat.
     * @param hasNextPage Otrzymuje informację, czy istnieje kolejna strona.
     * @param error Otrzymuje opis błędu pobierania lub odpowiedzi API.
     * @return true, jeśli API zwróciło poprawną stronę świec.
     */
    static bool fetchFilteredCandlePage(
        const std::string& startDate,
        const std::string& endDate,
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
     * Pobiera 15 najnowszych sygnałów wraz z decyzją AI i wynikiem po 20 minutach.
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
     * Pobiera wyniki dla okien 20 i 60 świec oraz spread sygnału.
     * @param signalId Liczbowy identyfikator sygnału zapisany jako tekst.
     * @param details Otrzymuje wyniki dla obu okien z tabeli signal_potention.
     * @param error Otrzymuje opis błędu, jeśli żądanie się nie powiedzie.
     * @return true, jeśli API zwróciło poprawne szczegóły sygnału.
     */
    static bool fetchSignalPotential(
        const std::string& signalId,
        SignalPotentialDetails& details,
        std::string& error);
};
