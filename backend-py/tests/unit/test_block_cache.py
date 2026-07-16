"""Hot-path client-ban cache: fail-open, ~no per-order I/O, opaque membership."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.clients import block_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    block_cache._cache["blocked"] = frozenset()
    block_cache._cache["expires_at"] = 0.0
    yield
    block_cache._cache["blocked"] = frozenset()
    block_cache._cache["expires_at"] = 0.0


@pytest.mark.asyncio
async def test_none_or_empty_client_is_free(mocker):
    red = mocker.patch.object(block_cache, "redis_client")
    red.smembers = AsyncMock(return_value=set())

    assert await block_cache.is_blocked(1, None) is False
    assert await block_cache.is_blocked(1, "") is False
    # No clientID ⇒ no Redis I/O at all (the common order path).
    red.smembers.assert_not_called()


@pytest.mark.asyncio
async def test_membership_is_per_merchant_and_client(mocker):
    red = mocker.patch.object(block_cache, "redis_client")
    red.smembers = AsyncMock(return_value={"7:cli-1"})

    assert await block_cache.is_blocked(7, "cli-1") is True
    assert await block_cache.is_blocked(7, "other") is False  # same merchant, other client
    assert await block_cache.is_blocked(8, "cli-1") is False  # other merchant, same client


@pytest.mark.asyncio
async def test_fail_open_on_redis_error(mocker):
    red = mocker.patch.object(block_cache, "redis_client")
    red.smembers = AsyncMock(side_effect=RuntimeError("redis down"))

    # No prior snapshot → empty → not blocked. The ban must never break creation.
    assert await block_cache.is_blocked(7, "cli-1") is False


@pytest.mark.asyncio
async def test_micro_cache_avoids_per_order_redis(mocker):
    red = mocker.patch.object(block_cache, "redis_client")
    red.smembers = AsyncMock(return_value={"7:cli-1"})

    assert await block_cache.is_blocked(7, "cli-1") is True
    assert await block_cache.is_blocked(7, "cli-1") is True
    # Second lookup within the TTL is served from the process-local snapshot.
    red.smembers.assert_awaited_once()


@pytest.mark.asyncio
async def test_fail_retains_last_good_snapshot(mocker):
    """A later Redis error must NOT un-block an already-blocked client: the
    refresh keeps the last-good snapshot (the inverse of cold-start fail-open)."""
    red = mocker.patch.object(block_cache, "redis_client")
    red.smembers = AsyncMock(return_value={"7:cli-1"})
    assert await block_cache.is_blocked(7, "cli-1") is True  # prime the snapshot

    # TTL expires and the next refresh throws.
    block_cache._cache["expires_at"] = 0.0
    red.smembers = AsyncMock(side_effect=RuntimeError("redis down"))

    # The blocked client STAYS blocked (retained snapshot)...
    assert await block_cache.is_blocked(7, "cli-1") is True
    # ...and a never-seen pair is still not blocked.
    assert await block_cache.is_blocked(7, "never") is False


# ── Redis write / reconcile helpers (admin action + beat reconcile) ──

@pytest.mark.asyncio
async def test_add_to_blocked_set_sadds_and_invalidates_local(mocker):
    red = mocker.patch.object(block_cache, "redis_client")
    red.sadd = AsyncMock()
    block_cache._cache["expires_at"] = 9e99  # pretend a fresh local snapshot

    await block_cache.add_to_blocked_set(7, "cli-1")

    red.sadd.assert_awaited_once_with(block_cache.BLOCKED_SET_KEY, "7:cli-1")
    assert block_cache._cache["expires_at"] == 0.0  # local cache forced to refresh


@pytest.mark.asyncio
async def test_remove_from_blocked_set_srems_and_invalidates_local(mocker):
    red = mocker.patch.object(block_cache, "redis_client")
    red.srem = AsyncMock()
    block_cache._cache["expires_at"] = 9e99

    await block_cache.remove_from_blocked_set(7, "cli-1")

    red.srem.assert_awaited_once_with(block_cache.BLOCKED_SET_KEY, "7:cli-1")
    assert block_cache._cache["expires_at"] == 0.0


def _fake_pipeline(red):
    pipe = MagicMock()
    pipe.delete = MagicMock()
    pipe.sadd = MagicMock()
    pipe.rename = MagicMock()
    pipe.execute = AsyncMock()
    red.pipeline = MagicMock(return_value=pipe)
    return pipe


@pytest.mark.asyncio
async def test_rebuild_blocked_set_atomic_swap(mocker):
    red = mocker.patch.object(block_cache, "redis_client")
    pipe = _fake_pipeline(red)

    await block_cache.rebuild_blocked_set(["1:a", "2:b"])

    tmp = f"{block_cache.BLOCKED_SET_KEY}:rebuild"
    pipe.delete.assert_called_once_with(tmp)
    pipe.sadd.assert_called_once_with(tmp, "1:a", "2:b")
    pipe.rename.assert_called_once_with(tmp, block_cache.BLOCKED_SET_KEY)  # atomic swap
    pipe.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_rebuild_blocked_set_empty_clears_live_key(mocker):
    red = mocker.patch.object(block_cache, "redis_client")
    pipe = _fake_pipeline(red)

    await block_cache.rebuild_blocked_set([])

    # No members → never SADD/RENAME (RENAME would fail on a missing temp key);
    # the live key is cleared directly instead.
    pipe.sadd.assert_not_called()
    pipe.rename.assert_not_called()
    assert pipe.delete.call_count == 2  # temp key + live key
    pipe.execute.assert_awaited_once()


# ── Blocked-attempt counter (telemetry, rejected branch only) ──

@pytest.mark.asyncio
async def test_record_blocked_attempt_hincrby(mocker):
    red = mocker.patch.object(block_cache, "redis_client")
    red.hincrby = AsyncMock()

    await block_cache.record_blocked_attempt(7, "cli-1")

    red.hincrby.assert_awaited_once_with(block_cache.BLOCKED_ATTEMPTS_KEY, "7:cli-1", 1)


@pytest.mark.asyncio
async def test_record_blocked_attempt_noop_without_client_id(mocker):
    red = mocker.patch.object(block_cache, "redis_client")
    red.hincrby = AsyncMock()

    await block_cache.record_blocked_attempt(7, None)
    await block_cache.record_blocked_attempt(7, "")

    red.hincrby.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_blocked_attempt_swallows_redis_error(mocker):
    red = mocker.patch.object(block_cache, "redis_client")
    red.hincrby = AsyncMock(side_effect=RuntimeError("redis down"))

    await block_cache.record_blocked_attempt(7, "cli-1")  # must not raise — reject path stays alive


@pytest.mark.asyncio
async def test_read_blocked_attempts_does_not_consume(mocker):
    """Read is NON-destructive — it must not decrement Redis (the caller acks
    only after the DB commit)."""
    red = mocker.patch.object(block_cache, "redis_client")
    red.hgetall = AsyncMock(return_value={b"7:cli-1": b"3", b"8:c2": b"1", b"9:c3": b"0"})
    red.hincrby = AsyncMock()

    out = await block_cache.read_blocked_attempts()

    assert out == {"7:cli-1": 3, "8:c2": 1}  # zero-delta field skipped
    red.hincrby.assert_not_awaited()          # nothing consumed on read


@pytest.mark.asyncio
async def test_read_blocked_attempts_empty_and_error(mocker):
    red = mocker.patch.object(block_cache, "redis_client")
    red.hgetall = AsyncMock(return_value={})
    assert await block_cache.read_blocked_attempts() == {}
    red.hgetall = AsyncMock(side_effect=RuntimeError("redis down"))
    assert await block_cache.read_blocked_attempts() == {}  # swallowed


@pytest.mark.asyncio
async def test_ack_blocked_attempts_decrements_each(mocker):
    red = mocker.patch.object(block_cache, "redis_client")
    red.hincrby = AsyncMock()

    await block_cache.ack_blocked_attempts({"7:cli-1": 3, "8:c2": 1})

    # HINCRBY -delta (not delete) preserves any increment arriving since the read.
    red.hincrby.assert_any_await(block_cache.BLOCKED_ATTEMPTS_KEY, "7:cli-1", -3)
    red.hincrby.assert_any_await(block_cache.BLOCKED_ATTEMPTS_KEY, "8:c2", -1)
    assert red.hincrby.await_count == 2


@pytest.mark.asyncio
async def test_ack_blocked_attempts_swallows_error(mocker):
    red = mocker.patch.object(block_cache, "redis_client")
    red.hincrby = AsyncMock(side_effect=RuntimeError("redis down"))
    await block_cache.ack_blocked_attempts({"7:cli-1": 3})  # must not raise
