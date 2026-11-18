"""Parametry rdzenia XAUUSD; progi są założeniami, nie kalibracją rynkową."""

from dataclasses import dataclass


# Backend: wspólne ustawienia integracji zapisu i uruchamiania strategii.
DB_SYMBOL = "XAU/USD"
STRATEGY_NAME = "Chat-gpt6/XAUUSDStrategyV1"
SAVE_NONE_SIGNALS = False
HISTORY_LIMIT = 2500
BEEP_ENABLED = True
SIGNAL_POTENTIAL_SPREAD = 0.30


@dataclass(frozen=True)
class StrategyConfig:
    """Przechowuje jawne parametry strategii bez zależności od bazy danych."""

    instrument: str = "XAUUSD"
    market_symbol: str = DB_SYMBOL
    ema_fast: int = 9
    ema_middle: int = 21
    ema_slow: int = 50
    rsi_period: int = 14
    atr_period: int = 14
    bb_period: int = 20
    bb_deviations: float = 2.0
    extrema_lookback: int = 20
    minimum_m1: int = 300
    minimum_m5: int = 100
    unexpected_gap_minutes: int = 3
    maximum_signal_age_seconds: int = 120
    maximum_m5_age_seconds: int = 600
    atr_minimum: float = 0.01
    atr_maximum: float = 20.0
    bb_width_minimum: float = 0.00005
    bb_width_maximum: float = 0.05
    long_rsi_minimum: float = 50.0
    long_rsi_maximum: float = 75.0
    short_rsi_minimum: float = 25.0
    short_rsi_maximum: float = 50.0
    pullback_distance_atr: float = 0.5
    breakout_buffer_atr: float = 0.0
    minimum_score: int = 70
    strong_score: int = 80
    weight_ema: int = 20
    weight_m5: int = 20
    weight_rsi: int = 20
    weight_atr: int = 15
    weight_vwap: int = 10
    weight_setup: int = 15
    stop_loss_atr: float = 1.5
    take_profit_atr: float = 2.0
    max_hold_minutes: int = 20


# Backend: po 100 kubełkach M5 wpływ ziarna EMA50 spada do około 1,9%.
# 100 pełnych M5 wymaga co najmniej 500 świec M1; próg 300 M1 jest niezależnym
# minimum. Progi i wagi są hipotezami, nie kalibracją ani obietnicą zysku.
# Ustawienia zmienia się w tym pliku; CONFIG jest niemodyfikowalną konfiguracją.
CONFIG = StrategyConfig()
