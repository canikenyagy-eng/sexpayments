from enum import Enum


class PayoutStatus(str, Enum):
    """Lifecycle of a merchant payout (pool → claim → execute → settle).

    DB stores the member NAME (uppercase) — same convention as OrderStatus —
    while the API serializes the lowercase ``.value``.
    """
    CREATED = "created"                # in the shared pool, unclaimed
    CLAIMED = "claimed"                # taken by a trader (exclusive lock)
    AWAITING_CHECK = "awaiting_check"  # receipt uploaded, pending admin verification
    COMPLETED = "completed"            # settled — merchant debited, trader credited
    CANCELED = "canceled"              # merchant/admin canceled → merchant refunded
    EXPIRED = "expired"                # TTL reached → merchant refunded


class PayoutReceiptStatus(str, Enum):
    """Per-receipt moderation state for a partial payout payment.

    A payout closes once the APPROVED receipts' amounts sum to the full payout
    amount (within the terminal's ``receipts_to_close`` cap). DB stores the
    member NAME (uppercase); API serializes the lowercase ``.value``.
    """
    PENDING = "pending"      # uploaded, awaiting admin verification
    APPROVED = "approved"    # counts toward closing the payout
    REJECTED = "rejected"    # voided — trader must re-upload that installment


# Claimable (visible in the pool).
PAYOUT_POOL_STATUSES = frozenset({PayoutStatus.CREATED})
# Non-terminal — merchant funds still frozen.
PAYOUT_ACTIVE_STATUSES = frozenset({
    PayoutStatus.CREATED, PayoutStatus.CLAIMED, PayoutStatus.AWAITING_CHECK,
})
PAYOUT_TERMINAL_STATUSES = frozenset({
    PayoutStatus.COMPLETED, PayoutStatus.CANCELED, PayoutStatus.EXPIRED,
})
