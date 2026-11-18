"""Parametry i progi strategii mikrotransakcji dla USD/JPY."""

from datetime import date, time


DB_SYMBOL = "USD/JPY"
INSTRUMENT_NAME = "USDJPY"
INSTRUMENT_PARAMS_NAME = "USD/JPY"
STRATEGY_NAME = "Claude-sonnet55/USDJPYStrategyV1"

LOCAL_TIME_ZONE = "Europe/Warsaw"
TOKYO_TIME_ZONE = "Asia/Tokyo"
LONDON_TIME_ZONE = "Europe/London"
NEW_YORK_TIME_ZONE = "America/New_York"

PIP_SIZE = 0.01
PRICE_DECIMALS = 3
INDICATOR_DECIMALS = 5
ASSUMED_COST_PIPS = 0.8
COST_MULTIPLE = 3.0
MIN_TAKE_PROFIT_PIPS = 2.4
MAX_HOLD_MINUTES = 20
TAKE_PROFIT_ATR_MULTIPLE = 2.0
STOP_LOSS_ATR_MULTIPLE = 1.5

HISTORY_LIMIT = 5000
POLL_INTERVAL_SECONDS = 1
WAIT_FOR_CANDLE_SECONDS = 20
RETRY_MAX_SECONDS = 30
CANDLE_DURATION_SECONDS = 60
SECONDS_PER_MINUTE = 60
STATE_FILE_NAME = "usdjpy_claude_last_processed.json"
SAVE_NONE_SIGNALS = True  # TYMCZASOWE — docelowo False.
BEEP_ENABLED = True

MARKET_CLOSE_ET = time(17, 0)
WEEKEND_OPEN_ET = time(17, 0)
SIGNAL_ROLLOVER_ET = time(16, 55)
SIGNAL_START_JST = time(9, 15)
PRE_BREAK_BUFFER_MIN = 30

TOKYO_OPEN_JST = time(9, 0)
TOKYO_SESSION_END_JST = time(18, 0)
TOKYO_FIXING_JST = time(9, 55)
LONDON_OPEN = time(8, 0)
LONDON_SESSION_END = time(16, 0)
NEW_YORK_DATA_OPEN = time(8, 30)
NEW_YORK_DATA_1000 = time(10, 0)
NEW_YORK_SESSION_END = time(16, 55)
LIQUIDITY_OVERLAP_END_ET = time(12, 0)
FOMC_TIME_ET = time(14, 0)
FRIDAY_LAST_SIGNAL_PL = time(21, 0)
BEEP_FREQUENCY_HZ = 1000
BEEP_DURATION_MS = 120
BEEP_PAUSE_SECONDS = 0.1

TOKYO_RANGE_START_JST = time(9, 0)
TOKYO_RANGE_END_JST = time(12, 0)
TOKYO_RANGE_MIN_COVERAGE = 0.8
RANGE_MIN_PIPS = 15.0
RANGE_MAX_PIPS = 80.0
RANGE_BREAKOUT_ATR_BUFFER = 0.1
RANGE_BREAKOUT_MAX_EXTENSION_ATR = 1.0
RANGE_BREAKOUT_REL_VOLUME_MIN = 1.2
RANGE_BREAKOUT_BODY_RATIO_MIN = 0.5
RANGE_BREAKOUT_LONDON_START = time(8, 10)
RANGE_BREAKOUT_LONDON_END = time(11, 0)

WARMUP_BREAKOUT = 20
WARMUP_PULLBACK = 80
WARMUP_REVERSION = 60
STALE_MAX_MINUTES = 3
GAP_LOOKBACK_MINUTES = 60
MAX_CONSECUTIVE_MISSING = 1
MAX_MISSING_IN_60 = 3

ATR_PERIOD = 14
ATR_MIN_PIPS = 1.2
ATR_MAX_PIPS = 12.0
ATR_RATIO_MIN = 0.7
ATR_RATIO_MAX = 2.5
ATR_RATIO_LOOKBACK = 200
EMA_FAST_PERIOD = 9
EMA_MIDDLE_PERIOD = 21
EMA_SLOW_PERIOD = 55
EMA_SLOPE_LOOKBACK = 10
RSI_PERIOD = 5
BB_PERIOD = 20
BB_DEVIATIONS = 2.0
EFFICIENCY_PERIOD = 30
RELATIVE_VOLUME_PERIOD = 20
RELATIVE_VOLUME_HARD_MIN = 0.5
RELATIVE_VOLUME_BONUS_MIN = 1.2
VWAP_RESET_ET = time(17, 0)

M5_BUCKET_MINUTES = 5
M5_MINIMUM_CANDLES = 4
M5_EMA_PERIOD = 20
M5_TREND_STALE_MAX_MINUTES = 5

CANDLE_RANGE_ATR_MAX = 3.0
RANGE_SPIKE_LOOKBACK = 3
RANGE_SPIKE_BASELINE = 60
RANGE_SPIKE_MAX_RATIO = 3.0
SPIKE_COOLDOWN_MINUTES = 15
COOLDOWN_MINUTES = 10
MAX_SIGNALS_PER_DAY = 10
MIN_SCORE = 70
SCORE_CORE = 60
SCORE_RELATIVE_VOLUME_BONUS = 5
SCORE_LIQUIDITY_BONUS = 15
SCORE_CONFIRMATION_BONUS = 10
SCORE_CANDLE_QUALITY_BONUS = 10
CANDLE_QUALITY_BODY_RATIO_MIN = 0.6

PULLBACK_TOKYO_START = time(9, 30)
PULLBACK_TOKYO_END = time(12, 0)
PULLBACK_NEW_YORK_START = time(8, 30)
PULLBACK_NEW_YORK_END = time(16, 30)
PULLBACK_ER_MIN = 0.30
PULLBACK_ER_BONUS_MIN = 0.45
PULLBACK_PROXIMITY_ATR = 0.25
PULLBACK_LOOKBACK_CANDLES = 5
PULLBACK_PRIOR_RSI_CANDLES = 3
PULLBACK_RSI_LOW = 40.0
PULLBACK_RSI_HIGH = 45.0
PULLBACK_RSI_SHORT_HIGH = 60.0
PULLBACK_RSI_SHORT_LOW = 55.0
PULLBACK_BODY_RATIO_MIN = 0.4

REVERSION_ER_MAX = 0.20
UNCLEAR_ER_MIN = 0.20
UNCLEAR_ER_MAX = 0.30
REVERSION_ZSCORE_THRESHOLD = 2.0
REVERSION_RSI_THRESHOLD = 25.0
REVERSION_RSI_SHORT_THRESHOLD = 75.0
REVERSION_RSI_LOOKBACK = 2
REVERSION_VWAP_DISTANCE_ATR = 1.5
REVERSION_SLOPE_ATR_MAX = 0.3
REVERSION_WICK_RATIO_MIN = 0.30

BLACKOUTS = {
    "tokyo_open_before_minutes": 0,
    "tokyo_open_after_minutes": 15,
    "tokyo_fixing_before_minutes": 3,
    "tokyo_fixing_after_minutes": 5,
    "london_open_duration_minutes": 10,
    "us_0830_before_minutes": 1,
    "us_0830_after_minutes": 5,
    "us_1000_before_minutes": 1,
    "us_1000_after_minutes": 3,
    "fomc_duration_minutes": 45,
    "news_after_minutes": 15,
}

# Daty wydarzeń i świąt należy aktualizować ręcznie; strategia nie pobiera kalendarza.
NO_TRADE_DATES: set[date] = set()
EARLY_CLOSE_DATES: dict[date, time] = {}
FOMC_DATES: set[date] = set()
HIGH_IMPACT_DAYS: set[date] = set()
NEWS_BLACKOUTS: list[dict[str, object]] = []

BLOCKED_REASON_PL = {
    "outside_session": "świeca przypada poza dozwolonym oknem sygnałów",
    "scheduled_break": "rynek jest w zaplanowanej przerwie",
    "holiday": "dzień został oznaczony jako bez handlu",
    "early_close": "świeca przypada po skróconym zamknięciu rynku",
    "high_impact_day": "dzień został oznaczony jako dzień ważnych wydarzeń",
    "tokyo_open": "trwa okno otwarcia rynku w Tokio",
    "tokyo_fixing": "trwa okno fixingu tokijskiego",
    "london_open": "trwa okno otwarcia rynku w Londynie",
    "us_0830": "trwa okno publikacji danych z USA o 08:30 ET",
    "us_1000": "trwa okno publikacji danych z USA o 10:00 ET",
    "fomc": "trwa okno decyzji lub komunikatu FOMC",
    "rollover": "trwa okno rollover rynku",
    "news": "świeca przypada w skonfigurowanym oknie ważnej publikacji",
    "pre_break_buffer": "zostało mniej niż 30 minut do zamknięcia rynku",
    "friday_cutoff": "minął piątkowy limit sygnałów",
    "stale_data": "ostatnia zamknięta świeca jest nieaktualna",
    "data_gap": "w danych występuje zbyt duża luka",
    "warmup": "segment nie zawiera jeszcze wymaganej liczby świec",
    "atr_out_of_range": "zmienność ATR jest poza dozwolonym zakresem",
    "atr_ratio_out_of_range": "zmienność względem mediany jest poza zakresem",
    "cost_filter": "potencjalny zysk nie pokrywa minimalnego kosztu",
    "candle_anomaly": "zakres ostatniej świecy jest nietypowo duży",
    "spike_cooldown": "obowiązuje przerwa po wykrytym skoku ceny",
    "low_relative_volume": "wolumen tickowy jest zbyt niski względem średniej",
    "regime_unclear": "reżim rynku jest niejednoznaczny",
    "cooldown": "obowiązuje przerwa po poprzednim sygnale",
    "daily_limit": "osiągnięto dzienny limit sygnałów",
    "setup_limit": "wykorzystano już sygnał wybicia w tym kierunku",
    "low_score": "wynik setupu jest niższy od wymaganego progu",
    "conflicting_setups": "setupy wskazują przeciwne kierunki",
    "no_setup": "warunki LONG ani SHORT nie zostały spełnione",
}
