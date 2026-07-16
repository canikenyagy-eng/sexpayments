from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.receipt_checks import ReceiptCheckStatus
from app.modules.base.repository import BaseRepository
from app.modules.receipt_checks.models import ReceiptCheck, ReceiptCheckProvider


class ReceiptCheckProviderRepository(BaseRepository[ReceiptCheckProvider]):
    def __init__(self, session: AsyncSession):
        super().__init__(ReceiptCheckProvider, session)

    async def get_active(self) -> Optional[ReceiptCheckProvider]:
        stmt = (
            select(ReceiptCheckProvider)
            .where(ReceiptCheckProvider.is_active.is_(True))
            .order_by(ReceiptCheckProvider.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> List[ReceiptCheckProvider]:
        stmt = select(ReceiptCheckProvider).order_by(ReceiptCheckProvider.id.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_active(self) -> List[ReceiptCheckProvider]:
        """Active providers a trader may choose from, ordered by id so the
        "first active" fallback is deterministic (lowest id wins)."""
        stmt = (
            select(ReceiptCheckProvider)
            .where(ReceiptCheckProvider.is_active.is_(True))
            .order_by(ReceiptCheckProvider.id.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_by_code(self, code: str) -> Optional[ReceiptCheckProvider]:
        stmt = select(ReceiptCheckProvider).where(ReceiptCheckProvider.code == code)
        return (await self.session.execute(stmt)).scalar_one_or_none()


class ReceiptCheckRepository(BaseRepository[ReceiptCheck]):
    def __init__(self, session: AsyncSession):
        super().__init__(ReceiptCheck, session)

    async def find_reusable_for_file(
        self, order_id: int, file_sha256: str
    ) -> Optional[ReceiptCheck]:
        """Look for a previous successful (or cached-from-success) check of
        the same file on the same order. Returns the latest such row, used
        to short-circuit duplicate manual clicks.
        """
        stmt = (
            select(ReceiptCheck)
            .where(
                ReceiptCheck.order_id == order_id,
                ReceiptCheck.file_sha256 == file_sha256,
                ReceiptCheck.status.in_(
                    [ReceiptCheckStatus.SUCCESS, ReceiptCheckStatus.CACHED]
                ),
            )
            .order_by(ReceiptCheck.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def find_active_for_file(
        self, order_id: int, file_sha256: str
    ) -> Optional[ReceiptCheck]:
        """Latest LIVE check (PENDING in-flight, or SUCCESS/CACHED done) for the
        same file on the same order. Used after a unique-index conflict to
        replay the concurrent winner instead of charging the trader twice."""
        stmt = (
            select(ReceiptCheck)
            .where(
                ReceiptCheck.order_id == order_id,
                ReceiptCheck.file_sha256 == file_sha256,
                ReceiptCheck.status.in_(
                    [ReceiptCheckStatus.PENDING, ReceiptCheckStatus.SUCCESS, ReceiptCheckStatus.CACHED]
                ),
            )
            .order_by(ReceiptCheck.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def latest_for_order(self, order_id: int) -> Optional[ReceiptCheck]:
        stmt = (
            select(ReceiptCheck)
            .where(ReceiptCheck.order_id == order_id)
            .order_by(ReceiptCheck.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def latest_for_orders(self, order_ids: List[int]) -> dict[int, ReceiptCheck]:
        """Return the most recent ReceiptCheck per order_id in a single
        SELECT. Used by the trader orders list to render the shield-icon
        verdict without firing one HTTP request per row (the previous
        per-order endpoint generated 20+ parallel requests per page open
        and saturated the API workers)."""
        if not order_ids:
            return {}

        # PostgreSQL DISTINCT ON: keep the highest-id (= latest) row per
        # order_id. Same logic as MAX(id) GROUP BY order_id, but lets us
        # pull the full row in a single round-trip.
        stmt = (
            select(ReceiptCheck)
            .where(ReceiptCheck.order_id.in_(order_ids))
            .order_by(ReceiptCheck.order_id, ReceiptCheck.id.desc())
            .distinct(ReceiptCheck.order_id)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return {r.order_id: r for r in rows}

    async def list_for_order(self, order_id: int) -> List[ReceiptCheck]:
        stmt = (
            select(ReceiptCheck)
            .where(ReceiptCheck.order_id == order_id)
            .order_by(ReceiptCheck.id.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())
