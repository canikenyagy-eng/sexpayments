from typing import TYPE_CHECKING, List, Optional, Tuple

from sqlalchemy import Select, case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.base.repository import BaseRepository
from app.modules.requisites.models import Requisite, RequisiteLimit

if TYPE_CHECKING:
    from app.modules.orders.models import Order


def ready_requisite_ids_select(trader_user_id: Optional[int] = None) -> Select:
    """``select(Requisite.id)`` of LOCAL requisites that can receive a payin
    RIGHT NOW — the amount-INDEPENDENT projection of
    ``PoolingService._get_base_query``: all static gates (is_active / not
    archived / status ENABLED / source LOCAL / trader ENABLED + is_payin_active
    / positive WORK-USDT balance) PLUS capacity headroom (a free concurrency
    slot AND room for at least ``limit_min_transaction`` in the daily/monthly
    limit, counting already-pending amounts). Optionally scoped to one trader.

    Single source of truth for the sidebar «Реквизиты» badge and the
    «принимают сейчас» filter. Keep in sync with pooling/service.py's gates
    (order-specific gates — exact amount, method/currency match, pinned bank,
    merchant routing — are intentionally omitted: they need a concrete order).
    """
    from app.common.enums.balances import BalanceType
    from app.common.enums.cascading import RequisiteSource
    from app.common.enums.finances import Currency
    from app.common.enums.orders import OrderStatus
    from app.common.enums.requisites import RequisiteStatus
    from app.common.enums.traders import TraderStatus
    from app.modules.finance.models import Balance
    from app.modules.orders.models import Order
    from app.modules.traders.models import Trader

    active_statuses = [
        OrderStatus.PENDING, OrderStatus.RECEIPT_UPLOADED, OrderStatus.DISPUTED,
    ]
    # Per-requisite active-order count + reserved (pending) amount, scoped to the
    # requisite's own currency (join on currency below) — exactly mirrors
    # pooling's per-currency capacity check, robust to a requisite whose currency
    # was changed while old-currency orders are still active.
    active_subq = (
        select(
            Order.requisite_id.label("requisite_id"),
            Order.currency.label("currency"),
            func.count(Order.id).label("active_count"),
            func.coalesce(func.sum(Order.amount), 0).label("pending"),
        )
        .where(Order.status.in_(active_statuses))
        .group_by(Order.requisite_id, Order.currency)
        .subquery()
    )

    stmt = (
        select(Requisite.id)
        .join(RequisiteLimit, RequisiteLimit.requisite_id == Requisite.id)
        .join(Trader, Trader.user_id == Requisite.trader_id)
        .outerjoin(
            active_subq,
            (Requisite.id == active_subq.c.requisite_id)
            & (Requisite.currency == active_subq.c.currency),
        )
        .outerjoin(
            Balance,
            (Balance.user_id == Requisite.trader_id)
            & (Balance.type == BalanceType.WORK)
            & (Balance.currency == Currency.USDT),
        )
        .where(
            Requisite.is_active.is_(True),
            Requisite.is_archived.is_(False),
            Requisite.source == RequisiteSource.LOCAL,
            Requisite.status == RequisiteStatus.ENABLED,
            Trader.status == TraderStatus.ENABLED,
            Trader.is_payin_active.is_(True),
            func.coalesce(Balance.amount, 0) > 0,
            (
                RequisiteLimit.current_daily_turnover
                + func.coalesce(active_subq.c.pending, 0)
                + RequisiteLimit.limit_min_transaction
            ) <= RequisiteLimit.limit_daily,
            (
                RequisiteLimit.current_monthly_turnover
                + func.coalesce(active_subq.c.pending, 0)
                + RequisiteLimit.limit_min_transaction
            ) <= RequisiteLimit.limit_monthly,
            (RequisiteLimit.limit_max_concurrent_orders.is_(None))
            | (
                func.coalesce(active_subq.c.active_count, 0)
                < RequisiteLimit.limit_max_concurrent_orders
            ),
        )
    )
    if trader_user_id is not None:
        stmt = stmt.where(Requisite.trader_id == trader_user_id)
    return stmt


class RequisiteRepository(BaseRepository[Requisite]):
    def __init__(self, session: AsyncSession):
        super().__init__(Requisite, session)

    async def get_with_limits(self, id: int) -> Optional[Requisite]:
        stmt = select(self.model).options(selectinload(self.model.limits)).where(self.model.id == id)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_by_trader(
        self,
        trader_id: int,
        skip: int = 0,
        limit: int = 100,
        *,
        nickname: Optional[str] = None,
        bank: Optional[str] = None,
        payment_method: Optional[str] = None,
        status: Optional[str] = None,
        is_active: Optional[bool] = None,
        include_archived: bool = False,
        state: Optional[str] = None,
        ready_only: bool = False,
    ) -> Tuple[List[Requisite], int]:
        from sqlalchemy import or_ as _or

        from app.common.enums.requisites import RequisiteStatus

        query = select(self.model).options(selectinload(self.model.limits))
        count_query = select(func.count()).select_from(self.model)

        filters = [self.model.trader_id == trader_id]
        # `state` is the unified, user-meaningful availability filter (replaces
        # the raw status/is_active confusion). "archived" opts into archived rows.
        show_archived = include_archived or state == "archived"
        if not show_archived:
            filters.append(self.model.is_archived.is_(False))
        if state == "active":
            filters.append(self.model.status == RequisiteStatus.ENABLED)
            filters.append(self.model.is_active.is_(True))
        elif state == "disabled":
            filters.append(
                _or(self.model.is_active.is_(False), self.model.status == RequisiteStatus.DISABLED)
            )
        elif state == "blocked":
            filters.append(self.model.status == RequisiteStatus.BLOCKED)
        elif state == "archived":
            filters.append(self.model.is_archived.is_(True))
        if ready_only:
            # Only requisites that can take a payin right now (pooling gates).
            filters.append(self.model.id.in_(ready_requisite_ids_select(trader_id)))
        if nickname:
            # Generic search across nickname / account holder / account number
            # so the trader can quickly find a requisite by any of those.
            filters.append(
                _or(
                    self.model.nickname.ilike(f"%{nickname}%"),
                    self.model.account_holder.ilike(f"%{nickname}%"),
                    self.model.account_number.ilike(f"%{nickname}%"),
                )
            )
        if bank:
            filters.append(self.model.bank_name.ilike(f"%{bank}%"))
        if payment_method:
            filters.append(self.model.payment_method == payment_method)
        if status:
            filters.append(self.model.status == status)
        if is_active is not None:
            filters.append(self.model.is_active.is_(is_active))

        for f in filters:
            query = query.where(f)
            count_query = count_query.where(f)

        query = query.order_by(self.model.id.desc()).offset(skip).limit(limit)

        result = await self.session.execute(query)
        count_result = await self.session.execute(count_query)

        return list(result.scalars().all()), count_result.scalar_one()

    async def get_all_with_limits(
        self,
        skip: int = 0,
        limit: int = 100,
        *,
        trader_login: Optional[str] = None,
        payment_method: Optional[str] = None,
        is_active: Optional[bool] = None,
        is_enabled: Optional[bool] = None,
        bank: Optional[str] = None,
    ) -> Tuple[List[Requisite], int]:
        from app.common.enums.requisites import RequisiteStatus
        from app.modules.users.models import User as UserModel

        query = select(self.model).options(selectinload(self.model.limits))
        count_query = select(func.count()).select_from(self.model)

        filters = []
        if trader_login:
            trader_ids_sub = (
                select(UserModel.id)
                .where(
                    UserModel.username.ilike(f"%{trader_login}%"),
                    UserModel.role == "trader",
                )
            )
            filters.append(self.model.trader_id.in_(trader_ids_sub))
        if payment_method:
            filters.append(self.model.payment_method == payment_method)
        if is_active is not None:
            filters.append(self.model.is_active.is_(is_active))
        if is_enabled is not None:
            if is_enabled:
                filters.append(self.model.status == RequisiteStatus.ENABLED)
            else:
                filters.append(self.model.status != RequisiteStatus.ENABLED)
        if bank:
            filters.append(self.model.bank_name.ilike(f"%{bank}%"))

        for f in filters:
            query = query.where(f)
            count_query = count_query.where(f)

        query = query.order_by(self.model.id.desc()).offset(skip).limit(limit)

        result = await self.session.execute(query)
        count_result = await self.session.execute(count_query)

        return list(result.scalars().all()), count_result.scalar_one()


class RequisiteLimitRepository(BaseRepository[RequisiteLimit]):
    def __init__(self, session: AsyncSession):
        super().__init__(RequisiteLimit, session)

    async def get_by_requisite_id(self, requisite_id: int) -> Optional[RequisiteLimit]:
        stmt = select(self.model).where(self.model.requisite_id == requisite_id)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_by_requisite_id_for_update(self, requisite_id: int) -> Optional[RequisiteLimit]:
        """``get_by_requisite_id`` with a row lock — serialises concurrent reads
        of the remaining daily capacity (e.g. parallel доливы on one requisite)
        so they can't both pass the limit check and over-commit it."""
        stmt = select(self.model).where(self.model.requisite_id == requisite_id).with_for_update()
        return (await self.session.execute(stmt)).scalars().first()

    async def increment_turnover_by_order(self, order: "Order") -> None:
        """Atomically increment daily and monthly turnover counters using order data."""
        if not order.requisite_id:
            return
        await self.session.execute(
            update(self.model)
            .where(self.model.requisite_id == order.requisite_id)
            .values(
                current_daily_turnover=self.model.current_daily_turnover + order.amount,
                current_monthly_turnover=self.model.current_monthly_turnover + order.amount,
            )
        )

    async def increment_turnover_by_amount(self, requisite_id: int, amount) -> None:
        """Atomically add ``amount`` (fiat) to a requisite's daily + monthly
        turnover counters — the same effect as a completed order, used by the
        долив flow to fill an under-used requisite's limit."""
        if not requisite_id:
            return
        await self.session.execute(
            update(self.model)
            .where(self.model.requisite_id == requisite_id)
            .values(
                current_daily_turnover=self.model.current_daily_turnover + amount,
                current_monthly_turnover=self.model.current_monthly_turnover + amount,
            )
        )

    async def decrement_turnover_by_amount(self, requisite_id: int, amount) -> None:
        """Atomically subtract ``amount`` (fiat) from a requisite's daily +
        monthly turnover — the mirror of ``increment_turnover_by_amount``, used
        when the admin долив state machine un-completes a settled долив (the
        turnover it filled must be given back). ``CASE`` clamps at 0 so a
        daily/monthly rollover between settle and reversal can't drive a counter
        negative (portable across PostgreSQL and the SQLite test engine)."""
        if not requisite_id:
            return
        new_daily = self.model.current_daily_turnover - amount
        new_monthly = self.model.current_monthly_turnover - amount
        await self.session.execute(
            update(self.model)
            .where(self.model.requisite_id == requisite_id)
            .values(
                current_daily_turnover=case((new_daily < 0, 0), else_=new_daily),
                current_monthly_turnover=case((new_monthly < 0, 0), else_=new_monthly),
            )
        )

    async def decrement_turnover_by_order(self, order: "Order") -> None:
        """Atomically reverse the turnover counters added by a prior success.

        Mirror of ``increment_turnover_by_order`` — used when a previously
        SUCCESS order is pulled into a dispute, so daily/monthly limits stop
        counting a deal that is no longer settled. The ``CASE`` clamp guards
        against driving a counter negative if it was reset (daily/monthly
        rollover) between settlement and dispute. ``CASE`` is used instead of
        ``GREATEST`` so the expression is portable across PostgreSQL and the
        SQLite test engine.
        """
        if not order.requisite_id:
            return
        new_daily = self.model.current_daily_turnover - order.amount
        new_monthly = self.model.current_monthly_turnover - order.amount
        await self.session.execute(
            update(self.model)
            .where(self.model.requisite_id == order.requisite_id)
            .values(
                current_daily_turnover=case((new_daily < 0, 0), else_=new_daily),
                current_monthly_turnover=case((new_monthly < 0, 0), else_=new_monthly),
            )
        )
