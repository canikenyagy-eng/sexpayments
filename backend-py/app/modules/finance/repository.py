from typing import List, Optional, Sequence

from sqlalchemy import String, cast, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency, WithdrawalStatus
from app.common.enums.users import UserRole
from app.modules.base.repository import BaseRepository
from app.modules.finance.models import Balance, LedgerEntry, WithdrawalRequest


class BalanceRepository(BaseRepository[Balance]):
    def __init__(self, session: AsyncSession):
        super().__init__(Balance, session)

    async def get_user_balance(
        self, user_id: int, b_type: BalanceType, currency: Currency, for_update: bool = False
    ) -> Optional[Balance]:
        stmt = select(Balance).where(
            Balance.user_id == user_id,
            Balance.type == b_type,
            Balance.currency == currency,
        )
        if for_update:
            # A locked balance read must reflect the committed DB row, not a
            # stale identity-map copy (expire_on_commit=False) — otherwise the
            # FOR UPDATE lock is defeated and the sufficiency check / read-
            # modify-write in transfer() runs on pre-lock values (lost update /
            # overdraft). populate_existing forces the refresh.
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_merchant_balance(
        self, merchant_id: int, b_type: BalanceType, currency: Currency, for_update: bool = False
    ) -> Optional[Balance]:
        stmt = select(Balance).where(
            Balance.merchant_id == merchant_id,
            Balance.type == b_type,
            Balance.currency == currency,
        )
        if for_update:
            # A locked balance read must reflect the committed DB row, not a
            # stale identity-map copy (expire_on_commit=False) — otherwise the
            # FOR UPDATE lock is defeated and the sufficiency check / read-
            # modify-write in transfer() runs on pre-lock values (lost update /
            # overdraft). populate_existing forces the refresh.
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_payout_terminal_balance(
        self, payout_terminal_id: int, b_type: BalanceType, currency: Currency, for_update: bool = False
    ) -> Optional[Balance]:
        stmt = select(Balance).where(
            Balance.payout_terminal_id == payout_terminal_id,
            Balance.type == b_type,
            Balance.currency == currency,
        )
        if for_update:
            # A locked balance read must reflect the committed DB row, not a
            # stale identity-map copy (expire_on_commit=False) — otherwise the
            # FOR UPDATE lock is defeated and the sufficiency check / read-
            # modify-write in transfer() runs on pre-lock values (lost update /
            # overdraft). populate_existing forces the refresh.
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_system_balance(
        self, b_type: BalanceType, currency: Currency, for_update: bool = False
    ) -> Optional[Balance]:
        stmt = select(Balance).where(
            Balance.is_system == True,
            Balance.type == b_type,
            Balance.currency == currency,
        )
        if for_update:
            # A locked balance read must reflect the committed DB row, not a
            # stale identity-map copy (expire_on_commit=False) — otherwise the
            # FOR UPDATE lock is defeated and the sufficiency check / read-
            # modify-write in transfer() runs on pre-lock values (lost update /
            # overdraft). populate_existing forces the refresh.
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_my_balances(self, user_id: int, merchant_id: Optional[int] = None) -> List[Balance]:
        stmt = select(Balance)
        if merchant_id is not None:
            stmt = stmt.where(Balance.merchant_id == merchant_id)
        else:
            stmt = stmt.where(Balance.user_id == user_id)
        stmt = stmt.order_by(Balance.id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_all(self, *, skip: int = 0, limit: int = 500) -> List[Balance]:
        stmt = select(Balance).order_by(Balance.id).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_for_update(self, id: int) -> Optional[Balance]:
        # populate_existing: refresh the locked row into the identity map so the
        # sufficiency check + read-modify-write in transfer() see the committed
        # value, not a stale preloaded copy (see get_or_create_* preloads).
        stmt = (
            select(Balance)
            .where(Balance.id == id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class LedgerRepository(BaseRepository[LedgerEntry]):
    def __init__(self, session: AsyncSession):
        super().__init__(LedgerEntry, session)

    async def list_entries(
        self,
        *,
        reference_type: Optional[LedgerReferenceType] = None,
        reference_types: Optional[Sequence[LedgerReferenceType]] = None,
        balance_ids: Optional[Sequence[int]] = None,
        skip: int = 0,
        limit: int = 100,
        order_search: Optional[str] = None,
        user_login: Optional[str] = None,
        amount_from: Optional[float] = None,
        amount_to: Optional[float] = None,
    ) -> List[LedgerEntry]:
        stmt = select(LedgerEntry)
        if reference_type:
            stmt = stmt.where(LedgerEntry.reference_type == reference_type)
        if reference_types:
            stmt = stmt.where(LedgerEntry.reference_type.in_(reference_types))
        if balance_ids:
            stmt = stmt.where(
                or_(
                    LedgerEntry.from_balance_id.in_(balance_ids),
                    LedgerEntry.to_balance_id.in_(balance_ids),
                )
            )
        if amount_from is not None:
            stmt = stmt.where(LedgerEntry.amount >= amount_from)
        if amount_to is not None:
            stmt = stmt.where(LedgerEntry.amount <= amount_to)

        if order_search:
            from app.modules.orders.models import Order
            conds = []
            if order_search.isdigit():
                conds.append(LedgerEntry.reference_id == order_search)
            order_ids_subq = select(cast(Order.id, String)).where(
                (cast(Order.uuid, String).ilike(f"%{order_search}%"))
                | (Order.external_id.ilike(f"%{order_search}%"))
            )
            conds.append(LedgerEntry.reference_id.in_(order_ids_subq))
            stmt = stmt.where(or_(*conds))

        if user_login:
            from app.modules.users.models import User
            user_ids_subq = select(User.id).where(User.username.ilike(f"%{user_login}%"))
            balance_ids_subq = select(Balance.id).where(Balance.user_id.in_(user_ids_subq))
            stmt = stmt.where(
                or_(
                    LedgerEntry.from_balance_id.in_(balance_ids_subq),
                    LedgerEntry.to_balance_id.in_(balance_ids_subq),
                )
            )

        stmt = stmt.order_by(desc(LedgerEntry.created_at)).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_balance_and_reference(
        self,
        *,
        balance_id: int,
        reference_type: LedgerReferenceType,
    ) -> List[LedgerEntry]:
        """Return all ledger entries for `reference_type` that touch a balance.

        Picks rows where `balance_id` appears either as `to_balance_id`
        (incoming, e.g. a teamlead reward payout) or `from_balance_id`
        (outgoing, e.g. a `_reversal` of a previously paid reward), so the
        caller can see the net history. Ordered newest first.
        """
        stmt = (
            select(LedgerEntry)
            .where(
                LedgerEntry.reference_type == reference_type,
                or_(
                    LedgerEntry.to_balance_id == balance_id,
                    LedgerEntry.from_balance_id == balance_id,
                ),
            )
            .order_by(desc(LedgerEntry.created_at))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class WithdrawalRequestRepository(BaseRepository[WithdrawalRequest]):
    def __init__(self, session: AsyncSession):
        super().__init__(WithdrawalRequest, session)

    async def get_for_update(self, request_id: int) -> Optional[WithdrawalRequest]:
        """Row-lock the request (``SELECT … FOR UPDATE``) so concurrent
        approve/reject (double-click, retried request, two admins) serialise:
        the second blocks until the first commits, then re-reads a non-PENDING
        status and is rejected — instead of paying out / refunding the same
        withdrawal twice from the shared ESCROW."""
        stmt = select(WithdrawalRequest).where(WithdrawalRequest.id == request_id).with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_uuid_and_merchant(self, uuid: str, merchant_id: int) -> Optional[WithdrawalRequest]:
        stmt = select(WithdrawalRequest).where(
            WithdrawalRequest.uuid == uuid,
            WithdrawalRequest.user_role == UserRole.MERCHANT,
            WithdrawalRequest.merchant_id == merchant_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_merchant(
        self,
        merchant_id: int,
        *,
        status: Optional[WithdrawalStatus] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[WithdrawalRequest]:
        """Legacy per-terminal listing — kept for the terminal view."""
        stmt = select(WithdrawalRequest).where(
            WithdrawalRequest.user_role == UserRole.MERCHANT,
            WithdrawalRequest.merchant_id == merchant_id,
        )
        if status:
            stmt = stmt.where(WithdrawalRequest.status == status)
        stmt = stmt.order_by(WithdrawalRequest.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_merchant_owner(
        self,
        owner_user_id: int,
        *,
        status: Optional[WithdrawalStatus] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[WithdrawalRequest]:
        """List every merchant withdrawal for a given owner (across terminals + sweep-all)."""
        stmt = select(WithdrawalRequest).where(
            WithdrawalRequest.user_role == UserRole.MERCHANT,
            WithdrawalRequest.user_id == owner_user_id,
        )
        if status:
            stmt = stmt.where(WithdrawalRequest.status == status)
        stmt = stmt.order_by(WithdrawalRequest.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_user(
        self,
        user_id: int,
        user_role: UserRole,
        *,
        status: Optional[WithdrawalStatus] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[WithdrawalRequest]:
        stmt = select(WithdrawalRequest).where(
            WithdrawalRequest.user_id == user_id,
            WithdrawalRequest.user_role == user_role,
        )
        if status:
            stmt = stmt.where(WithdrawalRequest.status == status)
        stmt = stmt.order_by(WithdrawalRequest.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_all(
        self,
        *,
        status: Optional[WithdrawalStatus] = None,
        skip: int = 0,
        limit: int = 100,
        user_role: Optional[UserRole] = None,
        user_login: Optional[str] = None,
    ) -> List[WithdrawalRequest]:
        stmt = select(WithdrawalRequest)
        if status:
            stmt = stmt.where(WithdrawalRequest.status == status)
        if user_role:
            stmt = stmt.where(WithdrawalRequest.user_role == user_role)
        if user_login:
            from app.modules.users.models import User
            # Since migration 016 `user_id` always references users.id for every
            # role (merchant withdrawals point at the merchant owner; a
            # terminal-scoped withdrawal additionally fills `merchant_id`). A
            # single lookup against User.username now covers every role.
            matching_user_ids = select(User.id).where(User.username.ilike(f"%{user_login}%"))
            stmt = stmt.where(WithdrawalRequest.user_id.in_(matching_user_ids))
        stmt = stmt.order_by(WithdrawalRequest.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
