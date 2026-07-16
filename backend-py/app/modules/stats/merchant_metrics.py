"""Pure, DB-free helpers for the admin «Мерчанты» stats tab.

Kept free of any DB/ORM/ClickHouse dependency so the bucket boundaries and the
funnel-ratio maths are unit-testable in isolation and are the single source of
truth shared by the repository (which builds the SQL ``FILTER`` columns from
:func:`check_size_bounds`) and the service (which assembles the response rows).
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

# Check-size buckets over the RUB order amount, LEFT-INCLUSIVE / RIGHT-EXCLUSIVE.
# These are the 12 boundaries; with the open [0, first) head and the open
# [last, +inf) tail they form 13 buckets. Editing this tuple reshapes both the
# SQL aggregate and the response in lock-step.
CHECK_SIZE_EDGES: Tuple[int, ...] = (
    1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000, 15000, 20000,
)


def check_size_bounds() -> List[Tuple[int, Optional[int]]]:
    """``(lo, hi)`` for each bucket, left-inclusive / right-exclusive.

    ``lo`` is always concrete; ``hi is None`` marks the open-ended top bucket
    (``20000+``). Length is ``len(CHECK_SIZE_EDGES) + 1`` (= 13).
    """
    lowers = (0,) + CHECK_SIZE_EDGES
    uppers = CHECK_SIZE_EDGES + (None,)
    return list(zip(lowers, uppers))


def check_size_labels() -> List[str]:
    """Human labels aligned 1:1 with :func:`check_size_bounds` — ``<1000``,
    ``1000–2000`` … ``20000+``."""
    labels: List[str] = []
    for lo, hi in check_size_bounds():
        if lo == 0:
            labels.append(f"<{CHECK_SIZE_EDGES[0]}")
        elif hi is None:
            labels.append(f"{lo}+")
        else:
            labels.append(f"{lo}–{hi}")
    return labels


def _pct(numerator: float, denominator: float) -> float:
    """Percentage, guarding division by zero (→ ``0.0``), rounded to 2 dp."""
    if not denominator:
        return 0.0
    return round(numerator / denominator * 100, 2)


def build_merchant_row(
    *,
    merchant_id: int,
    merchant_login: Optional[str],
    requests: int,
    orders_created: int,
    orders_success: int,
    buckets: Sequence[float],
) -> dict:
    """Assemble one per-merchant table row from the raw counters.

    * ``conversion_pct`` = успешные / созданные (success over created)
    * ``payout_pct``     = созданные / запросы  (created over requests — «Выдача»)
    ``check_size_buckets`` is coerced to floats, aligned with
    :func:`check_size_bounds` (RUB volume of successful orders per range).
    """
    return {
        "merchant_id": int(merchant_id),
        "merchant_login": merchant_login,
        "requests": int(requests),
        "orders_created": int(orders_created),
        "orders_success": int(orders_success),
        "conversion_pct": _pct(orders_success, orders_created),
        "payout_pct": _pct(orders_created, requests),
        "check_size_buckets": [float(b or 0) for b in buckets],
    }
