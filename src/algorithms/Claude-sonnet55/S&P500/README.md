# Strategia mikrotransakcji S&P 500

Strategia analizuje wyłącznie zamknięte świece M1 kontraktu futures `ES`.
Ceny, ATR, TP, SL i przyjęty koszt są wyrażone w punktach indeksu. Brak danych
bid/ask: koszt 0,6 pkt jest założeniem, nie pomiarem spreadu. Strategia nie jest
gwarancją zysku; progi wymagają kalibracji oraz sprawdzenia na historii i danych
poza próbą. W chwili implementacji w bazie nie było świec `ES`.

## Sesja i czas

Znacznik `candles.datetime` to otwarcie świecy jako lokalny `DATETIME(6)`
Warszawy bez strefy; `utc_offset_minutes` rozróżnia powtórzoną godzinę przy
zmianie czasu. Strategia odtwarza UTC z offsetu i analizuje tylko świece,
których otwarcie plus minuta nie jest późniejsze od bieżącego czasu.
`timestamp` w wyniku oznacza zamknięcie, `candle_open_time` otwarcie; oba
czasy są w ISO-8601 z offsetem Warszawy i mają odpowiedniki epoch.

Zgodnie z aktualną konfiguracją pobierania w API ES handluje w poniedziałek–czwartek
przed 17:00 i od 18:00 ET, w piątek przed 17:00 ET, a w niedzielę od 17:00 ET.
Sobota jest zamknięta. Przerwa 17:00–18:00 ET zwykle przypada 23:00–00:00
w Warszawie, a w tygodniach rozbieżnych zmian czasu USA i Polski 22:00–23:00.
Segment zaczyna się w niedzielę o 17:00 ET oraz w poniedziałek–czwartek o
18:00 ET i nie resetuje się o północy ani z powodu brakującej świecy.
To harmonogram aplikacji, nie potwierdzony kalendarz brokera.

Sesja kasowa trwa 09:30–16:00 czasu `America/New_York`; konwersja
`zoneinfo` uwzględnia DST obu krajów. Jej początek to zwykle 15:30 Warszawa,
a w tygodniach rozjazdu czasu letniego 14:30. W dni skrócone, wymienione w
`EARLY_CLOSE_DATES`, zamknięcie to 13:00 ET. `NO_TRADE_DATES`,
`EARLY_CLOSE_DATES` i `HIGH_IMPACT_DAYS` są początkowo puste: trzeba je
uzupełniać ręcznie. Bez aktualizacji algorytm nie rozpozna świąt ani
skróconych sesji. Te listy nie zmieniają harmonogramu pobierania C++.

Okno sygnałów to dni robocze 09:40–15:50 ET, a w dzień skrócony do 12:50 ET.
Setupy mają dodatkowe okna:

| Setup | Okno ET | Minimalna rozgrzewka segmentu |
|---|---|---|
| `OPENING_RANGE_BREAKOUT` | 09:45–11:00 | 15 świec |
| `TREND_PULLBACK` | 10:00–11:45 i 13:30–15:45 | 60 świec |
| `VWAP_REVERSION` | 11:30–14:00 | 45 świec |

Oprócz rozgrzewki setup wymaga dostępności wszystkich używanych wskaźników;
EMA20 M5 potrzebuje odpowiednio długiej historii kompletnych kubełków. Świece
przed 09:30 w tym samym segmencie mogą rozgrzewać wskaźniki, ale VWAP i zakres
otwarcia liczone są od 09:30 ET. Brak wartości lub zerowy wolumen nie jest
zastępowany wymyślonymi danymi.
Pierwsze wybicie ORB może zostać potwierdzone po zamknięciu świecy o 09:46 ET:
świeca zamknięta o 09:45 należy jeszcze do zakresu otwarcia i nie może
zamknąć się poza własnym maksimum lub minimum.

W `NEWS_BLACKOUTS` można dodać świadome strefy przedziały dat, np. przed FOMC
13:55–14:45 ET. Należy też uzupełniać zdarzenia CPI, NFP, PCE i wystąpienia
szefa Fed. Wbudowane blokady i ich parametry znajdują się w `config.py`;
`HIGH_IMPACT_DAYS` jest listą informacyjną, a nie automatycznym kalendarzem.

## Integracja i zapis

Lokalne API, analogicznie do DAX, uruchamia strategię w osobnym procesie Pythona
po zapisie pojedynczej zamkniętej świecy ES. Nie uruchamia jej dla paczek
backfillu. Wynik jest zapisywany w `signals` bez zmiany schematu: `product=ES`,
`datetime` to lokalny czas zamknięcia Warszawy bez strefy, `source_timestamp`
to epoch UTC otwarcia. Unikalny klucz `(product, algorithm_name, source_timestamp)`
chroni przed duplikatem. Pełny wynik i `params` trafiają do
`signal_parameters` JSON.

`SAVE_NONE_SIGNALS = True` tymczasowo zapisuje wyniki NONE w oknie sygnałów;
poza oknem API nie uruchamia analizy. Przełączenie flagi na `False` wyłącza
zapis NONE. Po udanym zapisie C++ odtwarza dwa dźwięki, także dla NONE przy
włączonej fladze; duplikat jest cichy. `BEEP_ENABLED` pozwala wyłączyć dźwięk.
LONG/SHORT tworzy również wiersz `signal_potention` z kosztem z konfiguracji
w tej samej transakcji. Raport potencjału nie jest backtestem: nie symuluje
SL/TP, a jego wyliczenie `100zl_deal` nie uwzględnia specyfikacji futures.

**Ograniczenie wybranego wyzwalania API:** bez osobnej pętli aplikacja nie
wykryje braku świecy po 20 sekundach, nie przeanalizuje automatycznie świecy
zapisanej jako formowana po jej późniejszym zamknięciu ani świecy uzupełnionej
w backfillu. Błąd strategii po zapisie świecy wymaga osobnego wznowienia;
ponowny zapis pojedynczej zamkniętej świecy może ponowić próbę analizy.
Jeśli wynik jest już w bazie, unikalny klucz nie dopuszcza drugiego rekordu.
Ręczny tryb `--loop` odczytuje bazę co
sekundę, czeka maksymalnie 20 sekund po zamknięciu i pomija brakującą minutę.
Stan ostatnio obsłużonej minuty jest utrwalany w pliku z `config.py`;
przed analizą pojedynczej świecy pętla sprawdza też, czy jej wynik jest już
zapisany w bazie. Bez pliku stanu zaczyna od najnowszej zamkniętej minuty.
Nie uruchamiaj jej równolegle z automatyczną integracją
API, jeśli nie chcesz wykonywać zbędnej podwójnej analizy. Unikalny klucz
gwarantuje najwyżej jeden **zapis**, nie jednokrotne wykonanie obliczeń przez
równoległe procesy. Przy `SAVE_NONE_SIGNALS = False` baza nie zawiera śladu
analizy NONE; plik stanu pętli nie obejmuje wywołań przez API.
Po błędzie MySQL ręczna pętla zapisuje komunikat i ponawia odczyt bez
przesuwania stanu. Po odzyskaniu połączenia sprawdza też dostępność świecy,
której czas oczekiwania już upłynął; brakującą minutę pomija.
Niepoprawny plik stanu, nieprawidłowe dane i błędy zapisu pliku kończą program
z komunikatem zamiast zgadywania stanu. Dla świecy formowanej lub nieobecnej
wynik diagnostyczny zawiera jej nominalne czasy, ale `price` wynosi `null`.

Odczyt historii wykorzystuje indeks `(symbol, datetime, utc_offset_minutes)`
i porządkuje wyniki według UTC, również przy cofnięciu zegara. Ponieważ zakres
kolumny offsetu dopuszcza wartości większe niż faktyczne przesunięcia Warszawy,
odczyt może wymagać przejrzenia ponad 22 dni danych albo całej dostępnej
historii, gdy świec jest za mało.

## Uruchamianie

Python 3.10+ wymaga `mysql-connector-python`, `numpy`, `pandas` oraz na
Windows, jeżeli system nie udostępnia bazy IANA, pakietu `tzdata`.
Połączenie korzysta ze zmiennych `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`,
`MYSQL_PASSWORD`, `MYSQL_DATABASE`. Baza z projektu Docker nie udostępnia portu
MySQL hostowi, więc ręczny skrypt uruchamiany na Windows wymaga osobnego
dostępu do tej bazy. Nie wpisuj haseł do kodu.

W katalogu strategii:

```powershell
python "S&P500StrategyV1.py"
python "S&P500StrategyV1.py" --loop
python -m pytest tests -q
```

Pierwsze wywołanie ocenia ostatnią zamkniętą świecę w bazie, także jeżeli jest
opóźniona; stara świeca jest blokowana filtrem `stale_data`. Wyjście to JSON.
Przykład C++ dla procesu, którego katalog roboczy jest katalogiem strategii:

```cpp
std::system("python \"S&P500StrategyV1.py\"");
```

Produkcyjna integracja API nie używa `std::system`: przekazuje epoch otwarcia
przez `--api-candle-open-epoch` do osobnego procesu. Kod 0 oznacza nowy zapis
i włączony dźwięk, 4 nowy zapis przy wyłączonym dźwięku, 3 brak zapisu;
pozostałe kody oznaczają błąd. W tym trybie dźwięk odtwarza C++ po potwierdzeniu.

## Backtest

```powershell
python backtest.py --end-epoch 1794841200 --limit 10000 --cost 0.6
```

Symulacja analizuje świece chronologicznie bez podglądu przyszłości, rozstrzyga
jednoczesne osiągnięcie TP i SL na korzyść SL i odejmuje koszt transakcji.
Pozycja trwa najwyżej 20 minut i nie przekracza segmentu ani końca sesji
kasowej. Przy luce lub końcu historii przed rozstrzygnięciem transakcja jest
oznaczana `nierozstrzygnieta` w dzienniku i wykluczana z metryk. Pole
`unresolved_trades` podaje liczbę takich transakcji; ich wynik i cena wyjścia
są `null`, ponieważ nie można wiarygodnie odtworzyć dalszego przebiegu. Raport zawiera
liczbę sygnałów, win rate, średnią wygraną i stratę, expectancy, profit
factor, maksymalne obsunięcie oraz wyniki według setupu i godziny Warszawy.
To symulacja, bez rzeczywistych bid/ask i modelu płynności.
