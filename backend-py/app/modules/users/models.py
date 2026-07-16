from sqlalchemy import Boolean, Column, DateTime, Enum, Integer, String

from app.common.enums.users import UserRole
from app.common.types import utcnow
from app.infrastructure.db.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    password = Column(String(255), nullable=False)
    
    # 2FA
    totp_secret = Column(String(64), nullable=True)
    totp_enabled = Column(Boolean, default=False, nullable=False)
    
    # Role
    role = Column(Enum(UserRole), default=UserRole.TRADER, nullable=False)
    
    # Settings
    use_shared_balance = Column(Boolean, default=True, nullable=False)
    is_blocked = Column(Boolean, default=False, nullable=False)

    # IANA timezone (e.g. "Europe/Moscow"). NULL = use browser timezone.
    timezone = Column(String(64), nullable=True)

    # System users back the cascade module: each external provider gets a
    # dedicated User+Trader so the existing finance/escrow flow works unchanged.
    # Excluded from regular trader listings/search.
    is_system = Column(Boolean, default=False, nullable=False, server_default="false")

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    @property
    def is_active(self) -> bool:
        return not self.is_blocked
