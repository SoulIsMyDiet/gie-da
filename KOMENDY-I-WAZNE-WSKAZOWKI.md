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

### Przebudowanie kontenerów po zmianach w bazie danych
```
docker compose up -d --build
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
DESCRIBE candles;
DESCRIBE candle_gaps;
```

Wszystkie produkty:

```sql
SELECT * FROM products;
```

Aktywne luki świecowe:

```sql
SELECT symbol, candle_datetime_utc, status, detected_at_utc
FROM candle_gaps
WHERE status = 'missing'
ORDER BY candle_datetime_utc;
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

- Przy uruchomieniu API tabela `price_history`, jeśli istnieje, jest usuwana wraz z zawartością.
- Świece są przechowywane w wolumenie Dockera `mysql_data`, więc zwykłe
  zatrzymanie kontenerów ich nie usuwa.
- **Nie używaj `docker compose down -v`, jeśli chcesz zachować dane.** Opcja `-v`
  usuwa wolumen z bazą.
- Plik `.env` zawiera hasła. Nie udostępniaj go i nie dodawaj do repozytorium.

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

Przy uruchomieniu aplikacja giełdy sprawdza luki w świecach EUR/USD i uzupełnia do
1000 najnowszych. Wynik i czas operacji są widoczne na ekranie głównym. Zmiany w API
wymagają `docker compose up -d --build`, a zmiany aplikacji — ponownej kompilacji.

Tabela `candle_gaps` zapisuje każdą wykrytą brakującą minutę. Po zapisaniu świecy jej
status zmienia się z `missing` na `filled`, a wpis pozostaje jako historia. Przy
wykryciu luki aplikacja odtwarza dźwięk i pokazuje komunikat, który wskazuje tę tabelę.
Podgląd aktywnych i uzupełnionych wpisów otworzysz przyciskiem „Luki świec” obok
przycisku „Raporty”. Przycisk „Sygnały” otwiera podgląd tabeli signals. API nie zapisuje świec przypadających na weekendowe zamknięcie
Forex (piątek 17:00 – niedziela 17:00 czasu Nowego Jorku), ani przy pobieraniu co
minutę, ani podczas nadrabiania historii.
