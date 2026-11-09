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

5. Sprawdź, czy API odpowiada:

   ```powershell
   Invoke-RestMethod http://localhost:8000/health
   ```

   Oczekiwany wynik to `status: ok`.
6. Uruchom aplikację giełdy w CLion. Przy starcie aplikacja wyszukuje luki w świecach EUR/USD i uzupełnia maksymalnie 1000 najnowszych brakujących świec. Jedno żądanie Twelve Data może pobrać do 5000 świec, więc wiele bliskich luk jest pobieranych razem. Następnie monitor pobiera nową świecę 1-minutową po zamknięciu minuty.

## Codzienna obsługa

- Start po ponownym uruchomieniu komputera: uruchom Docker Desktop, a następnie wykonaj `docker compose up -d` w folderze projektu.
- Podgląd działania usług: `docker compose ps`.
- Logi lokalnego API: `docker compose logs -f price-history-api`.
- Zatrzymanie usług: `docker compose stop`. Zatrzymanie nie usuwa zapisanych danych.

Dane bazy są przechowywane w wolumenie Dockera `mysql_data`. Przy uruchomieniu API tabela `price_history`, jeśli istnieje, jest usuwana wraz z zawartością. Nie używaj `docker compose down -v`, jeśli chcesz zachować pozostałe dane — opcja `-v` usuwa cały wolumen.

## Struktura danych

- `products`: unikalne symbole produktów (np. `EUR/USD`).
- `candles`: kolumny `symbol`, `datetime`, `open`, `high`, `low`, `close`, `volume` oraz `utc_offset_minutes`. `datetime` jest zapisywany w czasie lokalnym `Europe/Warsaw`; offset rozróżnia powtarzającą się godzinę przy zmianie czasu. Klucz główny `(symbol, datetime, utc_offset_minutes)` zapobiega duplikatom, a ponowne pobranie świecy aktualizuje wpis. `volume` jest `NULL`, jeśli dostawca go nie podał.
- `candle_gaps`: osobny wiersz dla każdej wykrytej brakującej świecy. `candle_datetime_utc` oznacza oczekiwany początek świecy w UTC, `status` ma wartość `missing` albo `filled`, a pola `detected_at_utc` i `filled_at_utc` zapisują moment wykrycia i uzupełnienia. Klucz `(symbol, candle_datetime_utc)` zapobiega duplikatom.
- `signals`: `id`, `datetime`, `product`, `price`, `signal`, `algorithm_name`, `ai_decision`, `transaction_id`, `candles_count` oraz `signal_parameters`. `datetime` ma typ `DATETIME(6)` i ma być zapisywany jako lokalny czas `Europe/Warsaw`; `signal` dopuszcza `long` lub `short`; `ai_decision` jest nullable JSON, więc może później przechowywać wartość logiczną albo obiekt JSON; `transaction_id` jest nullable i na razie nie ma klucza obcego, bo tabela transakcji jeszcze nie istnieje; `signal_parameters` przechowuje wymagany dokument JSON. Kolumna `product` wskazuje na istniejący symbol w `products`.
  describe signals;
Tabela `candles` zostanie utworzona automatycznie przy uruchomieniu lokalnego API. Istniejące wpisy, które wcześniej zapisano w UTC, zostaną jednorazowo przeliczone na `Europe/Warsaw`. Po zmianach kodu API lub konfiguracji bazy przebuduj i uruchom usługę poleceniem `docker compose up -d --build`.

Tabela `signals` również jest tworzona automatycznie przy starcie API. Po każdej nowej zamkniętej świecy EUR/USD API uruchamia strategię `Chat-gpt6/EURUSDStrategyV1`; wygenerowany sygnał zapisuje do tej tabeli, a jego pakiet JSON trafia do `signal_parameters`. Integracja z agentem AI nie jest jeszcze podłączona.

Strategia inicjalizuje EMA wartością pierwszego `Close`, a kolejne wartości oblicza ze współczynnikiem `2 / (N + 1)`. RSI/ATR są wygładzane metodą Wildera. Nie znaleziono w repozytorium istniejącego backtestu Python, z którym można porównać obliczenia wskaźników. Do czasu podłączenia BID/ASK bieżący spread oraz jego limit są przyjęte na sztywno jako 3 pipsy, więc warunek spreadu obecnie nie odrzuca sygnału.

Pojedyncza brakująca świeca M1 nie resetuje wskaźników. Brakujące minuty są liczone tylko w godzinach otwarcia Forex; zamknięcie od piątku 17:00 do niedzieli 17:00 czasu nowojorskiego jest pomijane. Więcej niż jedna brakująca minuta handlowa z rzędu resetuje wskaźniki i stan ponawiania sygnałów. Niedokończona agregacja M5 jest odrzucana przy każdej luce, aby nie łączyć świec z przerwą.

Uzupełnianie luk działa od najnowszych wpisów wstecz, pomija weekendowe zamknięcie rynku Forex i obejmuje maksymalnie 1000 świec przy jednym starcie aplikacji. Jeśli baza nie ma jeszcze świec, aplikacja pobiera do 1000 najnowszych świec. Na stronie głównej wyświetlana jest liczba zapisanych świec oraz łączny czas sprawdzenia, pobrania i zapisu. Każde żądanie `/time_series` kosztuje kredyty zgodnie z planem Twelve Data; odległe luki mogą wymagać osobnych żądań.

Lukę zapisujemy od razu przy jej pierwszym wykryciu: podczas skanowania bazy przy starcie aplikacji albo po otrzymaniu świecy, której czas wskazuje na brak wcześniejszych minut. Każda minuta ma osobny, unikalny wpis, więc powtórne wykrycie nie tworzy duplikatu. Po zapisaniu brakującej świecy jej status zmienia się na `filled`, ale rekord pozostaje w tabeli jako historia. Zwykłe zamknięcie rynku od piątku 17:00 do niedzieli 17:00 czasu nowojorskiego nie jest rejestrowane jako luka. Gdy aplikacja wykryje co najmniej jedną lukę, odtworzy dźwięk i pokaże komunikat z nazwą tabeli `candle_gaps`; przy starcie taki komunikat pojawi się także wtedy, gdy automatyczne uzupełnianie zaraz potem wypełni świecę.

Przykład ręcznego sprawdzenia aktywnych luk:

```sql
SELECT symbol, candle_datetime_utc, status, detected_at_utc
FROM candle_gaps
WHERE status = 'missing'
ORDER BY candle_datetime_utc;
```

Przycisk „Raporty” otwiera tabelę 20 najnowszych świec. Przyciski „Poprzednia” i „Następna” pozwalają przechodzić pomiędzy stronami, a „Strona główna” wraca do głównego widoku. Lista jest pobierana z lokalnego API pod adresem `/api/candles/report`; nowe świece są zapisywane przez `/api/candles`.

Istniejące świece zapisane wcześniej w UTC są jednorazowo przeliczane na `Europe/Warsaw`; nowe wpisy również są zapisywane w tym czasie. API i baza są dostępne wyłącznie w lokalnej konfiguracji projektu; port MySQL nie jest wystawiony na sieć hosta. Dalsze zmiany struktury będą wymagały migracji w `src/database/api/app.py` — samo `CREATE TABLE IF NOT EXISTS` nie zmienia już istniejących tabel.
