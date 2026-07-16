from enum import Enum

class Currency(str, Enum):
    """Поддерживаемые валюты в системе"""
    RUB = "RUB"   # Российский рубль
    AZN = "AZN"   # Азербайджанский манат
    USDT = "USDT" # Криптовалюта Tether


class WithdrawalStatus(str, Enum):
    """Статусы заявок на вывод средств"""
    PENDING = "pending"    # Ожидает обработки
    SUCCESS = "success"    # Успешно выплачена
    REJECTED = "rejected"  # Отклонена
