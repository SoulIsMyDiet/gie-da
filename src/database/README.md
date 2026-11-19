# Dane rynkowe w MySQL

Projekt używa MySQL uruchomionego w Dockerze. Pobrane świece rynkowe są przechowywane w tabeli `candles`.

## Pierwsze uruchomienie

1. Zainstaluj Docker Desktop dla Windows, jeśli jeszcze go nie masz, i uruchom go. Poczekaj, aż Docker Desktop pokaże, że silnik działa.
2. Otwórz PowerShell w głównym folderze projektu i utwórz własny plik ustawień:

   ```powershell
   Copy-Item .env.example .env
   ```

3. Otwórz `.env` i zmień oba przykładowe hasła na własne, różne, długie hasła. Tego pliku nie należy udostępniać ani dodawać do repozytorium.
4. Zbuduj i uruchom bazę oraz lokalne API:

   ```powershell
   docker compose up -d --build
   ```

   Nazwa projektu Docker jest ustawiona na `gieda` w pliku Compose, więc zmiana nazwy folderu nie tworzy nowego, pustego wolumenu z bazą.

5. Sprawdź, czy API odpowiada:

   ```powershell
   Invoke-RestMethod http://localhost:8000/health
   ```

   Oczekiwany wynik to `status: ok`.
6. Uruchom aplikację giełdy w CLion. Przy starcie aplikacja wyszukuje luki dla otwartych, monitorowanych instrumentów i uzupełnia maksymalnie 1000 najnowszych brakujących świec. Jedno żądanie Twelve Data może pobrać do 5000 świec, więc wiele bliskich luk jest pobieranych razem. Następnie monitor pobiera nowe świece 1-minutowe po zamknięciu minuty.

## Codzienna obsługa

- Start po ponownym uruchomieniu komputera: uruchom Docker Desktop, a następnie wykonaj `docker compose up -d` w folderze projektu.
- Podgląd działania usług: `docker compose ps`.
- Logi lokalnego API: `docker compose logs -f price-history-api`.
- Zatrzymanie usług: `docker compose stop`. Zatrzymanie nie usuwa zapisanych danych.

Dane bazy są przechowywane w wolumenie Dockera `mysql_data`. Przy uruchomieniu API tabela `price_history`, jeśli istnieje, jest usuwana wraz z zawartością. Nie używaj `docker compose down -v`, jeśli chcesz zachować pozostałe dane — opcja `-v` usuwa cały wolumen.

## Struktura danych

- `products`: unikalne symbole wewnętrzne produktów (np. `EURUSD/USD`). Przed wysłaniem żądania aplikacja mapuje `EURUSD/USD` na symbol Twelve Data `EUR/USD`; nazwa w lokalnej bazie pozostaje bez zmian.
- `candles`: kolumny `symbol`, `datetime`, `open`, `high`, `low`, `close`, `volume` oraz `utc_offset_minutes`. `datetime` jest zapisywany w czasie lokalnym `Europe/Warsaw`; offset rozróżnia powtarzającą się godzinę przy zmianie czasu. Klucz główny `(symbol, datetime, utc_offset_minutes)` zapobiega duplikatom, a ponowne pobranie świecy aktualizuje wpis. `volume` jest `NULL`, jeśli dostawca go nie podał.
- `candle_gaps`: osobny wiersz dla każdej wykrytej brakującej świecy. `candle_datetime_utc` oznacza oczekiwany początek świecy w UTC, `status` ma wartość `missing` albo `filled`, a pola `detected_at_utc` i `filled_at_utc` zapisują moment wykrycia i uzupełnienia. Klucz `(symbol, candle_datetime_utc)` zapobiega duplikatom.
- `signals`: `id`, `datetime`, `product`, `price`, `signal`, `algorithm_name`, `ai_decision`, `transaction_id`, `candles_count` oraz `signal_parameters`. `datetime` ma typ `DATETIME(6)` i ma być zapisywany jako lokalny czas `Europe/Warsaw`; `signal` dopuszcza `long` lub `short`; `ai_decision` jest nullable JSON, więc może później przechowywać wartość logiczną albo obiekt JSON; `transaction_id` jest nullable i na razie nie ma klucza obcego, bo tabela transakcji jeszcze nie istnieje; `signal_parameters` przechowuje wymagany dokument JSON. Kolumna `product` wskazuje na istniejący symbol w `products`.
- `prompts`: `id`, `prompt`, `used_context`, `used_parameters`, `prompt_in_use` oraz `used_candles`. `prompt` jest przechowywany jako `LONGTEXT`; przy pierwszym uruchomieniu API dodaje się do tabeli początkowy prompt oceniający sygnały.
- `signal_potention`: `id`, `signal_id`, kolumny sald oraz wymagany `spread` typu `DECIMAL(20, 5)` i całkowitoliczbowe `max_candle`. Wszystkie pola sald pozostają nullable i domyślnie `NULL`; spread jest zapisywany jawnie według instrumentu, bez wspólnej wartości domyślnej. `signal_id` wskazuje na `signals.id` i jest unikalne, dlatego dla jednego sygnału może istnieć najwyżej jeden wiersz z wynikami. Istniejące wartości i nowe wyniki są obcinane (bez zaokrąglania) do pięciu miejsc po przecinku.
  describe signals;
Tabela `candles` zostanie utworzona automatycznie przy uruchomieniu lokalnego API. Istniejące wpisy, które wcześniej zapisano w UTC, zostaną jednorazowo przeliczone na `Europe/Warsaw`. Po zmianach kodu API lub konfiguracji bazy przebuduj i uruchom usługę poleceniem `docker compose up -d --build`.

Tabela `signals` również jest tworzona automatycznie przy starcie API. Po każdej nowej zamkniętej świecy EUR/USD API uruchamia strategię `Chat-gpt6/EURUSD/EURUSDStrategyV1`; wygenerowany sygnał zapisuje w tej samej transakcji bazodanowej co świecę, a jego pakiet JSON trafia do `signal_parameters`. Po potwierdzonym zapisie aplikacja desktopowa odtwarza dwa piknięcia. Integracja z agentem AI nie jest jeszcze podłączona.

Po każdej nowej zamkniętej świecy `USD/MXN` API uruchamia strategię
`Chat-gpt6/USDMXN/USDMXNStrategyV1`. Odczyt ograniczony do ostatnich 1200 świec
odtwarza czas UTC z `datetime` i `utc_offset_minutes`; pełny wynik LONG, SHORT
lub NONE jest ograniczany do `price`, `signal`, `timestamp`, `instrument` i
krótkiego `reason` w `signal_parameters`; szczegółowa diagnostyka pozostaje
w wyniku strategii. Tabela
`algorithm_evaluations` trwale oznacza każdą ewaluację, także NONE pominięte
w `signals`, i blokuje jej powtórzenie po restarcie. Rekord sygnału i oczekujące
powiadomienie są zapisywane w jednej transakcji. Aplikacja desktopowa odtwarza
dwa krótkie piknięcia, a po ich odtworzeniu potwierdza zdarzenie; w razie błędu
ponawia pobranie. Dostarczenie jest typu co najmniej raz, więc awaria między
odtworzeniem a potwierdzeniem może powtórzyć dźwięk. Dźwięk jest dodawany do
outboxa tylko dla LONG i SHORT; wyniki NONE nie trafiają do tabeli `signals`.
Paczki `/api/candles/batch` również uruchamiają obie strategie USD/MXN dla
zamkniętych świec, ale nie odtwarzają dźwięków dla historycznych danych.

Po każdej nowej zamkniętej świecy `USD/JPY` API uruchamia niezależną strategię
`Chat-gpt6/USDJPY/USDJPYStrategyV1`. Historia jest ograniczona do 1200 świec,
a zamknięte M5 powstają wyłącznie z pięciu kolejnych świec M1. Pełny wynik
LONG, SHORT albo NONE trafia do `signal_parameters` jako `price`, `signal`,
`timestamp`, `instrument` i krótki `reason`; pełna diagnostyka pozostaje
w wyniku strategii, a ewaluacja jest chroniona
markerem `algorithm_evaluations`. Powiadomienia LONG i SHORT po zapisie
korzystają z tego samego outboxa i mechanizmu dwóch piknięć co pozostałe
strategie. Wyniki NONE nie są zapisywane do `signals`. Paczki
`/api/candles/batch` także uruchamiają strategię USD/JPY dla zamkniętych świec,
bez dźwięków dla historycznych danych.

Równolegle API uruchamia dla zamkniętych świec USD/JPY strategię
`Claude-sonnet55/USDJPYStrategyV1`, także podczas przetwarzania paczek.
Wyniki NONE nie są zapisywane do `signals`; odpowiedź zwiększa licznik dźwięku
tylko dla LONG/SHORT. API używa trybu bez lokalnego dźwięku.

Nowe rekordy XAU/USD zapisują w `signal_parameters` zwięzły JSON: `price`,
`signal`, epokę `timestamp`, `instrument` oraz `parameters` z EMA9, ATR14,
EMA21, RSI14, krótkim powodem i pasmami Bollingera. Pełne dane diagnostyczne
algorytmu nie są zapisywane, a istniejące rekordy pozostają bez zmian.

Po każdej zamkniętej świecy XAU/USD API uruchamia strategie
`Chat-gpt6/XAUUSD/XAUUSDStrategyV1` i
`Claude-sonnet55/XAUUSD/XAUUSDStrategyV1`. Dotyczy to zarówno pojedynczych
zapisów, jak i świec dostarczonych w paczce `/api/candles/batch`. Obie strategie zapisują wyłącznie sygnały LONG i SHORT; powiadomienie
dźwiękowe jest naliczane wyłącznie dla zapisanych sygnałów.
Uzupełnianie historii w paczce nie odtwarza dźwięków.

Pobieranie świec dla `GER30` (DAX40) i `ES` (S&P 500) jest tymczasowo wyłączone,
ponieważ dotychczasowe symbole nie zapewniały danych dla oczekiwanych indeksów.
API nie zwraca tych instrumentów jako monitorowanych ani otwartych i odrzuca
nowe zapisy oraz żądania uzupełnienia; zapisane wcześniej świece pozostają
w bazie. Monitor pobiera obecnie `EURUSD/USD`, `USD/MXN`, `USD/JPY` i `XAU/USD`.

Strategia `Claude-sonnet55/S&P500` pozostaje w kodzie. Po ponownym włączeniu
instrumentu API może uruchamiać ją po zapisaniu pojedynczej zamkniętej świecy
`ES`, a aplikacja Windows odtwarza dźwięk po nowym zapisie sygnału. Ten tryb nie
odpytuje samodzielnie bazy i nie wykrywa braku świecy po 20 sekundach ani nie
analizuje automatycznie uzupełnianej historii. Tryb `--loop` można uruchomić
osobno; szczegóły i ograniczenia znajdują się w README strategii.

Tabela `signal_potention` jest tworzona automatycznie przy uruchomieniu API. Przy aktualizacji istniejąca kolumna `saldo_10th_candle` jest bezpiecznie przemianowywana na `saldo_10aggr_candles`, z obcięciem wartości do pięciu miejsc po przecinku; dodawane są też kolumny `saldo_20_aggr_candles`, `max_candle` i `spread`. Przyjęte orientacyjne spready w jednostkach ceny to: EUR/USD `0,00030`, USD/MXN `0,01000`, USD/JPY `0,03000`, XAU/USD `0,30000`, ES `0,60000` i GER30 `1,50000`. Są to założenia do raportu, nie bieżące kwotowania brokera; przy starcie API istniejące wpisy są aktualizowane do wartości instrumentu, a nowe wpisy zapisują spread jawnie. Kolejne zamknięte świece M1, licząc od świecy po tej, która wygenerowała sygnał, stopniowo uzupełniają salda pierwszych pięciu świec. Po 10 i 20 świecach zapisywane są salda agregowane. Wynik LONG wykorzystuje najwyższe `high`, a SHORT najniższe `low`; od zmiany kursu odejmowany jest spread zapisany w tym samym wierszu. `max_candle` to numer od 1 do 20 świecy, która wyznaczyła ekstremum dla salda 20 świec. Po zebraniu 20 świec `100zl_deal` jest szacowany jako `100 * 30 * saldo_20_aggr_candles / cena_wejścia`; wynik jest w przybliżeniu w PLN i nie uwzględnia osobnych kursów przeliczeniowych walut ani specyfikacji kontraktu futures. Niewypełnione jeszcze kolumny pozostają `NULL`, a salda mogą być ujemne.

Strategia inicjalizuje EMA wartością pierwszego `Close`, a kolejne wartości oblicza ze współczynnikiem `2 / (N + 1)`. RSI/ATR są wygładzane metodą Wildera. Nie znaleziono w repozytorium istniejącego backtestu Python, z którym można porównać obliczenia wskaźników. Niezależnie od spreadów raportowanych w `signal_potention`, filtr strategii przed podłączeniem BID/ASK nadal przyjmuje bieżący spread i limit jako 3 pipsy, dlatego obecnie nie odrzuca sygnałów.

Pojedyncza brakująca świeca M1 nie resetuje wskaźników. Brakujące minuty są liczone tylko w godzinach otwarcia Forex; zamknięcie od piątku 17:00 do niedzieli 17:00 czasu nowojorskiego jest pomijane. Więcej niż jedna brakująca minuta handlowa z rzędu resetuje wskaźniki i stan ponawiania sygnałów. Niedokończona agregacja M5 jest odrzucana przy każdej luce, aby nie łączyć świec z przerwą.

API odrzuca zapis świec, których czas przypada na zamknięcie weekendowe. Dotyczy to zarówno pojedynczych świec z bieżącego monitorowania, jak i świec przesyłanych paczkami przy uzupełnianiu historii. Reguła jest sprawdzana po stronie API, więc działa także dla danych pobranych z Twelve Data.

Uzupełnianie luk działa od najnowszych wpisów wstecz, pomija weekendowe zamknięcie rynku Forex i obejmuje maksymalnie 1000 świec przy jednym starcie aplikacji. Jeśli baza nie ma jeszcze świec, aplikacja pobiera do 1000 najnowszych świec. Na stronie głównej wyświetlana jest liczba zapisanych świec oraz łączny czas sprawdzenia, pobrania i zapisu. Każde żądanie `/time_series` kosztuje kredyty zgodnie z planem Twelve Data; odległe luki mogą wymagać osobnych żądań.

Lukę zapisujemy od razu przy jej pierwszym wykryciu: podczas skanowania bazy przy starcie aplikacji albo po otrzymaniu świecy, której czas wskazuje na brak wcześniejszych minut. Każda minuta ma osobny, unikalny wpis, więc powtórne wykrycie nie tworzy duplikatu. Po zapisaniu brakującej świecy jej status zmienia się na `filled`, ale rekord pozostaje w tabeli jako historia. Zwykłe zamknięcie rynku od piątku 17:00 do niedzieli 17:00 czasu nowojorskiego nie jest rejestrowane jako luka. Wykrycie luki nie odtwarza dźwięku; aplikacja nadal pokazuje komunikat i zapisuje informację w tabeli `candle_gaps`. Przy starcie komunikat może pojawić się także wtedy, gdy automatyczne uzupełnianie zaraz potem wypełni świecę.

Przykład ręcznego sprawdzenia aktywnych luk:

```sql
SELECT symbol, candle_datetime_utc, status, detected_at_utc
FROM candle_gaps
WHERE status = 'missing'
ORDER BY candle_datetime_utc;
```

W aplikacji desktopowej przycisk „Luki świec” obok „Raporty” otwiera stronicowany
podgląd całej tabeli, w tym wpisów już uzupełnionych.

Przycisk „Raporty” otwiera tabelę 20 najnowszych świec. Przyciski „Poprzednia” i „Następna” pozwalają przechodzić pomiędzy stronami, a „Strona główna” wraca do głównego widoku. Lista jest pobierana z lokalnego API pod adresem `/api/candles/report`; nowe świece są zapisywane przez `/api/candles` lub paczkowo przez `/api/candles/batch`.

Przycisk „Sygnały” obok widoków świec i luk otwiera stronicowany podgląd 15 wpisów tabeli `signals` na stronę. Widok pokazuje czas, produkt, cenę i algorytm; przycisk „Szczegóły” pobiera dla wybranego `signal_id` saldo z 20 świec, numer świecy maksymalnego wyniku, wynik transakcji dla 100 zł i spread z tabeli `signal_potention`.

Istniejące świece zapisane wcześniej w UTC są jednorazowo przeliczane na `Europe/Warsaw`; nowe wpisy również są zapisywane w tym czasie. API jest dostępne lokalnie na `127.0.0.1:8000`, a MySQL na `127.0.0.1:3306` wyłącznie dla programów uruchomionych na tym samym komputerze; port nie jest wystawiony na interfejs sieciowy. Dalsze zmiany struktury będą wymagały migracji w `src/database/api/app.py` — samo `CREATE TABLE IF NOT EXISTS` nie zmienia już istniejących tabel.
