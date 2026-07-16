from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict


@dataclass
class TraderMetrics:
    """Готовые к оценке метрики одного трейдера.

    Собираются из ``trader_daily_volume`` ОДИН раз перед прогоном всех правил, чтобы
    evaluator'ы не ходили в БД (движок остаётся дешёвым и легко тестируемым).
    Универсальный вход для любого правила: новые метрики добавляются полями сюда,
    существующие evaluator'ы не ломаются."""

    trader_user_id: int
    today: date
    # date -> объём USDT за день. Присутствуют только дни с активностью;
    # отсутствующий день трактуется как 0 (см. ``volume_on``).
    daily_volume_usdt: Dict[date, Decimal] = field(default_factory=dict)

    def volume_on(self, day: date) -> Decimal:
        return self.daily_volume_usdt.get(day, Decimal(0))
