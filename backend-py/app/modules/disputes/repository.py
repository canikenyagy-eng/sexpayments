from typing import List, Optional
from uuid import UUID

from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.base.repository import BaseRepository
from app.modules.disputes.models import Dispute
from app.modules.orders.models import Order


class DisputeRepository(BaseRepository[Dispute]):
    def __init__(self, session: AsyncSession):
        super().__init__(Dispute, session)

    async def list_admin(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
        id_search: Optional[str] = None,
        trader_login: Optional[str] = None,
        merchant_login: Optional[str] = None,
        status: Optional[str] = None,
        reason: Optional[str] = None,
        payment_method: Optional[str] = None,
        amount_from: Optional[float] = None,
        amount_to: Optional[float] = None,
    ) -> List[Dispute]:
        from app.modules.merchants.models import Merchant
        from app.modules.users.models import User as UserModel

        stmt = select(Dispute).join(Order, Order.id == Dispute.order_id)

        if status:
            stmt = stmt.where(Dispute.status == status)
        if reason:
            stmt = stmt.where(Dispute.reason == reason)
        if payment_method:
            stmt = stmt.where(Order.payment_method == payment_method)
        if amount_from is not None:
            stmt = stmt.where(Order.amount >= amount_from)
        if amount_to is not None:
            stmt = stmt.where(Order.amount <= amount_to)

        if id_search:
            term = id_search.strip()
            conditions = [
                Order.external_id.ilike(f"%{term}%"),
                cast(Order.uuid, String).ilike(f"%{term}%"),
                cast(Dispute.uuid, String).ilike(f"%{term}%"),
            ]
            if term.isdigit():
                conditions.append(Order.id == int(term))
                conditions.append(Dispute.id == int(term))
            stmt = stmt.where(or_(*conditions))

        if trader_login:
            trader_ids_sub = (
                select(UserModel.id).where(
                    UserModel.username.ilike(f"%{trader_login}%"),
                    UserModel.role == "trader",
                )
            )
            stmt = stmt.where(Order.trader_id.in_(trader_ids_sub))

        if merchant_login:
            merchant_ids_sub = (
                select(Merchant.id)
                .join(UserModel, Merchant.user_id == UserModel.id)
                .where(UserModel.username.ilike(f"%{merchant_login}%"))
            )
            stmt = stmt.where(Dispute.merchant_id.in_(merchant_ids_sub))

        stmt = stmt.order_by(Dispute.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_order_id(self, order_id: int) -> Optional[Dispute]:
        stmt = select(Dispute).where(Dispute.order_id == order_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def lock_status(self, dispute_id: int):
        """Row-lock the dispute (``SELECT status … FOR UPDATE``) and return its
        COMMITTED status. Serialises concurrent resolve / reject / trader-accept:
        the second caller blocks here until the first commits, then reads the
        already-closed status and bails out on the guard — instead of both
        passing an unlocked read and double-writing the dispute row + audit log
        (mirrors OrderRepository.lock_status)."""
        stmt = select(Dispute.status).where(Dispute.id == dispute_id).with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_uuid_and_merchant(self, uuid_str: str, merchant_id: int) -> Optional[Dispute]:
        stmt = select(Dispute).where(
            Dispute.uuid == uuid_str,
            Dispute.merchant_id == merchant_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_uuid_and_trader(self, uuid_str: str, trader_id: int) -> Optional[Dispute]:
        """Return dispute if the given trader is assigned to the underlying order."""
        stmt = (
            select(Dispute)
            .join(Order, Order.id == Dispute.order_id)
            .where(
                Dispute.uuid == uuid_str,
                Order.trader_id == trader_id,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_merchant(
        self, merchant_id: int, *, skip: int = 0, limit: int = 50
    ) -> List[Dispute]:
        stmt = (
            select(Dispute)
            .where(Dispute.merchant_id == merchant_id)
            .order_by(Dispute.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_trader(
        self,
        trader_id: int,
        *,
        status: Optional[str] = None,
        reason: Optional[str] = None,
        payment_method: Optional[str] = None,
        id_search: Optional[str] = None,
        amount_from: Optional[float] = None,
        amount_to: Optional[float] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[Dispute]:
        """Return disputes where the underlying order is assigned to this trader."""
        stmt = (
            select(Dispute)
            .join(Order, Order.id == Dispute.order_id)
            .where(Order.trader_id == trader_id)
        )
        if status:
            stmt = stmt.where(Dispute.status == status)
        if reason:
            stmt = stmt.where(Dispute.reason == reason)
        if payment_method:
            stmt = stmt.where(Order.payment_method == payment_method)
        if amount_from is not None:
            stmt = stmt.where(Order.amount >= amount_from)
        if amount_to is not None:
            stmt = stmt.where(Order.amount <= amount_to)
        if id_search:
            term = id_search.strip()
            conditions = [
                Order.external_id.ilike(f"%{term}%"),
                cast(Order.uuid, String).ilike(f"%{term}%"),
                cast(Dispute.uuid, String).ilike(f"%{term}%"),
            ]
            if term.isdigit():
                conditions.append(Order.id == int(term))
                conditions.append(Dispute.id == int(term))
            stmt = stmt.where(or_(*conditions))
        stmt = stmt.order_by(Dispute.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
