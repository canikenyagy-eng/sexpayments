"""Token-bucket rate limiter for upsert_metrics / admin write paths.

Single-process, asyncio-friendly. The metrics API can be hit by external
data pipelines whose retries could otherwise hammer Redis or fast-forward
the bandit with fake feedback; the limiter caps per-key throughput.

This is intentionally process-local — the selector subsystem already runs
behind a small fleet, and a Redis-backed limiter would be overkill at this
scale. Promote to Redis if/when we deploy multi-node admin write paths.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class TokenBucketRateLimiter:
    def __init__(self, *, rate_per_sec: float, burst: float):
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        if burst <= 0:
            raise ValueError("burst must be > 0")
        self._rate = rate_per_sec
        self._burst = burst
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, key: str, *, now: float | None = None) -> bool:
        ts = time.monotonic() if now is None else now
        bucket = self._buckets.get(key)
        if bucket is None:
            self._buckets[key] = _Bucket(tokens=self._burst - 1.0, last_refill=ts)
            return True
        elapsed = ts - bucket.last_refill
        if elapsed > 0:
            bucket.tokens = min(self._burst, bucket.tokens + elapsed * self._rate)
            bucket.last_refill = ts
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True
        return False

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._buckets.clear()
        else:
            self._buckets.pop(key, None)
