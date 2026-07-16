from enum import Enum

class RequisiteStatus(str, Enum):
    """Статусы платежных реквизитов"""
    ENABLED = "enabled"    # Реквизит активен и может назначаться на новые заказы
    DISABLED = "disabled"  # Реквизит выключен трейдером
    BLOCKED = "blocked"    # Реквизит заблокирован администрацией
    ARCHIVED = "archived"  # Реквизит удален в архив (не используется, сохранен для истории)

