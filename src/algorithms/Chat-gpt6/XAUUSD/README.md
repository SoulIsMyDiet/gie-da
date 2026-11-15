# Strategia XAUUSDStrategyV1

Strategia analizuje zamknięte świece M1 złota z istniejącej bazy MySQL.
Symbol w bazie to `XAU/USD`; w wyniku JSON używana jest nazwa `XAUUSD`.
Algorytm nie składa zleceń ani nie zamyka pozycji.

## Czas i przerwy

`candles.datetime` to czas otwarcia w `Europe/Warsaw`; zapisany osobno
`utc_offset_minutes` pozwala rozróżnić powtórzoną godzinę jesiennej zmiany
czasu. Minuty porównywane są jako rzeczywiste chwile UTC, a czas zamknięcia
jest o minutę późniejszy od otwarcia. Agregacja M5 nie uzupełnia braków i
korzysta tylko z zamkniętych przedziałów.

Na potrzeby strategii przyjęto przerwę poniedziałek–czwartek od 17:00 do
18:00 czasu Nowego Jorku oraz od piątku 17:00 do niedzieli 18:00 ET.
W Warszawie dzienna przerwa przypada zwykle 23:00–00:00, a w tygodniach
rozbieżnych zmian czasu USA i Polski 22:00–23:00. Otwarcie niedzielne
przypada odpowiednio w poniedziałek o 00:00 albo w niedzielę o 23:00.
Konwersja używa `zoneinfo`, bez stałego przesunięcia. Sesyjny VWAP rozpoczyna
się o 18:00 ET.

Istniejący profil pobierania `gold_spot` nie zawiera dziennej przerwy i
dopuszcza otwarcie w niedzielę o 17:00 ET. Nie zmieniano tego mechanizmu:
zapisane w `candle_gaps` informacje mogą oznaczać niektóre zaplanowane minuty
jako brakujące, lecz strategia stosuje powyższy własny filtr. Trzy kolejne
nieoczekiwane brakujące minuty rozpoczynają nowy okres rozgrzewki; pojedynczy
brak ani regularna przerwa nie resetują wskaźników.

## Ograniczenia

Ceny wejścia, SL i TP są wyłącznie propozycją wyliczoną na podstawie świec;
kurs zamknięcia nie jest gwarantowaną ceną wykonania. Wyliczenia nie
uwzględniają rzeczywistego spreadu, poślizgu ani opłat. Przyjęto SL
1,5 × ATR i TP 2 × ATR. Sygnały nie są dowodem opłacalności: w obecnej bazie
nie ma świec XAU/USD, na których można byłoby skalibrować parametry.

Raport `signal_potention` nie jest generowany ani przeliczany dla tej
strategii, ponieważ jego domyślny spread nie jest znaną wartością dla XAUUSD.
