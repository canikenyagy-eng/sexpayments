"""
Integration tests for TRADER-SELECTED receipt-check provider against a REAL
in-memory ledger.

Proves (money math through the real FinanceService, provider HTTP faked):
  * ``resolve_provider_for_trader`` honours the trader's saved default and the
    first-active fallback, and rejects inactive/explicit-unavailable providers;
  * ``run_check_for_order(provider=...)`` charges the SELECTED provider's price
    (not the global "active" one) and records that provider on the check row;
  * multiple providers can be active at once (the single-active rule is gone).
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.receipt_checks import (
    ReceiptCheckProviderAdapter,
    ReceiptCheckStatus,
    ReceiptCheckTrigger,
)
from app.common.enums.receipt_moderations import ModerationStatus
from app.common.enums.traders import TraderStatus
from app.common.enums.users import UserRole
from app.core.exceptions import ValidationException
from app.core.security import get_password_hash
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.receipt_checks.models import ReceiptCheckProvider
from app.modules.receipt_checks.schemas import ProviderCheckResult, ReceiptCheckVerdictItem
from app.modules.receipt_checks.service import ReceiptCheckService
from app.modules.traders.models import Trader
from app.modules.users.models import User

TRADER_WORK_START = Decimal("100.0000")
PRICE_CHEAP = Decimal("0.5000")
PRICE_PREMIUM = Decimal("1.2500")


@pytest.fixture(autouse=True)
def _stub_celery():
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    with patch("app.workers.celery_app.celery_app", celery, create=True):
        yield celery


# ── builders ──────────────────────────────────────────────────────────


async def _mk_user(session, *, username, role=UserRole.TRADER) -> User:
    u = User(
        username=username, password=get_password_hash("pass12345"), role=role,
        totp_enabled=False, is_blocked=False, use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    return u


async def _mk_balance(session, amount, *, user_id=None, is_system=False) -> Balance:
    b = Balance(user_id=user_id, is_system=is_system, type=BalanceType.WORK,
                currency=Currency.USDT, amount=Decimal(amount))
    session.add(b)
    await session.flush()
    return b


async def _mk_provider(session, *, price, active=True, name="TREXO") -> ReceiptCheckProvider:
    p = ReceiptCheckProvider(
        code=f"prov-{uuid4().hex[:6]}", name=name,
        adapter_type=ReceiptCheckProviderAdapter.TREXO.value, is_active=active,
        base_url="https://api.trexo.example", api_key_encrypted="enc::",
        api_key_tail="abcd", price_usdt=Decimal(price), request_timeout_ms=90000,
        settings={},
    )
    session.add(p)
    await session.flush()
    return p


async def _mk_trader_profile(session, *, user_id, default_provider_id=None) -> Trader:
    t = Trader(
        user_id=user_id, status=TraderStatus.ENABLED,
        default_receipt_check_provider_id=default_provider_id,
    )
    session.add(t)
    await session.flush()
    return t


async def _mk_order(session, *, trader_id, receipt_file) -> Order:
    n = uuid4().hex[:8]
    owner = await _mk_user(session, username=f"mo_{n}", role=UserRole.MERCHANT)
    merchant = Merchant(user_id=owner.id, api_key=f"k-{n}", api_secret=f"s-{n}",
                        currency=Currency.RUB, fees={})
    session.add(merchant)
    await session.flush()
    order = Order(
        external_id=f"ext-{n}", merchant_id=merchant.id, trader_id=trader_id,
        direction=PaymentDirection.PAYIN, payment_method=PaymentMethod.SBP,
        amount=Decimal("1000.00"), currency=Currency.RUB,
        status=OrderStatus.RECEIPT_UPLOADED, moderation_status=ModerationStatus.NONE,
        receipt_file=receipt_file,
    )
    session.add(order)
    await session.flush()
    return order


def _mk_pdf(tmp_path, name="receipt.pdf") -> str:
    p = tmp_path / name
    p.write_bytes(b"%PDF-1.4\n%real receipt bytes\n")
    return str(p)


def _fake_client():
    client = MagicMock()

    async def _check_file(_path):
        return ProviderCheckResult(
            is_clean=True, verdict=[ReceiptCheckVerdictItem(type="OK")],
            parsed_data={"sum": "1000"}, provider_check_id="pc-1",
            provider_tx_id="tx-1", raw_response={"is_clean": True}, refundable=False,
        )

    client.check_file = _check_file
    return client


async def _work(session, *, user_id=None, is_system=False) -> Decimal:
    stmt = select(Balance).where(
        Balance.type == BalanceType.WORK, Balance.currency == Currency.USDT
    )
    stmt = stmt.where(Balance.is_system.is_(True)) if is_system else stmt.where(
        Balance.user_id == user_id
    )
    row = (await session.execute(stmt)).scalars().first()
    return row.amount if row else Decimal("0")


# ── tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_default_provider_is_resolved_and_its_price_charged(session, tmp_path):
    """Two providers are active; the trader's default is the PREMIUM one. The
    resolved provider is the default, and the check charges the PREMIUM price."""
    trader_user = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    await _mk_balance(session, TRADER_WORK_START, user_id=trader_user.id)
    await _mk_balance(session, Decimal("0"), is_system=True)

    cheap = await _mk_provider(session, price=PRICE_CHEAP, name="Cheap")
    premium = await _mk_provider(session, price=PRICE_PREMIUM, name="Premium")
    trader = await _mk_trader_profile(
        session, user_id=trader_user.id, default_provider_id=premium.id
    )
    order = await _mk_order(session, trader_id=trader_user.id, receipt_file=_mk_pdf(tmp_path))

    svc = ReceiptCheckService(session)
    resolved = await svc.resolve_provider_for_trader(trader, requested_id=None)
    assert resolved.id == premium.id  # default wins over the cheaper first-active

    with patch(
        "app.modules.receipt_checks.service.build_client_for_provider",
        return_value=_fake_client(),
    ):
        check = await svc.run_check_for_order(
            order, trader_user, ReceiptCheckTrigger.MANUAL, provider=resolved,
        )

    assert check.status == ReceiptCheckStatus.SUCCESS
    assert check.provider_id == premium.id
    assert check.price_usdt == PRICE_PREMIUM
    # Real ledger: trader WORK -PREMIUM, system WORK +PREMIUM.
    assert await _work(session, user_id=trader_user.id) == TRADER_WORK_START - PRICE_PREMIUM
    assert await _work(session, is_system=True) == PRICE_PREMIUM
    legs = list(
        (await session.execute(
            select(LedgerEntry).where(
                LedgerEntry.reference_type == LedgerReferenceType.RECEIPT_CHECK
            )
        )).scalars().all()
    )
    assert len(legs) == 1 and legs[0].amount == PRICE_PREMIUM


@pytest.mark.asyncio
async def test_explicit_provider_selection_charges_that_provider(session, tmp_path):
    """Trader explicitly picks the CHEAP provider even though their default is
    PREMIUM — the explicit choice is used and its price charged."""
    trader_user = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    await _mk_balance(session, TRADER_WORK_START, user_id=trader_user.id)
    await _mk_balance(session, Decimal("0"), is_system=True)

    cheap = await _mk_provider(session, price=PRICE_CHEAP, name="Cheap")
    premium = await _mk_provider(session, price=PRICE_PREMIUM, name="Premium")
    trader = await _mk_trader_profile(
        session, user_id=trader_user.id, default_provider_id=premium.id
    )
    order = await _mk_order(session, trader_id=trader_user.id, receipt_file=_mk_pdf(tmp_path))

    svc = ReceiptCheckService(session)
    resolved = await svc.resolve_provider_for_trader(trader, requested_id=cheap.id)
    assert resolved.id == cheap.id

    with patch(
        "app.modules.receipt_checks.service.build_client_for_provider",
        return_value=_fake_client(),
    ):
        check = await svc.run_check_for_order(
            order, trader_user, ReceiptCheckTrigger.MANUAL, provider=resolved,
        )

    assert check.provider_id == cheap.id
    assert await _work(session, user_id=trader_user.id) == TRADER_WORK_START - PRICE_CHEAP


@pytest.mark.asyncio
async def test_explicit_inactive_provider_is_rejected(session, tmp_path):
    trader_user = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    inactive = await _mk_provider(session, price=PRICE_CHEAP, active=False, name="Off")
    trader = await _mk_trader_profile(session, user_id=trader_user.id)

    svc = ReceiptCheckService(session)
    with pytest.raises(ValidationException):
        await svc.resolve_provider_for_trader(trader, requested_id=inactive.id)


@pytest.mark.asyncio
async def test_inactive_default_falls_back_to_first_active(session, tmp_path):
    """A trader whose saved default became inactive resolves to the first active
    provider (lowest id) instead of failing."""
    trader_user = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    inactive_default = await _mk_provider(session, price=PRICE_PREMIUM, active=False, name="Off")
    first_active = await _mk_provider(session, price=PRICE_CHEAP, name="On")
    trader = await _mk_trader_profile(
        session, user_id=trader_user.id, default_provider_id=inactive_default.id
    )

    svc = ReceiptCheckService(session)
    resolved = await svc.resolve_provider_for_trader(trader, requested_id=None)
    assert resolved.id == first_active.id
