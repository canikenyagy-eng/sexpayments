"""Auto-feedback for orders whose result never arrived.

The selector relies on a feedback signal per decision. If a decision is
issued and the downstream system never reports back (the merchant times
out, the trader vanished, …), we'd never learn that the choice was bad.

This job scans pending decisions older than ``timeout_sec``, applies
``feedback(reward=0, signal="timeout")`` to each, and is meant to run on
a 1-minute cron.

We track pending decisions via a Redis sorted set keyed by
``{ns}:pending`` with the timestamp as the score. ``mark_pending`` adds
an entry; ``sweep`` reads everything past the cutoff and feeds back. The
mark step is intentionally separate from ``EntitySelector.select`` so
callers who don't need this safety net don't pay for it on the hot path.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

import redis.asyncio as redis

from app.modules.selector.core.selector import EntitySelector


logger = logging.getLogger(__name__)


@dataclass
class TimeoutReport:
    selector_name: str
    swept: int
    fed_back: int
    duration_sec: float


class FeedbackTimeoutJob:
    def __init__(
        self,
        selector: EntitySelector,
        redis_client: redis.Redis,
    ):
        self._selector = selector
        self._redis = redis_client
        self._key = f"{selector.config.namespace}:pending"

    async def mark_pending(self, order_id: str, entity_id: str) -> None:
        """Register a decision so the sweeper can auto-timeout it later."""
        if not order_id:
            return
        try:
            await self._redis.zadd(
                self._key,
                {json.dumps({"o": order_id, "e": entity_id}): time.time()},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("selector_mark_pending_failed: %s", exc)

    async def sweep(self, *, now: Optional[float] = None) -> TimeoutReport:
        t0 = time.perf_counter()
        deadline = (now if now is not None else time.time()) - (
            self._selector.config.reward.timeout_sec
        )
        try:
            entries = await self._redis.zrangebyscore(self._key, 0, deadline)
        except Exception as exc:  # noqa: BLE001
            logger.warning("selector_timeout_zrange_failed: %s", exc)
            return TimeoutReport(
                selector_name=self._selector.name,
                swept=0,
                fed_back=0,
                duration_sec=time.perf_counter() - t0,
            )

        swept = len(entries)
        fed = 0
        for entry in entries:
            try:
                payload = json.loads(
                    entry.decode() if isinstance(entry, bytes) else entry
                )
                applied = await self._selector.feedback(
                    order_id=payload["o"],
                    entity_id=payload["e"],
                    reward=0.0,
                    signal="timeout",
                )
                if applied:
                    fed += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "selector_timeout_feedback_failed",
                    extra={"entry": str(entry)[:200], "error": str(exc)},
                )

        # Drop the swept entries regardless of whether each feedback applied —
        # if one slipped (duplicate), we shouldn't re-issue forever.
        try:
            await self._redis.zremrangebyscore(self._key, 0, deadline)
        except Exception as exc:  # noqa: BLE001
            logger.warning("selector_timeout_zremrange_failed: %s", exc)

        return TimeoutReport(
            selector_name=self._selector.name,
            swept=swept,
            fed_back=fed,
            duration_sec=time.perf_counter() - t0,
        )

    async def acknowledge(self, order_id: str) -> None:
        """Remove a pending entry once a real feedback has arrived."""
        if not order_id:
            return
        try:
            # zrem requires the member; we don't have it indexed, so scan a
            # small window. In practice the sorted set is small and recent.
            entries = await self._redis.zrange(self._key, 0, -1)
            for entry in entries:
                raw = entry.decode() if isinstance(entry, bytes) else entry
                try:
                    if json.loads(raw).get("o") == order_id:
                        await self._redis.zrem(self._key, entry)
                        return
                except (ValueError, KeyError):
                    continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("selector_timeout_ack_failed: %s", exc)
