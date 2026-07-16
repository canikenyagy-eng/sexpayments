import uuid as uuid_lib
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Sequence

from sqlalchemy import String, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutReceiptStatus, PayoutStatus
from app.modules.base.repository import BaseRepository
from app.modules.payouts.models import Payout, PayoutReceipt


class PayoutRepository(BaseRepository[Payout]):
    def __init__(self, session: AsyncSession):
        super().__init__(Payout, session)

    @staticmethod
    def _as_uuid(value):
        """Coerce a uuid string to a UUID (the column is UUID(as_uuid=True); SQLite
        won't accept a bare string). Returns None on a malformed id → no match."""
        if isinstance(value, uuid_lib.UUID):
            return value
        try:
            return uuid_lib.UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            return None

    async def get_by_uuid(self, uuid: str) -> Optional[Payout]:
        u = self._as_uuid(uuid)
        if u is None:
            return None
        res = await self.session.execute(select(Payout).where(Payout.uuid == u))
        return res.scalars().first()

    async def get_by_uuid_and_terminal(self, uuid: str, terminal_id: int) -> Optional[Payout]:
        u = self._as_uuid(uuid)
        if u is None:
            return None
        res = await self.session.execute(
            select(Payout).where(Payout.uuid == u, Payout.payout_terminal_id == terminal_id)
        )
        return res.scalars().first()

    async def get_by_external_and_terminal(self, external_id: str, terminal_id: int) -> Optional[Payout]:
        res = await self.session.execute(
            select(Payout).where(Payout.external_id == external_id, Payout.payout_terminal_id == terminal_id)
        )
        return res.scalars().first()

    async def lock(self, payout_id: int) -> Optional[Payout]:
        """SELECT ... FOR UPDATE — used for the exclusive claim / state transition.

        populate_existing: callers preload the payout (plain SELECT) before
        locking; with expire_on_commit=False a re-locked SELECT of an
        already-mapped row returns STALE attributes, so the exclusive-claim
        check and change_status' old_status read would see the pre-lock status
        (double-claim / double-settle). Force the identity-map refresh so the
        locked read reflects the committed row (mirrors the balance lock + the
        orders scalar lock_status).
        """
        res = await self.session.execute(
            select(Payout)
            .where(Payout.id == payout_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return res.scalars().first()

    # ── receipts (partial payments) ──────────────────────────────────────────

    async def create_receipt(self, data: dict) -> PayoutReceipt:
        receipt = PayoutReceipt(**data)
        self.session.add(receipt)
        await self.session.flush()
        return receipt

    async def get_receipt(self, receipt_id: int) -> Optional[PayoutReceipt]:
        res = await self.session.execute(select(PayoutReceipt).where(PayoutReceipt.id == receipt_id))
        return res.scalars().first()

    async def update_receipt(self, receipt_id: int, fields: dict) -> Optional[PayoutReceipt]:
        receipt = await self.get_receipt(receipt_id)
        if receipt is None:
            return None
        for k, v in fields.items():
            setattr(receipt, k, v)
        await self.session.flush()
        return receipt

    async def list_receipts(self, payout_id: int) -> List[PayoutReceipt]:
        res = await self.session.execute(
            select(PayoutReceipt).where(PayoutReceipt.payout_id == payout_id).order_by(PayoutReceipt.id)
        )
        return list(res.scalars().all())

    async def sum_receipts(self, payout_id: int, *, statuses: Sequence[PayoutReceiptStatus]) -> Decimal:
        """Sum of receipt amounts in the given statuses (e.g. APPROVED, or
        non-rejected = PENDING+APPROVED for remaining-capacity checks)."""
        stmt = select(func.coalesce(func.sum(PayoutReceipt.amount), 0)).where(
            PayoutReceipt.payout_id == payout_id,
            PayoutReceipt.status.in_(list(statuses)),
        )
        return Decimal(str((await self.session.execute(stmt)).scalar_one()))

    async def sum_pending_doliv_for_requisite(self, requisite_id: int) -> Decimal:
        """SUM(amount) of not-yet-settled доливы (CREATED/CLAIMED) targeting this
        requisite. They will fill its turnover on execute, so they count against
        the remaining daily limit at create time — otherwise concurrent доливы
        each pass the limit check (turnover isn't bumped until execute) and
        over-commit the requisite's daily cap."""
        stmt = select(func.coalesce(func.sum(Payout.amount), 0)).where(
            Payout.is_doliv.is_(True),
            Payout.refill_requisite_id == requisite_id,
            Payout.status.in_([PayoutStatus.CREATED, PayoutStatus.CLAIMED]),
        )
        return Decimal(str((await self.session.execute(stmt)).scalar_one()))

    async def count_receipts(self, payout_id: int, *, statuses: Sequence[PayoutReceiptStatus]) -> int:
        stmt = select(func.count(PayoutReceipt.id)).where(
            PayoutReceipt.payout_id == payout_id,
            PayoutReceipt.status.in_(list(statuses)),
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def list_pool(
        self, *, terminal_ids: Optional[Sequence[int]] = None, currency: Optional[Currency] = None,
        method: Optional[PaymentMethod] = None, skip: int = 0, limit: int = 50,
    ) -> List[Payout]:
        """Claimable payouts (CREATED), soonest-expiring first. ``terminal_ids``
        restricts to the trader's ACL-allowed terminals."""
        stmt = select(Payout).where(
            Payout.status == PayoutStatus.CREATED,
            Payout.is_doliv.is_(False),  # доливы have their own pool (DolivService), never the payout pool
        )
        if terminal_ids is not None:
            stmt = stmt.where(Payout.payout_terminal_id.in_(list(terminal_ids)))
        if currency is not None:
            stmt = stmt.where(Payout.currency == currency)
        if method is not None:
            stmt = stmt.where(Payout.payment_method == method)
        stmt = stmt.order_by(Payout.expires_at.asc().nulls_last()).offset(skip).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def list_for_trader(
        self, trader_id: int, *, statuses: Optional[Sequence[PayoutStatus]] = None,
        skip: int = 0, limit: int = 50,
    ) -> List[Payout]:
        stmt = select(Payout).where(Payout.trader_id == trader_id, Payout.is_doliv.is_(False))
        if statuses:
            stmt = stmt.where(Payout.status.in_(list(statuses)))
        stmt = stmt.order_by(Payout.created_at.desc()).offset(skip).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def list_admin(
        self, *, status: Optional[PayoutStatus] = None, terminal_id: Optional[int] = None,
        trader_id: Optional[int] = None, skip: int = 0, limit: int = 50,
    ) -> List[Payout]:
        stmt = select(Payout).where(Payout.is_doliv.is_(False))
        if status is not None:
            stmt = stmt.where(Payout.status == status)
        if terminal_id is not None:
            stmt = stmt.where(Payout.payout_terminal_id == terminal_id)
        if trader_id is not None:
            stmt = stmt.where(Payout.trader_id == trader_id)
        stmt = stmt.order_by(Payout.created_at.desc()).offset(skip).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def count_admin(
        self, *, status: Optional[PayoutStatus] = None, terminal_id: Optional[int] = None,
        trader_id: Optional[int] = None,
    ) -> int:
        stmt = select(func.count(Payout.id)).where(Payout.is_doliv.is_(False))
        if status is not None:
            stmt = stmt.where(Payout.status == status)
        if terminal_id is not None:
            stmt = stmt.where(Payout.payout_terminal_id == terminal_id)
        if trader_id is not None:
            stmt = stmt.where(Payout.trader_id == trader_id)
        return int((await self.session.execute(stmt)).scalar_one())

    @staticmethod
    def _owner_filters(
        stmt, terminal_ids: Sequence[int], *, status, payment_method, search,
    ):
        """Shared WHERE clauses for the merchant-cabinet (owner) payout list +
        count. Scoped to the owner's terminals; optional status / method / free
        text (UUID or external_id) filters."""
        stmt = stmt.where(Payout.payout_terminal_id.in_(list(terminal_ids)))
        if status is not None:
            stmt = stmt.where(Payout.status == status)
        if payment_method is not None:
            stmt = stmt.where(Payout.payment_method == payment_method)
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(or_(
                Payout.external_id.ilike(like),
                cast(Payout.uuid, String).ilike(like),
            ))
        return stmt

    async def list_for_terminals(
        self, terminal_ids: Sequence[int], *, status: Optional[PayoutStatus] = None,
        payment_method: Optional[PaymentMethod] = None, search: Optional[str] = None,
        skip: int = 0, limit: int = 50,
    ) -> List[Payout]:
        """Payouts across the given terminals (the owner's), newest first."""
        if not terminal_ids:
            return []
        stmt = self._owner_filters(
            select(Payout), terminal_ids,
            status=status, payment_method=payment_method, search=search,
        ).order_by(Payout.created_at.desc()).offset(skip).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def count_for_terminals(
        self, terminal_ids: Sequence[int], *, status: Optional[PayoutStatus] = None,
        payment_method: Optional[PaymentMethod] = None, search: Optional[str] = None,
    ) -> int:
        if not terminal_ids:
            return 0
        stmt = self._owner_filters(
            select(func.count(Payout.id)), terminal_ids,
            status=status, payment_method=payment_method, search=search,
        )
        return int((await self.session.execute(stmt)).scalar_one())

    # ── worker queries ──────────────────────────────────────────────────────

    async def list_expired(self, now: datetime, limit: int = 200) -> List[Payout]:
        # Money safety: never auto-expire (and refund the merchant) a payout the
        # trader has already started paying — i.e. one with ANY live (non-rejected)
        # receipt. Those are left for admin moderation / TTL-independent handling.
        has_live_receipt = exists().where(
            PayoutReceipt.payout_id == Payout.id,
            PayoutReceipt.status != PayoutReceiptStatus.REJECTED,
        )
        stmt = select(Payout).where(
            Payout.is_doliv.is_(False),  # доливы have their own expiry sweep (refund_doliv); never refund_payout(None)
            Payout.status.in_([PayoutStatus.CREATED, PayoutStatus.CLAIMED, PayoutStatus.AWAITING_CHECK]),
            Payout.expires_at.is_not(None), Payout.expires_at < now,
            ~has_live_receipt,
        ).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def list_stale_claims(self, now: datetime, limit: int = 200) -> List[Payout]:
        stmt = select(Payout).where(
            Payout.is_doliv.is_(False),  # доливы handled by the до/ sweep, not the payout terminal worker
            Payout.status == PayoutStatus.CLAIMED,
            Payout.claim_expires_at.is_not(None), Payout.claim_expires_at < now,
        ).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def list_holds_due(self, now: datetime, limit: int = 200) -> List[Payout]:
        stmt = select(Payout).where(
            Payout.is_doliv.is_(False),  # доливы don't use the trader-hold flow
            Payout.status == PayoutStatus.COMPLETED,
            Payout.trader_hold_until.is_not(None), Payout.trader_hold_until < now,
            Payout.hold_released_at.is_(None),
        ).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())
