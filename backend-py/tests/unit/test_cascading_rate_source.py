"""Integration tests for the cascade rate_source toggle.

Covers the two ways CascadingService picks the rate stamped on a winning
attempt:
  * PROVIDER mode → use the adapter's parsed provider_rate (or fall back to
    amount_fiat / amount_usdt when it's missing — the LegacyCrypto case)
  * PLATFORM mode → look up RateConfig.current_rate by rate_config_id

Both modes are exercised through CascadingService._race_group, not stubbed,
so we also validate the wiring inside _materialize_winner.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict

import pytest

from app.common.enums.cascading import (
    CascadeAttemptStatus,
    CascadeMode,
    CascadeRateSource,
    RequisiteSource,
)
from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.modules.cascading.integrations import registry as adapter_registry
from app.modules.cascading.integrations.base import ProviderRequisiteResponse
from app.modules.cascading.integrations.mock import MockProviderAdapter
from app.modules.cascading.models import (
    CascadeGroup,
    CascadeProvider,
    cascade_group_merchants,
    cascade_group_providers,
)
from app.modules.cascading.service import CascadingService
from app.modules.merchants.models import Merchant
from app.modules.users.models import User


class _QuotingAdapter(MockProviderAdapter):
    """Adapter that fills provider_rate explicitly — simulates LegacyCrypto."""

    code = "quoting"
    supports_provider_rate = True

    async def issue_requisite(self, *, provider, order_data, idempotency_key, timeout_ms):
        return ProviderRequisiteResponse(
            external_order_id=f"q-{idempotency_key}",
            bank_name="Bank Q",
            account_number="0000",
            account_holder="Holder",
            payment_method=order_data["payment_method"],
            payment_option_code=None,
            amount_fiat=Decimal(str(order_data["amount"])),
            expires_at=_expiry(),
            raw={"quoted_rate": 95.0},
            provider_rate=Decimal("95.0"),
        )


class _BareAdapter(MockProviderAdapter):
    """Adapter that does NOT provide provider_rate — service must fall back."""

    code = "bare"
    supports_provider_rate = True

    async def issue_requisite(self, *, provider, order_data, idempotency_key, timeout_ms):
        return ProviderRequisiteResponse(
            external_order_id=f"b-{idempotency_key}",
            bank_name="Bank B",
            account_number="0000",
            account_holder="Holder",
            payment_method=order_data["payment_method"],
            payment_option_code=None,
            amount_fiat=Decimal(str(order_data["amount"])),
            expires_at=_expiry(),
            raw={},
            provider_rate=None,
        )


def _expiry():
    from datetime import timedelta
    from app.common.types import utcnow

    return utcnow() + timedelta(seconds=600)


@pytest.fixture
def quoting_adapter():
    adapter = _QuotingAdapter()
    adapter_registry._REGISTRY["quoting"] = adapter
    yield adapter
    adapter_registry._REGISTRY.pop("quoting", None)


@pytest.fixture
def bare_adapter():
    adapter = _BareAdapter()
    adapter_registry._REGISTRY["bare"] = adapter
    yield adapter
    adapter_registry._REGISTRY.pop("bare", None)


async def _mk_provider(
    session,
    code: str,
    *,
    adapter_type: str,
    rate_source: CascadeRateSource = CascadeRateSource.PROVIDER,
    rate_config_id: int | None = None,
) -> CascadeProvider:
    from app.common.enums.balances import BalanceType
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

    # Cascade gates on the virtual trader's USDT WORK balance — fund it so the
    # provider passes the funding gate.
    session.add(Balance(
        user_id=user.id, type=BalanceType.WORK,
        currency=Currency.USDT, amount=Decimal("1000000"),
    ))
    await session.flush()

    provider = CascadeProvider(
        code=code,
        name=f"Provider {code}",
        adapter_type=adapter_type,
        is_active=True,
        base_url="https://example.com",
        virtual_user_id=user.id,
        virtual_trader_id=trader.id,
        fees={"sbp": 1.0},
        rate_source=rate_source,
        rate_config_id=rate_config_id,
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

    owner = User(
        username="owner_rs",
        password="x",
        role=UserRole.MERCHANT,
        is_system=False,
        is_blocked=False,
    )
    session.add(owner)
    await session.flush()

    merchant = Merchant(
        user_id=owner.id,
        name="Test",
        api_key="key",
        api_secret="sec",
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


def _order_data(merchant_id: int, *, amount: str = "1000", amount_usdt: str = "10.5") -> Dict[str, Any]:
    return {
        "merchant_id": merchant_id,
        "amount": Decimal(amount),
        "amount_usdt": Decimal(amount_usdt),
        "exchange_rate": Decimal("95.0"),
        "fee_usdt": Decimal("0.2632"),
        "currency": Currency.RUB,
        "payment_method": PaymentMethod.SBP,
        "payment_option_id": None,
        "payment_option_code": None,
    }


@pytest.mark.asyncio
async def test_provider_mode_uses_quoted_rate(session, quoting_adapter):
    merchant = await _mk_merchant(session)
    await _mk_provider(session, "q1", adapter_type="quoting")

    service = CascadingService(session)
    result = await service.try_cascade(
        merchant=merchant, order_data=_order_data(merchant.id), snapshot={}
    )

    assert result.success
    assert result.attempt.provider_rate == Decimal("95.0000")


@pytest.mark.asyncio
async def test_provider_mode_falls_back_to_rub_usdt_division(session, bare_adapter):
    merchant = await _mk_merchant(session)
    await _mk_provider(session, "b1", adapter_type="bare")

    service = CascadingService(session)
    result = await service.try_cascade(
        merchant=merchant,
        order_data=_order_data(merchant.id, amount="1000", amount_usdt="10"),
        snapshot={},
    )

    assert result.success
    # 1000 / 10 = 100.0000
    assert result.attempt.provider_rate == Decimal("100.0000")


@pytest.mark.asyncio
async def test_platform_mode_uses_rate_config(session, quoting_adapter):
    from app.common.enums.rates import RateSource, OrderBookSide
    from app.modules.rates.models import RateConfig

    cfg = RateConfig(
        name="Platform RUB",
        source=RateSource.BYBIT,
        side=OrderBookSide.BUY,
        position=1,
        payment_methods=["card"],
        fiat_currency=Currency.RUB,
        crypto_currency="USDT",
        update_interval_seconds=10,
        is_active=True,
        current_rate=87.5,
    )
    session.add(cfg)
    await session.flush()

    merchant = await _mk_merchant(session)
    await _mk_provider(
        session,
        "q2",
        adapter_type="quoting",
        rate_source=CascadeRateSource.PLATFORM,
        rate_config_id=cfg.id,
    )

    service = CascadingService(session)
    result = await service.try_cascade(
        merchant=merchant, order_data=_order_data(merchant.id), snapshot={}
    )

    assert result.success
    # Platform rate (87.5) overrides the provider's quoted 95.0.
    assert result.attempt.provider_rate == Decimal("87.5")


@pytest.mark.asyncio
async def test_platform_mode_without_config_falls_back(session, quoting_adapter):
    """When rate_source=platform but rate_config_id is missing, fall back to
    the provider's quoted rate so we don't crash on misconfigured providers."""
    merchant = await _mk_merchant(session)
    await _mk_provider(
        session,
        "q3",
        adapter_type="quoting",
        rate_source=CascadeRateSource.PLATFORM,
        rate_config_id=None,
    )

    service = CascadingService(session)
    result = await service.try_cascade(
        merchant=merchant, order_data=_order_data(merchant.id), snapshot={}
    )

    assert result.success
    # Falls back to the quoted 95.0 — no crash.
    assert result.attempt.provider_rate == Decimal("95.0")
