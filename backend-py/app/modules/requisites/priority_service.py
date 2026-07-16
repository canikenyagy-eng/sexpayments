"""Requisite priority redistribution + recompute.

Each requisite has a computed ``priority_score`` used by the WEIGHTED pooling
strategy. The trader's priority budget is conserved and redistributed by weight
per (trader, currency, method) group of his POOLABLE requisites:

    base    = max(0, 100 × (1 + priority_bonus_percent/100))   # admin lever
    score_i = trader_priority_i × (N × base) / Σ(trader_priority)

Off the order hot path: recompute runs on requisite CRUD / enable-disable and on
the admin % change, in the same transaction as the triggering write.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.cascading import RequisiteSource
from app.common.enums.requisites import RequisiteStatus
from app.modules.requisites.models import Requisite
from app.modules.traders.models import Trader

_BASE = Decimal("100")
_QUANT = Decimal("0.0001")


class PriorityService:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def compute_scores(weights: List[Tuple[int, int]], base: Decimal) -> Dict[int, Decimal]:
        """Pure redistribution. ``weights`` = list of (requisite_id, trader_priority).
        Returns {requisite_id: score}. Empty / non-positive Σ → {}."""
        n = len(weights)
        total = sum(w for _, w in weights)
        if n == 0 or total <= 0:
            return {}
        per_unit = (Decimal(n) * base) / Decimal(total)
        return {rid: (Decimal(w) * per_unit).quantize(_QUANT) for rid, w in weights}

    @staticmethod
    def base_for_percent(percent: Decimal) -> Decimal:
        base = _BASE * (Decimal(1) + Decimal(percent) / Decimal(100))
        return base if base > 0 else Decimal(0)

    async def _base_for_trader(self, trader_id: int) -> Decimal:
        pct = (await self.session.execute(
            select(Trader.priority_bonus_percent).where(Trader.user_id == trader_id)
        )).scalar_one_or_none()
        return self.base_for_percent(Decimal(pct) if pct is not None else Decimal(0))

    def _poolable(self, trader_id: int):
        return (
            Requisite.trader_id == trader_id,
            Requisite.source == RequisiteSource.LOCAL,
            Requisite.is_active.is_(True),
            Requisite.is_archived.is_(False),
            Requisite.status == RequisiteStatus.ENABLED,
        )

    async def recompute_group(self, trader_id: int, currency, method) -> None:
        base = await self._base_for_trader(trader_id)
        # Lock the group's rows: two config writes touching the same
        # (trader, currency, method) group serialize here, so each recompute reads
        # the other's committed trader_priority/% before writing scores (no
        # lost-update skew). The admin-% path recomputes under the same lock, so
        # locking these rows covers the %-vs-weight race too. FOR UPDATE is a no-op
        # on SQLite (tests). Off the order hot path.
        reqs = (await self.session.execute(
            select(Requisite).where(
                *self._poolable(trader_id),
                Requisite.currency == currency,
                Requisite.payment_method == method,
            ).with_for_update()
        )).scalars().all()
        scores = self.compute_scores([(r.id, int(r.trader_priority)) for r in reqs], base)
        for r in reqs:
            if r.id in scores:
                r.priority_score = scores[r.id]
                self.session.add(r)

    async def recompute_all_for_trader(self, trader_id: int) -> None:
        groups = (await self.session.execute(
            select(Requisite.currency, Requisite.payment_method)
            .where(*self._poolable(trader_id)).distinct()
        )).all()
        for currency, method in groups:
            await self.recompute_group(trader_id, currency, method)
