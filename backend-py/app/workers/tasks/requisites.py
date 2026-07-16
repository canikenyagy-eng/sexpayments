"""Periodic maintenance tasks for requisite limits.

Currently exposes ``reset_requisite_limits_task`` — a celery beat job that
zeroes ``current_daily_turnover`` and/or ``current_monthly_turnover`` on rows
where ``reset_enabled = TRUE`` and a period boundary has passed since
``last_reset_at``.

Design:
  * Daily boundary  = 00:00 UTC of today.
  * Monthly boundary = 00:00 UTC of the 1st of the current month.
  * Daily resets always fire when the boundary is crossed.
  * Monthly resets additionally zero ``current_monthly_turnover`` once per
    month (1st-of-month rollover).
  * Both runs share a single ``last_reset_at`` timestamp because the daily
    rollover always coincides with (or precedes) the monthly one.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.common.types import utcnow
from app.infrastructure.db.session import WorkerSessionLocal as async_session_maker
from app.modules.requisites.models import RequisiteLimit
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _start_of_day_utc(dt: datetime) -> datetime:
    """Return the UTC midnight that begins the day of ``dt``."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _start_of_month_utc(dt: datetime) -> datetime:
    """Return UTC midnight of the 1st-of-month for ``dt``."""
    sod = _start_of_day_utc(dt)
    return sod.replace(day=1)


async def _reset_requisite_limits_async() -> tuple[int, int]:
    """Zero turnover counters where the boundary has passed since the last
    reset. Returns ``(daily_reset_count, monthly_reset_count)`` for logging.
    """
    now = utcnow()
    today_start = _start_of_day_utc(now)
    month_start = _start_of_month_utc(now)

    async with async_session_maker() as session:
        async with session.begin():
            base = select(RequisiteLimit.id).where(
                RequisiteLimit.reset_enabled.is_(True),
            )

            # Daily rollover — boundary crossed since last reset.
            daily_stmt = base.where(
                (RequisiteLimit.last_reset_at.is_(None))
                | (RequisiteLimit.last_reset_at < today_start)
            )
            daily_ids = [row[0] for row in (await session.execute(daily_stmt)).all()]

            # Monthly rollover — additionally zero monthly counter at 1st.
            monthly_stmt = base.where(
                (RequisiteLimit.last_reset_at.is_(None))
                | (RequisiteLimit.last_reset_at < month_start)
            )
            monthly_ids = [row[0] for row in (await session.execute(monthly_stmt)).all()]

            if monthly_ids:
                # Run the monthly reset first because the daily reset will
                # bump last_reset_at to "now" and we'd otherwise skip these
                # rows in the monthly query if they were merged.
                await session.execute(
                    update(RequisiteLimit)
                    .where(RequisiteLimit.id.in_(monthly_ids))
                    .values(current_monthly_turnover=0)
                )

            if daily_ids:
                await session.execute(
                    update(RequisiteLimit)
                    .where(RequisiteLimit.id.in_(daily_ids))
                    .values(
                        current_daily_turnover=0,
                        last_reset_at=now,
                    )
                )

            return len(daily_ids), len(monthly_ids)


@celery_app.task(name="reset_requisite_limits_task")
def reset_requisite_limits_task():
    daily, monthly = asyncio.run(_reset_requisite_limits_async())
    if daily or monthly:
        logger.info(
            "Reset requisite limits: daily=%s, monthly=%s",
            daily,
            monthly,
        )
    return {"daily": daily, "monthly": monthly}
