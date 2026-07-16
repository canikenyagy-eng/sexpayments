"""
Integration tests for RECEIPT-CHECK charge/refund EDGES against a REAL in-memory
ledger — the cases NOT already covered by ``test_receipt_check_idempotency.py``
(which owns: single-charge happy path, the live-index replay/idempotency, the
CACHED dedup replay, and the refundable-error net-zero round-trip).

Money model (single source of truth = FinanceService):
  * A live receipt check charges trader USDT **WORK** → system **WORK**
    (ledger reference_type RECEIPT_CHECK, reference_id "receipt_check:<id>")
    — but ONLY when ``trader_user is not None and price > 0``.
  * A refundable provider error reverses that move in the same run via
    RECEIPT_CHECK_REFUND (system WORK → trader WORK) — but ONLY when the run
    actually charged (``check.charged and not check.refunded``).

This file pins the EDGES of those two guards:
  - price == 0 provider          → SUCCESS, charged False, ZERO ledger legs.
  - trader_user is None          → SUCCESS, charged False, ZERO ledger legs.
  - non-PDF file                 → FAILED, charged False, ZERO ledger legs.
  - refund_receipt_check_fee()   → exactly one RECEIPT_CHECK_REFUND leg,
                                    system→trader, exact amount, correct ref.
  - insufficient WORK (< price)  → charge raises AFTER the PENDING row exists;
                                    PENDING row persists, charged False, no leg.
  - refundable error w/o a charge → refund branch is a no-op (no spurious credit).
  - refund called TWICE          → pins the CURRENT (un-guarded) behaviour: a
                                    SECOND refund leg is emitted (see app_bugs).
  - refund to a non-trader user  → pins that refund_receipt_check_fee has NO
                                    role guard (charge does; refund does not).

SQLite harness note (read tests/conftest.py): SELECT ... FOR UPDATE inside
FinanceService.transfer is a no-op on SQLite's single shared connection, so these
tests prove (a) exact Decimal money math through the REAL ledger, and (b)
SEQUENTIAL idempotency — they do NOT prove real row-lock concurrency. The
provider HTTP client is faked (build_client_for_provider patched) so no network
is touched.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

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
from app.common.enums.users import UserRole
from app.core.exceptions import ValidationException
from app.core.security import get_password_hash
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.finance.service import FinanceService
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.receipt_checks.models import ReceiptCheck, ReceiptCheckProvider
from app.modules.receipt_checks.schemas import ProviderCheckResult, ReceiptCheckVerdictItem
from app.modules.receipt_checks.service import ReceiptCheckService
from app.modules.users.models import User

PRICE = Decimal("0.5000")           # provider fee per check
TRADER_WORK_START = Decimal("100.0000")

_INDEX_SQL = (
    "CREATE UNIQUE INDEX uq_receipt_check_active_file "
    "ON receipt_checks (order_id, file_sha256) "
    "WHERE status IN ('pending','success','cached')"
)


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


async def _mk_balance(session, amount, *, user_id=None, is_system=False,
                      btype=BalanceType.WORK) -> Balance:
    b = Balance(user_id=user_id, is_system=is_system, type=btype,
                currency=Currency.USDT, amount=Decimal(amount))
    session.add(b)
    await session.flush()
    return b


async def _mk_provider(session, *, price=PRICE, active=True) -> ReceiptCheckProvider:
    p = ReceiptCheckProvider(
        code=f"trexo-{uuid4().hex[:6]}", name="TREXO",
        adapter_type=ReceiptCheckProviderAdapter.TREXO.value, is_active=active,
        base_url="https://api.trexo.example", api_key_encrypted="enc::",
        api_key_tail="abcd", price_usdt=Decimal(price), request_timeout_ms=90000,
        settings={},
    )
    session.add(p)
    await session.flush()
    return p


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


def _mk_pdf(tmp_path, name="receipt.pdf", body=b"%PDF-1.4\n%real receipt bytes\n") -> str:
    p = tmp_path / name
    p.write_bytes(body)
    return str(p)


def _mk_file(tmp_path, name, body=b"not a pdf\n") -> str:
    p = tmp_path / name
    p.write_bytes(body)
    return str(p)


# ── readers ─────────────────────────────────────────────────────────────


async def _work(session, *, user_id=None, is_system=False) -> Decimal:
    stmt = select(Balance).where(
        Balance.type == BalanceType.WORK, Balance.currency == Currency.USDT
    )
    if is_system:
        stmt = stmt.where(Balance.is_system.is_(True))
    else:
        stmt = stmt.where(Balance.user_id == user_id)
    row = (await session.execute(stmt)).scalars().first()
    return row.amount if row else Decimal("0")


async def _ledger_legs(session, ref_type: LedgerReferenceType) -> list[LedgerEntry]:
    stmt = select(LedgerEntry).where(LedgerEntry.reference_type == ref_type)
    return list((await session.execute(stmt)).scalars().all())


async def _pending_rows(session, order_id: int) -> list[ReceiptCheck]:
    stmt = select(ReceiptCheck).where(
        ReceiptCheck.order_id == order_id,
        ReceiptCheck.status == ReceiptCheckStatus.PENDING,
    )
    return list((await session.execute(stmt)).scalars().all())


async def _check_count(session, order_id: int) -> int:
    stmt = select(func.count()).select_from(ReceiptCheck).where(
        ReceiptCheck.order_id == order_id
    )
    return int((await session.execute(stmt)).scalar_one())


def _fake_client(*, clean=True, refundable=False, error_code=None):
    """A fake provider client returning a deterministic verdict (no network)."""
    client = MagicMock()

    async def _check_file(_path):
        if error_code:
            return ProviderCheckResult(refundable=refundable, error_code=error_code,
                                       error_message="boom")
        return ProviderCheckResult(
            is_clean=clean,
            verdict=[ReceiptCheckVerdictItem(type="OK")],
            parsed_data={"sum": "1000"},
            provider_check_id="pc-1", provider_tx_id="tx-1",
            raw_response={"is_clean": clean}, refundable=False,
        )

    client.check_file = _check_file
    return client


async def _setup(session, tmp_path, *, with_index=True, price=PRICE,
                 trader_work=TRADER_WORK_START, ext=".pdf"):
    """Trader (WORK funded) + system WORK + active provider + order w/ a real
    receipt file (PDF by default; pass ext to exercise the non-PDF reject)."""
    if with_index:
        await session.execute(text(_INDEX_SQL))
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    await _mk_balance(session, trader_work, user_id=trader.id)
    await _mk_balance(session, Decimal("0"), is_system=True)
    provider = await _mk_provider(session, price=price)
    if ext == ".pdf":
        receipt = _mk_pdf(tmp_path, name=f"r_{uuid4().hex[:6]}.pdf")
    else:
        receipt = _mk_file(tmp_path, name=f"r_{uuid4().hex[:6]}{ext}")
    order = await _mk_order(session, trader_id=trader.id, receipt_file=receipt)
    return trader, provider, order, receipt


# ══════════════════════════════════════════════════════════════════════════
# CORRECT BEHAVIOUR
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_zero_price_provider_runs_but_charges_nothing(session, tmp_path):
    """A provider configured with price_usdt == 0 still runs the check and stores
    a SUCCESS verdict, but the ``price > 0`` guard means NO money moves: charged
    False, zero RECEIPT_CHECK legs, trader + system WORK untouched."""
    trader, provider, order, _f = await _setup(session, tmp_path, price=Decimal("0"))

    with patch(
        "app.modules.receipt_checks.service.build_client_for_provider",
        return_value=_fake_client(clean=True),
    ):
        check = await ReceiptCheckService(session).run_check_for_order(
            order, trader, ReceiptCheckTrigger.MANUAL,
        )

    assert check.status == ReceiptCheckStatus.SUCCESS
    assert check.charged is False and check.refunded is False
    assert check.price_usdt == Decimal("0")

    # No money moved at all.
    assert await _work(session, user_id=trader.id) == TRADER_WORK_START
    assert await _work(session, is_system=True) == Decimal("0")
    assert not await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK)
    assert not await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK_REFUND)


@pytest.mark.asyncio
async def test_no_trader_user_runs_but_charges_nothing(session, tmp_path):
    """An auto-check with no resolved trader (trader_user is None — e.g. order
    has no assigned trader) runs the verdict but charges nobody: the
    ``trader_user is not None`` guard skips the money move entirely. SUCCESS,
    charged False, zero ledger legs, trader_user_id NULL on the row."""
    trader, provider, order, _f = await _setup(session, tmp_path)
    work_before = await _work(session, user_id=trader.id)

    with patch(
        "app.modules.receipt_checks.service.build_client_for_provider",
        return_value=_fake_client(clean=True),
    ):
        check = await ReceiptCheckService(session).run_check_for_order(
            order, None, ReceiptCheckTrigger.AUTO,   # <- no trader
        )

    assert check.status == ReceiptCheckStatus.SUCCESS
    assert check.charged is False and check.refunded is False
    assert check.trader_user_id is None

    # The order's trader (and everyone else) is untouched; no legs at all.
    assert await _work(session, user_id=trader.id) == work_before
    assert await _work(session, is_system=True) == Decimal("0")
    assert not await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK)
    assert not await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK_REFUND)


@pytest.mark.asyncio
async def test_non_pdf_file_fails_before_any_charge(session, tmp_path):
    """A non-PDF receipt is rejected BEFORE the provider is called and before any
    charge: a FAILED row (error_code 'unsupported_format', price 0, charged
    False) is written and NO ledger leg is emitted. The build_client patch is
    set to blow up if reached — proving we never burn a provider request."""
    trader, provider, order, _f = await _setup(session, tmp_path, ext=".jpg")

    def _boom(_p):  # build_client_for_provider must NOT be called for non-PDF
        raise AssertionError("provider client built for a non-PDF file")

    with patch(
        "app.modules.receipt_checks.service.build_client_for_provider", _boom,
    ):
        check = await ReceiptCheckService(session).run_check_for_order(
            order, trader, ReceiptCheckTrigger.MANUAL,
        )

    assert check.status == ReceiptCheckStatus.FAILED
    assert check.error_code == "unsupported_format"
    assert check.charged is False and check.refunded is False
    assert check.price_usdt == Decimal("0")

    # Real ledger untouched.
    assert await _work(session, user_id=trader.id) == TRADER_WORK_START
    assert await _work(session, is_system=True) == Decimal("0")
    assert not await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK)
    assert not await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK_REFUND)


@pytest.mark.asyncio
async def test_refund_primitive_credits_trader_one_leg_exact_ref(session, tmp_path):
    """``FinanceService.refund_receipt_check_fee`` in isolation: system WORK →
    trader WORK, exactly ONE RECEIPT_CHECK_REFUND leg, exact amount, reference_id
    'receipt_check:<id>', value conserved (system -PRICE, trader +PRICE)."""
    # Fund system WORK so the refund (system → trader) has something to move.
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    await _mk_balance(session, Decimal("0"), user_id=trader.id)
    await _mk_balance(session, PRICE, is_system=True)

    finance = FinanceService(session)
    entry = await finance.refund_receipt_check_fee(trader, PRICE, check_id=4242)
    await session.flush()

    # Trader credited, system debited; total conserved.
    assert await _work(session, user_id=trader.id) == PRICE
    assert await _work(session, is_system=True) == Decimal("0")

    legs = await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK_REFUND)
    assert len(legs) == 1
    assert legs[0].id == entry.id
    assert legs[0].amount == PRICE
    assert legs[0].reference_id == "receipt_check:4242"
    # The opposite (charge) reference type was NOT touched.
    assert not await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK)


# ══════════════════════════════════════════════════════════════════════════
# ADVERSARIAL
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_insufficient_work_raises_after_pending_row_no_leg(session, tmp_path):
    """Trader WORK < price with an active paying provider: the PENDING row is
    claimed FIRST, then ``charge_receipt_check_fee`` raises 'Insufficient WORK
    balance'. We pin what PERSISTS after the exception surfaces:
      * the exception propagates out of run_check_for_order (NOT swallowed);
      * the PENDING row exists (the claim happened) with charged False;
      * NO RECEIPT_CHECK ledger leg was written (charge failed atomically);
      * trader + system WORK are unchanged.
    """
    # Trader funded with LESS than the price.
    trader, provider, order, _f = await _setup(
        session, tmp_path, trader_work=Decimal("0.1000"),
    )
    assert PRICE > Decimal("0.1000")

    with patch(
        "app.modules.receipt_checks.service.build_client_for_provider",
        return_value=_fake_client(clean=True),
    ):
        with pytest.raises(ValidationException):
            await ReceiptCheckService(session).run_check_for_order(
                order, trader, ReceiptCheckTrigger.MANUAL,
            )

    # The PENDING claim row survives (charge happens AFTER the row exists), and
    # it never flipped charged=True.
    pending = await _pending_rows(session, order.id)
    assert len(pending) == 1
    assert pending[0].charged is False
    assert pending[0].price_usdt == PRICE

    # No money moved; no charge leg.
    assert await _work(session, user_id=trader.id) == Decimal("0.1000")
    assert await _work(session, is_system=True) == Decimal("0")
    assert not await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK)
    assert not await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK_REFUND)


@pytest.mark.asyncio
async def test_refundable_error_without_charge_credits_nothing(session, tmp_path):
    """A REFUNDABLE provider error on a run that NEVER charged (price 0) must NOT
    credit the trader: the refund branch is gated on ``check.charged``. Pins that
    a refundable failure with no prior charge leaves money flat — no spurious
    RECEIPT_CHECK_REFUND leg, no system→trader transfer."""
    trader, provider, order, _f = await _setup(session, tmp_path, price=Decimal("0"))
    work_before = await _work(session, user_id=trader.id)

    with patch(
        "app.modules.receipt_checks.service.build_client_for_provider",
        return_value=_fake_client(error_code="upstream_error", refundable=True),
    ):
        check = await ReceiptCheckService(session).run_check_for_order(
            order, trader, ReceiptCheckTrigger.MANUAL,
        )

    assert check.status == ReceiptCheckStatus.FAILED
    assert check.error_code == "upstream_error"
    assert check.charged is False
    assert check.refunded is False                 # refund branch skipped

    # No money moved either direction.
    assert await _work(session, user_id=trader.id) == work_before
    assert await _work(session, is_system=True) == Decimal("0")
    assert not await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK)
    assert not await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK_REFUND)


@pytest.mark.asyncio
async def test_refund_primitive_has_no_idempotency_guard_double_credits(session, tmp_path):
    """ADVERSARIAL / BUG PIN: ``refund_receipt_check_fee`` carries NO idempotency
    guard of its own — calling it TWICE for the same check_id emits TWO
    RECEIPT_CHECK_REFUND legs and double-credits the trader.

    The service's run_check_for_order is protected by the ``not check.refunded``
    flag check, so this double-refund is unreachable via that path today — but the
    PRIMITIVE itself is unguarded, so any future caller that forgets the flag will
    over-credit. This test PINS the current (unguarded) behaviour: a regression
    that ADDS a guard would (correctly) break this test and force a re-think.
    """
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    await _mk_balance(session, Decimal("0"), user_id=trader.id)
    # System must hold >= 2*PRICE so both (un-guarded) refunds succeed.
    await _mk_balance(session, PRICE * 2, is_system=True)

    finance = FinanceService(session)
    await finance.refund_receipt_check_fee(trader, PRICE, check_id=77)
    await finance.refund_receipt_check_fee(trader, PRICE, check_id=77)  # <- same id again
    await session.flush()

    # CURRENT BEHAVIOUR (the bug): trader credited TWICE for one check.
    assert await _work(session, user_id=trader.id) == PRICE * 2
    assert await _work(session, is_system=True) == Decimal("0")
    legs = await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK_REFUND)
    assert len(legs) == 2                          # NOT de-duped on reference_id
    assert {leg.reference_id for leg in legs} == {"receipt_check:77"}


@pytest.mark.asyncio
async def test_refund_primitive_has_no_role_guard_credits_non_trader(session, tmp_path):
    """ADVERSARIAL / BUG PIN: ``charge_receipt_check_fee`` rejects non-TRADER
    accounts (role guard), but ``refund_receipt_check_fee`` has NO matching role
    guard — it will happily credit an ADMIN's WORK balance. Pins the current
    asymmetry: the refund primitive emits a leg for a non-trader where the charge
    primitive would have raised ValidationException."""
    admin = await _mk_user(session, username=f"adm_{uuid4().hex[:6]}", role=UserRole.ADMIN)
    await _mk_balance(session, Decimal("0"), user_id=admin.id)
    await _mk_balance(session, PRICE, is_system=True)

    finance = FinanceService(session)

    # Sanity: the CHARGE side DOES guard the role.
    with pytest.raises(ValidationException):
        await finance.charge_receipt_check_fee(admin, PRICE, check_id=9)

    # But the REFUND side does NOT — it credits the admin anyway.
    entry = await finance.refund_receipt_check_fee(admin, PRICE, check_id=9)
    await session.flush()

    assert await _work(session, user_id=admin.id) == PRICE        # credited despite non-trader
    assert await _work(session, is_system=True) == Decimal("0")
    legs = await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK_REFUND)
    assert len(legs) == 1
    assert legs[0].id == entry.id
    assert legs[0].reference_id == "receipt_check:9"
