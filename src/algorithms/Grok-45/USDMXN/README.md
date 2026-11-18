# Grok-45 USDMXNStrategyV1

Niezależna strategia micro-tradingowa dla USD/MXN. Działa obok istniejącej
strategii `Chat-gpt6/USDMXN/USDMXNStrategyV1`; każda ma osobną nazwę algorytmu,
trwały marker ewaluacji oraz rekord w tabeli `signals`.

## Wskaźniki i reguły

- EMA(5) i EMA(13), z inicjalizacją pierwszą ceną Close.
- RSI(7) i ATR(14) metodą Wildera.
- Bollinger Bands(20, 2), z populacyjnym odchyleniem standardowym.
- Momentum(5) = `(Close - Close sprzed 5 dostępnych świec) / Close sprzed 5 świec * 100`.
- BB width = `(upper - lower) / middle * 100`.
- Do decyzji wymagane jest 100 dostępnych świec M1. Brakujących świec nie
  interpolujemy ani nie wymagamy idealnej ciągłości danych.
- ATR musi być dodatni oraz co najmniej równy 20. percentylowi z dostępnych
  wartości ATR(14) z ostatnich maksymalnie 100 świec. Percentyl stosuje
  interpolację liniową.

LONG wymaga EMA(5) > EMA(13), Close > EMA(5), RSI > 52 i rosnącego, Momentum(5)
> 0,08%, filtra Bollingera `Close > middle LUB (Close > lower i RSI < 65)` oraz
przejścia filtra ATR. SHORT stosuje odpowiednie warunki lustrzane: RSI < 48 i
malejący, Momentum(5) < -0,08%, `Close < middle LUB (Close < upper i RSI > 35)`.

Przy braku warunków wynik to `NONE`. Pełny wynik strategii zawiera sygnał, cenę,
czas otwarcia jako Unix timestamp, wszystkie wartości wskaźników, progi,
diagnostykę każdego warunku i dostępne dane świecy. Adapter zapisuje do
`signals.signal_parameters` wyłącznie `price`, `signal`, `timestamp`,
`instrument` i `reason`. Zakładany maksymalny czas utrzymania pozycji to
20 minut. Wynik funkcji również udostępnia
`timestamp` jako epokę UTC czasu otwarcia; pole `candle_close_time` jest
pomocniczym czasem zamknięcia dla istniejącego zapisu API.

## Integracja i zapisy

Strategia korzysta z istniejącego API, repozytorium, tabeli `candles`,
`signals` i powiadomień outbox USD/MXN. Nie tworzy połączenia z bazą ani
niezależnego mechanizmu dźwięku. Ten sam zamknięty timestamp może być oceniony
przez obie strategie, ponieważ ich markery są rozdzielone nazwą algorytmu.
Zapis `NONE` można wyłączyć przez ustawienie `SAVE_NONE_SIGNALS = False` w
`config.py` (TODO: docelowo ustawić na `False`).

Uruchom testy:

```powershell
py -3.11 -m pytest --import-mode=importlib src/algorithms/Grok-45/USDMXN/tests
```
