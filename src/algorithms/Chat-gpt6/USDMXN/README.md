# USDMXNStrategyV1

Strategia działa w lokalnym API po zapisaniu nowej, zamkniętej świecy M1
instrumentu `USD/MXN`. Korzysta z istniejącego repozytorium i kalendarza rynku;
nie tworzy osobnego połączenia z bazą. Świece są odczytywane w ograniczonym
oknie 1200 rekordów. `datetime` z tabeli `candles` jest polskim czasem lokalnym,
a `utc_offset_minutes` służy do odtworzenia jednoznacznego czasu UTC także przy
zmianie CET/CEST.

## Wskaźniki i warm-up

- M1: EMA9, EMA21, RSI14 i ATR14.
- M5: EMA20, EMA50 i ADX14.
- EMA startuje od pierwszego Close; RSI, ATR i ADX używają metody Wildera.
- M5 powstaje wyłącznie z pięciu kolejnych świec M1. Niepełny przedział jest
  pomijany, a brakująca świeca nigdy nie jest sztucznie uzupełniana.
- Wymagane jest minimum 100 świec M1 oraz 100 kompletnych świec M5.
- Pojedyncza brakująca minuta nie resetuje wskaźników. Dłuższa luka w godzinach
  otwarcia rynku rozpoczyna warm-up od nowa. Kalendarz Forex API pomija zwykłe
  zamknięcie weekendowe. Długość długiej luki w diagnostyce może być podana jako
  dolna granica; pole `latest_unexpected_gap_minutes_is_lower_bound` wskazuje,
  czy jej wartość została obcięta dla wydajności.

## Warunki

LONG wymaga jednocześnie wzrostowego EMA20/EMA50 M5, ADX14 M5 co najmniej 18,
wzrostowego EMA9/EMA21 M1, dotknięcia EMA9 i zamknięcia powyżej niej,
przecięcia RSI14 z poziomu 50 w górę oraz zamknięcia ponad maksimum poprzedniej
świecy M1. SHORT stosuje dokładne odwrócenie tych warunków.

Stop loss wynosi 1,2 ATR14 M1, take profit 1,5 odległości stop loss, a limit
czasu utrzymania pozycji to 20 minut. Poziomy są orientacyjne i nie gwarantują
ceny wykonania. Wynik strategii zawiera pełne wskaźniki, OHLC, warunki obu
kierunków, progi, parametry transakcji i przyczynę wyniku NONE.

## Zapis i uruchamianie

API zapisuje w `signals.signal_parameters` wyłącznie `price`, `signal`,
`timestamp`, `instrument` i `reason`; pełna diagnostyka pozostaje w wyniku
strategii, a `source_timestamp` wskazuje epokę UTC otwarcia świecy M1. Tabela
`algorithm_evaluations` trwale zapisuje każdą ewaluację, również NONE pominięte
w `signals`, więc restart API nie powoduje ponownej oceny tej samej świecy.
Nowy rekord sygnału i oczekujące powiadomienie trafiają do bazy w jednej
transakcji. Klient C++ odtwarza dwa krótkie piknięcia, a następnie potwierdza
powiadomienie; przy błędzie pobieranie jest ponawiane. To zapewnia dostarczenie
co najmniej raz: awaria po odtworzeniu, ale przed potwierdzeniem, może spowodować
powtórne piknięcie.

Zapis wyników NONE można wyłączyć, zmieniając wyłącznie
`SAVE_NONE_SIGNALS = True` na `False` w `config.py`. Nie zmienia to zapisu
sygnałów LONG ani SHORT.

Testy uruchom poleceniem:

```powershell
py -3.11 -m pytest src/algorithms/Chat-gpt6/USDMXN/tests
```
