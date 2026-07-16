from enum import Enum

class PaymentDirection(str, Enum):
    """Направление платежа"""
    PAYIN = "payin"    # Прием платежа (входящий)
    PAYOUT = "payout"  # Выплата (исходящий)


class PaymentMethod(str, Enum):
    """Методы оплаты"""
    SBP = "sbp"    # Система быстрых платежей
    CARD = "card"  # Перевод по номеру карты
    SIM = "sim"    # Пополнение мобильного телефона (SIM-карты) (Мобильная коммерция)
