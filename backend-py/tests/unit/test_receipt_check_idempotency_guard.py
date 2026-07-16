"""Unit tests for the receipt-check IntegrityError guard — adversarial branches
NOT covered by tests/unit/test_receipt_check_service.py.

The #18 fix wraps the PENDING-check create in:

    try:
        async with self.session.begin_nested():
            check = await self.checks.create({... status=PENDING ...})
    except IntegrityError:
        existing = await self.checks.find_active_for_file(order.id, file_sha)
        if existing is not None:
            return existing          # ← replay the concurrent winner (covered)
        raise                        # ← re-raise on a NON-recoverable conflict (UNCOVERED)

The existing suite covers the `existing is not None → return existing` happy
replay. This file pins the adversarial complement: a conflict that is NOT a
recoverable live-check duplicate (find_active_for_file → None) must RE-RAISE —
the service must never swallow an unexpected DB error and silently proceed
(which would risk charging/returning against a non-existent claim). It also
re-asserts that the recoverable replay performs ZERO charge.

These mock the session + repos (no DB); the integration counterpart that drives
the REAL partial index + ledger lives in
tests/integration/test_receipt_check_idempotency.py.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.common.enums.receipt_checks import ReceiptCheckStatus, ReceiptCheckTrigger
from app.common.enums.users import UserRole
# Register mappers so Order.relationship back-refs init in isolation (same
# pattern as test_receipt_check_service.py / test_finance_service.py).
from app.modules.requisites.models import Requisite  # noqa: F401
from app.modules.payments.models import PaymentOption  # noqa: F401
from app.modules.orders.models import Order
from app.modules.receipt_checks.models import ReceiptCheckProvider
from app.modules.receipt_checks.service import ReceiptCheckService
from app.modules.users.models import User


class _ACM:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        # Mirror a real savepoint: do not suppress the in-block exception.
        return False


@pytest.fixture
def mock_session():
    s = MagicMock()
    s.begin.return_value = _ACM()
    s.begin_nested.return_value = _ACM()
    s.flush = AsyncMock()
    s.refresh = AsyncMock()
    s.execute = AsyncMock()
    return s


@pytest.fixture
def service(mock_session):
    svc = ReceiptCheckService(mock_session)
    svc.providers = AsyncMock()
    svc.checks = AsyncMock()
    svc.audit_log = AsyncMock()
    return svc


@pytest.fixture
def pdf_file(tmp_path):
    p = tmp_path / "receipt.pdf"
    p.write_bytes(b"%PDF-1.4\n%bytes\n")
    return str(p)


@pytest.fixture
def order(pdf_file):
    o = MagicMock(spec=Order)
    o.id = 42
    o.receipt_file = pdf_file
    return o


@pytest.fixture
def trader_user():
    u = MagicMock(spec=User)
    u.id = 7
    u.role = UserRole.TRADER
    return u


@pytest.fixture
def active_provider():
    p = MagicMock(spec=ReceiptCheckProvider)
    p.id = 1
    p.adapter_type = "trexo"
    p.is_active = True
    p.price_usdt = Decimal("0.50")
    return p


@pytest.mark.asyncio
async def test_non_recoverable_integrity_error_is_reraised_not_swallowed(
    service, order, trader_user, active_provider
):
    """IntegrityError on the claim INSERT, but find_active_for_file returns None
    (the conflict is NOT a recoverable live-check duplicate — e.g. an FK / other
    constraint). The service MUST re-raise and MUST NOT charge the trader."""
    service.providers.list_active = AsyncMock(return_value=[active_provider])
    service.checks.find_reusable_for_file = AsyncMock(return_value=None)
    service.checks.create = AsyncMock(
        side_effect=IntegrityError("INSERT", {}, Exception("fk violation"))
    )
    service.checks.find_active_for_file = AsyncMock(return_value=None)  # nothing to replay
    service._charge_trader = AsyncMock()

    with pytest.raises(IntegrityError):
        await service.run_check_for_order(order, trader_user, ReceiptCheckTrigger.MANUAL)

    service.checks.find_active_for_file.assert_awaited_once()
    service._charge_trader.assert_not_called()  # never charged on a failed claim


@pytest.mark.asyncio
async def test_recoverable_conflict_replays_a_success_winner_without_charge(
    service, order, trader_user, active_provider
):
    """Complement to the existing PENDING-winner test: the replayed winner may
    already be FINISHED (SUCCESS). The loser returns it verbatim and is NEVER
    charged."""
    from tests.unit.test_receipt_check_service import _make_check_row  # reuse builder

    service.providers.list_active = AsyncMock(return_value=[active_provider])
    service.checks.find_reusable_for_file = AsyncMock(return_value=None)
    service.checks.create = AsyncMock(
        side_effect=IntegrityError("INSERT", {}, Exception("dup"))
    )
    winner = _make_check_row(id=777, status=ReceiptCheckStatus.SUCCESS, charged=True)
    service.checks.find_active_for_file = AsyncMock(return_value=winner)
    service._charge_trader = AsyncMock()

    result = await service.run_check_for_order(order, trader_user, ReceiptCheckTrigger.MANUAL)

    assert result is winner
    assert result.status == ReceiptCheckStatus.SUCCESS
    service._charge_trader.assert_not_called()
