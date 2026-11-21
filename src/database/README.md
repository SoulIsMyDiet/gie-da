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

## Pliki błędów

Ostrzeżenia i błędy API oraz aplikacji C++ są dopisywane do wspólnych plików
`logs/LogError-MM.YYYY`, na przykład `logs/LogError-11.2026`. Każdy wpis
zajmuje jedną linię i zawiera poziom (`[Warning]` lub `[Error]`), lokalną datę
i godzinę oraz komunikat. Nowy plik dla kolejnego miesiąca jest tworzony przy
starcie aplikacji/API albo przy pierwszym wpisie w nowym miesiącu. Starsze
pliki pozostają zachowane.

Dane bazy są przechowywane w wolumenie Dockera `mysql_data`. Przy uruchomieniu API tabela `price_history`, jeśli istnieje, jest usuwana wraz z zawartością. Nie używaj `docker compose down -v`, jeśli chcesz zachować pozostałe dane — opcja `-v` usuwa cały wolumen.

## Struktura danych

- `products`: unikalne symbole wewnętrzne produktów (np. `EURUSD/USD`). Przed wysłaniem żądania aplikacja mapuje `EURUSD/USD` na symbol Twelve Data `EUR/USD`; nazwa w lokalnej bazie pozostaje bez zmian.
- `candles`: kolumny `symbol`, `datetime`, `open`, `high`, `low`, `close`, `volume` oraz `utc_offset_minutes`. `datetime` jest zapisywany w czasie lokalnym `Europe/Warsaw`; offset rozróżnia powtarzającą się godzinę przy zmianie czasu. Klucz główny `(symbol, datetime, utc_offset_minutes)` zapobiega duplikatom, a ponowne pobranie świecy aktualizuje wpis. `volume` jest `NULL`, jeśli dostawca go nie podał.
- `candle_gaps`: osobny wiersz dla każdej wykrytej brakującej świecy. `candle_datetime_utc` oznacza oczekiwany początek świecy w UTC, `status` ma wartość `missing` albo `filled`, a pola `detected_at_utc` i `filled_at_utc` zapisują moment wykrycia i uzupełnienia. Klucz `(symbol, candle_datetime_utc)` zapobiega duplikatom.
- `signals`: `id`, `datetime`, `product`, `price`, `signal`, `algorithm_name`, `ai_decision`, `transaction_id`, `candles_count` oraz `signal_parameters`. `datetime` ma typ `DATETIME(6)` i jest zapisywany jako lokalny czas `Europe/Warsaw`; `signal` dopuszcza `long`, `short` lub techniczne `none`; `ai_decision` i `transaction_id` pozostają nullable dla zgodności ze starszymi klientami. Weryfikator zapisuje dwie transakcje dla jednego sygnału w osobnej tabeli `transactions`, dlatego nie używa pojedynczego pola `signals.transaction_id`. `signal_parameters` przechowuje wymagany dokument JSON, a `product` wskazuje na istniejący symbol w `products`.
- `prompts`: `id`, `prompt`, `used_context`, `used_parameters`, `prompt_in_use`, `used_candles` oraz `prompt_template_version`. Kolumna `prompt` ma kodowanie `utf8mb4` i typ `LONGTEXT`. Przy zwykłym restarcie ręczne zmiany promptu pozostają zachowane; jego zamierzone odświeżenie wymaga zwiększenia wersji szablonu.
- `signal_potention`: `id`, `signal_id`, kolumny sald, wymagany `average_spread` typu `DECIMAL(20, 5)`, `best_candle_20_minute` wskazująca świecę 1–20 oraz wyniki dla 100 zł w kolumnach `100zl_deal_20` i `100zl_deal_60`. Wszystkie pola sald pozostają nullable i domyślnie `NULL`; średni spread jest zapisywany jawnie według instrumentu, bez wspólnej wartości domyślnej. `signal_id` wskazuje na `signals.id` i jest unikalne, dlatego dla jednego sygnału może istnieć najwyżej jeden wiersz z wynikami. Istniejące wartości i nowe wyniki są obcinane (bez zaokrąglania) do pięciu miejsc po przecinku.
  describe signals;
Tabela `candles` zostanie utworzona automatycznie przy uruchomieniu lokalnego API. Istniejące wpisy, które wcześniej zapisano w UTC, zostaną jednorazowo przeliczone na `Europe/Warsaw`. Po zmianach kodu API lub konfiguracji bazy przebuduj i uruchom usługę poleceniem `docker compose up -d --build`.

Tabela `signals` również jest tworzona automatycznie przy starcie API. Po każdej nowej zamkniętej świecy EUR/USD API uruchamia strategię `Chat-gpt6/EURUSD/EURUSDStrategyV1`; wygenerowany sygnał zapisuje w tej samej transakcji bazodanowej co świecę, a jego pakiet JSON trafia do `signal_parameters`.

Każdy nowy sygnał LONG lub SHORT jest dopisywany przez trigger MySQL do `ai_verification_jobs`. Wątek API przetwarza go asynchronicznie, więc wywołanie modelu nie blokuje zapisu świecy. Jeśli w `prompts` istnieje dokładnie jeden rekord z `prompt_in_use = TRUE`, API wybiera jednego lub więcej dostawców według instrumentu na podstawie `src/database/api/ai_verification_config.json`; przypisani dostawcy są odpytywani równolegle. Domyślnie instrumenty bez jawnej trasy korzystają z Groq (`qwen/qwen3.8-27b`). Prompt zawiera parametry sygnału, `STRATEGY_CONTEXT` właściwego algorytmu oraz historię do chwili sygnału: 50 świec M1 i 30 pełnych świec M5. Każdy model zwraca profile aggressive i moderate; `ai_decisions.ai_name` jest pobierane z konfiguracji klienta, a `transactions.verifier_id` pozwala rozróżnić model przy tym samym sygnale i profilu. Prawidłowe odpowiedzi wszystkich przypisanych modeli są zapisywane atomowo: po jednym rekordzie `transactions` i `ai_decisions` dla każdego profilu każdego modelu, a pełny prompt i odpowiedź danego modelu trafiają do `prompts_revision`. Decyzja `NO` również tworzy symulowaną transakcję.

### Przypisywanie instrumentów do dodatkowego modelu

Routing jest zapisany w `src/database/api/ai_verification_config.json`. Obecnie `USD/MXN` jest równolegle weryfikowane przez Claude Sonnet 5.5 (natywne Anthropic Messages API) i Groq (`qwen/qwen3.8-27b`); pozostałe instrumenty nadal korzystają tylko z Groq. Każdy model zapisuje niezależne transakcje i decyzje, a `ai_decisions.ai_name` wskazuje, który model je wygenerował. Dla USD/MXN oznacza to dwa żądania i większe zużycie tokenów/API na sygnał. Claude ma identyfikator API `claude-sonnet-5-5`; jego nazwa pochodzi z pola `display_name`, a nie z odpowiedzi JSON. Żądanie ma limit odpowiedzi 900 tokenów i średni poziom effort, aby ograniczyć koszt bez wymuszania najniższej jakości rozumowania.

Przypisanie instrumentu ma postać:

```json
"instrument_routes": {
  "USD/MXN": ["claude_sonnet55", "groq"]
}
```

Wartość może być nazwą jednego dostawcy albo listą nazw dostawców do równoległego uruchomienia. Każda nazwa musi wskazywać element z `providers`; symbole instrumentów muszą dokładnie odpowiadać `signals.product`, np. `EURUSD/USD`, `USD/MXN`, `USD/JPY` albo `XAU/USD`. Klucz API pozostaje w lokalnym `.env` jako `CLAUDE_API_KEY`; Docker Compose przekazuje go do kontenera. Nie umieszczaj klucza w pliku JSON ani w repozytorium. Inni dostawcy Anthropic mogą być dodani jako `type: "anthropic"`; dla usług zgodnych z OpenAI Chat Completions dostępny jest także typ `openai_compatible`.

Po zmianie routingu lub klucza odtwórz wyłącznie kontener API poleceniem `docker compose up -d --force-recreate --no-deps price-history-api` (bez `--build`, ponieważ plik routingu jest montowany z hosta). Zmiana kodu wymaga przebudowy kontenera. Routing jest wczytywany przy starcie workera. Jeśli którykolwiek z równolegle uruchomionych modeli zwróci błąd lub niepoprawną odpowiedź, zadanie zostanie oznaczone jako `failed` i żaden z wyników tego zadania nie zostanie zapisany; ponowienie jest ręczne i ponownie odpytuje wszystkie modele z trasy.

Tabela `transactions` przechowuje identyfikator modelu weryfikującego, kierunek sygnału i kierunek AI, poziomy wejścia/SL/TP, spread oraz stan `awaiting`, `in progress` albo `closed`. Gdy cena wejścia jest równa cenie zamknięcia świecy sygnałowej, transakcja rozpoczyna się od razu, a `time_to_open` wynosi `0`. W pozostałych przypadkach pozostaje `awaiting` do pierwszej świecy po sygnale, która osiąga ENTRY: gdy ENTRY jest powyżej ceny zamknięcia sygnału, warunkiem jest `HIGH >= ENTRY`; gdy jest poniżej, warunkiem jest `LOW <= ENTRY`. Reguła progu jest taka sama dla LONG i SHORT; kierunek AI określa później sposób sprawdzania SL i TP. `time_to_open` zapisuje numer świecy wejścia wśród zapisanych świec M1, a nie czas kalendarzowy. Jeśli na świecy wejścia zostanie trafiony SL lub TP, transakcja może zamknąć się na tej samej świecy i `time_from_open_to_close` wynosi wtedy `0`. W pozostałych przypadkach ten czas również liczy świece M1, więc pierwsza kolejna dostępna świeca po przerwie, np. weekendowej, daje wartość `1`. Gdy jedna świeca dotknie jednocześnie SL i TP, przyjmowany jest Stop Loss. Po uzupełnieniu wcześniej brakującej świecy ponownie przetwarzane są transakcje, których wynik mogła ona zmienić. Saldo oraz wynik dla 100 zł pozostają `NULL` do zamknięcia. Błędne odpowiedzi i błędy API nie blokują zapisu sygnału; szczegóły można odczytać z `GET /api/ai-verification/jobs?status=failed`, a zadania `failed` lub `skipped` ponowić ręcznie przez `POST /api/ai-verification/jobs/{signal_id}/retry`. Ponowienie wykonuje nowe żądanie do każdego modelu z trasy, więc nie jest automatyczne. Do kontenera API klucz Groq jest przekazywany ze zmiennej `GROK_API_KEY` w lokalnym pliku `.env`; klucza nie należy umieszczać w repozytorium.

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

Tabela `signal_potention` jest tworzona automatycznie przy uruchomieniu API. Przy aktualizacji istniejąca kolumna `saldo_10th_candle` jest bezpiecznie przemianowywana na `saldo_10aggr_candles`, z obcięciem wartości do pięciu miejsc po przecinku; kolumny `max_candle`, `100zl_deal` i `spread` są przemianowywane odpowiednio na `best_candle_20_minute`, `100zl_deal_20` i `average_spread`. Dodawane są też kolumny `saldo_20_aggr_candles`, `best_candle_60_datetime`, `saldo_60_aggr_candles` i `100zl_deal_60`. Przyjęte orientacyjne spready w jednostkach ceny to: EUR/USD `0,00030`, USD/MXN `0,01000`, USD/JPY `0,03000`, XAU/USD `0,30000`, ES `0,60000` i GER30 `1,50000`. Są to założenia do raportu, nie bieżące kwotowania brokera; przy starcie API istniejące wpisy są aktualizowane do wartości instrumentu, a nowe wpisy zapisują średni spread jawnie. Kolejne zamknięte świece M1, licząc od świecy po tej, która wygenerowała sygnał, stopniowo uzupełniają salda pierwszych pięciu świec. Po 10, 20 i 60 świecach zapisywane są salda agregowane. Dla obu okien wynik LONG wykorzystuje najwyższe `high`, a SHORT najniższe `low`; od zmiany kursu odejmowana jest wartość `average_spread` z tego samego wiersza. `best_candle_20_minute` to numer od 1 do 20 świecy, która wyznaczyła ekstremum dla salda 20 świec. `best_candle_60_datetime` zapisuje datę i czas świecy M1 wybranej w oknie 60 świec. Po zebraniu 20 świec `100zl_deal_20` jest szacowany jako `100 * 30 * saldo_20_aggr_candles / cena_wejścia`; po zebraniu 60 świec `100zl_deal_60` używa tego samego wzoru z `saldo_60_aggr_candles`. Wynik jest w przybliżeniu w PLN i nie uwzględnia osobnych kursów przeliczeniowych walut ani specyfikacji kontraktu futures. Niewypełnione jeszcze kolumny pozostają `NULL`, a salda mogą być ujemne. Przy starcie API i po zapisie świec kalkulator uzupełnia także nowe kolumny dla istniejących sygnałów; dotychczasowe wyniki 20-świecowe są zachowywane.

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

Klasa `CandlePromptContextBuilder` w `src/stockmarket` pobiera do 1000 zamkniętych świec M1 z `/api/candles/prompt-history`, agreguje ostatnie 30 pełnych świec M5 i zwraca skompresowany JSON z 50 świecami M1. Weryfikator API buduje równoważny kontekst bezpośrednio z bazy i ogranicza historię do czasu sygnału, aby model nie otrzymał przyszłych danych. Niepełne koszyki M5 są pomijane, a wykryta luka w otwartej sesji jest logowana.

Przycisk „Sygnały” obok widoków świec i luk otwiera stronicowany podgląd 15 wpisów tabeli `signals` na stronę. Widok pokazuje czas, produkt, algorytm, zagregowaną decyzję AI i wynik po 20 minutach; cenę pomija. Decyzja AI to `ZGODA`, jeśli co najmniej jeden wpis powiązany z sygnałem ma wartość `accept`; `ODRZUCENIE` pojawia się wyłącznie po zakończeniu weryfikacji, gdy wszystkie wpisy są `decline` i każdy powiązany rekord transakcji ma decyzję. W pozostałych przypadkach widoczne jest `OCZEKUJE`. Przycisk „Szczegóły” pobiera dla wybranego `signal_id` wyniki z okien 20 i 60 świec, czas świecy najlepszego wyniku z okna 60 świec oraz szacowany wynik dla 100 zł z tabeli `signal_potention`.

Istniejące świece zapisane wcześniej w UTC są jednorazowo przeliczane na `Europe/Warsaw`; nowe wpisy również są zapisywane w tym czasie. API jest dostępne lokalnie na `127.0.0.1:8000`, a MySQL na `127.0.0.1:3306` wyłącznie dla programów uruchomionych na tym samym komputerze; port nie jest wystawiony na interfejs sieciowy. Dalsze zmiany struktury będą wymagały migracji w `src/database/api/app.py` — samo `CREATE TABLE IF NOT EXISTS` nie zmienia już istniejących tabel.
