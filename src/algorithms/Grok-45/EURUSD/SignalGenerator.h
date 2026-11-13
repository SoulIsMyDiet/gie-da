#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace grok45 {

/**
 * Przechowuje dane OHLCV jednej zamkniętej świecy 1-minutowej.
 */
struct Candle {
    std::int64_t timestamp = 0;
    double open = 0.0;
    double high = 0.0;
    double low = 0.0;
    double close = 0.0;
    double volume = 0.0;
};

/**
 * Przechowuje górną, środkową i dolną wstęgę Bollingera.
 */
struct BollingerBands {
    double upper = 0.0;
    double middle = 0.0;
    double lower = 0.0;
};

/**
 * Oblicza wskaźniki techniczne niezależnie od logiki sygnałów.
 */
class IndicatorCalculator {
public:
    /**
     * Oblicza wartości EMA dla każdego indeksu; przed okresem rozgrzewki wynik jest pusty.
     * @param prices Ceny zamknięcia w kolejności chronologicznej.
     * @param period Liczba cen używana do inicjalizacji EMA.
     * @return Wektor EMA o takiej samej długości jak wejściowe ceny.
     * @throws Nie rzuca własnych wyjątków; alokacja pamięci może rzucić std::bad_alloc.
     */
    static std::vector<std::optional<double>> calculateEmaSeries(
        const std::vector<double>& prices, std::size_t period);

    /**
     * Oblicza ostatnią wartość EMA.
     * @param prices Ceny zamknięcia w kolejności chronologicznej.
     * @param period Liczba cen używana do inicjalizacji EMA.
     * @return Ostatnia EMA lub pusty wynik, gdy danych jest za mało albo są nieprawidłowe.
     * @throws Nie rzuca własnych wyjątków; alokacja pamięci może rzucić std::bad_alloc.
     */
    static std::optional<double> calculateEma(
        const std::vector<double>& prices, std::size_t period);

    /**
     * Oblicza wartości RSI metodą Wildera dla całego szeregu.
     * @param prices Ceny zamknięcia w kolejności chronologicznej.
     * @param period Liczba zmian cen używana do obliczenia RSI.
     * @return Wektor RSI o takiej samej długości jak wejściowe ceny.
     * @throws Nie rzuca własnych wyjątków; alokacja pamięci może rzucić std::bad_alloc.
     */
    static std::vector<std::optional<double>> calculateRsiSeries(
        const std::vector<double>& prices, std::size_t period);

    /**
     * Oblicza ostatnią wartość RSI metodą Wildera.
     * @param prices Ceny zamknięcia w kolejności chronologicznej.
     * @param period Liczba zmian cen używana do obliczenia RSI.
     * @return Ostatni RSI lub pusty wynik, gdy danych jest za mało albo są nieprawidłowe.
     * @throws Nie rzuca własnych wyjątków; alokacja pamięci może rzucić std::bad_alloc.
     */
    static std::optional<double> calculateRsi(
        const std::vector<double>& prices, std::size_t period);

    /**
     * Oblicza wstęgi Bollingera z odchyleniem standardowym populacji.
     * @param prices Ceny zamknięcia w kolejności chronologicznej.
     * @param period Liczba ostatnich cen w oknie.
     * @param standardDeviationMultiplier Mnożnik odchylenia standardowego.
     * @return Ostatnie wstęgi lub pusty wynik dla nieprawidłowych danych.
     * @throws Nie rzuca własnych wyjątków.
     */
    static std::optional<BollingerBands> calculateBollingerBands(
        const std::vector<double>& prices,
        std::size_t period,
        double standardDeviationMultiplier);

    /**
     * Oblicza ATR metodą Wildera dla świec w kolejności chronologicznej.
     * @param candles Świece OHLC w kolejności chronologicznej.
     * @param period Liczba zakresów używana do obliczenia ATR.
     * @return Ostatni ATR lub pusty wynik, gdy danych jest za mało albo są nieprawidłowe.
     * @throws Nie rzuca własnych wyjątków; alokacja pamięci może rzucić std::bad_alloc.
     */
    static std::optional<double> calculateAtr(
        const std::vector<Candle>& candles, std::size_t period);
};

/**
 * Tworzy krótkoterminowy sygnał EURUSD/USD i zwraca go jako dokument JSON.
 */
class SignalGenerator {
public:
    static constexpr std::size_t minimumCandles = 50;
    static constexpr std::size_t emaShortPeriod = 9;
    static constexpr std::size_t emaLongPeriod = 21;
    static constexpr std::size_t rsiPeriod = 14;
    static constexpr std::size_t bollingerPeriod = 20;
    static constexpr std::size_t atrPeriod = 14;
    static constexpr double minimumAtr = 0.00015;
    static constexpr std::size_t volatilityRangeCandles = 5;
    static constexpr std::size_t analysisWindowCandles = 100;

    /**
     * Analizuje zamknięte świece EURUSD/USD i zwraca wynik w formacie JSON.
     * @param candles Świece 1-minutowe, unikalne i ułożone od najstarszej do najnowszej.
     *        Do wskaźników używanych jest maksymalnie ostatnie 100 świec.
     * @return JSON z instrumentem, ceną, czasem, sygnałem i obliczonymi wskaźnikami.
     * @throws Nie rzuca własnych wyjątków; alokacja pamięci może rzucić std::bad_alloc.
     *
     * Wywołujący odpowiada za przekazanie danych dopiero po zamknięciu ostatniej świecy.
     * Analiza nie korzysta z wolumenu, spreadu, ceny bid ani ask.
     */
    std::string generate(const std::vector<Candle>& candles) const;
};

} // namespace grok45
