from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.types import utcnow
from app.core.exceptions import NotFoundException, ValidationException
from app.core.logging import get_logger
from app.modules.base.service import BaseService
from app.modules.clients import block_cache
from app.modules.clients.models import Client
from app.modules.clients.repository import ClientRepository
from app.modules.clients.schemas.admin import AdminClientOrderInfo, AdminClientResponse
from app.modules.clients.schemas.trader import TraderClientOrderInfo
from app.modules.merchants.models import Merchant

logger = get_logger(__name__)

# Throttle for the full clients rollup recompute. The beat task fires every ~60s
# (for the ban reconcile), but the GROUP-BY over orders only needs to run every
# few minutes — a Redis SET NX EX lock gates it to one run per window (and also
# prevents two workers recomputing at once). The TTL doubles as the window AND
# the implied max-scan-duration: a recompute must finish well under it (true for
# a hash-aggregate over orders today). If orders grows enough that the scan
# approaches this, re-measure (CLAUDE.md perf discipline) and move to a
# touched-by-updated_at window + an orders index before shortening it.
MATERIALIZE_THROTTLE_KEY = "clients:materialize:throttle"
MATERIALIZE_INTERVAL_S = 300


class ClientService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.repository = ClientRepository(session)

    # ── Admin list ─────────────────────────────────────────────
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
    ) -> List[AdminClientResponse]:
        rows = await self.repository.list_admin(
            merchant_id=merchant_id,
            client_search=client_search,
            is_blocked=is_blocked,
            sort_by=sort_by,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        out: List[AdminClientResponse] = []
        for client, merchant_name in rows:
            resp = AdminClientResponse.model_validate(client, from_attributes=True)
            resp.merchant_name = merchant_name
            out.append(resp)
        return out

    async def get_for_order(self, order_id: int) -> Optional[AdminClientOrderInfo]:
        """Client block for the admin order modal. GATED: returns ``None`` when the
        order's merchant has ``unique_clients_enabled`` off (so the block is never
        sent over the API), when the order carries no client, or when the client
        isn't materialised yet (nothing to show)."""
        client = await self._client_for_order(order_id)
        if client is None:
            return None
        return AdminClientOrderInfo.model_validate(client, from_attributes=True)

    async def get_for_order_trader(
        self, order_id: int, trader_user_id: int
    ) -> Optional[TraderClientOrderInfo]:
        """Client block for the TRADER order modal — same merchant-toggle gate as
        the admin view PLUS ownership: only the trader assigned to the order sees
        it. Restricted payload (public_id + turnover + conversion; no raw counts)."""
        client = await self._client_for_order(order_id, trader_user_id=trader_user_id)
        if client is None:
            return None
        conversion = (
            round(client.successful_orders / client.total_orders, 4)
            if client.total_orders
            else 0.0
        )
        return TraderClientOrderInfo(
            public_id=client.public_id,
            turnover_usdt=float(client.turnover_usdt),
            conversion=conversion,
        )

    async def _client_for_order(
        self, order_id: int, *, trader_user_id: Optional[int] = None
    ) -> Optional[Client]:
        """Resolve the materialised ``Client`` for an order under the shared gates:
        merchant ``unique_clients_enabled`` on, order carries a client, client
        materialised. When ``trader_user_id`` is given, also require the order to
        be assigned to that trader (else ``None``)."""
        ctx = await self.repository.get_order_client_context(order_id)
        if ctx is None:
            return None
        merchant_id, client_user_id, unique_enabled, trader_id = ctx
        if trader_user_id is not None and trader_id != trader_user_id:
            return None
        if not unique_enabled or not client_user_id:
            return None
        return await self.repository.get_by_merchant_and_client(merchant_id, client_user_id)

    # ── Block / unblock ────────────────────────────────────────
    async def block(
        self,
        *,
        admin_id: int,
        merchant_id: int,
        client_user_id: str,
        reason: Optional[str] = None,
    ) -> AdminClientResponse:
        """Block a client by natural key. Upserts the client row if it hasn't
        been materialised yet (so the order modal can block a brand-new client),
        then mirrors the ban into the Redis hot-path set."""
        merchant = await self.session.get(Merchant, merchant_id)
        if merchant is None:
            raise ValidationException(f"Merchant {merchant_id} not found")

        client = await self.repository.get_by_merchant_and_client(merchant_id, client_user_id)
        async with self.session.begin_nested():
            if client is None:
                client = await self.repository.create({
                    "merchant_id": merchant_id,
                    "client_user_id": client_user_id,
                })
            client.is_blocked = True
            client.block_reason = reason
            client.blocked_by_admin_id = admin_id
            client.blocked_at = utcnow()
            self.session.add(client)
            await self.session.flush()

            await self.audit_log(
                action="client_block",
                entity_type="client",
                entity_id=client.id,
                user_id=admin_id,
                new_values={
                    "merchant_id": merchant_id,
                    "client_user_id": client_user_id,
                    "reason": reason,
                },
            )

        # Mirror into the Redis hot-path set (best-effort — the reconcile job
        # heals it from the DB if Redis is unavailable).
        await self._safe_redis(block_cache.add_to_blocked_set, merchant_id, client_user_id)

        resp = AdminClientResponse.model_validate(client, from_attributes=True)
        resp.merchant_name = merchant.name
        return resp

    async def unblock(
        self, *, admin_id: int, merchant_id: int, client_user_id: str
    ) -> AdminClientResponse:
        merchant = await self.session.get(Merchant, merchant_id)
        client = await self.repository.get_by_merchant_and_client(merchant_id, client_user_id)
        if client is None:
            raise NotFoundException("Client not found")

        async with self.session.begin_nested():
            client.is_blocked = False
            client.block_reason = None
            client.blocked_by_admin_id = None
            client.blocked_at = None
            self.session.add(client)
            await self.session.flush()

            await self.audit_log(
                action="client_unblock",
                entity_type="client",
                entity_id=client.id,
                user_id=admin_id,
                new_values={"merchant_id": merchant_id, "client_user_id": client_user_id},
            )

        await self._safe_redis(block_cache.remove_from_blocked_set, merchant_id, client_user_id)

        resp = AdminClientResponse.model_validate(client, from_attributes=True)
        resp.merchant_name = merchant.name if merchant else None
        return resp

    # ── Off-hot-path maintenance (called by refresh_clients_task) ──
    async def refresh_materialization(self) -> bool:
        """Recompute the clients rollup (activity window + deal counts + turnover)
        from orders, THROTTLED to one run per ``MATERIALIZE_INTERVAL_S``. Returns
        ``True`` if it RAN this cycle (the caller must ``mark_materialized()`` AFTER
        committing).

        A full GROUP-BY recompute (status-change safe — see
        ``ClientRepository.materialize_full``). The throttle is a Redis marker with
        a TTL that is set only AFTER a successful commit (``mark_materialized``),
        NOT a lock held across the run — so it can never get stuck: a failed commit
        leaves no marker and the next beat retries (self-healing), and the marker
        always carries a TTL so it can't wedge materialisation forever (the bug
        that froze the Clients page when an older no-TTL lock lingered).

        Fails CLOSED: if the Redis throttle check errors we SKIP this cycle rather
        than run — a Redis outage shouldn't make every beat re-scan the hot
        ``orders`` table. The rollup is eventual-consistency; the next healthy
        window catches up. A rare concurrent double-run is harmless (the upsert is
        idempotent)."""
        from app.infrastructure.cache.redis import redis_client

        try:
            ttl = await redis_client.ttl(MATERIALIZE_THROTTLE_KEY)
        except Exception as exc:  # noqa: BLE001 — Redis down → skip the costly scan this cycle (fail closed)
            logger.warning("clients materialize throttle check failed, skipping recompute this cycle: %s", exc)
            return False

        # TTL semantics: >=0 armed (ran recently) → skip; -2 no marker → run; -1 a
        # marker with NO expiry — a wedged legacy lock (an older SET-without-EX)
        # that would freeze materialisation forever. Clear it and run, so a stuck
        # key self-heals on the next beat instead of needing manual Redis surgery.
        if ttl >= 0:
            return False
        if ttl == -1:
            logger.warning("clients materialize throttle had no TTL (wedged) — clearing and recomputing")
            try:
                await redis_client.delete(MATERIALIZE_THROTTLE_KEY)
            except Exception as exc:  # noqa: BLE001
                logger.warning("clients materialize throttle unstick failed: %s", exc)
        await self.repository.materialize_full()
        return True

    async def mark_materialized(self) -> None:
        """Arm the throttle for the next ``MATERIALIZE_INTERVAL_S`` — call ONLY
        after the recompute has COMMITTED. Post-commit + always-TTL so a failed
        run never wedges materialisation (see ``refresh_materialization``)."""
        from app.infrastructure.cache.redis import redis_client

        try:
            await redis_client.set(MATERIALIZE_THROTTLE_KEY, "1", ex=MATERIALIZE_INTERVAL_S)
        except Exception as exc:  # noqa: BLE001 — no marker → next beat re-runs; harmless (idempotent)
            logger.warning("clients materialize throttle arm failed: %s", exc)

    async def reconcile_blocked_set(self) -> None:
        """Rebuild the Redis blocked-set from the DB (self-heal after a flush)."""
        members = await self.repository.list_blocked_member_keys()
        await block_cache.rebuild_blocked_set(members)

    async def reconcile_blocked_attempts(self) -> dict:
        """Fold the Redis blocked-attempt counters into the durable
        ``clients.blocked_attempts`` column (off the hot path). READ-only against
        Redis (no consume) and STAGES the upsert on the session — the caller must
        ``ack_blocked_attempts(folded)`` only AFTER committing, so a commit failure
        loses nothing. Returns the folded ``{member_key: delta}`` (the valid keys
        that were upserted) for the caller to ack."""
        deltas = await block_cache.read_blocked_attempts()
        if not deltas:
            return {}
        items: List = []
        folded: dict = {}
        for key, delta in deltas.items():
            mid_str, sep, cid = key.partition(":")
            if not sep or not cid:
                continue
            try:
                mid = int(mid_str)
            except ValueError:
                continue
            items.append((mid, cid, delta))
            folded[key] = delta
        if not items:
            return {}
        await self.repository.add_blocked_attempts(items)
        return folded

    @staticmethod
    async def ack_blocked_attempts(folded: dict) -> None:
        """Consume the Redis deltas already committed by ``reconcile_blocked_attempts``.
        Call ONLY after ``session.commit()`` so a commit failure leaves them intact."""
        if folded:
            await block_cache.ack_blocked_attempts(folded)

    @staticmethod
    async def _safe_redis(fn, *args) -> None:
        try:
            await fn(*args)
        except Exception as exc:  # noqa: BLE001 — DB is source of truth; reconcile heals Redis
            logger.warning("clients Redis blocked-set update failed (%s): %s", getattr(fn, "__name__", fn), exc)
