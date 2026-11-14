# DAX40StrategyV1 (Chat-gpt6)

Strategia analizuje zamknięte świece M1 zapisane w bazie pod symbolem `GER30`.
Wyniki publikuje jako instrument `DAX40` i algorytm
`Chat-gpt6/DAX40/DAX40StrategyV1`. Działa równolegle ze strategią Claude DAX40;
nie zmienia jej ani strategii innych instrumentów.

## Integracja i czas

C++ wysyła świece przez istniejący endpoint `POST /api/candles`. API sprawdza,
czy świeca jest zamknięta, pobiera do 500 ostatnich świec istniejącym storage
DAX40 i wywołuje tę strategię oraz strategię Claude. Harmonogram nie jest
kopiowany do tego folderu: strategia otrzymuje funkcję `is_market_open` z
istniejącego `market_products.py`. Czasy z bazy są przeliczane przez istniejący
adapter na świadome timestampy UTC, a wynik zwraca lokalne ISO-8601 dla
Europe/Warsaw z właściwym offsetem CET/CEST. Świeca otwarta w trakcie sesji
jest oceniana po zamknięciu również wtedy, gdy jej zamknięcie przypada dokładnie
na koniec sesji.
Świece uzupełnione przez backfill służą do odbudowania historii wskaźników;
strategia nie zapisuje dla nich sygnałów historycznych. Sygnały live zaczyna
generować od kolejnej nowej zamkniętej świecy.

## Wskaźniki i reguły

- EMA9/21/50: rekurencja `alpha = 2 / (n + 1)`, inicjalizacja pierwszą ceną.
- RSI14, ATR14 oraz +DI14/-DI14/ADX14: wygładzanie Wildera po inicjalnej
  średniej z pełnego okresu.
- Poziomy wybicia są wyznaczane z 10 poprzednich rzeczywistych świec,
  bez świecy aktualnie ocenianej.
- Wymagane jest minimum 100 świec w bieżącym segmencie. Jedna brakująca minuta
  nie resetuje wskaźników; dłuższa luka rozpoczyna nowy segment i okres
  rozgrzewania. Przerwy sesji rozpoznaje istniejący harmonogram API. Dla
  dłuższych luk `gap_length_scan_truncated` informuje, że raportowany rozmiar
  jest dolną granicą, a nie pełnym zliczeniem.
- LONG/SHORT wymagają zgodnego trendu EMA, ADX, wybicia z buforem ATR,
  odpowiedniego korpusu/pozycji zamknięcia oraz zakresu RSI.
- Aby nie powtarzać sygnału na trwającym wybiciu, wymagane jest również
  przekroczenie poziomu, którego poprzednia świeca jeszcze nie przekroczyła.
  Progi decyzji i wartości poprzedniej świecy są zapisywane w JSON.
- Wolumen jest przekazywany do diagnostyki, ale nie wpływa na decyzję.

## Zapis i dźwięk

Sygnały trafiają do istniejącej tabeli `signals`; wszystkie wskaźniki i
diagnostyka są w kolumnie JSON `signal_parameters`. Idempotencję zapewnia
unikalny klucz `(product, algorithm_name, source_timestamp)`. Parametr
`SAVE_NONE_SIGNALS` znajduje się wyłącznie w `config.py`; ustawienie go na
`False` wyłącza zapis NONE. Po każdym nowym zapisie API zwraca liczbę nowych
rekordów, a aplikacja C++ odtwarza dwa krótkie piknięcia dla każdego z nich.
Duplikat nie powoduje nowego sygnału dźwiękowego.

## Test historii

`DAX40StrategyV1.backtest(candles)` lub `run_historical_backtest(...)`
przetwarza świece chronologicznie, używając tylko prefiksu dostępnego do
ocenianej świecy. Zwraca liczbę LONG/SHORT/NONE oraz liczniki przyczyn NONE.
Tryb historyczny nie zapisuje do bazy i nie emituje dźwięków. Dane wejściowe
powinny być listą obiektów z `timestamp_utc`, `open`, `high`, `low`, `close`
i opcjonalnym `volume`.
