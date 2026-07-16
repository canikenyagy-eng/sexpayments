from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.orders import OrderStatus
from app.modules.base.repository import BaseRepository
from app.modules.clients.models import Client
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order


class ClientRepository(BaseRepository[Client]):
    def __init__(self, session: AsyncSession):
        super().__init__(Client, session)

    # Sortable columns for the admin list (key → ORDER BY expression). Conversion
    # is the derived successful/total ratio (NULLIF guards a zero denominator).
    _SORT_EXPR = {
        "last_seen_at": Client.last_seen_at,
        "first_seen_at": Client.first_seen_at,
        "total_orders": Client.total_orders,
        "successful_orders": Client.successful_orders,
        "turnover_usdt": Client.turnover_usdt,
        "blocked_attempts": Client.blocked_attempts,
        # COALESCE(...,0): a zero-order client sorts as conversion 0 — same value
        # AdminClientResponse.conversion serialises — not pinned last by NULLIF.
        "conversion": func.coalesce(
            Client.successful_orders * 1.0 / func.nullif(Client.total_orders, 0), 0
        ),
    }

    async def add_blocked_attempts(self, items: List[Tuple[int, str, int]]) -> None:
        """Fold drained per-client blocked-attempt deltas into the durable
        ``clients.blocked_attempts`` column (off the hot path). UPSERT so a
        delta for a not-yet-materialised client still lands; accumulates on
        conflict. ``items`` = ``[(merchant_id, client_user_id, delta), ...]``.
        Leaves all other columns untouched."""
        if not items:
            return
        rows = [
            {"merchant_id": mid, "client_user_id": cid, "blocked_attempts": delta}
            for mid, cid, delta in items
            if delta > 0
        ]
        if not rows:
            return
        stmt = pg_insert(Client).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_clients_merchant_client",
            set_={
                "blocked_attempts": Client.blocked_attempts + stmt.excluded.blocked_attempts,
                "updated_at": func.now(),
            },
        )
        await self.session.execute(stmt)

    async def get_by_merchant_and_client(
        self, merchant_id: int, client_user_id: str
    ) -> Optional[Client]:
        result = await self.session.execute(
            select(Client).where(
                Client.merchant_id == merchant_id,
                Client.client_user_id == client_user_id,
            )
        )
        return result.scalars().first()

    async def get_order_client_context(
        self, order_id: int
    ) -> Optional[Tuple[int, Optional[str], bool, Optional[int]]]:
        """``(merchant_id, client_user_id, merchant.unique_clients_enabled,
        trader_id)`` for an order — the inputs the order modal needs to decide
        whether to show (and which) client, plus the assigned trader (for the
        trader-side ownership check). Returns ``None`` when the order doesn't exist."""
        result = await self.session.execute(
            select(
                Order.merchant_id,
                Order.client_user_id,
                Merchant.unique_clients_enabled,
                Order.trader_id,
            )
            .join(Merchant, Merchant.id == Order.merchant_id)
            .where(Order.id == order_id)
        )
        row = result.first()
        if row is None:
            return None
        return (row[0], row[1], bool(row[2]), row[3])

    async def list_admin(
        self,
        *,
        merchant_id: Optional[int] = None,
        client_search: Optional[str] = None,
        is_blocked: Optional[bool] = None,
        sort_by: Optional[str] = None,
        sort_order: str = "desc",
        skip: int = 0,
        limit: int = 50,
    ) -> List[Tuple[Client, Optional[str]]]:
        """Return ``(client, merchant_name)`` rows for the admin list. Reads the
        materialised ``clients`` table — never a GROUP BY over the hot ``orders``
        table. Sorts by ``sort_by`` (allowlisted in ``_SORT_EXPR``; defaults to
        newest activity); unknown keys fall back to the default."""
        stmt = (
            select(Client, Merchant.name)
            .join(Merchant, Merchant.id == Client.merchant_id)
        )
        if merchant_id is not None:
            stmt = stmt.where(Client.merchant_id == merchant_id)
        if is_blocked is not None:
            stmt = stmt.where(Client.is_blocked == is_blocked)
        if client_search:
            stmt = stmt.where(Client.client_user_id.ilike(f"%{client_search}%"))

        sort_expr = self._SORT_EXPR.get(sort_by or "", Client.last_seen_at)
        direction = sort_expr.asc() if sort_order == "asc" else sort_expr.desc()
        stmt = (
            stmt.order_by(direction.nullslast(), Client.id.desc())  # id tiebreak = stable paging
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def list_blocked_member_keys(self) -> List[str]:
        """``"{merchant_id}:{client_user_id}"`` for every blocked client — the
        source of truth the Redis blocked-set is reconciled from."""
        result = await self.session.execute(
            select(Client.merchant_id, Client.client_user_id).where(Client.is_blocked.is_(True))
        )
        return [f"{m}:{c}" for m, c in result.all()]

    async def materialize_full(self) -> None:
        """Recompute every client's rollup from ``orders`` in one GROUP-BY pass
        and upsert it: activity window + ``total_orders`` / ``successful_orders``
        / ``turnover_usdt`` (Σ amount_usdt of SUCCESS orders).

        A FULL recompute (not an incremental slice) because the stats depend on
        the order STATUS, which mutates after a row is first seen (pending→success,
        a dispute resolving days later) — an id/created_at watermark would
        permanently undercount those. So each run reflects the current statuses.
        Background-only: the caller throttles it (one run per window), and order
        creation never hits this path. IDEMPOTENT — the stat columns are SET to
        the freshly computed totals; first/last seen use LEAST/GREATEST so an
        admin-pre-created row (blocked before any order) isn't regressed, and
        ``is_blocked`` is left untouched (the ban is owned by the admin action).

        PERF: this is a full hash-aggregate over ``orders`` (unbounded, no
        supporting index — orders is indexed only on id/uuid/external_id). It's a
        throttled BACKGROUND scan, off the order-creation hot path. Per the
        CLAUDE.md perf discipline, re-measure (stage tracer + pg_stat_user_tables)
        before shortening the throttle as ``orders`` grows; the cheaper next step
        is a touched-by-``updated_at`` window + an ``orders(merchant_id,
        client_user_id)`` index rather than recomputing every client each run.
        """
        rollup = (
            select(
                Order.merchant_id.label("merchant_id"),
                Order.client_user_id.label("client_user_id"),
                func.min(Order.created_at).label("first_seen_at"),
                func.max(Order.created_at).label("last_seen_at"),
                func.count().label("total_orders"),
                func.count().filter(Order.status == OrderStatus.SUCCESS).label("successful_orders"),
                func.coalesce(
                    func.sum(Order.amount_usdt).filter(Order.status == OrderStatus.SUCCESS),
                    0,
                ).label("turnover_usdt"),
            )
            .where(Order.client_user_id.isnot(None))
            .group_by(Order.merchant_id, Order.client_user_id)
        ).subquery()

        stmt = pg_insert(Client).from_select(
            [
                "public_id", "merchant_id", "client_user_id", "first_seen_at",
                "last_seen_at", "total_orders", "successful_orders", "turnover_usdt",
            ],
            select(
                # Per-row unique id, evaluated by Postgres for EACH selected row.
                # The model's Python-side ``default=uuid.uuid4`` does NOT fire on an
                # INSERT..SELECT — SQLAlchemy embeds a single constant — so ≥2 new
                # clients in one pass collided on ``ix_clients_public_id`` and rolled
                # back the whole rollup (froze the Clients page). ``gen_random_uuid()``
                # (PG13+ built-in) yields a fresh value per row. Only the INSERT path
                # uses it; on conflict we DON'T update public_id, so existing rows
                # keep their id.
                func.gen_random_uuid(),
                rollup.c.merchant_id,
                rollup.c.client_user_id,
                rollup.c.first_seen_at,
                rollup.c.last_seen_at,
                rollup.c.total_orders,
                rollup.c.successful_orders,
                rollup.c.turnover_usdt,
            ),
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_clients_merchant_client",
            set_={
                "first_seen_at": func.least(
                    func.coalesce(Client.first_seen_at, stmt.excluded.first_seen_at),
                    stmt.excluded.first_seen_at,
                ),
                "last_seen_at": func.greatest(Client.last_seen_at, stmt.excluded.last_seen_at),
                "total_orders": stmt.excluded.total_orders,
                "successful_orders": stmt.excluded.successful_orders,
                "turnover_usdt": stmt.excluded.turnover_usdt,
                "updated_at": func.now(),
            },
        )
        await self.session.execute(stmt)
