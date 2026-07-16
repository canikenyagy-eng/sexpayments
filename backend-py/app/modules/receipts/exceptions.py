"""Domain exceptions for the receipts module.

Thin aliases over the shared core exceptions so call sites read intent-fully
and the centralized error handler maps them to the right HTTP status.
"""
from app.core.exceptions import ConflictException, NotFoundException, ValidationException


class DuplicateReceiptError(ConflictException):
    """The same file (by sha256) was already uploaded for this order."""


class ReceiptLimitReachedError(ValidationException):
    """The per-order receipt cap has been reached."""


class ModerationAlreadyDecidedError(ConflictException):
    """The moderation cycle for this order is already final.

    Raised when an admin clicks a button on a card whose order has already
    been moved out of ``ModerationStatus.PENDING`` by another admin / a
    duplicate callback. Maps to HTTP 400 with code ``conflict``.
    """

    def __init__(self, message: str = "Order moderation is no longer pending"):
        super().__init__(message=message)


class ModerationRowNotFoundError(NotFoundException):
    """No moderation row found for the order.

    Either the premoderation flag was off when the receipt was uploaded
    (no row was ever created) or the order itself is missing. Maps to
    HTTP 404 so the bot can drop the keyboard with «Ордер не найден».
    """

    def __init__(self, order_id: int):
        super().__init__(message=f"No moderation row for order {order_id}")
