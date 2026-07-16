"""Celery tasks for the cascade module.

* ``cancel_provider_request`` — race-loser cancel notification, fire-and-forget.
* ``forward_receipt_to_provider`` — push merchant's RECEIPT_UPLOADED to the
  provider so they can release/confirm the order on their end.
* ``forward_dispute_to_provider`` — escalate a dispute to the provider.
* ``aggregate_cascade_metrics_task`` — hourly rollup CascadeOrderAttempt →
  CascadeProviderMetric for admin dashboards.
* ``poll_cascade_in_flight_task`` — defensive polling for stuck IN_FLIGHT
  attempts in case a webhook went missing.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.common.enums.cascading import CascadeAttemptStatus
from app.infrastructure.db.session import WorkerSessionLocal as SessionLocal
from app.modules.cascading.integrations import registry
from app.modules.cascading.integrations.base import provider_request_type
from app.modules.cascading.models import (
    CascadeOrderAttempt,
    CascadeProvider,
    CascadeProviderMetric,
)
from app.modules.cascading.repository import (
    CascadeOrderAttemptRepository,
    CascadeProviderMetricRepository,
    CascadeProviderRepository,
)
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _start_of_hour_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(minute=0, second=0, microsecond=0)


# ─── cancel race losers ──────────────────────────────────────


async def _cancel_async(provider_code: str, external_order_id: str) -> bool:
    async with SessionLocal() as session:
        provider_repo = CascadeProviderRepository(session)
        provider = await provider_repo.get_by_code(provider_code)
        if not provider:
            logger.warning(
                "cascade_cancel_provider_not_found",
                extra={"provider_code": provider_code},
            )
            return False
        adapter = registry.get(provider.adapter_type)
        with provider_request_type("cancel"):
            return await adapter.cancel_request(
                provider=provider,
                external_order_id=external_order_id,
                timeout_ms=provider.cancel_timeout_ms,
            )


@celery_app.task(
    name="app.workers.tasks.cascade.cancel_provider_request",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
)
def cancel_provider_request(self, provider_code: str, external_order_id: str):
    try:
        asyncio.run(_cancel_async(provider_code, external_order_id))
    except Exception as exc:
        logger.warning(
            "cascade_cancel_failed",
            extra={"provider_code": provider_code, "error": str(exc)},
        )
        raise self.retry(exc=exc)


async def _cancel_for_order_async(order_id: int) -> str:
    async with SessionLocal() as session:
        attempt = await CascadeOrderAttemptRepository(session).get_won_for_order(order_id)
        if not attempt or not attempt.external_order_id:
            return "no_attempt"
        provider = await CascadeProviderRepository(session).get(attempt.provider_id)
        if not provider:
            return "no_provider"
        adapter = registry.get(provider.adapter_type)
        with provider_request_type("cancel"):
            ok = await adapter.cancel_request(
                provider=provider,
                external_order_id=attempt.external_order_id,
                timeout_ms=provider.cancel_timeout_ms,
            )
        return "sent" if ok else "adapter_failed"


@celery_app.task(
    name="app.workers.tasks.cascade.cancel_provider_for_order",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
)
def cancel_provider_for_order(self, order_id: int):
    """Resolve order → provider, then push a cancel to the upstream provider.

    No-op for non-cascade orders. Used when our own order is cancelled and we
    need to release the reservation upstream.
    """
    try:
        outcome = asyncio.run(_cancel_for_order_async(order_id))
    except Exception as exc:
        logger.warning(
            "cascade_cancel_for_order_failed",
            extra={"order_id": order_id, "error": str(exc)},
        )
        raise self.retry(exc=exc)
    if outcome == "adapter_failed":
        raise self.retry(exc=RuntimeError("cancel_request returned False"))
    return {"order_id": order_id, "outcome": outcome}


# ─── receipt forward ─────────────────────────────────────────


async def _forward_receipt_async(order_id: int, receipt_id: int | None = None) -> str:
    """Returns one of: 'sent', 'no_attempt', 'no_provider', 'adapter_failed'.

    Forwards a SPECIFIC receipt (``receipt_id``) when given — so concurrent
    multi-uploads each reach the provider exactly once and a newer upload
    overwriting ``orders.receipt_file`` can't shadow an earlier receipt. Falls
    back to the order mirror (latest receipt) when ``receipt_id`` is None.
    """
    async with SessionLocal() as session:
        attempt_repo = CascadeOrderAttemptRepository(session)
        attempt = await attempt_repo.get_won_for_order(order_id)
        if not attempt or not attempt.external_order_id:
            # Not a cascade order — nothing to do, don't retry.
            return "no_attempt"

        from app.modules.orders.repository import OrderRepository

        order = await OrderRepository(session).get(order_id)
        if not order:
            return "no_attempt"

        receipt_path = order.receipt_file or ""
        if receipt_id is not None:
            from app.modules.receipts.models import Receipt

            receipt = await session.get(Receipt, receipt_id)
            if receipt and receipt.file_path:
                receipt_path = receipt.file_path

        provider = await CascadeProviderRepository(session).get(attempt.provider_id)
        if not provider:
            return "no_provider"

        adapter = registry.get(provider.adapter_type)
        with provider_request_type("upload"):
            ok = await adapter.notify_receipt(
                provider=provider,
                external_order_id=attempt.external_order_id,
                receipt_path=receipt_path,
                comment=order.trader_comment,
            )
        return "sent" if ok else "adapter_failed"


@celery_app.task(
    name="app.workers.tasks.cascade.forward_receipt_to_provider",
    bind=True,
    max_retries=5,
    default_retry_delay=60,
)
def forward_receipt_to_provider(self, order_id: int, receipt_id: int | None = None):
    try:
        outcome = asyncio.run(_forward_receipt_async(order_id, receipt_id))
    except Exception as exc:
        logger.warning(
            "cascade_forward_receipt_failed",
            extra={"order_id": order_id, "receipt_id": receipt_id, "error": str(exc)},
        )
        raise self.retry(exc=exc)
    if outcome == "adapter_failed":
        raise self.retry(exc=RuntimeError("adapter notify_receipt returned False"))
    return {"order_id": order_id, "outcome": outcome}


# ─── dispute forward ─────────────────────────────────────────


async def _forward_dispute_async(dispute_id: int) -> str:
    async with SessionLocal() as session:
        from app.modules.disputes.repository import DisputeRepository

        dispute = await DisputeRepository(session).get(dispute_id)
        if not dispute:
            return "no_dispute"

        attempt_repo = CascadeOrderAttemptRepository(session)
        attempt = await attempt_repo.get_won_for_order(dispute.order_id)
        if not attempt or not attempt.external_order_id:
            return "no_attempt"

        provider = await CascadeProviderRepository(session).get(attempt.provider_id)
        if not provider:
            return "no_provider"

        adapter = registry.get(provider.adapter_type)
        # ``evidence_files`` is a JSONB array of paths (the model field is
        # plural). The old code read ``evidence_file`` (singular, nonexistent)
        # via getattr → evidence never actually reached the provider.
        evidence = list(dispute.evidence_files or [])
        # Union with appeal-evidence receipts (unified receipts store) — files
        # the merchant uploaded against this dispute via the dispute-bot. Dedup
        # while preserving order so the provider gets every proof exactly once.
        from app.modules.receipts.repository import ReceiptRepository

        for r in await ReceiptRepository(session).list_for_dispute(dispute.id):
            if r.file_path and r.file_path not in evidence:
                evidence.append(r.file_path)
        # ``reason`` is a DisputeReason enum — forward its string value.
        reason = (
            dispute.reason.value
            if hasattr(dispute.reason, "value")
            else (dispute.reason or "merchant_dispute")
        )

        ok = await adapter.raise_dispute(
            provider=provider,
            external_order_id=attempt.external_order_id,
            reason=reason,
            evidence_paths=evidence,
        )
        return "sent" if ok else "adapter_failed"


@celery_app.task(
    name="app.workers.tasks.cascade.forward_dispute_to_provider",
    bind=True,
    max_retries=5,
    default_retry_delay=60,
)
def forward_dispute_to_provider(self, dispute_id: int):
    try:
        outcome = asyncio.run(_forward_dispute_async(dispute_id))
    except Exception as exc:
        logger.warning(
            "cascade_forward_dispute_failed",
            extra={"dispute_id": dispute_id, "error": str(exc)},
        )
        raise self.retry(exc=exc)
    if outcome == "adapter_failed":
        raise self.retry(exc=RuntimeError("adapter raise_dispute returned False"))
    return {"dispute_id": dispute_id, "outcome": outcome}


# ─── metrics aggregation ─────────────────────────────────────


async def _aggregate_metrics_async() -> int:
    """Aggregate the previous hour's attempts into CascadeProviderMetric."""
    from app.common.types import utcnow

    now = utcnow()
    bucket_at = _start_of_hour_utc(now - timedelta(hours=1))
    bucket_end = bucket_at + timedelta(hours=1)
    written = 0

    async with SessionLocal() as session:
        async with session.begin():
            providers_result = await session.execute(
                select(CascadeProvider.id)
            )
            provider_ids = [row[0] for row in providers_result.all()]

            for provider_id in provider_ids:
                rows = await session.execute(
                    select(CascadeOrderAttempt).where(
                        CascadeOrderAttempt.provider_id == provider_id,
                        CascadeOrderAttempt.started_at >= bucket_at,
                        CascadeOrderAttempt.started_at < bucket_end,
                    )
                )
                attempts = list(rows.scalars().all())
                if not attempts:
                    continue

                request_count = len(attempts)
                success_count = sum(
                    1 for a in attempts if a.status == CascadeAttemptStatus.WON
                )
                failure_count = sum(
                    1 for a in attempts if a.status == CascadeAttemptStatus.ERROR
                )
                timeout_count = sum(
                    1 for a in attempts if a.status == CascadeAttemptStatus.TIMEOUT
                )
                cancel_count = sum(
                    1 for a in attempts if a.status == CascadeAttemptStatus.CANCELLED
                )
                total_latency_ms = sum(int(a.latency_ms or 0) for a in attempts)
                volume = sum(
                    (Decimal(a.requisite_snapshot.get("amount_fiat", 0))
                     if a.requisite_snapshot else Decimal("0"))
                    for a in attempts
                    if a.status == CascadeAttemptStatus.WON
                )
                profit = sum(
                    (Decimal(a.our_profit_usdt or 0) for a in attempts),
                    Decimal("0"),
                )

                await CascadeProviderMetricRepository(session).upsert_bucket(
                    provider_id=provider_id,
                    bucket_at=bucket_at,
                    request_count=request_count,
                    success_count=success_count,
                    failure_count=failure_count,
                    timeout_count=timeout_count,
                    cancel_count=cancel_count,
                    total_latency_ms=total_latency_ms,
                    total_volume_usdt=volume,
                    total_profit_usdt=profit,
                )
                written += 1

    return written


@celery_app.task(name="aggregate_cascade_metrics_task")
def aggregate_cascade_metrics_task():
    try:
        written = asyncio.run(_aggregate_metrics_async())
        if written:
            logger.info("cascade_metrics_aggregated", extra={"buckets": written})
        return {"buckets": written}
    except Exception as exc:
        logger.error("cascade_metrics_aggregation_failed", extra={"error": str(exc)})
        raise


# ─── in-flight polling (defensive) ───────────────────────────


async def _poll_in_flight_async(stale_seconds: int = 120) -> int:
    """Look at attempts stuck in IN_FLIGHT longer than ``stale_seconds`` and
    ask the provider for status if the adapter implements ``poll_status``.
    """
    polled = 0
    async with SessionLocal() as session:
        attempts = await CascadeOrderAttemptRepository(session).list_in_flight(
            older_than_seconds=stale_seconds,
        )
        if not attempts:
            return 0

        from app.modules.cascading.service import CascadingService

        service = CascadingService(session)

        for attempt in attempts:
            if not attempt.external_order_id:
                # Never received a response in the first place — flip to ERROR
                # so it doesn't sit in IN_FLIGHT forever.
                attempt.status = CascadeAttemptStatus.ERROR
                attempt.error_code = "stuck_in_flight"
                session.add(attempt)
                continue

            provider = await session.get(CascadeProvider, attempt.provider_id)
            if not provider:
                continue
            adapter = registry.get(provider.adapter_type)
            try:
                with provider_request_type("check"):
                    parsed = await adapter.poll_status(
                        provider=provider,
                        external_order_id=attempt.external_order_id,
                        timeout_ms=provider.request_timeout_ms,
                    )
            except Exception as exc:
                logger.warning(
                    "cascade_poll_failed",
                    extra={"provider_id": provider.id, "error": str(exc)},
                )
                continue
            if parsed:
                await service.apply_callback(provider=provider, parsed=parsed)
                polled += 1

        await session.commit()
    return polled


@celery_app.task(name="poll_cascade_in_flight_task")
def poll_cascade_in_flight_task():
    try:
        polled = asyncio.run(_poll_in_flight_async())
        if polled:
            logger.info("cascade_polled_in_flight", extra={"count": polled})
        return {"polled": polled}
    except Exception as exc:
        logger.warning("cascade_poll_loop_failed", extra={"error": str(exc)})
