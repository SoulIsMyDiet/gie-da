"""Backend: krótki opis przeznaczony dla późniejszej weryfikacji sygnałów."""

STRATEGY_CONTEXT = """
Strategia BTCUSDStrategyV1 analizuje zamknięte świece M1 BTC/USD, używając M5
do potwierdzenia trendu. Wykrywa wybicie po ściśnięciu zmienności, korektę w
trendzie oraz odwrócenie po zebraniu płynności. Stosuje EMA, RSI, ATR, MACD,
zmienność i strukturę ceny. Horyzont oceny wynosi maksymalnie 20 minut.
Strategia nie ma dostępu do BID/ASK ani rzeczywistego spreadu; koszt wejścia i
wyjścia jest jedynie założeniem. Sygnał jest kandydatem do oceny, a nie
gwarancją zysku ani zleceniem transakcji.
"""
