from enum import Enum


class AchievementRuleType(str, Enum):
    """Тип правила достижения/бонуса трейдера.

    Каждый тип имеет свой evaluator в реестре
    (``app/modules/achievements/evaluators.py``). Движок и хранилище от типов не
    зависят — добавление нового вида бонуса = новый evaluator + запись в реестр,
    без изменения расчёта/материализации. Правила и их параметры конфигурируются
    из настроек площадки (``achievement_rules``, JSON)."""

    # Единая модель: стрик-гейт + уровень по СРЕДНЕМУ обороту за X дней. Трейдер
    # разблокирует бонус, удержав ``streak_days`` дней подряд с оборотом ≥
    # ``min_daily_volume``; размер бонуса = полоса среднего оборота за эти X дней.
    STREAK_VOLUME_TIER = "streak_volume_tier"

    # Legacy (до объединения стрика и уровня): больше не имеют evaluator'а —
    # старый конфиг с этими типами движок пропускает. Оставлены, чтобы значения
    # PG-enum ``achievementruletype`` совпадали с Python-enum.
    CONSECUTIVE_DAYS_VOLUME = "consecutive_days_volume"
    DAILY_VOLUME_TIER = "daily_volume_tier"
