"""Backend: krótki opis przeznaczony dla późniejszej weryfikacji sygnałów."""

STRATEGY_CONTEXT = """
Strategia BTCUSDStrategyV1 analizuje zamknięte świece M1 BTC/USD, używając M5
do potwierdzenia trendu. Wykrywa wybicie po ściśnięciu zmienności, korektę w
trendzie oraz odwrócenie po zebraniu płynności. Stosuje EMA, RSI, ATR, MACD,
zmienność i strukturę ceny. Horyzont oceny wynosi maksymalnie 20 minut.
Strategia wykorzystuje kwotowania BID/ASK i spread ask-bid z ocenianej świecy,
jeśli są dostępne. Bez kwotowań nie zakłada kosztu ani nie ocenia opłacalności
po kosztach. Sygnał jest kandydatem do oceny, a nie gwarancją zysku ani
zleceniem transakcji.
"""
