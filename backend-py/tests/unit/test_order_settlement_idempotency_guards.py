"""
Unit-level adversarial guards for the order settlement state machine that the
existing ``tests/unit/test_order_service.py`` does NOT cover.

That file already pins (with mocks):
  * complete_order is idempotent when the order is already SUCCESS,
  * change_status reads the locked COMMITTED status (not the caller snapshot)
    and no-ops on a same-status move,
  * complete_order loads the order FOR UPDATE before settling.

UNCOVERED (added here) — the ``_ALLOWED_TRANSITIONS`` legality guard inside
``change_status``: a graph-illegal move must raise ``ConflictException`` BEFORE
any money funnel runs, and the same-status short-circuit must fire even for a
terminal status without consulting the graph. These are pure
transition-legality assertions — no real ledger needed, so they live as fast
mock unit tests. (``force=True`` bypassing the graph is proven end-to-end in
the integration suite, which exercises the real funnel.)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.common.enums.orders import OrderStatus
from app.core.exceptions import ConflictException
from app.modules.orders.models import Order
from app.modules.orders.service import OrderService


@pytest.fixture
def service():
    """OrderService with a mocked repository. ``lock_status`` returns None so
    change_status falls back to the in-memory ``order.status`` the test sets."""
    session = MagicMock()
    svc = OrderService(session)
    svc.repository = AsyncMock()
    svc.repository.lock_status = AsyncMock(return_value=None)
    svc.audit_log = AsyncMock()
    return svc


def _order(status: OrderStatus) -> Order:
    order = MagicMock(spec=Order)
    order.id = 42
    order.status = status
    return order


# ── the transition-legality guard (uncovered by the existing unit suite) ──


@pytest.mark.asyncio
@pytest.mark.parametrize("old_status,new_status", [
    # Terminal → terminal without a dispute/force is illegal.
    (OrderStatus.SUCCESS, OrderStatus.CANCELED),
    (OrderStatus.SUCCESS, OrderStatus.FAILED),
    (OrderStatus.SUCCESS, OrderStatus.PENDING),
    (OrderStatus.CANCELED, OrderStatus.SUCCESS),
    (OrderStatus.FAILED, OrderStatus.SUCCESS),
    # Backwards / nonsensical active moves.
    (OrderStatus.PENDING, OrderStatus.CREATED),
    (OrderStatus.RECEIPT_UPLOADED, OrderStatus.PENDING),
    # CREATED can only go to PENDING/FAILED/CANCELED — not straight to SUCCESS.
    (OrderStatus.CREATED, OrderStatus.SUCCESS),
])
async def test_change_status_rejects_illegal_transition(service, old_status, new_status):
    """An out-of-graph transition raises ConflictException and NEVER reaches the
    money funnel (no repository.update / no nested tx)."""
    order = _order(old_status)

    with pytest.raises(ConflictException, match="Illegal order transition"):
        await service.change_status(order, new_status, audit_action=None)

    # Guard fired before any write — the money funnel never ran.
    service.repository.update.assert_not_called()


@pytest.mark.asyncio
async def test_change_status_same_status_short_circuits_before_legality_check(service):
    """A same-status move returns immediately — it must not even consult the
    transition graph (so an order already in a terminal state can be re-confirmed
    to the SAME terminal status without a spurious ConflictException)."""
    order = _order(OrderStatus.CANCELED)

    out = await service.change_status(order, OrderStatus.CANCELED, audit_action=None)

    assert out is order
    service.repository.update.assert_not_called()
