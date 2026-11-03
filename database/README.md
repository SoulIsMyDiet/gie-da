# Historia cen produktów w MySQL

Projekt używa MySQL uruchomionego w Dockerze. Aplikacja C++ wysyła pobrane ceny do lokalnego API, a API zapisuje je w tabeli `price_history`. Tabela `products` pozwala później dodawać kolejne symbole bez zmiany struktury historii.

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
6. Uruchom aplikację giełdy w CLion. Każda nowa cena będzie zapisywana z datą i czasem lokalnym dla strefy `Europe/Warsaw`. Przy braku połączenia z bazą aplikacja nadal pokaże pobrany kurs, a w statusie pojawi się informacja o błędzie zapisu.

## Codzienna obsługa

- Start po ponownym uruchomieniu komputera: uruchom Docker Desktop, a następnie wykonaj `docker compose up -d` w folderze projektu.
- Podgląd działania usług: `docker compose ps`.
- Logi lokalnego API: `docker compose logs -f price-history-api`.
- Zatrzymanie usług: `docker compose stop`. Zatrzymanie nie usuwa zapisanych danych.

Dane bazy są przechowywane w wolumenie Dockera `mysql_data`. Nie używaj `docker compose down -v`, jeśli chcesz zachować historię — opcja `-v` usuwa wolumen z danymi.

## Struktura danych

- `products`: unikalne symbole produktów (np. `EUR/USD`).
- `price_history`: osobny wiersz dla każdego odczytu, z ceną w typie dziesiętnym i czasem lokalnym `Europe/Warsaw`.

Wpisy zapisane przed tą zmianą pozostają w UTC i nie są modyfikowane; nowe wpisy będą w czasie `Europe/Warsaw`. Oznacza to, że w tej samej kolumnie starsze i nowe rekordy mają różną interpretację czasu. API i baza są dostępne wyłącznie w lokalnej konfiguracji projektu; port MySQL nie jest wystawiony na sieć hosta. Dalsze zmiany struktury będą wymagały migracji w `database/api/app.py` — samo `CREATE TABLE IF NOT EXISTS` nie zmienia już istniejących tabel.
