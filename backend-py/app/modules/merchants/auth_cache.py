"""Two-tier cache for merchant authentication on the hot order-creation path.

Layer 1: in-process TTL cache keyed by sha256(api_key)[:16]. Survives within
a single uvicorn worker; covers 99% of requests from the same merchant.

Layer 2: Redis. Survives across workers; fills L1 on first hit per worker.

A reverse index `merchant:auth:rev:{merchant_id}` -> hash lets us invalidate
by merchant_id in O(1) without scanning Redis keys (which is expensive in
production).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.enums.cascading import CascadeMode
from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.infrastructure.cache.redis import redis_client
from app.modules.merchants.models import Merchant

logger = logging.getLogger(__name__)

L1_TTL_SECONDS = 30
L1_MAX_ENTRIES = 512
L2_TTL_SECONDS = 300
NEGATIVE_TTL_SECONDS = 5

_REDIS_KEY_PREFIX = "merchant:auth:"
_REDIS_REV_PREFIX = "merchant:auth:rev:"
_REDIS_NEG_PREFIX = "merchant:auth:neg:"


def _hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


class CachedTraderGroup(BaseModel):
    """Minimal projection of TraderGroup — only what hot paths read."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class CachedMerchant(BaseModel):
    """Detached merchant snapshot safe to share across requests.

    Mirrors the fields that the merchant auth dependency and order-creation
    service read. Enum fields keep their proper types so existing code can
    still do `merchant.status.value` etc.
    """
    model_config = ConfigDict(from_attributes=True, use_enum_values=False)

    id: int
    user_id: int
    name: Optional[str] = None
    api_key: str
    api_secret: str
    status: TerminalStatus
    currency: Currency
    fees: Dict[str, Any] = {}
    rate_config_id: Optional[int] = None
    order_ttl_seconds: int
    cascade_mode: CascadeMode
    requisite_search_timeout_ms: int
    webhook_url: Optional[str] = None
    withdrawal_fee_fixed: Decimal = Decimal("0")
    telegram_user_ids: List[Any] = []
    trader_groups: List[CachedTraderGroup] = []


class _TTLEntry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Optional[CachedMerchant], expires_at: float) -> None:
        self.value = value
        self.expires_at = expires_at


class MerchantAuthCache:
    """L1 (in-process) + L2 (Redis) cache for merchant lookup by API key.

    A single asyncio.Lock guards the miss path; we don't expect contention
    high enough to need per-key locks.
    """

    def __init__(self) -> None:
        self._l1: Dict[str, _TTLEntry] = {}
        self._lock = asyncio.Lock()
        self._hits_l1 = 0
        self._hits_l2 = 0
        self._misses = 0

    def _l1_get(self, key_hash: str) -> Optional[_TTLEntry]:
        entry = self._l1.get(key_hash)
        if entry is None:
            return None
        if entry.expires_at < time.monotonic():
            self._l1.pop(key_hash, None)
            return None
        return entry

    def _l1_set(self, key_hash: str, value: Optional[CachedMerchant], ttl: int) -> None:
        if len(self._l1) >= L1_MAX_ENTRIES:
            now = time.monotonic()
            expired = [k for k, v in self._l1.items() if v.expires_at < now]
            for k in expired:
                self._l1.pop(k, None)
            if len(self._l1) >= L1_MAX_ENTRIES:
                try:
                    self._l1.pop(next(iter(self._l1)))
                except StopIteration:
                    pass
        self._l1[key_hash] = _TTLEntry(value, time.monotonic() + ttl)

    async def get(self, api_key: str, session: AsyncSession) -> Optional[CachedMerchant]:
        if not api_key:
            return None
        key_hash = _hash_api_key(api_key)
        entry = self._l1_get(key_hash)
        if entry is not None:
            self._hits_l1 += 1
            return entry.value

        async with self._lock:
            entry = self._l1_get(key_hash)
            if entry is not None:
                self._hits_l1 += 1
                return entry.value

            try:
                neg = await redis_client.get(_REDIS_NEG_PREFIX + key_hash)
                if neg is not None:
                    self._l1_set(key_hash, None, NEGATIVE_TTL_SECONDS)
                    self._hits_l2 += 1
                    return None

                raw = await redis_client.get(_REDIS_KEY_PREFIX + key_hash)
            except Exception:
                logger.warning("merchant_auth_cache: redis read failed; falling back to DB", exc_info=True)
                raw = None
                neg = None

            if raw is not None:
                try:
                    cached = CachedMerchant.model_validate_json(raw)
                    self._l1_set(key_hash, cached, L1_TTL_SECONDS)
                    self._hits_l2 += 1
                    return cached
                except Exception:
                    logger.warning("merchant_auth_cache: failed to parse L2 entry; refetching", exc_info=True)

            self._misses += 1
            merchant = await self._fetch_from_db(api_key, session)
            if merchant is None:
                self._l1_set(key_hash, None, NEGATIVE_TTL_SECONDS)
                try:
                    await redis_client.set(_REDIS_NEG_PREFIX + key_hash, "1", ex=NEGATIVE_TTL_SECONDS)
                except Exception:
                    logger.warning("merchant_auth_cache: redis negative write failed", exc_info=True)
                return None

            cached = self._to_cached(merchant)
            self._l1_set(key_hash, cached, L1_TTL_SECONDS)
            try:
                payload = cached.model_dump_json()
                await redis_client.set(_REDIS_KEY_PREFIX + key_hash, payload, ex=L2_TTL_SECONDS)
                await redis_client.set(_REDIS_REV_PREFIX + str(cached.id), key_hash, ex=L2_TTL_SECONDS)
            except Exception:
                logger.warning("merchant_auth_cache: redis write failed", exc_info=True)
            return cached

    async def invalidate(
        self,
        *,
        api_key: Optional[str] = None,
        merchant_id: Optional[int] = None,
    ) -> None:
        """Drop a cached merchant from both layers.

        Pass api_key when you have it (regenerate-api-key flow must pass the
        OLD key). Pass merchant_id when you've just mutated the merchant
        through an admin endpoint and only know its id.
        """
        if not api_key and not merchant_id:
            return

        key_hash: Optional[str] = None
        if api_key:
            key_hash = _hash_api_key(api_key)
        elif merchant_id is not None:
            try:
                key_hash = await redis_client.get(_REDIS_REV_PREFIX + str(merchant_id))
            except Exception:
                logger.warning("merchant_auth_cache: redis rev lookup failed", exc_info=True)
                key_hash = None

        if key_hash:
            self._l1.pop(key_hash, None)
            try:
                await redis_client.delete(_REDIS_KEY_PREFIX + key_hash)
                await redis_client.delete(_REDIS_NEG_PREFIX + key_hash)
            except Exception:
                logger.warning("merchant_auth_cache: redis delete failed", exc_info=True)

        if merchant_id is not None:
            try:
                await redis_client.delete(_REDIS_REV_PREFIX + str(merchant_id))
            except Exception:
                logger.warning("merchant_auth_cache: redis rev delete failed", exc_info=True)

    def invalidate_local(self) -> None:
        """Drop the in-process layer only — used by tests and emergency reload."""
        self._l1.clear()

    @property
    def stats(self) -> Dict[str, int]:
        return {"l1_hits": self._hits_l1, "l2_hits": self._hits_l2, "misses": self._misses}

    @staticmethod
    async def _fetch_from_db(api_key: str, session: AsyncSession) -> Optional[Merchant]:
        stmt = (
            select(Merchant)
            .options(selectinload(Merchant.trader_groups))
            .where(Merchant.api_key == api_key)
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    def _to_cached(merchant: Merchant) -> CachedMerchant:
        return CachedMerchant.model_validate(merchant)


merchant_auth_cache = MerchantAuthCache()
