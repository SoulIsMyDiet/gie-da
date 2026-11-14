"""
Backend
Stałe i parametry strategii mikrotransakcji EURUSD.
Todas wartości tutaj mogą być zmieniane dla tuningu algorytmu.
"""

from datetime import time
from zoneinfo import ZoneInfo

# ============================================================================
# OGÓLNE PARAMETRY INSTRUMENTU
# ============================================================================
INSTRUMENT = "EURUSD"
PIP = 0.0001  # 1 pip dla EURUSD
ASSUMED_COST_PIPS = 1.5  # Założony spread/prowizja w pipsach
COST_MULTIPLE = 3.0  # Mnożnik: oczekiwany ruch musi być ≥ COST_MULTIPLE * ASSUMED_COST_PIPS

# ============================================================================
# FILTRY CZASU (sesje handlowe)
# ============================================================================
TRADING_START_UTC = time(7, 0)  # 7:00 UTC (Londyn otwarcie)
TRADING_END_UTC = time(16, 30)  # 16:30 UTC (NY zamknięcie)
PRIME_SESSION_START_UTC = time(12, 0)  # Premium czas sesji (bonus +10 pkt)
PRIME_SESSION_END_UTC = time(16, 0)

# Blokada: ostatnie 2 minuty każdej pełnej godziny i połowy godziny
BLACKOUT_MINUTE_EVERY_HOUR = [58, 59, 0, 1]

# Blokada: piątek po tej godzinie (UTC)
FRIDAY_CLOSE_UTC = time(19, 0)

# Blokada: niedziela/wtorek zamknięcie (UTC)
WEEKEND_CLOSE_START_UTC = time(21, 0)
WEEKEND_CLOSE_END_UTC = time(23, 59)

# Dni handlowe: 0=Poniedziałek, 4=Piątek, 6=Niedziela (0-6 format Python weekday())
TRADING_DAYS = [0, 1, 2, 3, 4, 5, 6]  # Poniżej dodajemy filtrowanie

# Czasy blackoutu (news events itd.) - domyślnie puste
NEWS_BLACKOUTS: list[tuple[time, time]] = []

# ============================================================================
# PARAMETRY WSKAŹNIKÓW (1M)
# ============================================================================
EMA_SHORT = 9  # EMA dla trendu krótkoterminowego
EMA_MEDIUM = 21  # EMA dla trendu średnioterminowego
EMA_LONG = 55  # EMA dla trendu długoterminowego
RSI_PERIOD = 5  # RSI dla impulsu (krótki okres)
ATR_PERIOD = 14  # ATR (Wilder) dla zmienności
BB_PERIOD = 20  # Bollinger Bands
BB_STD_DEV = 2.0
EFFICIENCY_RATIO_PERIOD = 30  # Kaufman ER dla trend/noise ratio
VWAP_ANCHOR = "session"  # "session" = początek doby UTC

# ============================================================================
# PARAMETRY WSKAŹNIKÓW (5M)
# ============================================================================
EMA_5M = 20  # EMA na 5M dla potwierdzenia trendu

# ============================================================================
# FILTRY ZMIENNOŚCI (ATR)
# ============================================================================
ATR_MIN_PIPS = 0.7  # Minimalna zmienność (spread zaadził by wszystko)
ATR_MAX_PIPS = 3.0  # Maksymalna zmienność (zbyt ryzykowne)
ATR_RATIO_MIN = 0.8  # ATR względem mediany 200 świec
ATR_RATIO_MAX = 2.5

# ============================================================================
# FILTRY ANOMALII
# ============================================================================
MAX_CANDLE_RANGE_MULTIPLIER = 3.0  # Zakres świecy ≤ 3 * ATR
MIN_RELATIVE_VOLUME = 0.6  # vol / SMA(vol, 20) ≥
MIN_CANDLE_HISTORY = 300  # Minimum świec do analizy
MAX_STALE_DATA_MINUTES = 2  # Ostatnia świeca nie starsza niż X minut

# ============================================================================
# PARAMETRY SETUP 1: TREND_PULLBACK (ER ≥ 0.30)
# ============================================================================
TREND_PULLBACK_ER_MIN = 0.30  # Minimum Efficiency Ratio dla trendowego
PULLBACK_ATR_DISTANCE = 0.25  # Jak blisko EMA21 musi być low (× ATR)
RSI_OVERSOLD_THRESHOLD = 40  # RSI bounce signal
RSI_RECOVERY_THRESHOLD = 45  # RSI musi być > tego dla LONG

# ============================================================================
# PARAMETRY SETUP 2: RANGE_REVERSION (ER ≤ 0.20)
# ============================================================================
RANGE_REVERSION_ER_MAX = 0.20  # Maximum ER dla range-bound
RANGE_EMA55_SLOPE_THRESHOLD = 0.3  # EMA55 nachylenie (× ATR) dla płaskiego
BB_ZSCORE_EXTREME = -2.0  # Bollinger ekstremum
RSI_OVERSOLD_RANGE = 25  # RSI threshold dla range
REVERSION_ATR_DISTANCE = 1.5  # Odległość od VWAP (× ATR)

# Strefa niepewna między 0.20 a 0.30
REGIME_UNCERTAIN_ZONE = (0.20, 0.30)

# ============================================================================
# SCORING
# ============================================================================
MIN_SCORE = 70  # Minimum score aby wygenerować sygnał
CORE_SETUP_POINTS = 60  # Punkty za spełnienie rdzenia setupu
BONUS_RELATIVE_VOLUME = 10  # ≥ 1.2
BONUS_PRIME_SESSION = 10  # 12:00-16:00 UTC
BONUS_STRONG_TREND = 10  # ER > 0.45 dla TREND_PULLBACK
BONUS_CANDLE_QUALITY = 10  # Jakość świecy

# ============================================================================
# ZARZĄDZANIE POZYCJAMI
# ============================================================================
SL_MULTIPLIER = 1.5  # SL = 1.5 × ATR
TP_MULTIPLIER = 2.0  # TP = 2.0 × ATR
MIN_TP_PIPS = 2.0  # Minimum TP w pipsach
MAX_HOLD_MINUTES = 20  # Time stop

# ============================================================================
# COOLDOWN I IDEMPOTENTNOŚĆ
# ============================================================================
COOLDOWN_MINUTES = 10  # Czekaj po sygnale LONG/SHORT

# ============================================================================
# ZAPIS SYGNAŁÓW I TESTING
# ============================================================================
SAVE_NONE_SIGNALS = True  # TYMCZASOWE - zmień na False aby nie zapisywać NONE
STRATEGY_NAME = "Claude-sonnet55/EURUSD/MicroTransactions"

# ============================================================================
# STREFY CZASOWE
# ============================================================================
UTC_TIMEZONE = ZoneInfo("UTC")
WARSAW_TIMEZONE = ZoneInfo("Europe/Warsaw")  # Lokalna strefa - czas w bazie
