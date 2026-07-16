from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)

from app.common.enums.achievements import AchievementRuleType
from app.common.types import utcnow
from app.infrastructure.db.base import Base


class TraderDailyVolume(Base):
    """Дневной агрегат обработанного объёма трейдера — одна строка на (трейдер, день).

    Наполняется фоновыми джобами (сегодняшний день — каждые ~5 мин, прошлые дни
    финализируются ночью), НЕ на горячем пути. Это метрика, по которой движок
    достижений считает стрики/уровни, вместо тяжёлого ``GROUP BY`` по ``orders``.
    ``trader_user_id`` = ``users.id`` (как ``order.trader_id``)."""

    __tablename__ = "trader_daily_volume"
    __table_args__ = (
        UniqueConstraint("trader_user_id", "date", name="uq_trader_daily_volume_day"),
    )

    id = Column(Integer, primary_key=True)
    trader_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date = Column(Date, nullable=False, index=True)
    amount_usdt = Column(Numeric(18, 4), nullable=False, default=0)
    amount_rub = Column(Numeric(18, 4), nullable=False, default=0)
    order_count = Column(Integer, nullable=False, default=0)
    updated_at = Column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class TraderAchievement(Base):
    """Текущее состояние достижения трейдера по ОДНОМУ правилу (тип). Upsert-ится
    движком при пересчёте — источник блока «достижения» в кабинете трейдера.

    Итоговый бонус (сумма по всем правилам, с общим капом) материализуется в
    ``traders.achievement_bonus_percent`` для горячего пути расчёта комиссии; здесь
    хранится РАЗБИВКА — какой уровень по какому правилу сейчас разблокирован."""

    __tablename__ = "trader_achievements"
    __table_args__ = (
        UniqueConstraint("trader_user_id", "rule_type", name="uq_trader_achievement_rule"),
    )

    id = Column(Integer, primary_key=True)
    trader_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_type = Column(
        Enum(
            AchievementRuleType,
            name="achievementruletype",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    # Человекочитаемый ключ достигнутого уровня (напр. "7d" или "10000+") — для UI.
    level_key = Column(String(64), nullable=True)
    bonus_percent = Column(Numeric(5, 2), nullable=False, default=0)
    updated_at = Column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
