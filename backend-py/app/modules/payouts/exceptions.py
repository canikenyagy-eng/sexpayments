"""Payout domain exceptions — thin wrappers over core AppException types."""
from app.core.exceptions import ConflictException, NotFoundException, ValidationException


class PayoutNotFound(NotFoundException):
    pass


class PayoutConflict(ConflictException):
    """Invalid state transition (e.g. claiming an already-claimed payout)."""
    pass


class PayoutValidation(ValidationException):
    pass
