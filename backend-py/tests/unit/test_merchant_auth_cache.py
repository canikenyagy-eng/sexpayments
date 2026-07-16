from unittest.mock import AsyncMock, MagicMock

import pytest

# Register all mappers that Merchant relationships and selectinload paths
# touch — without these imports SQLAlchemy fails to resolve string-based
# relationships at the first DB query in an isolated test run.
from app.modules.users.models import User  # noqa: F401
from app.modules.requisites.models import Requisite  # noqa: F401
from app.modules.payments.models import PaymentOption  # noqa: F401
from app.modules.traders.models import Trader, TraderGroup  # noqa: F401
from app.modules.cascading.models import CascadeGroup  # noqa: F401
from app.modules.orders.models import Order  # noqa: F401

from app.common.enums.cascading import CascadeMode
from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.modules.merchants.auth_cache import (
    CachedMerchant,
    MerchantAuthCache,
    _hash_api_key,
)


def _fake_orm_merchant(*, mid: int = 1, api_key: str = "key-1") -> MagicMock:
    """Build a duck-typed stand-in for the SQLAlchemy Merchant ORM object.

    pydantic's `model_validate(..., from_attributes=True)` reads attributes by
    name, so a MagicMock with the right attributes is enough.
    """
    m = MagicMock()
    m.id = mid
    m.user_id = 100 + mid
    m.name = f"merchant-{mid}"
    m.api_key = api_key
    m.api_secret = "encrypted-secret"
    m.status = TerminalStatus.ENABLED
    m.currency = Currency.RUB
    m.fees = {"card": 2.5}
    m.rate_config_id = None
    m.order_ttl_seconds = 1800
    m.cascade_mode = CascadeMode.OFF
    m.requisite_search_timeout_ms = 300
    m.webhook_url = None
    m.withdrawal_fee_fixed = 0
    m.telegram_user_ids = []
    m.trader_groups = []
    return m


def _mock_session_returning(merchant):
    """AsyncSession mock whose execute() yields an exec result with first()=merchant."""
    session = AsyncMock()
    exec_result = MagicMock()
    scalars = MagicMock()
    scalars.first = MagicMock(return_value=merchant)
    exec_result.scalars = MagicMock(return_value=scalars)
    session.execute = AsyncMock(return_value=exec_result)
    return session


@pytest.fixture
def redis_mock(monkeypatch):
    """Replace the module-level redis_client used by MerchantAuthCache."""
    rc = AsyncMock()
    rc.get = AsyncMock(return_value=None)
    rc.set = AsyncMock(return_value=True)
    rc.delete = AsyncMock(return_value=1)
    monkeypatch.setattr("app.modules.merchants.auth_cache.redis_client", rc)
    return rc


@pytest.fixture
def cache():
    return MerchantAuthCache()


@pytest.mark.asyncio
async def test_l1_hit_skips_db_and_redis(cache, redis_mock):
    """Second call with the same api_key must come from L1 — no DB, no Redis."""
    orm = _fake_orm_merchant()
    session = _mock_session_returning(orm)

    first = await cache.get("key-1", session)
    assert isinstance(first, CachedMerchant)
    assert first.id == 1
    assert session.execute.await_count == 1
    assert redis_mock.set.await_count >= 1

    redis_mock.reset_mock()
    second = await cache.get("key-1", session)
    assert second is first
    assert session.execute.await_count == 1
    assert redis_mock.get.await_count == 0


@pytest.mark.asyncio
async def test_l2_hit_skips_db(cache, redis_mock):
    """Empty L1 + populated Redis → no DB query, L1 gets warmed."""
    orm = _fake_orm_merchant()
    serialized = CachedMerchant.model_validate(orm).model_dump_json()
    redis_mock.get = AsyncMock(side_effect=[None, serialized])  # neg miss, then real hit

    session = _mock_session_returning(None)
    result = await cache.get("key-1", session)
    assert result is not None
    assert result.id == 1
    assert session.execute.await_count == 0


@pytest.mark.asyncio
async def test_db_miss_fills_both_layers(cache, redis_mock):
    orm = _fake_orm_merchant()
    session = _mock_session_returning(orm)

    result = await cache.get("key-1", session)
    assert result is not None
    assert result.api_secret == "encrypted-secret"

    redis_keys = [call.args[0] for call in redis_mock.set.await_args_list]
    assert any(k.startswith("merchant:auth:") and "rev" not in k for k in redis_keys)
    assert any(k.startswith("merchant:auth:rev:") for k in redis_keys)


@pytest.mark.asyncio
async def test_not_found_negative_cached(cache, redis_mock):
    """Unknown api_key returns None; second call doesn't re-hit the DB."""
    session = _mock_session_returning(None)

    assert await cache.get("nope", session) is None
    assert session.execute.await_count == 1

    second = await cache.get("nope", session)
    assert second is None
    assert session.execute.await_count == 1  # cached negative


@pytest.mark.asyncio
async def test_invalidate_by_api_key(cache, redis_mock):
    orm = _fake_orm_merchant()
    session = _mock_session_returning(orm)
    await cache.get("key-1", session)
    assert session.execute.await_count == 1

    await cache.invalidate(api_key="key-1")
    redis_keys = [call.args[0] for call in redis_mock.delete.await_args_list]
    assert "merchant:auth:" + _hash_api_key("key-1") in redis_keys

    # After invalidation the next get must reach the DB again
    await cache.get("key-1", session)
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_invalidate_by_merchant_id_uses_reverse_index(cache, redis_mock):
    orm = _fake_orm_merchant(mid=42, api_key="key-42")
    session = _mock_session_returning(orm)
    await cache.get("key-42", session)
    assert session.execute.await_count == 1

    # invalidate(merchant_id=) must consult the reverse-index in Redis
    redis_mock.get = AsyncMock(return_value=_hash_api_key("key-42"))
    await cache.invalidate(merchant_id=42)

    deleted = [call.args[0] for call in redis_mock.delete.await_args_list]
    assert "merchant:auth:" + _hash_api_key("key-42") in deleted
    assert "merchant:auth:rev:42" in deleted

    # Reset Redis to clean state — both negative-cache and the main key are
    # gone, so the next get must reach the DB
    redis_mock.get = AsyncMock(return_value=None)
    await cache.get("key-42", session)
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_get_empty_api_key_returns_none(cache, redis_mock):
    session = _mock_session_returning(None)
    assert await cache.get("", session) is None
    assert session.execute.await_count == 0


@pytest.mark.asyncio
async def test_redis_failure_falls_through_to_db(cache, redis_mock):
    """If Redis is down, get must still answer from DB (degraded mode)."""
    redis_mock.get = AsyncMock(side_effect=Exception("redis down"))
    redis_mock.set = AsyncMock(side_effect=Exception("redis down"))

    orm = _fake_orm_merchant()
    session = _mock_session_returning(orm)

    result = await cache.get("key-1", session)
    assert result is not None
    assert result.id == 1


def test_hash_api_key_is_deterministic_and_short():
    h = _hash_api_key("abc")
    assert len(h) == 16
    assert h == _hash_api_key("abc")
    assert h != _hash_api_key("abd")
