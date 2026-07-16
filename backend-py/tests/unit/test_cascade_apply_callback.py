"""Integration tests for the cascade callback pipeline.

Covers the two CascadingService methods that ingest a provider's webhook:

  parse_provider_callback(headers, body)
    → looks up the provider by ``code``, delegates to the adapter's
      parse_callback(), translates CallbackVerificationError into
      UnauthorizedException, raises NotFoundException for unknown
      providers. Inactive providers are STILL accepted — in-flight deals
      must settle after a provider is deactivated.

  apply_callback(provider, parsed)
    → looks up the cascade attempt by external_order_id and, on a
      terminal SUCCESS only, settles the order via complete_order.
      Non-success provider statuses (failed / canceled / paid /
      intermediate) are acknowledged but NOT applied — cancellation
      and expiry are driven by our own TTL timer, never the provider.
      Must be idempotent — replays on an already-terminal order must
      not re-trigger settlement.

The full flow `merchant → cascade → provider → callback → closure`
is otherwise tested piecewise (see test_cascading_service.py for
selection, test_provider_adapter_*.py for adapter parse_callback);
this file closes the gap at the apply step.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, patch

import pytest

from app.common.enums.cascading import (
    CascadeAttemptStatus,
    CascadeMode,
    ProviderStatus,
    RequisiteSource,
)
from app.common.enums.finances import Currency
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.requisites import RequisiteStatus
from app.common.types import utcnow
from app.core.exceptions import NotFoundException, UnauthorizedException
from app.modules.cascading.integrations import registry as adapter_registry
from app.modules.cascading.integrations.base import (
    CallbackVerificationError,
    ParsedCallback,
)
from app.modules.cascading.integrations.mock import MockProviderAdapter
from app.modules.cascading.models import (
    CascadeOrderAttempt,
    CascadeProvider,
)
from app.modules.cascading.service import CascadingService
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.orders.service import OrderService
from app.modules.requisites.models import Requisite
from app.modules.users.models import User


# ─── Scripted adapter that returns whatever ParsedCallback we hand it ─────

class _CallbackAdapter(MockProviderAdapter):
    """Adapter where parse_callback returns the queued ParsedCallback or
    raises the queued exception. Lets each test drive apply_callback
    without crafting signed webhook bodies for every provider scheme.
    """

    code = "cb_scripted"

    def __init__(self):
        super().__init__()
        self.next_result: Optional[ParsedCallback] = None
        self.next_error: Optional[Exception] = None

    def parse_callback(self, *, provider, headers, body):
        if self.next_error is not None:
            err = self.next_error
            self.next_error = None
            raise err
        assert self.next_result is not None, "test must queue a ParsedCallback"
        result = self.next_result
        self.next_result = None
        return result


@pytest.fixture
def cb_adapter():
    adapter = _CallbackAdapter()
    adapter_registry._REGISTRY["cb_scripted"] = adapter
    yield adapter
    adapter_registry._REGISTRY.pop("cb_scripted", None)


# ─── DB fixture helpers ────────────────────────────────────────────────

async def _mk_user(session, username: str, role) -> User:
    user = User(
        username=username,
        password="x",
        role=role,
        is_system=False,
        is_blocked=False,
    )
    session.add(user)
    await session.flush()
    return user


async def _mk_provider(session, code: str = "cb_p") -> CascadeProvider:
    from app.common.enums.traders import TraderStatus
    from app.common.enums.users import UserRole
    from app.modules.traders.models import Trader

    virtual_user = User(
        username=f"sys_{code}",
        password="x",
        role=UserRole.TRADER,
        is_system=True,
        is_blocked=False,
    )
    session.add(virtual_user)
    await session.flush()

    trader = Trader(
        user_id=virtual_user.id,
        status=TraderStatus.ENABLED,
        is_payin_active=True,
        is_payout_active=False,
        accept_all_merchants=False,
    )
    session.add(trader)
    await session.flush()

    provider = CascadeProvider(
        code=code,
        name=f"Provider {code}",
        adapter_type="cb_scripted",
        is_active=True,
        base_url="https://example.com",
        virtual_user_id=virtual_user.id,
        virtual_trader_id=trader.id,
        rates={"RUB": 95.0},
        fees={"sbp": 1.0},
        cb_window_seconds=300,
        cb_threshold_failures=999,
        cb_threshold_rate=1.0,
        cb_cooldown_seconds=60,
        request_timeout_ms=5000,
        cancel_timeout_ms=1000,
        priority_weight=100,
        settings={},
    )
    session.add(provider)
    await session.flush()
    return provider


async def _mk_merchant(session) -> Merchant:
    from app.common.enums.merchants import TerminalStatus
    from app.common.enums.users import UserRole

    owner = await _mk_user(session, "merchant_owner", UserRole.MERCHANT)
    merchant = Merchant(
        user_id=owner.id,
        name="CB Merchant",
        api_key="cb_test_key",
        api_secret="cb_test_secret",
        status=TerminalStatus.ENABLED,
        currency=Currency.RUB,
        fees={"sbp": 2.5},
        order_ttl_seconds=1800,
        requisite_search_timeout_ms=5000,
        cascade_mode=CascadeMode.POOLED,
    )
    session.add(merchant)
    await session.flush()
    return merchant


async def _mk_order_with_attempt(
    session,
    *,
    provider: CascadeProvider,
    merchant: Merchant,
    external_order_id: str = "EXT-1",
    order_status: OrderStatus = OrderStatus.PENDING,
) -> tuple[Order, CascadeOrderAttempt, Requisite]:
    """Fully wires up the chain the apply_callback path expects:

      Merchant ── Order ── Requisite (cascade-sourced, owned by provider's
                              virtual trader) ── CascadeOrderAttempt(WON,
                              external_order_id pointing at the row)
    """
    requisite = Requisite(
        trader_id=provider.virtual_user_id,
        nickname=f"cascade:{provider.code}",
        bank_name="Bank-cb",
        account_number="0000",
        account_holder="Holder",
        payment_method=PaymentMethod.SBP,
        status=RequisiteStatus.ENABLED,
        currency=Currency.RUB,
        is_active=True,
        is_archived=False,
        source=RequisiteSource.CASCADE,
    )
    session.add(requisite)
    await session.flush()

    order = Order(
        external_id=f"ext-{external_order_id}",
        merchant_id=merchant.id,
        trader_id=provider.virtual_user_id,
        requisite_id=requisite.id,
        direction=PaymentDirection.PAYIN,
        payment_method=PaymentMethod.SBP,
        amount=Decimal("1000"),
        currency=Currency.RUB,
        amount_usdt=Decimal("10.5263"),
        exchange_rate=Decimal("95.0"),
        fee_usdt=Decimal("0.2632"),
        status=order_status,
    )
    session.add(order)
    await session.flush()

    attempt = CascadeOrderAttempt(
        order_id=order.id,
        provider_id=provider.id,
        started_at=utcnow(),
        finished_at=utcnow(),
        status=CascadeAttemptStatus.WON,
        external_order_id=external_order_id,
        requisite_id=requisite.id,
        idempotency_key=f"idem-{external_order_id}",
    )
    session.add(attempt)
    await session.flush()
    return order, attempt, requisite


def _parsed(status: ProviderStatus, external_id: str = "EXT-1") -> ParsedCallback:
    return ParsedCallback(
        external_order_id=external_id,
        status=status,
        raw={"event": "test"},
    )


# ─── parse_provider_callback ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_provider_callback_unknown_provider_raises_not_found(session):
    """A webhook hitting /callbacks/<bogus> must 404 — the FastAPI route
    relies on this so attackers can't probe valid provider codes via
    response shape differences."""
    service = CascadingService(session)
    with pytest.raises(NotFoundException):
        await service.parse_provider_callback(
            provider_code="nonexistent", headers={}, body=b"{}"
        )


@pytest.mark.asyncio
async def test_parse_provider_callback_inactive_provider_still_accepted(
    session, cb_adapter
):
    """Deactivating a provider stops NEW deals (selection is gated in
    _eligible_providers) but MUST NOT reject its callbacks — deals already in
    flight have to settle. Prod incident: swifty_1 was disabled, sent an
    ACCEPTED callback, and we 404'd "provider not found" so the order never
    closed. The signature is still verified by the adapter, and apply_callback
    only touches orders with a matching attempt, so this is safe."""
    provider = await _mk_provider(session)
    provider.is_active = False
    await session.flush()
    cb_adapter.next_result = _parsed(ProviderStatus.SUCCESS)

    service = CascadingService(session)
    got_provider, parsed = await service.parse_provider_callback(
        provider_code=provider.code, headers={}, body=b"{}"
    )
    assert got_provider.id == provider.id
    assert parsed.status == ProviderStatus.SUCCESS


@pytest.mark.asyncio
async def test_inactive_provider_callback_completes_inflight_order(
    session, cb_adapter
):
    """End-to-end regression for the prod incident: a provider is deactivated
    while one of its deals is still in flight; the provider's terminal SUCCESS
    callback must still complete OUR order (before the fix, parse_* 404'd and
    the deal hung)."""
    provider = await _mk_provider(session)
    merchant = await _mk_merchant(session)
    order, _, _ = await _mk_order_with_attempt(
        session, provider=provider, merchant=merchant,
        external_order_id="INACT-1", order_status=OrderStatus.PENDING,
    )
    provider.is_active = False
    await session.flush()

    service = CascadingService(session)
    cb_adapter.next_result = _parsed(ProviderStatus.SUCCESS, "INACT-1")
    got_provider, parsed = await service.parse_provider_callback(
        provider_code=provider.code, headers={}, body=b"{}"
    )
    with patch.object(
        OrderService, "complete_order", new=AsyncMock(return_value=order)
    ) as complete_mock:
        result = await service.apply_callback(provider=got_provider, parsed=parsed)
    assert result is not None
    assert result.id == order.id
    complete_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_parse_provider_callback_bad_signature_raises_unauthorized(
    session, cb_adapter
):
    """Adapter-level CallbackVerificationError surfaces as HTTP 401, not
    500 — the global error handler will turn that into a clean Unauthorized
    response to the provider's webhook retry pipeline."""
    provider = await _mk_provider(session)
    cb_adapter.next_error = CallbackVerificationError("bad signature")

    service = CascadingService(session)
    with pytest.raises(UnauthorizedException):
        await service.parse_provider_callback(
            provider_code=provider.code, headers={}, body=b"{}"
        )


@pytest.mark.asyncio
async def test_parse_provider_callback_returns_provider_and_parsed(
    session, cb_adapter
):
    provider = await _mk_provider(session)
    cb_adapter.next_result = _parsed(ProviderStatus.SUCCESS)

    service = CascadingService(session)
    got_provider, parsed = await service.parse_provider_callback(
        provider_code=provider.code, headers={}, body=b"{}"
    )
    assert got_provider.id == provider.id
    assert parsed.status == ProviderStatus.SUCCESS


# ─── apply_callback — lookup paths ──────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_callback_unknown_external_id_returns_none(session, cb_adapter):
    """A webhook referencing an external_order_id we never persisted (replay
    after manual cleanup, provider mistake) must NOT touch any order. The
    service answers None so the endpoint can return 200-with-no-op rather
    than 4xx and provoke endless retries from the provider."""
    provider = await _mk_provider(session)
    parsed = _parsed(ProviderStatus.SUCCESS, external_id="NEVER-SEEN")

    service = CascadingService(session)
    result = await service.apply_callback(provider=provider, parsed=parsed)
    assert result is None


@pytest.mark.asyncio
async def test_apply_callback_attempt_without_order_returns_none(
    session, cb_adapter
):
    """Edge case: attempt was persisted before OrderService stamped order_id
    (rare race during cascade-on-create). Should be a no-op."""
    provider = await _mk_provider(session)
    attempt = CascadeOrderAttempt(
        order_id=None,
        provider_id=provider.id,
        started_at=utcnow(),
        status=CascadeAttemptStatus.WON,
        external_order_id="ORPHAN-1",
        idempotency_key="idem-orphan-1",
    )
    session.add(attempt)
    await session.flush()

    service = CascadingService(session)
    result = await service.apply_callback(
        provider=provider, parsed=_parsed(ProviderStatus.SUCCESS, "ORPHAN-1")
    )
    assert result is None


# ─── apply_callback — state transitions ─────────────────────────────────


@pytest.mark.asyncio
async def test_apply_callback_success_completes_pending_order(session, cb_adapter):
    provider = await _mk_provider(session)
    merchant = await _mk_merchant(session)
    order, _, _ = await _mk_order_with_attempt(
        session, provider=provider, merchant=merchant,
        external_order_id="OK-1", order_status=OrderStatus.PENDING,
    )

    service = CascadingService(session)
    with patch.object(
        OrderService, "complete_order", new=AsyncMock(return_value=order)
    ) as complete_mock:
        result = await service.apply_callback(
            provider=provider, parsed=_parsed(ProviderStatus.SUCCESS, "OK-1")
        )
    assert result is not None
    assert result.id == order.id
    complete_mock.assert_awaited_once()
    # Called with the provider's virtual user as the "trader" so finance
    # service can release the escrow to the right wallet.
    args, kwargs = complete_mock.await_args
    called_trader = kwargs.get("trader") or args[0]
    assert called_trader.id == provider.virtual_user_id


@pytest.mark.asyncio
async def test_apply_callback_success_completes_receipt_uploaded_order(
    session, cb_adapter
):
    """When the merchant marked the receipt before the provider's terminal
    callback arrived, we still finalise on SUCCESS — the receipt state is
    intermediate."""
    provider = await _mk_provider(session)
    merchant = await _mk_merchant(session)
    order, _, _ = await _mk_order_with_attempt(
        session, provider=provider, merchant=merchant,
        external_order_id="RU-1",
        order_status=OrderStatus.RECEIPT_UPLOADED,
    )

    service = CascadingService(session)
    with patch.object(
        OrderService, "complete_order", new=AsyncMock(return_value=order)
    ) as complete_mock:
        result = await service.apply_callback(
            provider=provider, parsed=_parsed(ProviderStatus.SUCCESS, "RU-1")
        )
    assert result is not None
    complete_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_callback_success_is_idempotent_on_already_success(
    session, cb_adapter
):
    """Provider re-delivers the same `payin.confirmed` callback (their
    retry pipeline did its job). We must NOT call complete_order twice
    — that would double-debit escrow / double-pay the trader."""
    provider = await _mk_provider(session)
    merchant = await _mk_merchant(session)
    order, _, _ = await _mk_order_with_attempt(
        session, provider=provider, merchant=merchant,
        external_order_id="DUP-1", order_status=OrderStatus.SUCCESS,
    )

    service = CascadingService(session)
    with patch.object(
        OrderService, "complete_order", new=AsyncMock(return_value=order)
    ) as complete_mock:
        result = await service.apply_callback(
            provider=provider, parsed=_parsed(ProviderStatus.SUCCESS, "DUP-1")
        )
    assert result is not None
    complete_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_callback_failed_is_ignored(session, cb_adapter):
    """Success-only policy: a provider FAILED callback is acknowledged but NOT
    applied. We never let the provider fail (and release the escrow of) a
    still-valid order — cancellation/expiry is OURS, driven by the TTL timer
    (``expire_orders_task``). The order keeps its state; no fail_order."""
    provider = await _mk_provider(session)
    merchant = await _mk_merchant(session)
    order, _, _ = await _mk_order_with_attempt(
        session, provider=provider, merchant=merchant,
        external_order_id="F-1", order_status=OrderStatus.PENDING,
    )

    service = CascadingService(session)
    with patch.object(
        OrderService, "fail_order", new=AsyncMock(return_value=order)
    ) as fail_mock:
        result = await service.apply_callback(
            provider=provider, parsed=_parsed(ProviderStatus.FAILED, "F-1")
        )
    fail_mock.assert_not_awaited()
    assert result is not None
    await session.refresh(order)
    assert order.status == OrderStatus.PENDING


@pytest.mark.asyncio
async def test_apply_callback_canceled_is_ignored(session, cb_adapter):
    """Success-only policy: a provider CANCELED callback is ignored too — the
    provider can't cancel/release our order; only our TTL timer does. The order
    stays PENDING and no fail_order fires."""
    provider = await _mk_provider(session)
    merchant = await _mk_merchant(session)
    order, _, _ = await _mk_order_with_attempt(
        session, provider=provider, merchant=merchant,
        external_order_id="C-1", order_status=OrderStatus.PENDING,
    )

    service = CascadingService(session)
    with patch.object(
        OrderService, "fail_order", new=AsyncMock(return_value=order)
    ) as fail_mock:
        result = await service.apply_callback(
            provider=provider, parsed=_parsed(ProviderStatus.CANCELED, "C-1")
        )
    fail_mock.assert_not_awaited()
    assert result is not None
    await session.refresh(order)
    assert order.status == OrderStatus.PENDING


@pytest.mark.asyncio
async def test_apply_callback_paid_is_ignored_no_flip(session, cb_adapter):
    """Success-only policy: a provider PAID ("customer paid, awaiting confirm")
    callback is intermediate, NOT a success — it's ignored. No flip to
    RECEIPT_UPLOADED, no money moves; the order stays PENDING until a genuine
    SUCCESS or our TTL timer closes it."""
    provider = await _mk_provider(session)
    merchant = await _mk_merchant(session)
    order, _, _ = await _mk_order_with_attempt(
        session, provider=provider, merchant=merchant,
        external_order_id="P-1", order_status=OrderStatus.PENDING,
    )

    service = CascadingService(session)
    with (
        patch.object(OrderService, "complete_order", new=AsyncMock()) as complete_mock,
        patch.object(OrderService, "fail_order", new=AsyncMock()) as fail_mock,
    ):
        result = await service.apply_callback(
            provider=provider, parsed=_parsed(ProviderStatus.PAID, "P-1")
        )
    assert result is not None
    complete_mock.assert_not_awaited()
    fail_mock.assert_not_awaited()
    await session.refresh(order)
    assert order.status == OrderStatus.PENDING
    assert order.receipt_uploaded_by is None


@pytest.mark.asyncio
async def test_apply_callback_paid_ignored_when_not_pending(session, cb_adapter):
    """If we're already past PENDING (success / failed / receipt_uploaded),
    a late PAID callback is a no-op — terminal states win."""
    provider = await _mk_provider(session)
    merchant = await _mk_merchant(session)
    order, _, _ = await _mk_order_with_attempt(
        session, provider=provider, merchant=merchant,
        external_order_id="P-IGN", order_status=OrderStatus.SUCCESS,
    )

    service = CascadingService(session)
    with patch.object(OrderService, "complete_order", new=AsyncMock()):
        await service.apply_callback(
            provider=provider, parsed=_parsed(ProviderStatus.PAID, "P-IGN")
        )
    await session.refresh(order)
    # Status stays SUCCESS, receipt timestamps not stamped retroactively.
    assert order.status == OrderStatus.SUCCESS
    assert order.receipt_uploaded_by is None


@pytest.mark.asyncio
async def test_apply_callback_disputed_does_not_trigger_finance(session, cb_adapter):
    """DISPUTED is an intermediate state — surfaces in the dispute pipeline
    but does NOT push the order anywhere financially. apply_callback must
    leave OrderService alone for this status."""
    provider = await _mk_provider(session)
    merchant = await _mk_merchant(session)
    order, _, _ = await _mk_order_with_attempt(
        session, provider=provider, merchant=merchant,
        external_order_id="D-1", order_status=OrderStatus.PENDING,
    )

    service = CascadingService(session)
    with (
        patch.object(OrderService, "complete_order", new=AsyncMock()) as complete_mock,
        patch.object(OrderService, "fail_order", new=AsyncMock()) as fail_mock,
    ):
        result = await service.apply_callback(
            provider=provider, parsed=_parsed(ProviderStatus.DISPUTED, "D-1")
        )
    # Result is the order, but no terminal transition fired.
    assert result is not None
    complete_mock.assert_not_awaited()
    fail_mock.assert_not_awaited()
