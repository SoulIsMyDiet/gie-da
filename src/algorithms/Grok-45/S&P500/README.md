# Strategia Grok-45 dla S&P 500

Strategia analizuje zamknięte świece M1 kontraktu zapisanego w bazie jako
`ES`. W wyniku JSON instrument nosi nazwę `S&P500`. Nie wysyła zleceń ani
nie zamyka pozycji; 20 minut to założony maksymalny czas mikrotransakcji,
którego trader lub osobny system musi dopilnować.

## Budowa i interfejs

- `config.py` zawiera ustawienia, w tym progi i przełączniki zapisu oraz dźwięku.
- `indicators.py` oblicza EMA, RSI, ATR, ADX i DI oraz wolumen i cechy świecy.
- `signal_logic.py` udostępnia `generate_signal(candles_df, now=None)`.
- `storage.py`, `trigger.py` i `sound.py` obsługują bazę, wywołanie i dźwięk.
- `S&P500StrategyV1.py` jest punktem wejścia, a `tests` zawiera testy.

`generate_signal` przyjmuje DataFrame z kolumnami `open`, `high`, `low`,
`close`, `volume` oraz świadomym `timestamp_utc` lub czasem warszawskim
`datetime`. Naiwny `datetime` wymaga `utc_offset_minutes`, żeby rozróżnić
powtórzoną godzinę przy zmianie czasu. `now`, jeśli podany, musi mieć strefę.
Wynikiem jest słownik nadający się do JSON, z ceną, czasem zamknięcia,
otwarciem świecy, kierunkiem i `signal_parameters`.

Strategia bierze maksymalnie 200 świec, odrzuca formowane i sortuje pozostałe
według czasu UTC. Jedna brakująca minuta nie przerywa segmentu; dłuższa
przerwa rozpoczyna nowy segment i wymaga ponownie co najmniej 50 świec.
Nie są tworzone sztuczne świece. Świeca starsza niż trzy minuty od zamknięcia
nie daje aktywnego sygnału.

Wymagane są jednocześnie wszystkie warunki trendu EMA, pullbacku, RSI, dodatniego
ATR, względnego wolumenu, zachowania świecy, ADX i kierunkowego DI. Wynik
punktowy 0–100 sumuje wagi spełnionych warunków; aktywny sygnał wymaga
wszystkich warunków, zatem otrzymuje 100 punktów. Progi i wagi można zmienić
w `config.py`. Nie potwierdzają one opłacalności ani nie uwzględniają
rzeczywistego spreadu.

## Uruchamianie i baza

Potrzebne są Python 3.10+, `pandas`, `mysql-connector-python` i dane stref
czasowych systemu (na Windows również pakiet `tzdata`). Z katalogu strategii:

```powershell
python "S&P500StrategyV1.py" --candle-open-epoch 1700000040
```

Argument to czas otwarcia konkretnej świecy w sekundach Unix, na pełnej
minucie. Połączenie wykorzystuje zmienne `MYSQL_HOST`, `MYSQL_PORT`,
`MYSQL_USER`, `MYSQL_PASSWORD` i `MYSQL_DATABASE`. Baza musi mieć istniejące
tabele aplikacji `candles`, `signals`, `signal_potention`, produkt `ES` oraz
unikalny klucz sygnału `(product, algorithm_name, source_timestamp)`.
Nie umieszczaj hasła w kodzie ani w poleceniu zapisanym w historii.

JSON jest wypisywany na stdout, a diagnostyka na stderr. Kod procesu 0 oznacza
nowy zapis z włączonym dźwiękiem, 4 zapis z wyłączonym dźwiękiem, 3 brak
zapisu, a 1 błąd. `SAVE_NONE_SIGNALS = True` zapisuje także NONE; ustawienie
`False` wyłącza ich zapis. Sygnały są idempotentne dzięki unikalnemu kluczowi.
Przy wyłączonym zapisie NONE jego analiza nie pozostawia trwałego śladu.

Po zatwierdzeniu nowego zapisu lokalny tryb wydaje dwa dźwięki. Błąd dźwięku
jest logowany i nie cofa zapisu. LONG/SHORT tworzy również `signal_potention`
w tej samej transakcji z przyjętym kosztem 0,6 punktu. Istniejący raport
potencjału nie jest backtestem, a jego wyliczenie „100 zł” nie uwzględnia
specyfiki mnożnika kontraktu futures.

## Integracja z aplikacją

API po zapisaniu pojedynczej, już zamkniętej świecy ES wywołuje niezależne
procesy strategii Claude i Grok, bez dodatkowych zapytań do zewnętrznego API.
Tryb `--api-candle-open-epoch` nie generuje dźwięku w kontenerze: odpowiedź
API przekazuje liczbę zapisów klientowi C++, który odtwarza dźwięk. Na przykład
bezpośrednie wywołanie procesu z C++ wymaga przekazania ścieżki skryptu jako
jednego argumentu, np. `python "S&P500StrategyV1.py" --candle-open-epoch 1700000040`;
w aplikacji używany jest wariant API.

Jeśli jedna strategia zawiedzie po zapisie świecy, API odpowiada HTTP 503 z
`SP500_PARTIAL` oraz licznikami luk i potwierdzonych dźwięków w kolejnych
wierszach. Klient zgłasza błąd i obsługuje potwierdzone liczniki. API i
klienta C++ należy zaktualizować razem.

Nie ma pętli ani automatycznego nadrabiania analiz: zapis świecy formowanej
nie uruchamia później analizy samoczynnie, podobnie jak zbiorczy backfill.
Powtórne wywołanie nie gwarantuje dokładnie jednokrotnego obliczenia, chociaż
unikalny klucz chroni przed podwójnym zapisem. Utrata odpowiedzi HTTP po
zatwierdzeniu zapisu może uniemożliwić dostarczenie dźwięku.

Strategia wymaga walidacji na rzeczywistych danych ES przed użyciem w handlu;
nie przeprowadza backtestu ani nie gwarantuje zysku.
