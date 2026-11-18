# Strategia mikrotransakcji XAU/USD

Strategia analizuje wyłącznie zamknięte świece M1 z tabeli `candles`.
Nie składa zleceń. Wyliczone TP, SL i cena są propozycją, a nie gwarantowaną
ceną wykonania. Cena zapisywanego sygnału jest zaokrąglana do 5 miejsc po
przecinku, a poziomy TP/SL do 2.

## Integracja i uruchamianie

Katalog ma nazwę z myślnikiem, dlatego skrypt główny dodaje swój katalog do
`sys.path` i używa importów płaskich. Wszystkie parametry znajdują się w
`config.py`.

Uruchomienie bez argumentów albo z `--loop` włącza pętlę. Co sekundę sprawdza
ona bazę, czeka maksymalnie 20 sekund na brakującą świecę po jej zamknięciu,
pomija brakującą minutę, zapisuje stan w pliku i kończy pracę po Ctrl+C.
Zmienna `XAUUSD_STATE_FILE` pozwala wskazać inny plik stanu. Przy starcie bez
pliku stanu pętla analizuje najnowszą zamkniętą świecę albo wznawia pracę od
ostatniego zapisu w bazie.

Uruchomienie `python XAUUSDStrategyV1.py --candle-open-epoch <epoch>` analizuje
jedną świecę. Flaga `--api-candle-open-epoch <epoch>` jest wariantem
wywoływanym przez API po zapisaniu świecy. API uruchamia również dotychczasową
strategię `Chat-gpt6/XAUUSDStrategyV1`, więc oba algorytmy działają niezależnie.
Zwrócony przez API licznik nowych zapisów wykorzystuje istniejąca aplikacja
Windows do odtworzenia dźwięku.

Połączenie korzysta z istniejących zmiennych `MYSQL_HOST`, `MYSQL_PORT`,
`MYSQL_DATABASE`, `MYSQL_USER` i `MYSQL_PASSWORD`. `signals.signal_parameters`
zawiera wyłącznie `price`, `signal`, `timestamp`, `instrument` i `reason`.
Setup, poziomy TP/SL i diagnostyka nie są serializowane w tym polu; historia
wybić rozpoznaje nowe rekordy po treści `reason` i nadal obsługuje starszy format
z kluczem `setup`. Epoka zamknięcia świecy jest zapisywana w `source_timestamp`.
Osobne kolumny dla setupu i poziomów TP/SL nie są używane.
Kolumna `signals.datetime` przechowuje naiwny czas zamknięcia świecy
w Warszawie. W odróżnieniu od niej `candles.datetime` oznacza czas otwarcia.

## Czas i harmonogram

`candles.datetime` to naiwny czas otwarcia świecy w `Europe/Warsaw`, a
`utc_offset_minutes` pozwala rozróżnić powtórzoną godzinę jesiennej zmiany
czasu. Świeca jest dopuszczona dopiero, gdy jej otwarcie plus minuta nie
przekracza bieżącego czasu. Obliczenia porównują świadome chwile UTC, a sesje
Londynu i Nowego Jorku używają `zoneinfo`.

Harmonogram pobierania złota jest współdzielony z `market_products.py`:
poniedziałek–czwartek 00:00–17:00 i 18:00–24:00 ET, piątek 00:00–17:00 ET,
sobota zamknięta, niedziela 18:00–24:00 ET. Przerwa rollover trwa 17:00–18:00
ET. Planowane przerwy nie są lukami. Wskaźniki są liczone tylko w bieżącym
ciągłym segmencie; nie ma wypełniania ani interpolowania brakujących świec.

Sygnały są dozwolone od poniedziałku do piątku od 08:15 czasu londyńskiego do
17:00 ET, z piątkowym limitem 21:00 czasu polskiego oraz 30-minutowym buforem
przed przerwą rynku. Pętla nie podejmuje decyzji dla świecy poza sesją.
`SAVE_NONE_SIGNALS=True` oznacza, że także wyniki NONE są zapisywane.

## Setupy

- `RANGE_BREAKOUT`: pierwsze wybicie dziennego zakresu Azji w godzinach
  08:15–11:00 LON; zakres musi być pokryty w co najmniej 80%.
- `TREND_PULLBACK`: zgodny trend M1/M5 i odbicie od EMA21 w oknie londyńskim
  lub nowojorskim, przy ER(30) co najmniej 0,30.
- `MEAN_REVERSION`: skrajne pasmo Bollingera, niski ER(30), RSI(5) i powrót
  ceny w kierunku VWAP.

Wynik setupu zaczyna się od 60 punktów; premie są naliczane wyłącznie po
spełnieniu jego warunków bazowych. Sygnał wymaga co najmniej 70 punktów.
TP/SL są liczone od zamknięcia świecy, a maksymalny czas trzymania wynosi
20 minut. Rzeczywisty spread, poślizg i opłaty nie są pobierane z danych;
używana jest konfigurowalna wartość `ASSUMED_COST_USD`.

## Święta i wydarzenia

Na początku `NO_TRADE_DATES`, `EARLY_CLOSE_DATES`, `FOMC_DATES`,
`HIGH_IMPACT_DAYS` i `NEWS_BLACKOUTS` są puste i można je ręcznie uzupełnić.
Daty `NO_TRADE_DATES` są datami polskimi, a klucze `EARLY_CLOSE_DATES` są
datami nowojorskimi z godziną zamknięcia ET. `NEWS_BLACKOUTS` zawiera wpisy
ISO z polami `start`, `end`, opcjonalnym `timezone` i `code`.

Dodając NFP, CPI, PCE, decyzję lub konferencję FOMC, wystąpienie szefa Fed albo
EBC, wpisz przedział publikacji i bufor do `NEWS_BLACKOUTS` lub oznacz datę w
`HIGH_IMPACT_DAYS`. Godzin nie należy na stałe przeliczać na czas polski:
strefa wpisu i `zoneinfo` uwzględniają różnice w terminach DST.

## Kalibracja i backtest

`python calibrate.py` raportuje percentyle 5/25/50/75/95 ATR (USD i bps)
oraz wolumenu względnego osobno dla sesji LONDON, OVERLAP i NY. Zakres Azji
raportowany jest globalnie dla dni z pokryciem co najmniej 80%. Analiza pomija
świece formowane. Skrypt tylko proponuje ocenę progów — nie zmienia `config.py`.

`python backtest.py` analizuje świece po jednej, bez look-ahead. W pojedynczej
świecy dotknięcie SL i TP oznacza najpierw SL. Pozycja kończy się po 20
minutach albo na końcu segmentu. Raport odejmuje `ASSUMED_COST_USD` i zawiera
wyniki ogólne oraz podziały według setupu, sesji i godziny polskiej.
