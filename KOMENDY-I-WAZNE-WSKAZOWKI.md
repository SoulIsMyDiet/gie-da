# Komendy i wskazówki do projektu

Ten plik zawiera najczęściej potrzebne komendy do uruchamiania projektu i pracy z bazą MySQL.
Polecenia PowerShell uruchamiaj w głównym folderze repozytorium — tam, gdzie znajduje się
`docker-compose.yml`.

## MySQL w Dockerze

### Uruchomienie bazy i API

Uruchom Docker Desktop, a następnie w PowerShellu wpisz:

```powershell
docker compose up -d
```

Sprawdź, czy kontenery działają:

```powershell
docker compose ps
```

### Otwarcie konsoli MySQL

```powershell
docker compose exec mysql mysql -u gie_da_app -p gie_da
```

Po wyświetleniu `Enter password:` wpisz wartość `MYSQL_PASSWORD` z pliku `.env`.
Podczas wpisywania hasło nie będzie widoczne.

Po połączeniu zobaczysz znak zachęty `mysql>`. Wpisuj tam komendy SQL i kończ je
średnikiem `;`.

### Podstawowe komendy SQL

Lista tabel:

```sql
SHOW TABLES;
```

Struktura tabel:

```sql
DESCRIBE products;
DESCRIBE price_history;
```

Wszystkie produkty:

```sql
SELECT * FROM products;
```

Najnowsze wpisy historii cen:

```sql
SELECT id, symbol, price, recorded_at, zlote_rady
FROM price_history
ORDER BY recorded_at DESC, id DESC
LIMIT 50;
```

Policz wpisy w historii:

```sql
SELECT COUNT(*) AS liczba_wpisow
FROM price_history;
```

Pokaż wpisy dla wybranego symbolu:

```sql
SELECT id, symbol, price, recorded_at, zlote_rady
FROM price_history
WHERE symbol = 'EUR/USD'
ORDER BY recorded_at DESC, id DESC
LIMIT 50;
```

Wyjdź z konsoli MySQL:

```sql
exit
```

### Przydatne komendy Docker Compose

Logi API:

```powershell
docker compose logs -f price-history-api
```

Logi MySQL:

```powershell
docker compose logs -f mysql
```

Sprawdzenie API:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

Zatrzymanie kontenerów bez usuwania danych:

```powershell
docker compose stop
```

Ponowne uruchomienie zatrzymanych kontenerów:

```powershell
docker compose start
```

## Ważne informacje o danych

- Historia cen jest przechowywana w wolumenie Dockera `mysql_data`, więc zwykłe
  zatrzymanie kontenerów jej nie usuwa.
- **Nie używaj `docker compose down -v`, jeśli chcesz zachować dane.** Opcja `-v`
  usuwa wolumen z bazą.
- Plik `.env` zawiera hasła. Nie udostępniaj go i nie dodawaj do repozytorium.
- Nowe wpisy `recorded_at` są zapisywane w czasie `Europe/Warsaw`. Wiersze zapisane
  przed zmianą formatu czasu pozostają bez zmian, więc w bazie mogą być wpisy o
  różnej interpretacji czasu.
- Kolumna `zlote_rady` zawiera obecnie losowy rzeczownik.

## Projekt i kompilacja

Zbuduj aplikację w istniejącym katalogu kompilacji:

```powershell
cmake --build cmake-build-debug
```

Jeśli PowerShell nie rozpoznaje polecenia `cmake`, uruchom kompilację przez przycisk
Build w CLion.

Kod API i konfiguracja bazy znajdują się w `src/database`. Tabela jest przygotowywana
przy uruchomieniu API, a jej zmiany należy wprowadzać ostrożnie — nie usuwaj wolumenu
MySQL przy zmianie kodu.
