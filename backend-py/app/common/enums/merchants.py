from enum import Enum


class TerminalStatus(str, Enum):
    """Статусы терминалов мерчанта"""
    PENDING = "pending"    # На модерации, ожидает подтверждения администратором
    TEST = "test"          # Тестовый режим для интеграции (без реальных финансовых операций)
    ENABLED = "enabled"    # Активен, принимает реальные платежи
    DISABLED = "disabled"  # Временно отключен мерчантом
    BLOCKED = "blocked"    # Заблокирован администрацией
    ARCHIVED = "archived"  # Удален в архив
