# Historia cen produktów w MySQL

Projekt używa MySQL uruchomionego w Dockerze. Starsze odczyty cen pozostają w tabeli `price_history`, a pobrane świece są przechowywane osobno w tabeli `candles`.

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
6. Uruchom aplikację giełdy w CLion. Aplikacja pobiera jedną świecę 1-minutową EUR/USD z Twelve Data po zamknięciu minuty i zapisuje ją w tabeli `candles`. Ponawianie pobrania jest na razie wyłączone, aby oszczędzać limit API. Nie zapisuje nowych odczytów cen do `price_history`.

## Codzienna obsługa

- Start po ponownym uruchomieniu komputera: uruchom Docker Desktop, a następnie wykonaj `docker compose up -d` w folderze projektu.
- Podgląd działania usług: `docker compose ps`.
- Logi lokalnego API: `docker compose logs -f price-history-api`.
- Zatrzymanie usług: `docker compose stop`. Zatrzymanie nie usuwa zapisanych danych.

Dane bazy są przechowywane w wolumenie Dockera `mysql_data`. Nie używaj `docker compose down -v`, jeśli chcesz zachować historię — opcja `-v` usuwa wolumen z danymi.

## Struktura danych

- `products`: unikalne symbole produktów (np. `EUR/USD`).
- `price_history`: osobny wiersz dla każdego odczytu, z ceną w typie dziesiętnym, czasem lokalnym `Europe/Warsaw` oraz kolumną `zlote_rady` z losowym rzeczownikiem.
- `candles`: kolumny `symbol`, `datetime`, `open`, `high`, `low`, `close`, `volume` oraz `utc_offset_minutes`. `datetime` jest zapisywany w czasie lokalnym `Europe/Warsaw`; offset rozróżnia powtarzającą się godzinę przy zmianie czasu. Klucz główny `(symbol, datetime, utc_offset_minutes)` zapobiega duplikatom, a ponowne pobranie świecy aktualizuje wpis. `volume` jest `NULL`, jeśli dostawca go nie podał.

Tabela `candles` zostanie utworzona automatycznie przy uruchomieniu lokalnego API. Istniejące wpisy, które wcześniej zapisano w UTC, zostaną jednorazowo przeliczone na `Europe/Warsaw`. Po zmianach kodu API lub konfiguracji bazy przebuduj i uruchom usługę poleceniem `docker compose up -d --build`.

Przy uruchomieniu API istniejąca tabela zostanie uzupełniona o `zlote_rady`, a każdy wiersz bez wartości otrzyma losowy rzeczownik. Nowe wpisy również otrzymują rzeczownik automatycznie.

Przycisk „Raporty” otwiera tabelę 20 najnowszych świec. Przyciski „Poprzednia” i „Następna” pozwalają przechodzić pomiędzy stronami, a „Strona główna” wraca do głównego widoku. Lista jest pobierana z lokalnego API pod adresem `/api/candles/report`; nowe świece są zapisywane przez `/api/candles`.

Wpisy zapisane przed tą zmianą pozostają w UTC i nie są modyfikowane; nowe wpisy będą w czasie `Europe/Warsaw`. Oznacza to, że w tej samej kolumnie starsze i nowe rekordy mają różną interpretację czasu. API i baza są dostępne wyłącznie w lokalnej konfiguracji projektu; port MySQL nie jest wystawiony na sieć hosta. Dalsze zmiany struktury będą wymagały migracji w `src/database/api/app.py` — samo `CREATE TABLE IF NOT EXISTS` nie zmienia już istniejących tabel.
