"""Client ban check on the order-creation hot path.

A merchant-supplied client id (payin ``clientID`` / legacy ``userId``, stored on
``orders.client_user_id``) can be blocked by an admin. A blocked client gets no
requisite — payin creation fails with the SAME generic "no requisite" as a real
miss, so the ban is opaque to the merchant.

The blocked set lives in Redis (``clients:blocked`` — a SET of
``"{merchant_id}:{client_user_id}"``), kept in sync by the admin block/unblock
action (SADD/SREM) and reconciled from the DB by ``refresh_clients_task``. The
hot-path accessor ``is_blocked()`` wraps it in a tiny process-local cache (~5s),
so order creation does at most one Redis call per few seconds per worker — the
ban never adds per-order I/O. Any Redis failure degrades to "not blocked": the
ban can never slow or break order creation (fail-open, like prime-time).
"""
import time
from typing import FrozenSet, Optional

from app.core.logging import get_logger
from app.infrastructure.cache.redis import redis_client

logger = get_logger(__name__)

BLOCKED_SET_KEY = "clients:blocked"
# Per-client counter of withheld requisites (ban rejections), as a Redis HASH
# of ``member_key → count``. Incremented on the ALREADY-rejected branch only
# (never on the success path) and drained into ``clients.blocked_attempts`` by
# the reconcile job. Pure telemetry — a Redis flush at worst loses the un-drained
# delta; the DB column keeps the durable total.
BLOCKED_ATTEMPTS_KEY = "clients:blocked_attempts"

# Hot-path micro-cache: order creation reads an in-memory set and refreshes
# from Redis at most once per this many seconds per worker.
_CACHE_TTL_S = 5.0
_cache: dict = {"blocked": frozenset(), "expires_at": 0.0}


def member_key(merchant_id: int, client_user_id: str) -> str:
    return f"{merchant_id}:{client_user_id}"


async def _get_blocked_set() -> FrozenSet[str]:
    now = time.monotonic()
    if now < _cache["expires_at"]:
        return _cache["blocked"]

    blocked = _cache["blocked"]  # keep the last-good snapshot on failure
    try:
        members = await redis_client.smembers(BLOCKED_SET_KEY)
        blocked = frozenset(members or ())
    except Exception as exc:  # noqa: BLE001 — must never break order creation
        logger.warning("blocked-clients read failed, using last snapshot: %s", exc)

    _cache["blocked"] = blocked
    _cache["expires_at"] = now + _CACHE_TTL_S
    return blocked


async def is_blocked(merchant_id: int, client_user_id: Optional[str]) -> bool:
    """Hot-path check: is this merchant's client banned? ``None``/empty → never.

    Process-local micro-cache (~5s) over a Redis SET. Fail-safe: any error →
    ``False`` (the ban must never block/slow order creation)."""
    if not client_user_id:
        return False
    return member_key(merchant_id, client_user_id) in await _get_blocked_set()


def invalidate_local_cache() -> None:
    """Force the next ``is_blocked`` in THIS process to re-read Redis. Other
    workers converge within the TTL — admin writes also SADD/SREM directly."""
    _cache["expires_at"] = 0.0


async def add_to_blocked_set(merchant_id: int, client_user_id: str) -> None:
    """Mark a client blocked in the Redis hot-path set (admin action)."""
    await redis_client.sadd(BLOCKED_SET_KEY, member_key(merchant_id, client_user_id))
    invalidate_local_cache()


async def remove_from_blocked_set(merchant_id: int, client_user_id: str) -> None:
    """Unmark a client in the Redis hot-path set (admin action)."""
    await redis_client.srem(BLOCKED_SET_KEY, member_key(merchant_id, client_user_id))
    invalidate_local_cache()


async def rebuild_blocked_set(members: list[str]) -> None:
    """Atomically replace the Redis blocked-set from the DB source of truth
    (reconcile job — self-heals after a Redis flush). Builds a temp key and
    RENAMEs it over the live key so readers never see a half-built set."""
    tmp_key = f"{BLOCKED_SET_KEY}:rebuild"
    pipe = redis_client.pipeline()
    pipe.delete(tmp_key)
    if members:
        pipe.sadd(tmp_key, *members)
        pipe.rename(tmp_key, BLOCKED_SET_KEY)
    else:
        # RENAME fails on a missing (empty) temp key — just clear the live key.
        pipe.delete(BLOCKED_SET_KEY)
    await pipe.execute()
    invalidate_local_cache()


async def record_blocked_attempt(merchant_id: int, client_user_id: Optional[str]) -> None:
    """Count one withheld requisite for a blocked client (HINCRBY). Called ONLY
    on the already-rejected branch of order creation — never on the success path,
    so legit issuance keeps zero added I/O. Best-effort telemetry: any Redis
    failure is swallowed (the ban must never slow or break order handling)."""
    if not client_user_id:
        return
    try:
        await redis_client.hincrby(BLOCKED_ATTEMPTS_KEY, member_key(merchant_id, client_user_id), 1)
    except Exception as exc:  # noqa: BLE001 — telemetry only; never break the reject path
        logger.warning("record_blocked_attempt failed (%s:%s): %s", merchant_id, client_user_id, exc)


async def read_blocked_attempts() -> dict:
    """Read (do NOT consume) the per-client blocked-attempt deltas for the
    reconcile job to fold into ``clients.blocked_attempts``. Returns
    ``{member_key: delta}`` for positive deltas; ``{}`` on a Redis read failure.

    Non-destructive on purpose: the caller acks (``ack_blocked_attempts``) only
    AFTER the DB upsert has COMMITTED, so a commit failure leaves the deltas in
    Redis for the next run — no telemetry loss (mirrors the idempotent
    materialize self-heal). The residual is the opposite, rarer window: a failed
    post-commit ack re-folds the same delta next run (a small over-count, since
    the upsert accumulates)."""
    try:
        raw = await redis_client.hgetall(BLOCKED_ATTEMPTS_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("read_blocked_attempts failed: %s", exc)
        return {}

    out: dict = {}
    for field, value in (raw or {}).items():
        key = field.decode() if isinstance(field, (bytes, bytearray)) else field
        try:
            delta = int(value)
        except (TypeError, ValueError):
            continue
        if delta > 0:
            out[key] = delta
    return out


async def ack_blocked_attempts(deltas: dict) -> None:
    """Decrement Redis by exactly the deltas already FOLDED + COMMITTED into the
    DB (``HINCRBY -delta``, not delete — so an increment arriving since the read
    is preserved as the residual). Call ONLY after the upsert commits. Best-effort
    per key: a failed decrement just leaves that delta to be re-folded next run."""
    for key, delta in (deltas or {}).items():
        if delta <= 0:
            continue
        try:
            await redis_client.hincrby(BLOCKED_ATTEMPTS_KEY, key, -delta)
        except Exception as exc:  # noqa: BLE001 — leave the delta for the next run
            logger.warning("ack_blocked_attempts decrement failed (%s): %s", key, exc)
