# Algorytm Mikrotransakcji EURUSD (Claude-sonnet55)

Algorytm skanuje świece 1-minutowe EURUSD w poszukiwaniu szans na krótkie transakcje
(do 20 minut) z dwoma setupami technicznymi:

1. **TREND_PULLBACK** - dla rynków trendowych (Efficiency Ratio ≥ 0.30)
2. **RANGE_REVERSION** - dla rynków bocznych (Efficiency Ratio ≤ 0.20)

## Instalacja

```bash
cd src/algorithms/Claude-sonnet55/EURUSD

# Wymagania Python 3.10+
pip install -r requirements.txt
```

## Uruchamianie

### Tryb jednorazowy (oblicza sygnał dla ostatnich N świec)

```bash
python EURUSDStrategyV1.py
```

Drukuje JSON z wynikiem na stdout.

### Tryb loop (opcjonalnie)

```bash
python EURUSDStrategyV1.py --loop
```

Czeka na pojawienie się nowej świecy w bazie i wtedy oblicza sygnał.

## Struktura plików

- **config.py** - Wszystkie parametry i stałe (modyfikuj tutaj dla tuningu)
- **indicators.py** - Wskaźniki techniczne (EMA, RSI, ATR, BB, ER, VWAP)
- **signal_logic.py** - Logika filtrów i setupów (LONG/SHORT/NONE)
- **storage.py** - Połączenie z MySQL i zapis sygnałów
- **sound.py** - Sygnały dźwiękowe (potwierdzenie/błąd)
- **EURUSDStrategyV1.py** - Główny plik z klasą strategii
- **tests/** - Testy jednostkowe (pytest)
- **README.md** - Ta dokumentacja

## Zmienne środowiskowe

Ustaw w `.env`:

```
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password
MYSQL_DATABASE=gielda
```

## Parametry algorytmu

Wszystkie parametry znajdują się w `config.py`:

- **Godziny handlu**: `TRADING_START_UTC`, `TRADING_END_UTC`, `PRIME_SESSION_*`
- **Zmienność**: `ATR_MIN_PIPS`, `ATR_MAX_PIPS`, `ATR_RATIO_MIN/MAX`
- **Wskaźniki**: `EMA_SHORT`, `EMA_MEDIUM`, `EMA_LONG`, `RSI_PERIOD`, `ATR_PERIOD`
- **Scoring**: `CORE_SETUP_POINTS`, bonusy
- **Zarządzanie**: `SL_MULTIPLIER`, `TP_MULTIPLIER`, `MAX_HOLD_MINUTES`
- **Testing**: `SAVE_NONE_SIGNALS` (True = zapisuje sygnały NONE)

## Format wyjścia (JSON)

```json
{
  "instrument": "EURUSD",
  "signal": "LONG",
  "price": 1.0850,
  "timestamp": "2024-01-08T09:15:00+00:00",
  "setup": "TREND_PULLBACK",
  "score": 85,
  "reasons": [
    "5m_trend_up",
    "ema_order_correct",
    "pullback_detected",
    "rsi_bounce_confirmed",
    "candle_bullish",
    "trend_pullback_core_conditions_met",
    "bonus_high_volume",
    "bonus_prime_session"
  ],
  "blocked_by": null,
  "params": {
    "atr_pips": 1.25,
    "atr_ratio": 1.05,
    "rsi5": 48.5,
    "ema9": 1.0840,
    "ema21": 1.0835,
    "ema55": 1.0825,
    "efficiency_ratio": 0.35,
    "relative_volume": 1.15
  },
  "max_hold_minutes": 20,
  "tp_pips": 2.5,
  "sl_pips": 1.875,
  "tp_price": 1.0852,
  "sl_price": 1.0848
}
```

## Sygnały dźwiękowe

- **2 piknięcia**: Sygnał został zapisany do bazy
- **4 piknięcia** (wysoka częstotliwość): Błąd (baza, wskaźniki)

## Testy

```bash
pytest tests/test_indicators.py -v
```

## Idempotentność

Sygnał jest zapisywany z unikalnym kluczem:
`(product, algorithm_name, source_timestamp)`

Ponowne uruchomienie dla tej samej świecy NIE utworzy duplikatu.

## Cooldown

Po sygnale LONG/SHORT następny sygnał jest dopuszczalny po `COOLDOWN_MINUTES` (domyślnie 10).

## Backtest (TODO)

```bash
python backtest.py
```

Symuluje wyniki dla historii z bazy (TP/SL/time stop, odejmuje spread).

## Założenia algorytmu

1. **Tylko zamknięte świece** - Ostatnia świeca nie starsza niż 2 minuty
2. **Brak look-ahead** - Wskaźniki używają tylko danych historycznych
3. **Pip = 0.0001** - Zgodnie z EURUSD
4. **Czas w UTC** - Wszystkie znaczniki czasu to UTC
5. **Spread/Prowizja** - Domyślnie 1.5 pipsów (konfigurowalny `ASSUMED_COST_PIPS`)
6. **Mikrotransakcje** - Max 20 minut hold (time stop)
7. **Dwa setupy** - Algoritm wybiera na podstawie Efficiency Ratio (trend vs range)

## Tuning parametrów

1. Zmień wartości w `config.py`
2. Uruchom backtest: `python backtest.py`
3. Sprawdź metryki: win rate, profit factor, max drawdown
4. Iteruj

## Troubleshooting

- **"Za mało świec"**: Baza musi mieć ≥ 300 świec dla EURUSD
- **"Stale data"**: Ostatnia świeca w bazie jest zbyt stara (> 2 minuty)
- **"regime_unclear"**: ER między 0.20 a 0.30 - żaden setup nie jest preferowany
- **"insufficient_expected_move"**: ATR zbyt niski, spread by zjadł zysk

## Autorstwo

Algorytm Claude-sonnet55 dla mikrotransakcji EURUSD. Do użytku wewnętrznego.
