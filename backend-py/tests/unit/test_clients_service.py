"""ClientService block/unblock + the order-creation ban guards."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.common.enums.orders import OrderSource, OrderStatus
from app.core.exceptions import NotFoundException, ValidationException
from app.modules.clients.models import Client
from app.modules.clients.service import ClientService
from app.modules.orders.service import OrderService


def _mock_session():
    session = MagicMock()

    class _ACM:
        async def __aenter__(self):
            return session
        async def __aexit__(self, *a):
            return False

    session.begin_nested.return_value = _ACM()
    session.get = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


def _merchant(merchant_id: int = 1, name: str = "ACME") -> MagicMock:
    # NB: ``name`` is a reserved MagicMock ctor kwarg — must be set after init.
    m = MagicMock(id=merchant_id)
    m.name = name
    return m


def _client(**over) -> Client:
    c = Client(
        id=10, merchant_id=1, client_user_id="cli-1", is_blocked=False,
        block_reason=None, blocked_by_admin_id=None, blocked_at=None,
        first_seen_at=None, last_seen_at=None,
        total_orders=0, successful_orders=0, turnover_usdt=0, blocked_attempts=0,
        created_at=datetime(2026, 6, 1), updated_at=datetime(2026, 6, 1),
    )
    for k, v in over.items():
        setattr(c, k, v)
    return c


@pytest.fixture
def service():
    svc = ClientService(_mock_session())
    svc.repository = AsyncMock()
    svc.audit_log = AsyncMock()
    return svc


# ── block / unblock ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_block_creates_client_when_not_materialised(service, mocker):
    """The order modal can block a brand-new client that the materialiser hasn't
    rolled up yet — block upserts the row."""
    service.session.get = AsyncMock(return_value=_merchant())
    service.repository.get_by_merchant_and_client = AsyncMock(return_value=None)
    service.repository.create = AsyncMock(return_value=_client(is_blocked=False))
    add = mocker.patch("app.modules.clients.block_cache.add_to_blocked_set", AsyncMock())

    resp = await service.block(admin_id=42, merchant_id=1, client_user_id="cli-1", reason="fraud")

    service.repository.create.assert_awaited_once()
    assert resp.is_blocked is True
    assert resp.block_reason == "fraud"
    assert resp.blocked_by_admin_id == 42
    assert resp.merchant_name == "ACME"
    add.assert_awaited_once_with(1, "cli-1")
    assert service.audit_log.await_args.kwargs["action"] == "client_block"


@pytest.mark.asyncio
async def test_block_existing_client_updates_in_place(service, mocker):
    service.session.get = AsyncMock(return_value=_merchant())
    service.repository.get_by_merchant_and_client = AsyncMock(return_value=_client())
    add = mocker.patch("app.modules.clients.block_cache.add_to_blocked_set", AsyncMock())

    resp = await service.block(admin_id=7, merchant_id=1, client_user_id="cli-1")

    service.repository.create.assert_not_called()
    assert resp.is_blocked is True
    add.assert_awaited_once_with(1, "cli-1")


@pytest.mark.asyncio
async def test_block_rejects_unknown_merchant(service, mocker):
    service.session.get = AsyncMock(return_value=None)
    add = mocker.patch("app.modules.clients.block_cache.add_to_blocked_set", AsyncMock())

    with pytest.raises(ValidationException, match="Merchant"):
        await service.block(admin_id=1, merchant_id=999, client_user_id="cli-1")

    service.repository.create.assert_not_called()
    add.assert_not_awaited()


@pytest.mark.asyncio
async def test_unblock_clears_state_and_redis(service, mocker):
    service.session.get = AsyncMock(return_value=_merchant())
    blocked = _client(is_blocked=True, block_reason="fraud", blocked_by_admin_id=42)
    service.repository.get_by_merchant_and_client = AsyncMock(return_value=blocked)
    rem = mocker.patch("app.modules.clients.block_cache.remove_from_blocked_set", AsyncMock())

    resp = await service.unblock(admin_id=9, merchant_id=1, client_user_id="cli-1")

    assert resp.is_blocked is False
    assert resp.block_reason is None
    assert resp.blocked_by_admin_id is None
    rem.assert_awaited_once_with(1, "cli-1")
    assert service.audit_log.await_args.kwargs["action"] == "client_unblock"


@pytest.mark.asyncio
async def test_unblock_unknown_client_rejected(service, mocker):
    service.session.get = AsyncMock(return_value=_merchant())
    service.repository.get_by_merchant_and_client = AsyncMock(return_value=None)
    rem = mocker.patch("app.modules.clients.block_cache.remove_from_blocked_set", AsyncMock())

    with pytest.raises(NotFoundException, match="Client not found"):
        await service.unblock(admin_id=1, merchant_id=1, client_user_id="ghost")

    rem.assert_not_awaited()


# ── order-modal client block (get_for_order, merchant-toggle gated) ─────

@pytest.mark.asyncio
async def test_get_for_order_none_when_order_missing(service):
    service.repository.get_order_client_context = AsyncMock(return_value=None)
    assert await service.get_for_order(99) is None


@pytest.mark.asyncio
async def test_get_for_order_none_when_toggle_off(service):
    """Merchant has unique_clients disabled → no block served (the API gate)."""
    service.repository.get_order_client_context = AsyncMock(return_value=(1, "cli-1", False, 10))
    service.repository.get_by_merchant_and_client = AsyncMock()
    assert await service.get_for_order(99) is None
    service.repository.get_by_merchant_and_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_for_order_none_when_order_has_no_client(service):
    """Toggle on but the order carries no client_user_id → nothing to show."""
    service.repository.get_order_client_context = AsyncMock(return_value=(1, None, True, 10))
    service.repository.get_by_merchant_and_client = AsyncMock()
    assert await service.get_for_order(99) is None
    service.repository.get_by_merchant_and_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_for_order_none_when_client_not_materialised(service):
    service.repository.get_order_client_context = AsyncMock(return_value=(1, "cli-1", True, 10))
    service.repository.get_by_merchant_and_client = AsyncMock(return_value=None)
    assert await service.get_for_order(99) is None


@pytest.mark.asyncio
async def test_get_for_order_builds_block_with_public_id_conversion_turnover(service):
    """Toggle on + client materialised → block carries the internal public_id,
    all-time deals/conversion (successful/total) and turnover."""
    from uuid import uuid4
    pid = uuid4()
    service.repository.get_order_client_context = AsyncMock(return_value=(1, "cli-1", True, 10))
    service.repository.get_by_merchant_and_client = AsyncMock(
        return_value=_client(public_id=pid, total_orders=10, successful_orders=7, turnover_usdt=555)
    )
    info = await service.get_for_order(99)
    assert info is not None
    assert info.public_id == pid
    assert info.total_orders == 10
    assert info.conversion == 0.7
    assert info.turnover_usdt == 555


@pytest.mark.asyncio
async def test_get_for_order_trader_none_when_not_owner(service):
    """A trader only sees the client on THEIR OWN order — a foreign trader gets None
    (ownership is checked before the toggle/materialisation)."""
    service.repository.get_order_client_context = AsyncMock(return_value=(1, "cli-1", True, 10))
    service.repository.get_by_merchant_and_client = AsyncMock()
    assert await service.get_for_order_trader(99, trader_user_id=999) is None
    service.repository.get_by_merchant_and_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_for_order_trader_builds_restricted_block(service):
    """Own order + toggle on + materialised → restricted block (public_id, turnover,
    conversion). Raw order counts are NOT exposed to the trader."""
    from uuid import uuid4
    pid = uuid4()
    service.repository.get_order_client_context = AsyncMock(return_value=(1, "cli-1", True, 10))
    service.repository.get_by_merchant_and_client = AsyncMock(
        return_value=_client(public_id=pid, total_orders=10, successful_orders=7, turnover_usdt=123.5)
    )
    info = await service.get_for_order_trader(99, trader_user_id=10)
    assert info is not None
    assert info.public_id == pid
    assert info.turnover_usdt == 123.5
    assert info.conversion == 0.7
    dumped = info.model_dump()
    assert "total_orders" not in dumped and "successful_orders" not in dumped


# ── order-creation ban guards ──────────────────────────────────────

@pytest.mark.asyncio
async def test_create_payin_rejects_blocked_client(mocker):
    """Blocked client → fail fast as the generic 'no requisite' BEFORE pooling,
    and the withheld attempt is counted."""
    svc = OrderService(AsyncMock())
    mocker.patch("app.modules.clients.block_cache.is_blocked", AsyncMock(return_value=True))
    rec = mocker.patch("app.modules.clients.block_cache.record_blocked_attempt", AsyncMock())

    with pytest.raises(NotFoundException, match="No available requisite"):
        await svc._create_payin_order_inner(
            merchant=MagicMock(id=1),
            data=MagicMock(userId="cli-1"),
            snapshot={"result": {}},
            source=OrderSource.API,
        )

    rec.assert_awaited_once_with(1, "cli-1")


@pytest.mark.asyncio
async def test_create_payin_passes_guard_when_not_blocked(mocker):
    """Not blocked → guard passes and creation proceeds to idempotency. The
    attempt counter is NEVER touched on the success path."""
    svc = OrderService(AsyncMock())
    svc.repository = MagicMock()
    svc.repository.get_by_external_id_and_merchant = AsyncMock(return_value=MagicMock())  # dup
    mocker.patch("app.modules.clients.block_cache.is_blocked", AsyncMock(return_value=False))
    rec = mocker.patch("app.modules.clients.block_cache.record_blocked_attempt", AsyncMock())

    with pytest.raises(ValidationException, match="already exists"):
        await svc._create_payin_order_inner(
            merchant=MagicMock(id=1),
            data=MagicMock(userId="cli-1", internalId="dup-1"),
            snapshot={"result": {}},
            source=OrderSource.API,
        )

    rec.assert_not_awaited()  # success/non-blocked path adds zero counter I/O


@pytest.mark.asyncio
async def test_assign_requisite_skips_blocked_client(mocker):
    """A client blocked AFTER an async order was created → never assigned, and
    the withheld attempt is counted."""
    svc = OrderService(AsyncMock())
    svc.repository = MagicMock()
    order = MagicMock(status=OrderStatus.CREATED, merchant_id=1, client_user_id="cli-1")
    svc.repository.get = AsyncMock(return_value=order)
    mocker.patch("app.modules.clients.block_cache.is_blocked", AsyncMock(return_value=True))
    rec = mocker.patch("app.modules.clients.block_cache.record_blocked_attempt", AsyncMock())
    pooling = mocker.patch("app.modules.orders.service.PoolingService")

    await svc.assign_requisite(5)

    pooling.assert_not_called()  # returned before pooling
    rec.assert_awaited_once_with(1, "cli-1")


# ── rollup stats: conversion field, throttled recompute, sortable list ─────────

def _compile_pg(stmt, *, literal_binds: bool = False, lower: bool = True) -> str:
    from sqlalchemy.dialects import postgresql
    sql = str(stmt.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": literal_binds} if literal_binds else {},
    ))
    return sql.lower() if lower else sql


def test_conversion_is_derived_from_counts():
    from app.modules.clients.schemas.admin import AdminClientResponse
    base = dict(
        id=1, merchant_id=1, client_user_id="c", is_blocked=False,
        created_at=datetime(2026, 6, 1), updated_at=datetime(2026, 6, 1),
    )
    r = AdminClientResponse(**base, total_orders=10, successful_orders=7, turnover_usdt=123.5)
    assert r.conversion == 0.7
    assert r.model_dump()["conversion"] == 0.7  # serialised for the client
    # Rounds to 4dp server-side (the API contract; the UI further truncates to 1dp).
    assert AdminClientResponse(**base, total_orders=3, successful_orders=1).conversion == 0.3333
    # All failed (non-zero denominator) → 0.
    assert AdminClientResponse(**base, total_orders=5, successful_orders=0).conversion == 0.0
    # No orders → 0, never a ZeroDivisionError.
    assert AdminClientResponse(**base, total_orders=0, successful_orders=0).conversion == 0.0


@pytest.mark.asyncio
async def test_materialize_full_compiles_to_valid_postgres():
    """The rollup is Postgres-only (FILTER + ON CONFLICT) — assert it compiles
    and aggregates counts/turnover by status (not runnable on SQLite)."""
    from app.modules.clients.repository import ClientRepository
    session = MagicMock()
    session.execute = AsyncMock()
    await ClientRepository(session).materialize_full()
    stmt = session.execute.await_args.args[0]
    sql = _compile_pg(stmt, literal_binds=True)
    assert "insert into clients" in sql and "on conflict" in sql
    # public_id must be generated PER ROW by Postgres (gen_random_uuid) — NOT the
    # model's Python-side default=uuid.uuid4, which fires only ONCE for an
    # INSERT..SELECT and made ≥2 new clients collide on ix_clients_public_id,
    # rolling back the whole rollup (froze the Clients page for ~1.5 days on prod).
    assert "insert into clients (public_id," in sql
    assert "gen_random_uuid()" in sql
    # successful_orders / turnover_usdt count & sum behind a status FILTER.
    assert "count(*) filter (where orders.status =" in sql
    assert "coalesce(sum(orders.amount_usdt) filter (where orders.status =" in sql
    assert "successful_orders" in sql and "turnover_usdt" in sql
    # Pin the EXACT enum label the DB receives: Enum(OrderStatus) binds the member
    # NAME ('SUCCESS'), not its value — assert case-sensitively so a regression to
    # another status is caught (the .lower() helper would otherwise mask it).
    raw = _compile_pg(stmt, literal_binds=True, lower=False)
    assert "orders.status = 'SUCCESS'" in raw
    for other in ("'FAILED'", "'REFUNDED'", "'CANCELED'", "'DISPUTED'"):
        assert other not in raw


@pytest.mark.asyncio
@pytest.mark.parametrize("sort_by,order,expect_in_order_by", [
    ("turnover_usdt", "asc", "clients.turnover_usdt asc"),
    ("total_orders", "desc", "clients.total_orders desc"),
    ("blocked_attempts", "desc", "clients.blocked_attempts desc"),
    ("conversion", "desc", "nullif"),                       # derived ratio expression
    (None, "desc", "clients.last_seen_at desc"),            # default
    ("bogus_field", "desc", "clients.last_seen_at desc"),   # unknown → fallback
])
async def test_list_admin_sort_builds_order_by(sort_by, order, expect_in_order_by):
    from app.modules.clients.repository import ClientRepository
    result = MagicMock()
    result.all.return_value = []
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    await ClientRepository(session).list_admin(sort_by=sort_by, sort_order=order)
    sql = _compile_pg(session.execute.await_args.args[0])
    order_clause = sql.split("order by", 1)[1]
    assert expect_in_order_by in order_clause
    assert "clients.id desc" in order_clause  # stable-paging tiebreak always last


@pytest.mark.asyncio
async def test_reconcile_blocked_attempts_reads_folds_and_returns(service, mocker):
    """Read (non-destructive) Redis deltas are parsed (member_key → merchant_id/
    client) and folded into clients.blocked_attempts; malformed keys are skipped;
    the folded keys are RETURNED for the caller to ack post-commit (and Redis is
    NOT consumed here)."""
    ack = mocker.patch("app.modules.clients.block_cache.ack_blocked_attempts", AsyncMock())
    mocker.patch(
        "app.modules.clients.block_cache.read_blocked_attempts",
        AsyncMock(return_value={"7:cli-1": 3, "8:a:b": 2, "bad-no-colon": 5, "x:cid": 1}),
    )

    folded = await service.reconcile_blocked_attempts()

    items = service.repository.add_blocked_attempts.await_args.args[0]
    # 'bad-no-colon' (no merchant prefix) and 'x:cid' (non-int merchant) are dropped;
    # 'a:b' shows a client id may itself contain ':'.
    assert sorted(items) == [(7, "cli-1", 3), (8, "a:b", 2)]
    # Only the valid/folded keys come back for the post-commit ack.
    assert folded == {"7:cli-1": 3, "8:a:b": 2}
    ack.assert_not_awaited()  # reconcile does NOT consume Redis — that's the worker's post-commit ack


@pytest.mark.asyncio
async def test_reconcile_blocked_attempts_noop_when_empty(service, mocker):
    mocker.patch("app.modules.clients.block_cache.read_blocked_attempts", AsyncMock(return_value={}))
    assert await service.reconcile_blocked_attempts() == {}
    service.repository.add_blocked_attempts.assert_not_awaited()


@pytest.mark.asyncio
async def test_ack_blocked_attempts_delegates_to_redis(mocker):
    ack = mocker.patch("app.modules.clients.block_cache.ack_blocked_attempts", AsyncMock())
    await ClientService.ack_blocked_attempts({"7:cli-1": 3})
    ack.assert_awaited_once_with({"7:cli-1": 3})
    # No-op for an empty fold (nothing committed → nothing to consume).
    await ClientService.ack_blocked_attempts({})
    assert ack.await_count == 1


@pytest.mark.asyncio
async def test_add_blocked_attempts_compiles_to_accumulating_upsert():
    """The blocked-attempts fold is a Postgres ON CONFLICT upsert that ACCUMULATES
    (not overwrites) on the merchant+client constraint — assert the compiled SQL,
    mirroring test_materialize_full_compiles_to_valid_postgres."""
    from app.modules.clients.repository import ClientRepository
    session = MagicMock()
    session.execute = AsyncMock()
    await ClientRepository(session).add_blocked_attempts([(7, "cli-1", 3)])
    sql = _compile_pg(session.execute.await_args.args[0])
    assert "insert into clients" in sql and "on conflict on constraint uq_clients_merchant_client" in sql
    # Accumulate, not SET-overwrite.
    assert "clients.blocked_attempts + excluded.blocked_attempts" in sql


@pytest.mark.asyncio
async def test_add_blocked_attempts_noop_on_empty():
    from app.modules.clients.repository import ClientRepository
    session = MagicMock()
    session.execute = AsyncMock()
    await ClientRepository(session).add_blocked_attempts([])
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_materialization_throttled_when_marker_armed(service, mocker):
    """Marker present with a live TTL (ran recently) → no recompute, returns False."""
    redis = mocker.patch("app.infrastructure.cache.redis.redis_client")
    redis.ttl = AsyncMock(return_value=200)  # >=0 → armed
    ran = await service.refresh_materialization()
    assert ran is False
    service.repository.materialize_full.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_materialization_recomputes_when_no_marker(service, mocker):
    """No marker (TTL -2) → recompute runs and returns True (caller arms the
    marker AFTER committing); nothing is armed during the run."""
    redis = mocker.patch("app.infrastructure.cache.redis.redis_client")
    redis.ttl = AsyncMock(return_value=-2)  # key absent
    redis.set = AsyncMock()
    redis.delete = AsyncMock()
    ran = await service.refresh_materialization()
    assert ran is True
    service.repository.materialize_full.assert_awaited_once()
    redis.set.assert_not_awaited()      # armed post-commit, not during the run
    redis.delete.assert_not_awaited()   # nothing to unstick


@pytest.mark.asyncio
async def test_refresh_materialization_unsticks_wedged_no_ttl_key(service, mocker):
    """A wedged marker with NO expiry (TTL -1) — the freeze bug — is DELETED and
    the recompute runs, so it self-heals without manual Redis surgery."""
    redis = mocker.patch("app.infrastructure.cache.redis.redis_client")
    redis.ttl = AsyncMock(return_value=-1)  # exists, no expiry → wedged
    redis.delete = AsyncMock()
    ran = await service.refresh_materialization()
    assert ran is True
    redis.delete.assert_awaited_once_with("clients:materialize:throttle")
    service.repository.materialize_full.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_materialized_arms_throttle_with_ttl(service, mocker):
    """The throttle marker is set only with a TTL (never a no-expiry lock — the
    bug that froze materialisation), and without NX (it's not a lock)."""
    redis = mocker.patch("app.infrastructure.cache.redis.redis_client")
    redis.set = AsyncMock()
    await service.mark_materialized()
    redis.set.assert_awaited_once()
    assert redis.set.await_args.kwargs.get("ex") == 300
    assert "nx" not in redis.set.await_args.kwargs


@pytest.mark.asyncio
async def test_refresh_materialization_skips_when_redis_down(service, mocker):
    """Redis unavailable → fail CLOSED: SKIP the costly full-orders scan this
    cycle rather than amplify it while the cache is already degraded."""
    redis = mocker.patch("app.infrastructure.cache.redis.redis_client")
    redis.ttl = AsyncMock(side_effect=RuntimeError("redis down"))
    ran = await service.refresh_materialization()
    assert ran is False
    service.repository.materialize_full.assert_not_awaited()
