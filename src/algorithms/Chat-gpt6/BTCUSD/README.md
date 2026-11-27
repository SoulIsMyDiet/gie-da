# Chat-gpt6 BTC/USD Strategy V1

Strategia analizuje zamknięte świece M1 dla `BTC/USD`. Trend potwierdza
kompletna, zamknięta świeca M5. LONG i SHORT wymagają zgodnego trendu M5,
korekty do EMA9/EMA21, powrotu ceny nad/pod EMA9, RSI14 i histogramu MACD
potwierdzających momentum oraz zakresu świecy nie większego niż 2*ATR14.

BTC jest zarejestrowany w projekcie jako rynek `crypto_24_7`; nie ma
zaplanowanych przerw. Jedna brakująca minuta nie resetuje wskaźników, ale
dwie lub więcej kolejnych brakujących minut rozpoczynają nowy segment.
Nie powstają sztuczne świece. M5 powstaje wyłącznie z pięciu prawdziwych M1.

Nowe świece dostarcza IBKR do `candles_IBKR`. Strategię wywołuje istniejące API
po zapisaniu zamkniętej świecy; `MarketDataRepository` zapisuje ewaluację i
sygnał idempotentnie. Historyczna tabela `candles` jest źródłem legacy.
`SAVE_NONE_SIGNALS` jest tymczasowo ustawione na `True`.

Nie ma dostępu do BID/ASK ani wiarygodnej informacji o jednostce wolumenu.
Wolumen jest raportowany, ale nie wpływa na warunki sygnału. Strategia nie
składa zleceń, nie ustawia SL/TP i nie gwarantuje zysku. Horyzont 20 minut
jest wyłącznie informacyjny.

Testy uruchom z katalogu repozytorium poleceniem:

```powershell
python -m pytest src/algorithms/Chat-gpt6/BTCUSD/tests
```
