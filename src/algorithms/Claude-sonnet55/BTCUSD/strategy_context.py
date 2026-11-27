"""Backend: opis konfiguracji używany przy ocenie sygnałów BTC/USD."""

from . import config

_PARAMETER_DESCRIPTIONS = {
    "setup": "rodzaj wykrytego układu rynkowego",
    "score": "wynik punktowy sygnału",
    "tp_price": "cena take-profit w USD",
    "sl_price": "cena stop-loss w USD",
    "spread_bps": "spread BID/ASK wyrażony w punktach bazowych",
    "net_reward_risk": "stosunek potencjalnego zysku do ryzyka po spreadzie",
    "atr14_bps": "ATR(14) względem ceny, w punktach bazowych",
    "rsi5": "wartość RSI(5)",
    "efficiency_ratio_30": "efektywność kierunkowa z 30 świec",
    "rel_volume": "wolumen względem średniej, jeśli wiarygodny",
    "session_name": "nazwa sesji UTC użyta w ocenie",
}


def build_strategy_context() -> str:
    """Tworzy opis strategii z aktualnych progów konfiguracji.

    Zwraca kontekst do przekazania razem z sygnałem. Nie wykonuje zapytań
    sieciowych ani nie zmienia konfiguracji strategii.
    """
    parameter_descriptions = "; ".join(
        f"{key}: {_PARAMETER_DESCRIPTIONS[key]}"
        for key in config.SIGNAL_PARAMS_EXTRA_KEYS
    )
    release_times = ", ".join(
        event_time.strftime("%H:%M")
        for event_time in config.US_DATA_RELEASE_TIMES
    )
    return f"""
BTCUSDStrategyV1 ocenia zamknięte BTC/USD M1, a M5 potwierdza trend. Koszyk M5
ma {config.FIVE_MINUTE_PERIOD} minut i wymaga {config.MINIMUM_M1_CANDLES_PER_M5}
rzeczywistych świec. Rozgrzewka: {config.M1_WARMUP_CANDLES} M1 i
{config.M5_WARMUP_CANDLES} M5; SQUEEZE_BREAKOUT wymaga osobno
{config.SQUEEZE_BREAKOUT_WARMUP_CANDLES} M1. Wskaźniki: EMA
{config.EMA_FAST}/{config.EMA_MID}/{config.EMA_SLOW}, M5 EMA
{config.FIVE_MINUTE_EMA}, RSI({config.RSI_PERIOD}), ATR({config.ATR_PERIOD}),
ER({config.ER_PERIOD}), BB({config.BB_PERIOD}, {config.BB_DEVIATIONS}),
Donchian({config.DONCHIAN_PERIOD}), MACD
{config.MACD_FAST}/{config.MACD_SLOW}/{config.MACD_SIGNAL}.

Setupy: SQUEEZE_BREAKOUT, TREND_PULLBACK, SWEEP_REVERSION. SQUEEZE: percentyl
BB <= {config.SQUEEZE_MAX_PERCENTILE}, korpus >= {config.SQUEEZE_MIN_BODY_RATIO},
zakres <= {config.SQUEEZE_MAX_RANGE_ATR} ATR. PULLBACK: {config.PULLBACK_LOOKBACK}
świec, odległość <= {config.PULLBACK_DISTANCE_ATR} ATR, ER >=
{config.PULLBACK_MIN_ER}, RSI względem {config.PULLBACK_RSI_MIDPOINT}. SWEEP:
RSI LONG <= {config.SWEEP_RSI_LONG_MAX}, SHORT >= {config.SWEEP_RSI_SHORT_MIN},
knot >= {config.SWEEP_MIN_WICK_RATIO}.

Punktacja: baza {config.CORE_SCORE}, bonus {config.BONUS_SCORE}, próg
{config.MIN_SCORE}; weekend/thin-session wymagają
{config.WEEKEND_MIN_SCORE}/{config.THIN_SESSION_MIN_SCORE}. Bonusowe progi:
rel_volume {config.BONUS_RELATIVE_VOLUME}, korpus {config.BONUS_BODY_RATIO},
ER {config.BONUS_EFFICIENCY_RATIO}. OFFHOURS_ENABLED={config.OFFHOURS_ENABLED}.
Wolumen jest niewiarygodny, jeśli źródło go nie potwierdza. TP={config.TP_ATR}
ATR, SL={config.SL_ATR} ATR, minimalne RR={config.MIN_NET_REWARD_RISK},
cel={config.MIN_TARGET_BPS} bps; horyzont={config.MAX_HOLD_MINUTES} min,
maksymalny wiek danych={config.MAX_STALE_MINUTES} min.

Blokady: cash open {config.US_CASH_OPEN.strftime("%H:%M")} ET ±
{config.US_CASH_OPEN_BLACKOUT_MINUTES} min; publikacje {release_times} ET ±
{config.NEWS_BLACKOUT_MINUTES} min; FOMC
{config.FOMC_ANNOUNCEMENT_TIME.strftime("%H:%M")} ET z blokadą do
{config.FOMC_BLACKOUT_AFTER_MINUTES} min po komunikacie; NEWS_BLACKOUTS,
HIGH_IMPACT_DAYS, piątek {config.OPTIONS_EXPIRY_TIME_UTC.strftime("%H:%M")} UTC
i początek godziny. Daty wydarzeń są jawnie konfigurowane w czasie Nowego Jorku.
Strategia nie zakłada funding ani typu kontraktu bez potwierdzonych danych.
Sygnał nie jest gwarancją zysku ani zleceniem. Pola dodatkowe: {parameter_descriptions}.
""".strip()


STRATEGY_CONTEXT = build_strategy_context()
