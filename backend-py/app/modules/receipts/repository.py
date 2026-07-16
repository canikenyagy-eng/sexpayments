import uuid
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.receipt_moderations import _RECEIPT_VISIBLE_TO_TRADER, ModerationDecision
from app.modules.base.repository import BaseRepository
from app.modules.orders.models import Order
from app.modules.receipts.models import Receipt, ReceiptModeration


class ReceiptRepository(BaseRepository[Receipt]):
    def __init__(self, session: AsyncSession):
        super().__init__(Receipt, session)

    async def list_for_order(
        self, order_id: int, *, visible_to_trader_only: bool = False
    ) -> List[Receipt]:
        """All receipts of an order, oldest first.

        ``visible_to_trader_only`` filters to the statuses a trader may see
        (mirrors ``is_receipt_visible_to_trader`` — hides receipts still in
        premoderation).
        """
        stmt = select(Receipt).where(Receipt.order_id == order_id)
        if visible_to_trader_only:
            stmt = stmt.where(Receipt.moderation_status.in_(list(_RECEIPT_VISIBLE_TO_TRADER)))
        stmt = stmt.order_by(Receipt.created_at.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_for_dispute(self, dispute_id: int) -> List[Receipt]:
        stmt = (
            select(Receipt)
            .where(Receipt.dispute_id == dispute_id)
            .order_by(Receipt.created_at.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_by_uuid(self, uuid_str: str) -> Optional[Receipt]:
        try:
            u = uuid.UUID(uuid_str)
        except (ValueError, TypeError):
            return None
        stmt = select(Receipt).where(Receipt.uuid == u)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def find_by_sha_for_order(self, order_id: int, sha256: str) -> Optional[Receipt]:
        """The existing receipt with this file hash on this order, if any
        (used to reject duplicate uploads)."""
        if not sha256:
            return None
        stmt = select(Receipt).where(
            Receipt.order_id == order_id, Receipt.sha256 == sha256
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def count_for_order(self, order_id: int) -> int:
        stmt = select(func.count(Receipt.id)).where(Receipt.order_id == order_id)
        return int((await self.session.execute(stmt)).scalar_one())

    async def get_latest_for_order(self, order_id: int) -> Optional[Receipt]:
        """The most recently created receipt of an order — the one a just-fired
        trader / premoderation notification is about. Callers read its
        ``dispute_id`` to tell whether that receipt is dispute evidence, so the
        message can be worded differently from a first-time check."""
        stmt = (
            select(Receipt)
            .where(Receipt.order_id == order_id)
            .order_by(desc(Receipt.id))
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalars().first()


class ReceiptModerationRepository(BaseRepository[ReceiptModeration]):
    """Data access for the receipt-moderation (human-review) log."""

    def __init__(self, session: AsyncSession):
        super().__init__(ReceiptModeration, session)

    async def get_latest_for_order(self, order_id: int) -> Optional[ReceiptModeration]:
        """Newest moderation row for the order (PENDING or already decided)."""
        result = await self.session.execute(
            select(ReceiptModeration)
            .where(ReceiptModeration.order_id == order_id)
            .order_by(desc(ReceiptModeration.id))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_for_order(self, order_id: int) -> List[ReceiptModeration]:
        """All moderation rows for the order — used by the admin order page."""
        result = await self.session.execute(
            select(ReceiptModeration)
            .where(ReceiptModeration.order_id == order_id)
            .order_by(desc(ReceiptModeration.id))
        )
        return list(result.scalars().all())

    async def list_with_filters(
        self,
        decision: Optional[ModerationDecision] = None,
        pending_only: bool = False,
        merchant_id: Optional[int] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Tuple[List[Tuple[ReceiptModeration, Order]], int]:
        """Admin debug-list. JOIN с orders, чтобы UI сразу видел uuid/merchant.

        ``pending_only`` is a convenience flag — pending row is `decision IS NULL`.
        Returns (rows, total) where total is the full-set count for paging.
        """
        base = select(ReceiptModeration, Order).join(
            Order, Order.id == ReceiptModeration.order_id
        )

        if decision is not None:
            base = base.where(ReceiptModeration.decision == decision)
        if pending_only:
            base = base.where(ReceiptModeration.decision.is_(None))
        if merchant_id is not None:
            base = base.where(Order.merchant_id == merchant_id)
        if date_from is not None:
            base = base.where(ReceiptModeration.created_at >= date_from)
        if date_to is not None:
            base = base.where(ReceiptModeration.created_at <= date_to)

        count_q = select(func.count()).select_from(base.subquery())
        total = (await self.session.execute(count_q)).scalar_one()

        page = base.order_by(desc(ReceiptModeration.created_at)).offset(skip).limit(limit)
        result = await self.session.execute(page)
        rows = [(row.ReceiptModeration, row.Order) for row in result.all()]
        return rows, total
