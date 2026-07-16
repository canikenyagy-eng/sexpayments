"""Domain exceptions for the долив feature.

Thin subclasses of the shared ``AppException`` hierarchy so the global error
handler maps them to the right HTTP status with a stable ``code``.
"""
from app.core.exceptions import ConflictException, ForbiddenException, NotFoundException


class DolivNotFound(NotFoundException):
    """No долив with this uuid (or the row isn't a долив). Maps to HTTP 404."""


class DolivConflict(ConflictException):
    """The долив is no longer in the expected state (claimed by someone, already
    executed/canceled, or busy under a concurrent lock). Maps to HTTP 400."""


class DolivForbidden(ForbiddenException):
    """The caller may not act on this долив (not the requester / not the claimer /
    not an authorised доливщик). Maps to HTTP 403."""
