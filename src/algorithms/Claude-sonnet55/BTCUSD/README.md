# Strategia mikrotransakcji Claude-sonnet55 BTC/USD

Strategia analizuje zamknięte świece M1 BTC/USD, korzysta z M5 do potwierdzenia
trendu i zwraca kandydata `LONG`, `SHORT` albo `NONE`. Nie składa zleceń i nie
zarządza pozycjami. `tp_price`, `sl_price` oraz `max_hold_minutes` są wyłącznie
parametrami analizy i backtestu, nie zleceniami ani gwarantowanymi cenami.

## Dane i czas

Aktywny przepływ aplikacji zapisuje BTC/USD do `candles_IBKR` z symbolem
`BTC/USD`; strategia jest uruchamiana po zapisaniu zamkniętej świecy przez
lokalne API. Nie korzysta z tabeli legacy `candles`. Nie dodano tabel ani nie
zmieniono schematu. Zapis sygnału i trwały marker ewaluacji używają istniejącego
repozytorium i unikalnego identyfikatora produktu, algorytmu oraz otwarcia
świecy.

`candles.datetime` jest lokalnym `DATETIME(6)` Warszawy, a
`utc_offset_minutes` pozwala jednoznacznie odtworzyć UTC, także przy jesiennym
cofnięciu zegara. Czas wyniku `timestamp` to epoch sekundy zamknięcia świecy;
`candle_open_time` i `candle_close_time` są ISO-8601 z offsetem Warszawy.
Świece, których otwarcie plus minuta jest późniejsze niż czas analizy, nie są
używane. Dane bez jednoznacznego offsetu w powtórzonej godzinie są odrzucane.

Produkt BTC/USD ma profil 24/7, więc weekend ani różnica timestampów nie
oznaczają zaplanowanej przerwy. Wskaźniki są liczone tylko na prawdziwych
świecach. Pojedyncza brakująca minuta nie tworzy sztucznej świecy ani nie
resetuje segmentu. Dwie lub więcej kolejnych brakujących minut rozpoczynają
nowy segment M1/M5 i wymagają ponownego rozgrzania. M5 powstaje wyłącznie z
pięciu świec M1 wyrównanych do granic UTC i kompletnych w chwili oceny.

## Logika strategii

Używane wskaźniki i filtry obejmują EMA9/21/55, EMA20 M5, RSI5, ATR14,
MACD(12,26,9), Bollinger Bands, percentyl ich szerokości, efficiency ratio,
VWAP, Donchian, rozpiętość świecy oraz sesje UTC. Setupy:

| Setup | Zasada |
|---|---|
| `SQUEEZE_BREAKOUT` | Wybicie maksimum/minimum z 20 poprzednich świec po ściśnięciu zmienności, z potwierdzeniem świecy i MACD. |
| `TREND_PULLBACK` | Powrót do EMA w zgodnym układzie M1/M5, następnie kierunkowe zamknięcie i poprawa RSI/MACD. |
| `SWEEP_REVERSION` | Naruszenie ekstremum 20 świec, zamknięcie z powrotem w zakresie oraz kierunkowy knot i RSI. |

Sesje są klasyfikowane w UTC: ASIA 00:00–07:00, EUROPE 07:00–13:00,
OVERLAP 13:00–17:00, US 17:00–21:00 i LATE 21:00–24:00. Otwarcie kasowe,
publikacje danych USA i FOMC są liczone w `America/New_York`; konwersja
`zoneinfo` obsługuje tygodnie różnicy DST USA/UE. Blokady publikacji danych i
FOMC wymagają dopisania konkretnych dat do `config.py`; nie ma bezpłatnego
kalendarza wydarzeń ani automatycznego pobierania danych makro. Weekend i
sesje ASIA/LATE podnoszą minimalny próg score zamiast blokować sygnały.

Nie ma wiarygodnych danych BID/ASK ani potwierdzonej jednostki wolumenu IBKR.
Dlatego `VOLUME_RELIABLE = False`, a wolumen nie wpływa na sygnały. Nie jest
tworzony sztuczny spread. `ASSUMED_COST_BPS = 5.0` to założenie kosztu
wejścia i wyjścia, używane wyłącznie do filtra opłacalności i backtestu.
`PRICE_DECIMALS = 2` jest konfigurowalnym założeniem formatu BTC/USD.

Wynik pełny trafia na stdout lub do logiki lokalnej aplikacji. Baza otrzymuje
kompaktowy JSON: zawsze pięć pierwszych pól `price`, `signal`, `timestamp`,
`instrument`, `reason`; dla `LONG`/`SHORT` także wybrane płaskie pola z
`SIGNAL_PARAMS_EXTRA_KEYS`. JSON jest ograniczony do 512 bajtów UTF-8.
`SAVE_NONE_SIGNALS = False` pomija rekordy NONE, ale API zapisuje marker
ewaluacji, aby ta sama świeca nie była wielokrotnie analizowana.

## Uruchamianie

Python 3.10+ oraz zależności API z `src/database/api/requirements.txt` są
wymagane. Ustawienia MySQL w samodzielnym trybie pochodzą ze zmiennych
`MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD` i `MYSQL_DATABASE`.
Nie umieszczaj haseł w plikach strategii.

W katalogu `src/algorithms/Claude-sonnet55/BTCUSD`:

```powershell
python BTCUSDStrategyV1.py
python BTCUSDStrategyV1.py --loop
python backtest.py --end-epoch 1794841200 --limit 5000
python calibrate.py --end-epoch 1794841200 --limit 5000
python -m pytest tests -q
```

Uruchomienie bez argumentów analizuje najnowszą zamkniętą świecę. `--loop`
sprawdza każdą minutę, czeka do 20 sekund na brakującą świecę, a potem zapisuje
checkpoint i pomija tę minutę. Stan pętli znajduje się w
`BTCUSD_CLAUDE_STATE_FILE` albo domyślnie w katalogu domowym. Nie uruchamiaj
samodzielnej pętli równolegle z automatycznym wyzwalaniem API.

Backtest analizuje świece chronologicznie, używając do każdej decyzji wyłącznie
danych dostępnych do jej zamknięcia. TP i SL osiągnięte w tej samej świecy są
rozstrzygane na korzyść SL; przy luce lub niepełnym horyzoncie wynik pozostaje
nierozstrzygnięty. Kalibracja dzieli historię chronologicznie, raportuje próg
score i nigdy nie modyfikuje automatycznie konfiguracji.

## Ograniczenia

Kalendarz wydarzeń USA jest ręczny; brak dat oznacza brak blokady dla
konkretnych publikacji makro lub FOMC. Nieznana jednostka wolumenu wyłącza
filtry wolumenowe. Koszt 5 bps, precyzja 2 miejsca oraz potraktowanie
instrumentu jako spotu są założeniami wymagającymi potwierdzenia. Sygnały nie
uwzględniają spreadu, płynności rzeczywistej, funding ani wykonania zlecenia.
