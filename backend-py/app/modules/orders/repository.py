import uuid
from datetime import datetime
from typing import List, Optional, Sequence, Union

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.orders import OrderSource, OrderStatus
from app.common.enums.payments import PaymentMethod
from app.modules.base.repository import BaseRepository
from app.modules.orders.models import Order

# PostgreSQL int4 upper bound. ``Order.id`` is an INTEGER column, so an
# all-digit search term longer than this (e.g. a 16-20 digit bank account
# number) must NOT be compared against ``Order.id`` — Postgres rejects the
# whole statement with "integer out of range". The numeric-id search branch
# is gated on this limit.
_INT4_MAX = 2_147_483_647


class OrderRepository(BaseRepository[Order]):
    def __init__(self, session: AsyncSession):
        super().__init__(Order, session)

    # ── generic order listing ───────────────────────────────────────────
    #
    # ONE role-agnostic query that every caller composes by passing concrete
    # filters. The service layer (which knows the caller's role) translates
    # "who is asking" into these filters — the repository stays unaware of
    # merchant / trader / admin / bot semantics. Filters are additive (AND);
    # ``search`` is OR-composed per ``search_scope``.

    def _search_conditions(self, term: str, scope: str) -> list:
        """OR-conditions for a free-text id/requisite search.

        Scopes preserve the historical per-surface behaviour exactly:
          * ``merchant`` — external_id / uuid only.
          * ``admin``    — external_id / uuid / provider_order_id
                           (+ integer id when it fits int4).
          * ``trader``   — uuid / external_id / requisite fields
                           (account / holder / nickname) (+ integer id).
        """
        term = term.strip()
        conds = [
            Order.external_id.ilike(f"%{term}%"),
            cast(Order.uuid, String).ilike(f"%{term}%"),
        ]
        if scope == "admin":
            conds.append(Order.provider_order_id.ilike(f"%{term}%"))
        if scope == "trader":
            from app.modules.requisites.models import Requisite

            requisite_ids_sub = select(Requisite.id).where(
                or_(
                    Requisite.account_number.ilike(f"%{term}%"),
                    Requisite.account_holder.ilike(f"%{term}%"),
                    Requisite.nickname.ilike(f"%{term}%"),
                )
            )
            conds.append(Order.requisite_id.in_(requisite_ids_sub))
        # Treat a numeric term as an order id only when it fits the INTEGER
        # column. The ``merchant`` scope historically never matched on integer
        # id, so it's excluded there.
        if scope in ("admin", "trader") and term.isdigit() and int(term) <= _INT4_MAX:
            conds.append(Order.id == int(term))
        return conds

    def _filtered_orders_select(
        self,
        *,
        merchant_ids: Optional[Union[int, Sequence[int]]] = None,
        trader_id: Optional[int] = None,
        source: Optional[OrderSource] = None,
        statuses: Optional[Sequence[OrderStatus]] = None,
        payment_method: Optional[PaymentMethod] = None,
        amount_from: Optional[float] = None,
        amount_to: Optional[float] = None,
        created_from: Optional[datetime] = None,
        created_to: Optional[datetime] = None,
        search: Optional[str] = None,
        search_scope: str = "merchant",
        trader_login: Optional[str] = None,
        merchant_login: Optional[str] = None,
        login_search: Optional[str] = None,
    ):
        """Build the filtered ``SELECT Order`` (WHERE clauses only — no ordering
        / pagination). Returns ``None`` when an empty merchant-id sequence is
        passed (a hard "no rows" short-circuit), so both ``list_orders`` and
        ``count_orders`` apply identical filtering from one place."""
        stmt = select(Order)

        # merchant scope: int → single terminal; sequence → many (an empty
        # sequence means "no terminals" → no rows). ``None`` → no merchant filter.
        if merchant_ids is not None:
            if isinstance(merchant_ids, int):
                stmt = stmt.where(Order.merchant_id == merchant_ids)
            else:
                ids = list(merchant_ids)
                if not ids:
                    return None
                stmt = stmt.where(Order.merchant_id.in_(ids))

        if trader_id is not None:
            stmt = stmt.where(Order.trader_id == trader_id)
        if source is not None:
            stmt = stmt.where(Order.source == source)
        if statuses:
            stmt = stmt.where(Order.status.in_(list(statuses)))
        if payment_method:
            stmt = stmt.where(Order.payment_method == payment_method)
        if amount_from is not None:
            stmt = stmt.where(Order.amount >= amount_from)
        if amount_to is not None:
            stmt = stmt.where(Order.amount <= amount_to)
        if created_from is not None:
            stmt = stmt.where(Order.created_at >= created_from)
        if created_to is not None:
            stmt = stmt.where(Order.created_at <= created_to)

        if search:
            stmt = stmt.where(or_(*self._search_conditions(search, search_scope)))

        # Admin-only cross-table login filters. The repo just exposes them as
        # additive predicates — it doesn't know "admin"; only the admin service
        # method happens to pass them.
        if trader_login or merchant_login or login_search:
            from app.modules.merchants.models import Merchant
            from app.modules.users.models import User as UserModel

            if trader_login:
                sub = select(UserModel.id).where(
                    UserModel.username.ilike(f"%{trader_login}%"),
                    UserModel.role == "trader",
                )
                stmt = stmt.where(Order.trader_id.in_(sub))
            if merchant_login:
                sub = (
                    select(Merchant.id)
                    .join(UserModel, Merchant.user_id == UserModel.id)
                    .where(UserModel.username.ilike(f"%{merchant_login}%"))
                )
                stmt = stmt.where(Order.merchant_id.in_(sub))
            if login_search:
                m_sub = (
                    select(Merchant.id)
                    .join(UserModel, Merchant.user_id == UserModel.id)
                    .where(UserModel.username.ilike(f"%{login_search}%"))
                )
                t_sub = select(UserModel.id).where(
                    UserModel.username.ilike(f"%{login_search}%"),
                    UserModel.role == "trader",
                )
                stmt = stmt.where(
                    (Order.merchant_id.in_(m_sub)) | (Order.trader_id.in_(t_sub))
                )

        return stmt

    async def list_orders(
        self,
        *,
        order: Optional[str] = "desc",
        skip: Optional[int] = None,
        limit: Optional[int] = None,
        **filters,
    ) -> List[Order]:
        stmt = self._filtered_orders_select(**filters)
        if stmt is None:
            return []

        if order == "desc":
            stmt = stmt.order_by(Order.created_at.desc())
        elif order == "asc":
            stmt = stmt.order_by(Order.created_at.asc())
        # order is None → no ORDER BY (matches the old period getter).

        if skip:
            stmt = stmt.offset(skip)
        if limit is not None:
            stmt = stmt.limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_orders(self, **filters) -> int:
        """Total number of orders matching the same filters as ``list_orders``
        (no pagination). Feeds the ``total`` of the paginated list envelope."""
        stmt = self._filtered_orders_select(**filters)
        if stmt is None:
            return 0
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        return int((await self.session.execute(count_stmt)).scalar_one() or 0)

    # ── single-row getters (distinct, reached directly by endpoints) ─────

    async def get_by_uuid(self, uuid_str: str) -> Optional[Order]:
        try:
            order_uuid = uuid.UUID(uuid_str)
        except ValueError:
            return None

        stmt = select(Order).where(Order.uuid == order_uuid)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_for_update(self, order_id: int) -> Optional[Order]:
        """``get`` with a row-level lock (``SELECT … FOR UPDATE``). Serialises
        concurrent state transitions on the same order — e.g. a double confirm
        (cabinet + trader-bot button, double-click, or a retried request): the
        second caller blocks until the first commits, then re-reads the (now
        SUCCESS) status and no-ops instead of settling the order twice."""
        stmt = select(Order).where(Order.id == order_id).with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def lock_status(self, order_id: int) -> Optional[OrderStatus]:
        """Row-lock the order (``SELECT status … FOR UPDATE``) and return its
        COMMITTED status. Used by ``OrderService.change_status`` — the single
        money funnel — to serialise concurrent transitions and read the
        authoritative status WITHOUT re-loading / clobbering the caller's
        in-memory order object. The lock is held to the request-end commit, so a
        second concurrent transition blocks here and then no-ops on the
        same-status guard instead of double-moving money."""
        stmt = select(Order.status).where(Order.id == order_id).with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_uuid_and_merchant(self, uuid_str: str, merchant_id: int) -> Optional[Order]:
        try:
            order_uuid = uuid.UUID(uuid_str)
        except ValueError:
            return None

        stmt = select(Order).where(Order.uuid == order_uuid, Order.merchant_id == merchant_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_external_id_and_merchant(self, external_id: str, merchant_id: int) -> Optional[Order]:
        stmt = select(Order).where(Order.external_id == external_id, Order.merchant_id == merchant_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_ids_for_trader_by_uuids(
        self, uuid_strs: Sequence[str], trader_user_id: int
    ) -> dict[str, int]:
        """Resolve trader-scoped order UUIDs to integer ids in a single SELECT.

        Used by the receipt-check bulk endpoint to map the UUIDs the frontend
        knows to the integer PKs the receipt_checks index is built on.
        Returns ``{uuid_str: order_id}`` only for UUIDs that exist AND belong
        to the given trader.
        """
        valid_uuids: list[uuid.UUID] = []
        for s in uuid_strs:
            try:
                valid_uuids.append(uuid.UUID(s))
            except ValueError:
                continue
        if not valid_uuids:
            return {}

        stmt = select(Order.id, Order.uuid).where(
            Order.uuid.in_(valid_uuids),
            Order.trader_id == trader_user_id,
        )
        rows = (await self.session.execute(stmt)).all()
        return {str(row.uuid): row.id for row in rows}
