"""Access policies for merchant operations.

Write / creation endpoints (e.g. creating a payin order) must check that the
merchant is in an *active* state. Reads stay open — a disabled or blocked
merchant can still inspect its existing orders/withdrawals (variant B).
"""
from app.common.enums.merchants import TerminalStatus

ACTIVE_MERCHANT_STATUSES: tuple[TerminalStatus, ...] = (
    TerminalStatus.ENABLED,
    TerminalStatus.TEST,
)


def is_merchant_active(status: TerminalStatus) -> bool:
    """True iff a merchant in this status may create new orders / operations."""
    return status in ACTIVE_MERCHANT_STATUSES
