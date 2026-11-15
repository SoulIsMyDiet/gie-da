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

API uruchamia osobny proces pętli Groka, który co pięć sekund sprawdza MySQL
w godzinach sesji ES oraz przez 15 minut po jej zamknięciu. Nie pobiera danych
z zewnętrznego API. Analizuje zamknięte świece z ostatnich 15 minut, także te
zapisane wcześniej jako formowane lub dostarczone z opóźnieniem. Świeca
zamknięta przed startem pętli albo nieaktualna nie daje aktywnego sygnału.
Upływ minuty nie potwierdza, że dostawca zaktualizował wcześniej zapisaną
świecę: ewentualna późniejsza korekta nie wyzwala ponownej analizy.
Strategia Claude nadal jest wywoływana oddzielnie po pojedynczym zapisie.

Znaczniki przetwarzania pętli są przechowywane w pliku SQLite, również gdy
zapis NONE jest wyłączony. Docker Compose umieszcza go w wolumenie
`grok_sp500_state`; zmienna `GROK_SP500_STATE_PATH` pozwala zmienić ścieżkę.
Usunięcie wolumenu usuwa te znaczniki. Zapis sygnału i znacznik SQLite nie są
jedną transakcją; unikalny klucz MySQL chroni przed duplikatem rekordu, ale
nie gwarantuje dokładnie jednokrotnego obliczenia, zwłaszcza przy replikach.

Potwierdzenia dźwięku z pętli są udostępniane klientowi Windows przez API,
oddzielnie od odpowiedzi na zapis świecy. API i klient C++ należy zaktualizować
razem. Powiadomienia z czasu, gdy klient nie działa, nie są odtwarzane po jego
ponownym uruchomieniu. Dostarczenie dźwięku nie jest gwarantowane w razie
awarii lub równoległych zapisów zatwierdzanych w innej kolejności niż ich ID.
Polecenie lokalne odtwarza dźwięk bez kolejnego powiadomienia klienta.

Strategia wymaga walidacji na rzeczywistych danych ES przed użyciem w handlu;
nie przeprowadza backtestu ani nie gwarantuje zysku.
