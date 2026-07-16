import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency, WithdrawalStatus
from app.common.enums.users import UserRole
from app.common.types import utcnow
from app.infrastructure.db.base import Base


class Balance(Base):
    __tablename__ = "balances"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=True, index=True)
    # Payout terminals hold their OWN balance (funded by admin top-up); payout
    # freeze/settle/refund move money on this balance, not the payin merchant's.
    payout_terminal_id = Column(Integer, ForeignKey("payout_terminals.id"), nullable=True, index=True)
    is_system = Column(Boolean, default=False, nullable=False, index=True)

    type = Column(Enum(BalanceType), nullable=False)
    currency = Column(Enum(Currency), nullable=False)
    amount = Column(Numeric(15, 4), default=0, nullable=False)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('user_id', 'type', 'currency', name='uq_user_balance'),
        UniqueConstraint('merchant_id', 'type', 'currency', name='uq_merchant_balance'),
        UniqueConstraint('payout_terminal_id', 'type', 'currency', name='uq_payout_terminal_balance'),
    )


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id = Column(Integer, primary_key=True, index=True)
    from_balance_id = Column(Integer, ForeignKey("balances.id"), nullable=True, index=True)
    to_balance_id = Column(Integer, ForeignKey("balances.id"), nullable=True, index=True)
    
    amount = Column(Numeric(15, 4), nullable=False)
    currency = Column(Enum(Currency), nullable=False)
    
    reference_type = Column(Enum(LedgerReferenceType), nullable=False, index=True)
    reference_id = Column(String(100), nullable=False, index=True)
    description = Column(String(255), nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        # Admin "top-up by hash" (CRYPTO_DEPOSIT) is idempotent on the TRC20 tx
        # hash stored in reference_id: the same hash can NEVER credit twice, even
        # under concurrent confirms. Partial unique — scoped to crypto deposits so
        # other reference types (which reuse ids like order/withdrawal ids) are
        # unaffected.
        Index(
            "uq_ledger_crypto_deposit_ref",
            "reference_id",
            unique=True,
            postgresql_where=text("reference_type = 'CRYPTO_DEPOSIT'"),
            sqlite_where=text("reference_type = 'CRYPTO_DEPOSIT'"),
        ),
    )


class WithdrawalRequest(Base):
    __tablename__ = "withdrawal_requests"

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(UUID(as_uuid=True), default=uuid.uuid4, unique=True, index=True, nullable=False)

    user_role = Column(Enum(UserRole), nullable=False, index=True)
    # Semantics of `user_id` depend on `user_role`:
    #   - trader / teamlead → users.id
    #   - merchant          → merchants.id when `merchant_id` is null (legacy
    #     single-terminal withdrawal), else users.id (merchant owner, new
    #     sweep-all withdrawals). The legacy layout is preserved so existing
    #     rows continue to resolve without a data migration.
    user_id = Column(Integer, nullable=False, index=True)

    # Populated only for merchant withdrawals. When set, the request is
    # scoped to a single terminal (funds are frozen on that merchant's
    # balances). When null for a merchant withdrawal, funds are swept from
    # every terminal owned by `user_id` onto the merchant-owner user-level
    # balance at request time and the withdrawal settles from there.
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=True, index=True)

    amount = Column(Numeric(15, 4), nullable=False)
    currency = Column(Enum(Currency), nullable=False)
    destination_address = Column(String(255), nullable=False)
    
    fee_amount = Column(Numeric(15, 4), default=0, nullable=False)
    
    status = Column(Enum(WithdrawalStatus), default=WithdrawalStatus.PENDING, nullable=False, index=True)
    
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    
    processed_at = Column(DateTime(timezone=True), nullable=True)
    processed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    rejection_reason = Column(String(255), nullable=True)
