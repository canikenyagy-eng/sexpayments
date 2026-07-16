from enum import Enum


class PoolingStrategy(str, Enum):
    """Стратегии выбора реквизитов (пулинга) для новых заказов"""
    RANDOM = "random"                            # Случайный выбор подходящего реквизита
    LEAST_RECENTLY_USED = "least_recently_used"  # Выбор реквизита, который использовался дольше всего назад (LRU)
    BANDIT = "bandit"                            # MAB-селектор по конверсии / диспутам / ставке
    WEIGHTED = "weighted"                        # Взвешенный по priority_score выбор реквизита
