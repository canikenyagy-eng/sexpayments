from enum import Enum

class RateSource(str, Enum):
    """Источники получения курсов валют"""
    BYBIT = "bybit"    # Биржа Bybit
    RAPIRA = "rapira"  # Биржа Rapira


class OrderBookSide(str, Enum):
    """Сторона в стакане ордеров (Order Book)"""
    BUY = "buy"   # Покупка (зеленая сторона)
    SELL = "sell" # Продажа (красная сторона)
