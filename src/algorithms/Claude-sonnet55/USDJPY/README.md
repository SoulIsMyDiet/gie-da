# Strategia mikrotransakcji USD/JPY

Strategia analizuje wyłącznie zamknięte świece M1 z tabeli `candles`. Nie
składa zleceń: poziomy TP i SL są rekomendacją dla osobnego modułu wykonującego.
Nie korzysta z danych bid/ask ani z zewnętrznego kalendarza.

## Uruchamianie

W katalogu projektu uruchom usługi MySQL i API:

```powershell
docker compose up -d
```

Port MySQL jest publikowany wyłącznie pod `127.0.0.1:3306`. Skrypt Windows
czyta `MYSQL_DATABASE`, `MYSQL_USER` i `MYSQL_PASSWORD` z procesu lub z
lokalnego, nieśledzonego pliku `.env`; `MYSQL_HOST` domyślnie wskazuje
`127.0.0.1`. Nie umieszczaj haseł w repozytorium ani w poleceniach, które mogą
trafić do historii powłoki.

Zainstaluj zależności projektu, jeśli nie są dostępne w używanym Pythonie:

```powershell
python -m pip install -r src/database/api/requirements.txt
```

`python src/algorithms/Claude-sonnet55/USDJPY/USDJPYStrategyV1.py` wykonuje
jedną analizę najnowszej zamkniętej świecy i wypisuje JSON na stdout.
Jeśli tabela `candles` nie zawiera jeszcze świecy USD/JPY, tryb jednorazowy
zapisuje ostrzeżenie i kończy się kodem 2; nie tworzy fikcyjnego sygnału bez
ceny i czasu zamknięcia świecy.
`python src/algorithms/Claude-sonnet55/USDJPY/USDJPYStrategyV1.py --loop`
uruchamia pętlę; `python src/algorithms/Claude-sonnet55/USDJPY/trigger.py`
uruchamia tę samą pętlę domyślnie. Zatrzymanie odbywa się przez Ctrl+C.
Zmienna `USDJPY_STATE_FILE` pozwala wskazać inny plik stanu; dźwięk można
wyłączyć przez `BEEP_ENABLED = False` w `config.py`. Główna konfiguracja
progów jest w `config.py`.

Alternatywa do pętli: po zatwierdzonym zapisie świecy C++ może uruchomić
`USDJPYStrategyV1.py --candle-open-epoch <epoch_otwarcia_UTC>`. Proces sprawdzi
zamknięcie i idempotentność, wykona pojedynczą analizę i zakończy się. Ten
wariant jest używany przez API z argumentem
`--api-candle-open-epoch <epoch_otwarcia_UTC>` albo
`--api-candle-open-epochs <epoki...>` dla paczki. Zapisuje wyniki bez lokalnego
dźwięku, a API przekazuje do klienta dźwięk wyłącznie dla LONG/SHORT. Nie należy
równolegle uruchamiać osobnej pętli `--loop`, jeśli API obsługuje te same świece.

## Czas, rynek i dane

`candles.datetime` to otwarcie świecy jako naiwny czas `Europe/Warsaw`;
`utc_offset_minutes` odtwarza jednoznaczny czas UTC także w tygodniach zmiany
DST. Wejściowe chwile są przeliczane do UTC, a świeca jest dopuszczona dopiero,
gdy `otwarcie + 1 minuta <= teraz`. Wynik używa epoki UTC czasu zamknięcia oraz
`candle_open_time` w ISO-8601 z warszawskim offsetem.

Kod monitora i profilu API opisuje Forex jako czynny od niedzieli 17:00 do
piątku 17:00 czasu Nowego Jorku, bez dziennej przerwy rollover i bez kalendarza
świąt. Dlatego harmonogram nie uznaje codziennego blackout rollover za lukę:
wskaźniki resetują się dopiero na granicy tygodniowego segmentu. Rollover od
16:55 ET jest blokadą sygnału, a nie przerwą pobierania. Święta i skrócone
sesje można wpisać ręcznie do `NO_TRADE_DATES` i `EARLY_CLOSE_DATES`.

Sesje Tokio, Londynu i Nowego Jorku oraz fixing i okna danych są budowane w ich
własnych strefach `zoneinfo`; nie są kodowane na stałe jako godziny polskie.
Testy obejmują tygodnie rozjazdu DST USA, UE i UK oraz brak DST w Japonii.
Brakujące minuty są liczone tylko w minutach handlowych, bez sztucznych świec.
Wskaźniki liczone są na bieżącym tygodniowym segmencie; pierwsza świeca ma
`TR = high - low`. Kubełki M5 zawierają co najmniej cztery rzeczywiste świece
i są używane dopiero po zamknięciu całego kubełka.

### Wydarzenia i ograniczenia danych

Stałe okna otwarcia Tokio i Londynu, fixingu tokijskiego, publikacji USA o
08:30/10:00 ET i rollover są kodowane w `schedule.py`. FOMC, BoJ, święta,
skrócone sesje i dodatkowe publikacje wymagają ręcznego uzupełnienia w
`FOMC_DATES`, `HIGH_IMPACT_DAYS`, `NO_TRADE_DATES`, `EARLY_CLOSE_DATES` lub
`NEWS_BLACKOUTS` w `config.py`. Lista jest domyślnie pusta, bo projekt nie ma
źródła kalendarza. W README i konfiguracji opisano dodawanie NFP, CPI, PCE,
FOMC, BoJ, wystąpień Fed oraz zdarzeń MoF/BoJ.

Korelacji z rentownościami USA nie można obliczyć z samych świec USD/JPY.
Wynik diagnostyczny jawnie zwraca `yield_correlation_available: false`;
strategia nie pobiera dodatkowych danych i nie udaje, że ten filtr działa.
Ryzyko gwałtownego ruchu jest ograniczane wyłącznie heurystyką zakresu świecy
i `range_spike_ratio`; nie jest to potwierdzenie interwencji MoF.

## Setupy i ryzyko

- `RANGE_BREAKOUT`: pierwsze wybicie zakresu 09:00–12:00 JST w oknie
  08:10–11:00 czasu londyńskiego; zakres wymaga co najmniej 80% pokrycia.
- `TREND_PULLBACK`: trend EMA M1/M5 i odbicie od EMA21 w zadanych oknach
  Tokio/Nowego Jorku przy ER(30) co najmniej 0,30.
- `MEAN_REVERSION`: powrót do wnętrza wstęg przy niskim ER i ruchu w stronę
  VWAP; nie działa w skonfigurowanym oknie newsowym.

Sygnał wymaga wyniku co najmniej 70 punktów. ATR, jego relacja do mediany,
wolumen tickowy, luki, cooldowny, limit dzienny i skok zakresu są filtrami
konfigurowalnymi. Pip wynosi `0.01`; cena zapisywanego sygnału jest zaokrąglana
do 5 miejsc, poziomy TP/SL do 3, a wskaźniki do 5. TP bazuje na 2 ATR, SL na
1.5 ATR, a pozycja nie może trwać dłużej niż 20 minut.

Pętla analizuje każdą nową zamkniętą świecę i czeka maksymalnie 20 sekund na
jej zapis. Brakującą minutę loguje i pomija; nie generuje dla niej sygnału na
starych danych. Wyniki `NONE`, w tym `outside_session`, są tymczasowo
zapisywane przez `SAVE_NONE_SIGNALS=True`. Poza rzeczywistymi godzinami rynku
pętla nie analizuje świec.

## Zapis i kodowanie

Strategia używa istniejących kolumn `signals`: `datetime` przechowuje zamknięcie
świecy w naiwnym czasie Warszawy, `product` ma wartość `USD/JPY`, `price`,
`signal`, `algorithm_name`, `candles_count`, `signal_parameters` i
`source_timestamp` otrzymują odpowiadające im dane. Epoka zamknięcia w
`source_timestamp` i istniejący klucz unikalny `(product, algorithm_name,
source_timestamp)` chronią przed duplikatami. Pozostałe wartości, m.in. setup,
score, TP/SL i wskaźniki nie są zapisywane w `signal_parameters`; pole zawiera
wyłącznie `price`, `signal`, `timestamp`, `instrument` i `reason`. Historia
wybić rozpoznaje nowe wpisy po `reason` i nadal obsługuje starsze wpisy z kluczem
`setup`. Nie jest potrzebna migracja tabeli.

JSON jest serializowany z `ensure_ascii=False`, bez `NaN` i `inf`, a połączenie
MySQL używa `utf8mb4`. Kolejność wymaganych kluczy jest zachowana w serializacji
przed zapisem i w wyniku stdout; typ MySQL `JSON` może ją znormalizować przy
odczycie, zgodnie z zaakceptowanym pozostawieniem obecnego typu kolumny.
Po potwierdzonym commicie Windows odtwarza dwa krótkie piknięcia. Błąd dźwięku
jest logowany i nie cofa zapisu.

## Backtest i kalibracja

`python backtest.py` analizuje dane świeca po świecy, przekazując algorytmowi
tylko historię dostępną w danym momencie. Przy jednoczesnym dotknięciu TP i SL
pierwszy jest SL; wynik odejmuje koszt `ASSUMED_COST_PIPS`. Pozycja kończy się
po maksymalnie 20 minutach lub na końcu segmentu. Raport obejmuje liczbę
sygnałów, win rate, średnią wygraną/przegraną, expectancy, profit factor,
max drawdown oraz podziały po setupie, sesji i godzinie polskiej.

`python calibrate.py` raportuje percentyle 5/25/50/75/95 ATR,
`range_spike_ratio` i wolumenu tickowego osobno dla sesji
TOKYO/LONDON/OVERLAP/NY; rozkład zakresu Tokio znajduje się pod TOKYO oraz w
osobnym podsumowaniu `TOKYO_RANGE_PIPS`.
Pokazuje propozycje korekt `ATR_MIN_PIPS`, `ATR_MAX_PIPS` i `RANGE_MIN_PIPS`/
`RANGE_MAX_PIPS`; niczego samodzielnie nie zmienia.
