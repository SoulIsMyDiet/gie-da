"""Parametry strategii mikrotransakcji dla złota XAU/USD."""

from datetime import date, time


DB_SYMBOL = "XAU/USD"
INSTRUMENT_NAME = "XAUUSD"
INSTRUMENT_PARAMS_NAME = "XAU/USD"
STRATEGY_NAME = "Claude-sonnet55/XAUUSDStrategyV1"

PRICE_DECIMALS = 2
SIGNAL_PRICE_DECIMALS = 5
INDICATOR_DECIMALS = 5
SAVE_NONE_SIGNALS = False
BEEP_ENABLED = True

HISTORY_LIMIT = 5000
POLL_INTERVAL_SECONDS = 1
WAIT_FOR_CANDLE_SECONDS = 20
STATE_FILE_NAME = "xauusd_claude_last_processed.json"

LOCAL_TIME_ZONE = "Europe/Warsaw"
LONDON_TIME_ZONE = "Europe/London"
NEW_YORK_TIME_ZONE = "America/New_York"
CANDLE_DURATION_SECONDS = 60
SECONDS_PER_MINUTE = 60
RETRY_MAX_SECONDS = 30
M5_EMA_PERIOD = 20
M5_TREND_STALE_MAX_MINUTES = 5
DAILY_SESSION_START_ET = time(18, 0)
MARKET_CLOSE_ET = time(17, 0)
LONDON_OPEN = time(8, 0)
COMEX_OPEN = time(8, 20)
GAP_LOOKBACK_MINUTES = 60
EMA_SLOPE_LOOKBACK = 10
RELATIVE_VOLUME_BONUS_MIN = 1.2

ASSUMED_COST_USD = 0.40
COST_MULTIPLE = 3.0
MIN_TAKE_PROFIT_USD = 1.2
TAKE_PROFIT_ATR_MULTIPLE = 2.0
STOP_LOSS_ATR_MULTIPLE = 1.5
MAX_HOLD_MINUTES = 20

ATR_PERIOD = 14
ATR_MIN_USD = 0.8
ATR_MAX_USD = 8.0
ATR_RATIO_MIN = 0.7
ATR_RATIO_MAX = 2.5
ATR_RATIO_LOOKBACK = 200
EMA_FAST_PERIOD = 9
EMA_MIDDLE_PERIOD = 21
EMA_SLOW_PERIOD = 55
RSI_PERIOD = 5
BB_PERIOD = 20
BB_DEVIATIONS = 2.0
EFFICIENCY_PERIOD = 30
RELATIVE_VOLUME_PERIOD = 20
RANGE_ASIA_START = time(1, 0)
RANGE_ASIA_END = time(8, 0)
RANGE_MIN_USD = 4.0
RANGE_MAX_USD = 40.0
RANGE_BREAKOUT_ATR_BUFFER = 0.1
RANGE_BREAKOUT_MAX_EXTENSION_ATR = 1.0
RANGE_BREAKOUT_REL_VOLUME_MIN = 1.3
RANGE_BREAKOUT_BODY_RATIO_MIN = 0.5
RANGE_ASIA_MIN_COVERAGE = 0.8
RANGE_BREAKOUT_LONDON_START = time(8, 15)
RANGE_BREAKOUT_LONDON_END = time(11, 0)

WARMUP_BREAKOUT = 20
WARMUP_PULLBACK = 80
WARMUP_REVERSION = 60
STALE_MAX_MINUTES = 3
MAX_CONSECUTIVE_MISSING = 1
MAX_MISSING_IN_60 = 3
M5_MINIMUM_CANDLES = 4
M5_BUCKET_MINUTES = 5

SIGNAL_WINDOW_LONDON_START = time(8, 15)
SIGNAL_WINDOW_NEW_YORK_END = time(17, 0)
FRIDAY_LAST_SIGNAL_PL = time(21, 0)
PRE_BREAK_BUFFER_MIN = 30
COOLDOWN_MINUTES = 10
MAX_SIGNALS_PER_DAY = 10
MIN_SCORE = 70
PULLBACK_LONDON_START = time(8, 30)
PULLBACK_LONDON_END = time(12, 0)
PULLBACK_NEW_YORK_START = time(8, 45)
PULLBACK_NEW_YORK_END = time(16, 30)
PULLBACK_ER_MIN = 0.30
PULLBACK_PROXIMITY_ATR = 0.25
PULLBACK_LOOKBACK_CANDLES = 5
PULLBACK_PRIOR_RSI_CANDLES = 3
PULLBACK_RSI_LOW = 40.0
PULLBACK_RSI_HIGH = 45.0
PULLBACK_RSI_SHORT_HIGH = 60.0
PULLBACK_RSI_SHORT_LOW = 55.0
PULLBACK_BODY_RATIO_MIN = 0.4
REVERSION_ER_MAX = 0.20
REVERSION_ZSCORE_THRESHOLD = 2.0
REVERSION_RSI_THRESHOLD = 25.0
REVERSION_RSI_SHORT_THRESHOLD = 75.0
REVERSION_RSI_LOOKBACK = 2
REVERSION_VWAP_DISTANCE_ATR = 1.5
REVERSION_SLOPE_ATR_MAX = 0.3
REVERSION_WICK_RATIO_MIN = 0.30
UNCLEAR_ER_MIN = 0.20
UNCLEAR_ER_MAX = 0.30
RELATIVE_VOLUME_HARD_MIN = 0.6
CANDLE_RANGE_ATR_MAX = 3.0
PULLBACK_ER_BONUS_MIN = 0.45
CANDLE_QUALITY_BODY_RATIO_MIN = 0.6
SCORE_CORE = 60
SCORE_BONUS = 10
LIQUIDITY_LONDON_START = time(8, 15)
LIQUIDITY_LONDON_END = time(11, 0)
LIQUIDITY_OVERLAP_START = time(8, 30)
LIQUIDITY_OVERLAP_END = time(12, 0)
LONDON_SESSION_END = time(16, 0)
NEW_YORK_SESSION_START = time(12, 0)
NEW_YORK_SESSION_END = time(16, 55)
TREND_PULLBACK_SCORE_DESCRIPTION = "odbicie od EMA21 i potwierdzenie RSI(5)"
MEAN_REVERSION_SCORE_DESCRIPTION = "powrót ceny do wnętrza wstęg Bollingera"

BLACKOUTS = {
    "london_open_start": time(8, 0),
    "london_open_end": time(8, 10),
    "lbma_fixing_times": (time(10, 30), time(15, 0)),
    "lbma_fixing_buffer_minutes": 2,
    "us_0830_time": time(8, 30),
    "us_0830_before_minutes": 1,
    "us_0830_after_minutes": 5,
    "us_1000_time": time(10, 0),
    "us_1000_before_minutes": 1,
    "us_1000_after_minutes": 3,
    "fomc_start": time(14, 0),
    "fomc_duration_minutes": 45,
    "rollover_start": time(16, 55),
    "news_after_minutes": 15,
}

# Daty są puste, dopóki użytkownik nie uzupełni ich ręcznie.
NO_TRADE_DATES: set[date] = set()
EARLY_CLOSE_DATES: dict[date, time] = {}
FOMC_DATES: set[date] = set()
HIGH_IMPACT_DAYS: set[date] = set()
NEWS_BLACKOUTS: list[dict[str, object]] = []

BLOCKED_REASON_PL = {
    "outside_session": "poza dozwolonym oknem handlu",
    "scheduled_break": "rynek jest w zaplanowanej przerwie",
    "holiday": "dzień został oznaczony jako bez handlu",
    "early_close": "sygnał przypada po skróconym zamknięciu rynku",
    "high_impact_day": "dzień został oznaczony jako dzień ważnych wydarzeń",
    "london_open": "trwa okno otwarcia rynku w Londynie",
    "lbma_fixing": "trwa okno fixingu LBMA",
    "us_0830": "trwa okno publikacji danych z USA o 08:30 ET",
    "us_1000": "trwa okno publikacji danych z USA o 10:00 ET",
    "fomc": "trwa okno decyzji lub komunikatu FOMC",
    "rollover": "trwa okno rollover rynku",
    "news": "sygnał przypada w skonfigurowanym oknie ważnej publikacji",
    "pre_break_buffer": "zostało mniej niż 30 minut do przerwy rynku",
    "friday_cutoff": "minął piątkowy limit sygnałów",
    "stale_data": "ostatnia zamknięta świeca jest nieaktualna",
    "data_gap": "w danych występuje zbyt duża luka",
    "warmup": "segment nie zawiera jeszcze wymaganej liczby świec",
    "atr_out_of_range": "zmienność ATR jest poza dozwolonym zakresem",
    "atr_ratio_out_of_range": "zmienność względem mediany jest poza zakresem",
    "cost_filter": "potencjalny zysk nie pokrywa minimalnego kosztu",
    "candle_anomaly": "zakres ostatniej świecy jest nietypowo duży",
    "low_relative_volume": "wolumen jest zbyt niski względem średniej",
    "regime_unclear": "reżim rynku jest niejednoznaczny",
    "cooldown": "obowiązuje przerwa po poprzednim sygnale",
    "daily_limit": "osiągnięto dzienny limit sygnałów",
    "setup_limit": "wykorzystano już sygnał wybicia w tym kierunku",
    "low_score": "wynik setupu jest niższy od wymaganego progu",
    "conflicting_setups": "setupy wskazują przeciwne kierunki",
    "no_setup": "warunki LONG ani SHORT nie zostały spełnione",
}
