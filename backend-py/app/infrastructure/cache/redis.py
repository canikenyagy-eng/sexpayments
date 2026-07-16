import json
import logging
from typing import Any, Awaitable, Callable, Optional

import redis.asyncio as redis

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

redis_client = redis.from_url(
    str(settings.REDIS_URL),
    encoding="utf-8",
    decode_responses=True,
)


class RedisCache:
    def __init__(self, client: redis.Redis):
        self.client = client

    async def get(self, key: str) -> Optional[str]:
        return await self.client.get(key)

    async def set(self, key: str, value: Any, ttl: int) -> None:
        await self.client.set(key, value, ex=ttl)

    async def delete(self, key: str) -> None:
        await self.client.delete(key)


def get_redis_cache() -> RedisCache:
    return RedisCache(redis_client)


async def cache_json(
    key: str,
    ttl: int,
    compute: Callable[[], Awaitable[Any]],
) -> Any:
    """Read-through JSON cache for any jsonable result.

    On hit — returns the parsed JSON payload (note: dataclasses become
    plain dicts on the round-trip; callers that need a typed instance
    should reconstruct on the way out).

    On miss — runs ``compute()``, stores the JSON-encoded result with
    ``ttl`` seconds expiry, and returns it.

    Cache failures (Redis down, malformed JSON, unserialisable result)
    are logged and bypassed — the function never fails the caller. This
    is intentional: caching here is a perf optimisation, not a load-
    shedding mechanism, so an outage should degrade to "slow" not "down".
    """
    try:
        raw = await redis_client.get(key)
        if raw is not None:
            return json.loads(raw)
    except Exception as exc:  # noqa: BLE001 — cache must not break callers
        logger.warning("cache read failed for %s: %s", key, exc)

    result = await compute()

    try:
        await redis_client.set(key, json.dumps(result, default=str), ex=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache write failed for %s: %s", key, exc)

    return result
