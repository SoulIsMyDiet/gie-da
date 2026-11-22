"""Parametry i ustawienia strategii BTC/USD."""

from datetime import timedelta
from decimal import Decimal

INSTRUMENT = "BTCUSD"
DATABASE_SYMBOL = "BTC/USD"
STRATEGY_NAME = "Grok-45/BTCUSDStrategyV1"

HISTORY_LIMIT = 60
MINIMUM_CANDLES = 40
CANDLE_DURATION = timedelta(minutes=1)
COOLDOWN_SECONDS = 4 * 60
MAX_HOLDING_MINUTES = 20

EMA_FAST_PERIOD = 8
EMA_SLOW_PERIOD = 21
RSI_PERIOD = 9
VOLUME_SMA_PERIOD = 20
VOLUME_SPIKE_MULTIPLIER = 1.35
ATR_PERIOD = 14
BREAKOUT_LOOKBACK = 6
MINIMUM_BODY_ATR_MULTIPLIER = 0.35

LONG_RSI_MINIMUM = 52.0
LONG_RSI_MAXIMUM = 72.0
SHORT_RSI_MINIMUM = 28.0
SHORT_RSI_MAXIMUM = 48.0

# Przyjęty spread to 0,2% ceny wejścia; jest to założenie, a nie bieżące kwotowanie.
BTC_SPREAD_RATE = Decimal("0.002")

# Ustaw tę flagę na False, aby zapisywać tylko sygnały LONG i SHORT.
SAVE_NONE_SIGNALS = True

# Powiadomienia nie są wymagane do zapisu sygnału; można je włączyć osobno.
BEEP_ENABLED = False
