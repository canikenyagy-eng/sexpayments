"""The merchant write-gate: only ENABLED/TEST merchants may create orders.

`get_active_merchant` is used on creation endpoints; reads keep using
`get_current_merchant` (variant B — disabled merchants stay read-only).
"""
from unittest.mock import MagicMock

import pytest

from app.api.merchant.dependencies import get_active_merchant
from app.common.enums.merchants import TerminalStatus
from app.core.exceptions import ForbiddenException
from app.modules.merchants.permissions import (
    ACTIVE_MERCHANT_STATUSES,
    is_merchant_active,
)

_ACTIVE = [TerminalStatus.ENABLED, TerminalStatus.TEST]
_INACTIVE = [
    TerminalStatus.PENDING,
    TerminalStatus.DISABLED,
    TerminalStatus.BLOCKED,
    TerminalStatus.ARCHIVED,
]


def test_active_statuses_are_exactly_enabled_and_test():
    assert set(ACTIVE_MERCHANT_STATUSES) == {TerminalStatus.ENABLED, TerminalStatus.TEST}


@pytest.mark.parametrize("status", _ACTIVE)
def test_is_merchant_active_true(status):
    assert is_merchant_active(status) is True


@pytest.mark.parametrize("status", _INACTIVE)
def test_is_merchant_active_false(status):
    assert is_merchant_active(status) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _ACTIVE)
async def test_get_active_merchant_allows_active(status):
    m = MagicMock(status=status)
    assert await get_active_merchant(merchant=m) is m


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _INACTIVE)
async def test_get_active_merchant_rejects_inactive(status):
    m = MagicMock(status=status)
    with pytest.raises(ForbiddenException) as exc:
        await get_active_merchant(merchant=m)
    assert exc.value.status_code == 403
