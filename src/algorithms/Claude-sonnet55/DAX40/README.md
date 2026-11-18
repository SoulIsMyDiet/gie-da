# Strategia mikrotransakcji GER30 / DAX40

Strategia analizuje wyłącznie zamknięte świece M1 symbolu `GER30` i publikuje je jako
instrument `DAX40`. Wyniki podaje w punktach indeksu i zapisuje w istniejącej tabeli
`signals`; wszystkie parametry znajdują się w `config.py`.

## Sesja i czas

Sesja jest otwarta od poniedziałku do piątku:

- zimą: 01:10–22:00 Europe/Warsaw;
- latem: 02:10–22:00 Europe/Warsaw.

Weekend jest zamknięty. Strefy Europe/Warsaw i America/New_York używają `ZoneInfo`,
więc zmiany czasu są liczone według właściwych kalendarzy DST. Nie jest używany
kalendarz świąt. Znacznik wejściowej świecy oznacza jej początek. Wyjściowe `timestamp`
oznacza zamknięcie minuty (ISO z przesunięciem Warszawy i epoch), a `candle_open_time`
zachowuje czas otwarcia. Wiek danych liczony jest od zamknięcia; świeca formująca
lub starsza niż 3 minuty jest odrzucana.

## Setupy i filtry

- `OPENING_RANGE_BREAKOUT`: 09:15–10:30, wybicie zamknięciem poza zakres
  09:00–09:15 (co najmniej 13 świec), szerokość 15–90 punktów i wymagane
  potwierdzenia wolumenu, VWAP, ukończonego 5M EMA20 oraz odległości.
- `TREND_PULLBACK`: trend EMA 9/21/55, cofnięcie do EMA21 i potwierdzenie
  ukończonego trendu 5M; działa 09:30–11:30 oraz 14:00–17:20, wymaga ER(30) ≥
  0.30 i 100 świec w bieżącym segmencie.
- `RANGE_REVERSION`: skrajne pasmo Bollingera, RSI i odchylenie od VWAP;
  działa 11:00–15:15, wymaga ER(30) ≤ 0.20 i 60 świec w bieżącym segmencie.

Twarde filtry: ATR(14) 2.5–20 punktów, ATR ratio 0.7–2.5, wolumen względny ≥ 0.6,
ostatni zakres świecy ≤ 3 ATR, cel co najmniej 3 × domyślny koszt (1.5 punktu),
10-minutowa przerwa po sygnale i maksymalnie 12 LONG/SHORT dziennie. Okno sygnałów
to dni robocze 09:05–17:25 Warszawa; obowiązują też wyłączenia 09:00–09:04,
17:30–17:35 i trzy publikacje USA 08:30, 09:30, 10:00 czasu Nowego Jorku ±3 minuty. Puste
`NEWS_BLACKOUTS` w `config.py` można uzupełnić krotkami (początek, koniec)
`datetime` w czasie Warszawy lub ISO; wartości domyślne nie blokują publikacji.

Rozgrzewka: ATR/RSI/BB liczone są odpowiednio dla 14/5/20, pullback wymaga 100
ciągłych świec, reversion 60, a ORB 15 minut zakresu. VWAP jest liczony od 09:00
Warszawa z pełnej historii bieżącej sesji i nie resetuje się po luce. EMA używa
rekurencji `ewm(span=n, adjust=False)`. Wskaźniki segmentowe rozpoczynają się od
nowa po brakującej minucie i na nowej sesji. Jeśli wolumen sesji wynosi zero,
VWAP jest niezdefiniowany i nie jest zastępowany średnią arytmetyczną.
Brakujące minuty poza planem handlu nie są lukami.

Wszystkie wskaźniki są resetowane po luce. Luki w zaplanowanym zamknięciu rynku
nie są liczone. Algorytm toleruje jedną brakującą minutę, ale blokuje sygnały przy
więcej niż jednej luce z rzędu albo więcej niż trzech lukach w 60 minutach handlu.
Nie tworzy świec syntetycznych. Koszyki 5M są używane wyłącznie po zamknięciu i
przy co najmniej 4 z 5 świec M1.

Pojedynczy warunek setupu nie wystarcza: filtr zmienności, zakres świecy, minimalny
ruch względem kosztu, scoring, cooldown i limit 12 sygnałów dziennie są sprawdzane
przed zwróceniem LONG/SHORT. Sygnały setupów o przeciwnych kierunkach są odrzucane.
Domyślny koszt wynosi 1.5 punktu, a parametry SL/TP są w punktach.

## Integracja i zapis

Lokalne API wywołuje strategię wyłącznie dla pojedynczej świecy GER30 z endpointu
`POST /api/candles`, po zapisaniu jej do bazy i tylko wtedy, gdy jest zamknięta.
Uzupełnianie historii przez endpoint batch nie uruchamia strategii. Każde
wywołanie odczytuje historię świec i zapisane sygnały z MySQL, co odtwarza stan po
restarcie bez dodatkowej tabeli.

Sygnał oraz pełne parametry JSON są zapisywane w jednym INSERT i transakcji.
`source_timestamp` to epoch UTC czasu otwarcia świecy; unikalny klucz bazy chroni
przed duplikatami. `SAVE_NONE_SIGNALS=False` w `config.py` pomija wyniki NONE,
więc do tabeli trafiają wyłącznie rekordy LONG i SHORT.
Nowy zapis zwraca do C++ istniejący znacznik `signal_saved`; aplikacja Windows
odtwarza dwa krótkie piknięcia dla każdego nowego wpisu LONG/SHORT, nigdy dla
duplikatu. Flaga `BEEP_ENABLED` włącza lub wyłącza to powiadomienie. API pomija
ponowne analizowanie już przetworzonej minuty w trakcie działania procesu.

## Uruchomienie i testy

Wymagany jest Python 3.10+ oraz zależności API z `src/database/api/requirements.txt`.
Konfiguracja MySQL pochodzi ze zmiennych `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`,
`MYSQL_PASSWORD`, `MYSQL_DATABASE`.

```powershell
python DAX40StrategyV1.py
python DAX40StrategyV1.py --loop
python backtest.py
python -m pytest tests -v
```

Bez `--loop` skrypt wykonuje pojedynczą analizę. Opcja `--loop` sprawdza bazę
aż do pojawienia się nowej zamkniętej świecy i wtedy uruchamia analizę.

Backtest ocenia kolejne świece bez dostępu do przyszłości, odejmuje koszt
transakcyjny, rozstrzyga dotknięcie SL i TP w jednej świecy na niekorzyść
(SL pierwszy), nie używa świec syntetycznych i kończy transakcję na końcu
bieżącego segmentu/sesji lub po 20 minutach. Raport zawiera łączną liczbę
sygnałów, win rate, średnią wygraną/przegraną, expectancy, profit factor,
maksymalne obsunięcie oraz podsumowania wg setupu i godziny Warszawy.
