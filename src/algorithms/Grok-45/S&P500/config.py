"""Backend: konfigurowalne parametry niezależnej strategii Grok-45 dla S&P500."""

from zoneinfo import ZoneInfo

DB_SYMBOL = "ES"
INSTRUMENT = "S&P500"
STRATEGY_NAME = "Grok-45/S&P500"

WARSAW = ZoneInfo("Europe/Warsaw")
NEW_YORK = ZoneInfo("America/New_York")

HISTORY_LIMIT = 200
MIN_SEGMENT_CANDLES = 50
MAX_HOLD_MINUTES = 20
MAX_STALE_MINUTES = 3
SAVE_NONE_SIGNALS = True
BEEP_ENABLED = True
BEEP_FREQUENCY_HZ = 1000
BEEP_DURATION_MS = 120
BEEP_PAUSE_MS = 100
ASSUMED_COST_POINTS = 0.6

EMA_FAST_PERIOD = 9
EMA_MIDDLE_PERIOD = 21
EMA_SLOW_PERIOD = 50
RSI_PERIOD = 14
ATR_PERIOD = 14
VOLUME_SMA_PERIOD = 20
ADX_PERIOD = 14

MIN_ATR = 0.0
MIN_RELATIVE_VOLUME = 1.1
MIN_ADX = 20.0
LONG_RSI_MIN = 40.0
LONG_RSI_MAX = 65.0
SHORT_RSI_MIN = 35.0
SHORT_RSI_MAX = 60.0

PULLBACK_LOOKBACK_MIN = 3
PULLBACK_LOOKBACK_MAX = 5
PULLBACK_MAX_DISTANCE_ATR = 0.25
MIN_REJECTION_WICK_RATIO = 0.5

# Udziały sumują się do 100; DI pozostaje osobnym, obowiązkowym filtrem kierunku.
SCORE_WEIGHTS = {
    "ema_trend": 20,
    "pullback": 15,
    "rsi": 15,
    "atr": 10,
    "relative_volume": 15,
    "candle": 10,
    "adx": 10,
    "directional_indicator": 5,
}
