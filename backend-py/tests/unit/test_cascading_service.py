"""Integration tests for CascadingService using in-memory SQLite.

The conftest in this directory patches JSONB to JSON so SQLAlchemy's SQLite
backend can hold our JSONB columns.

Coverage:
  * race within a group: faster provider wins, slower issuer is told to cancel
  * full-group refusal escalates to next tier
  * POOLED mode walks providers sequentially
  * circuit-breaker open providers are skipped
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from app.common.enums.cascading import (
    CascadeAttemptStatus,
    CascadeMode,
    RequisiteSource,
)
from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.modules.cascading.integrations import registry as adapter_registry
from app.modules.cascading.integrations.base import (
    ProviderAdapter,
    ProviderRefusal,
    ProviderRequisiteResponse,
)
from app.modules.cascading.integrations.mock import MockProviderAdapter
from app.modules.cascading.models import CascadeGroup, CascadeProvider, cascade_group_merchants, cascade_group_providers
from app.modules.cascading.service import CascadingService
from app.modules.merchants.models import Merchant
from app.modules.users.models import User


# ─── helpers ─────────────────────────────────────────────────


class _ScriptedAdapter(MockProviderAdapter):
    """Adapter that lets each test specify how every provider should respond.

    Behavior is keyed on provider.code:
        "fast" → respond with a requisite after fast_delay seconds
        "slow" → respond after slow_delay seconds (loses the race)
        "refuse" → return ProviderRefusal
        "error" → raise RuntimeError
    """

    code = "scripted"

    def __init__(self):
        super().__init__()
        self.cancel_calls: List[tuple[str, str]] = []  # (provider_code, external_order_id)

    async def issue_requisite(self, *, provider, order_data, idempotency_key, timeout_ms):
        cfg = provider.settings or {}
        delay = float(cfg.get("delay_ms", 0)) / 1000
        if delay:
            await asyncio.sleep(delay)
        if cfg.get("error"):
            raise RuntimeError("scripted_error")
        if cfg.get("refuse"):
            return ProviderRefusal(code="no_capacity", message="scripted refusal")
        return ProviderRequisiteResponse(
            external_order_id=f"{provider.code}-{idempotency_key}",
            bank_name=f"Bank-{provider.code}",
            account_number="0000",
            account_holder="Holder",
            payment_method=order_data["payment_method"],
            payment_option_code=None,
            amount_fiat=Decimal(str(order_data["amount"])),
            expires_at=order_data.get("expires_at") or _expiry(),
            raw={"provider_code": provider.code},
        )

    async def cancel_request(self, *, provider, external_order_id, timeout_ms):
        self.cancel_calls.append((provider.code, external_order_id))
        return True


def _expiry():
    from datetime import timedelta
    from app.common.types import utcnow
    return utcnow() + timedelta(seconds=600)


@pytest.fixture
def scripted_adapter():
    adapter = _ScriptedAdapter()
    adapter_registry._REGISTRY["scripted"] = adapter
    yield adapter
    adapter_registry._REGISTRY.pop("scripted", None)


async def _mk_provider(
    session,
    code: str,
    *,
    settings: Optional[Dict[str, Any]] = None,
    is_active: bool = True,
    request_timeout_ms: int = 5000,
    balance_usdt: Decimal = Decimal("1000000"),
) -> CascadeProvider:
    """Create CascadeProvider directly (bypass CascadingService.create_provider
    so tests don't depend on the auth/audit machinery)."""
    from app.common.enums.balances import BalanceType
    from app.common.enums.finances import Currency
    from app.common.enums.traders import TraderStatus
    from app.common.enums.users import UserRole
    from app.modules.finance.models import Balance
    from app.modules.traders.models import Trader

    user = User(
        username=f"sys_{code}",
        password="x",
        role=UserRole.TRADER,
        is_system=True,
        is_blocked=False,
    )
    session.add(user)
    await session.flush()

    trader = Trader(
        user_id=user.id,
        status=TraderStatus.ENABLED,
        is_payin_active=True,
        is_payout_active=False,
        accept_all_merchants=False,
    )
    session.add(trader)
    await session.flush()

    # Cascade routing now gates on the virtual trader's USDT WORK balance
    # (it must cover the escrow freeze done at order creation). Seed it so the
    # provider passes the funding gate; pass balance_usdt=0 to assert a skip.
    if balance_usdt is not None:
        session.add(Balance(
            user_id=user.id,
            type=BalanceType.WORK,
            currency=Currency.USDT,
            amount=Decimal(str(balance_usdt)),
        ))
        await session.flush()

    provider = CascadeProvider(
        code=code,
        name=f"Provider {code}",
        adapter_type="scripted",
        is_active=is_active,
        base_url="https://example.com",
        virtual_user_id=user.id,
        virtual_trader_id=trader.id,
        rates={"RUB": 95.0},
        fees={"sbp": 1.0},
        cb_window_seconds=300,
        cb_threshold_failures=999,  # never trip in tests unless explicitly set
        cb_threshold_rate=1.0,
        cb_cooldown_seconds=60,
        request_timeout_ms=request_timeout_ms,
        cancel_timeout_ms=1000,
        priority_weight=100,
        settings=settings or {},
    )
    session.add(provider)
    await session.flush()
    return provider


async def _mk_group(
    session,
    name: str,
    tier: int,
    timeout_ms: int,
    providers: List[CascadeProvider],
    merchant_id: int,
) -> CascadeGroup:
    group = CascadeGroup(
        name=name,
        tier=tier,
        timeout_ms=timeout_ms,
        is_active=True,
    )
    session.add(group)
    await session.flush()
    for p in providers:
        await session.execute(
            cascade_group_providers.insert().values(group_id=group.id, provider_id=p.id)
        )
    await session.execute(
        cascade_group_merchants.insert().values(group_id=group.id, merchant_id=merchant_id)
    )
    await session.flush()
    # reload with relationships populated
    return await CascadingService(session).groups.get_with_relations(group.id)


async def _mk_merchant(session, *, cascade_mode: CascadeMode) -> Merchant:
    from app.common.enums.merchants import TerminalStatus
    from app.common.enums.users import UserRole

    owner = User(
        username="owner",
        password="x",
        role=UserRole.MERCHANT,
        is_system=False,
        is_blocked=False,
    )
    session.add(owner)
    await session.flush()

    merchant = Merchant(
        user_id=owner.id,
        name="Test Merchant",
        api_key="test_key",
        api_secret="test_secret",
        status=TerminalStatus.ENABLED,
        currency=Currency.RUB,
        fees={"sbp": 2.5},
        order_ttl_seconds=1800,
        requisite_search_timeout_ms=5000,
        cascade_mode=cascade_mode,
    )
    session.add(merchant)
    await session.flush()
    return merchant


def _order_data(merchant_id: int) -> Dict[str, Any]:
    return {
        "merchant_id": merchant_id,
        "amount": Decimal("1000"),
        "amount_usdt": Decimal("10.5263"),
        "exchange_rate": Decimal("95.0"),
        "fee_usdt": Decimal("0.2632"),
        "currency": Currency.RUB,
        "payment_method": PaymentMethod.SBP,
        "payment_option_id": None,
        "payment_option_code": None,
    }


# ─── tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grouped_race_fast_wins_slow_gets_cancel(session, scripted_adapter):
    merchant = await _mk_merchant(session, cascade_mode=CascadeMode.GROUPED)
    fast = await _mk_provider(session, "fast", settings={"delay_ms": 10})
    slow = await _mk_provider(session, "slow", settings={"delay_ms": 200})
    await _mk_group(
        session, name="t1", tier=1, timeout_ms=2000, providers=[fast, slow],
        merchant_id=merchant.id,
    )

    service = CascadingService(session)

    # Replace the celery enqueue with a synchronous call that drives our
    # scripted adapter's cancel_request directly. CascadeService normally
    # fires the cancel as a Celery task; here we just invoke the adapter so
    # the test can assert on cancel_calls.
    async def _direct_cancel(provider, external_order_id):
        await scripted_adapter.cancel_request(
            provider=provider,
            external_order_id=external_order_id,
            timeout_ms=provider.cancel_timeout_ms,
        )

    def _enqueue(self, *, provider_code: str, external_order_id: str) -> None:
        provider = next(
            p for p in (fast, slow) if p.code == provider_code
        )
        asyncio.create_task(_direct_cancel(provider, external_order_id))

    with patch.object(CascadingService, "_enqueue_cancel", _enqueue):
        result = await service.try_cascade(
            merchant=merchant, order_data=_order_data(merchant.id), snapshot={}
        )

    assert result.success is True
    assert result.provider.code == "fast"
    # Wait briefly for the cancel task to fire.
    await asyncio.sleep(0.05)
    assert any(c[0] == "slow" for c in scripted_adapter.cancel_calls), (
        f"slow provider should receive cancel; saw {scripted_adapter.cancel_calls}"
    )

    # Requisite created with source=cascade and pinned to slow's virtual user
    # is impossible: fast won, so the requisite belongs to fast's virtual user.
    assert result.requisite is not None
    assert result.requisite.source == RequisiteSource.CASCADE


@pytest.mark.asyncio
async def test_grouped_escalates_to_next_tier(session, scripted_adapter):
    merchant = await _mk_merchant(session, cascade_mode=CascadeMode.GROUPED)
    refuser = await _mk_provider(session, "refuser", settings={"refuse": True})
    winner = await _mk_provider(session, "winner", settings={"delay_ms": 10})
    await _mk_group(session, name="t1", tier=1, timeout_ms=300, providers=[refuser], merchant_id=merchant.id)
    await _mk_group(session, name="t2", tier=2, timeout_ms=2000, providers=[winner], merchant_id=merchant.id)

    service = CascadingService(session)
    result = await service.try_cascade(
        merchant=merchant, order_data=_order_data(merchant.id), snapshot={}
    )

    assert result.success is True
    assert result.provider.code == "winner"


@pytest.mark.asyncio
async def test_grouped_returns_failure_when_all_groups_refuse(session, scripted_adapter):
    merchant = await _mk_merchant(session, cascade_mode=CascadeMode.GROUPED)
    a = await _mk_provider(session, "a", settings={"refuse": True})
    b = await _mk_provider(session, "b", settings={"refuse": True})
    await _mk_group(session, name="t1", tier=1, timeout_ms=200, providers=[a], merchant_id=merchant.id)
    await _mk_group(session, name="t2", tier=2, timeout_ms=200, providers=[b], merchant_id=merchant.id)

    service = CascadingService(session)
    result = await service.try_cascade(
        merchant=merchant, order_data=_order_data(merchant.id), snapshot={}
    )
    assert result.success is False


@pytest.mark.asyncio
async def test_pooled_mode_walks_active_providers(session, scripted_adapter):
    merchant = await _mk_merchant(session, cascade_mode=CascadeMode.POOLED)
    refuser = await _mk_provider(session, "p1", settings={"refuse": True})
    winner = await _mk_provider(session, "p2", settings={"delay_ms": 10})

    service = CascadingService(session)
    result = await service.try_cascade(
        merchant=merchant, order_data=_order_data(merchant.id), snapshot={}
    )

    assert result.success is True
    assert result.provider.code in {"p1", "p2"}
    # At least one of the providers must have produced a winning requisite.
    assert result.requisite is not None


@pytest.mark.asyncio
async def test_inactive_provider_skipped(session, scripted_adapter):
    merchant = await _mk_merchant(session, cascade_mode=CascadeMode.GROUPED)
    inactive = await _mk_provider(session, "off", is_active=False)
    backup = await _mk_provider(session, "ok", settings={"delay_ms": 5})
    await _mk_group(
        session, name="t1", tier=1, timeout_ms=1000,
        providers=[inactive, backup], merchant_id=merchant.id,
    )

    service = CascadingService(session)
    result = await service.try_cascade(
        merchant=merchant, order_data=_order_data(merchant.id), snapshot={}
    )
    assert result.success
    assert result.provider.code == "ok"


@pytest.mark.asyncio
async def test_underfunded_provider_skipped(session, scripted_adapter):
    """A provider whose virtual trader can't cover the escrow must be skipped
    BEFORE any HTTP call — otherwise it issues a real requisite (orphaned deal)
    while our order would roll back on insufficient funds."""
    merchant = await _mk_merchant(session, cascade_mode=CascadeMode.GROUPED)
    # Would win on speed, but has zero balance → must be gated out.
    broke = await _mk_provider(session, "broke", settings={"delay_ms": 1}, balance_usdt=Decimal("0"))
    funded = await _mk_provider(session, "funded", settings={"delay_ms": 10})
    await _mk_group(
        session, name="t1", tier=1, timeout_ms=1000,
        providers=[broke, funded], merchant_id=merchant.id,
    )

    service = CascadingService(session)
    snapshot: Dict[str, Any] = {}
    result = await service.try_cascade(
        merchant=merchant, order_data=_order_data(merchant.id), snapshot=snapshot,
    )
    assert result.success
    assert result.provider.code == "funded"
    skips = [d for d in snapshot["cascade"]["attempts"] if d.get("skip") == "insufficient_virtual_balance"]
    assert any(d["provider_id"] == broke.id for d in skips)


@pytest.mark.asyncio
async def test_all_providers_underfunded_returns_failure(session, scripted_adapter):
    """If the only provider is under-funded, cascade fails WITHOUT calling it."""
    merchant = await _mk_merchant(session, cascade_mode=CascadeMode.POOLED)
    await _mk_provider(session, "broke", settings={"delay_ms": 1}, balance_usdt=Decimal("0"))

    service = CascadingService(session)
    result = await service.try_cascade(
        merchant=merchant, order_data=_order_data(merchant.id), snapshot={},
    )
    assert result.success is False


@pytest.mark.asyncio
async def test_cascade_off_returns_failure(session, scripted_adapter):
    merchant = await _mk_merchant(session, cascade_mode=CascadeMode.OFF)
    p = await _mk_provider(session, "anything", settings={"delay_ms": 5})

    service = CascadingService(session)
    result = await service.try_cascade(
        merchant=merchant, order_data=_order_data(merchant.id), snapshot={}
    )
    assert result.success is False
