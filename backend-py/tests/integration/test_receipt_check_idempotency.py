"""
Integration tests for the RECEIPT-CHECK double-charge guard (#18) against a
REAL in-memory ledger.

Money model (single source of truth = FinanceService):
  * A live receipt check charges the trader's USDT **WORK** → system **WORK**
    (ledger reference_type RECEIPT_CHECK, reference_id "receipt_check:<id>")
    exactly ONCE, only after the PENDING row has been claimed.
  * The claim is an INSERT guarded by a partial unique index over LIVE checks
    `uq_receipt_check_active_file ON receipt_checks (order_id, file_sha256)
     WHERE status IN ('pending','success','cached')`. A concurrent second run
    (manual + auto trigger, double-click, retry) conflicts on this INSERT; the
    service catches the IntegrityError, replays the winner via
    `find_active_for_file`, and DOES NOT charge the trader again.

SQLite harness note (read tests/conftest.py): the PG partial index lives in
migration 054 and is NOT created by Base.metadata.create_all. SQLite DOES
support partial indexes, so each test that drives the real IntegrityError→replay
path CREATEs the index manually in setup. SELECT ... FOR UPDATE inside
FinanceService.transfer is a no-op on SQLite's single shared connection — so
these tests do NOT prove real row-lock concurrency; they prove (a) exact money
math through the real ledger and (b) SEQUENTIAL idempotency: the application-level
unique-index claim + replay charges exactly once when the same (order_id,
file_sha256) is run twice in a row.

The provider HTTP client is faked (build_client_for_provider patched) so no
network is touched; the verdict is deterministic.
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
from app.core.security import get_password_hash
from app.modules.finance.models import Balance, LedgerEntry
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


async def _live_check_count(session, order_id: int, file_sha: str) -> int:
    stmt = select(func.count()).select_from(ReceiptCheck).where(
        ReceiptCheck.order_id == order_id,
        ReceiptCheck.file_sha256 == file_sha,
        ReceiptCheck.status.in_(
            [ReceiptCheckStatus.PENDING, ReceiptCheckStatus.SUCCESS, ReceiptCheckStatus.CACHED]
        ),
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


async def _setup(session, tmp_path, *, with_index=True, trader_work=TRADER_WORK_START):
    """Trader (WORK funded) + system WORK + active provider + order w/ a real PDF."""
    if with_index:
        await session.execute(text(_INDEX_SQL))
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    await _mk_balance(session, trader_work, user_id=trader.id)
    await _mk_balance(session, Decimal("0"), is_system=True)
    provider = await _mk_provider(session)
    pdf = _mk_pdf(tmp_path)
    order = await _mk_order(session, trader_id=trader.id, receipt_file=pdf)
    return trader, provider, order, pdf


# ── correct behaviour: a single live check charges WORK exactly once ─────


@pytest.mark.asyncio
async def test_single_check_charges_trader_work_exactly_once(session, tmp_path):
    trader, provider, order, _pdf = await _setup(session, tmp_path)

    with patch(
        "app.modules.receipt_checks.service.build_client_for_provider",
        return_value=_fake_client(clean=True),
    ):
        check = await ReceiptCheckService(session).run_check_for_order(
            order, trader, ReceiptCheckTrigger.MANUAL,
        )

    assert check.status == ReceiptCheckStatus.SUCCESS
    assert check.charged is True and check.refunded is False

    # Money math: trader WORK -PRICE, system WORK +PRICE, value conserved.
    assert await _work(session, user_id=trader.id) == TRADER_WORK_START - PRICE
    assert await _work(session, is_system=True) == PRICE

    legs = await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK)
    assert len(legs) == 1                                   # charged once
    assert legs[0].amount == PRICE
    assert legs[0].reference_id == f"receipt_check:{check.id}"
    assert not await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK_REFUND)

    # Exactly one live row for this file.
    assert await _live_check_count(session, order.id, check.file_sha256) == 1


# ── adversarial #1: duplicate LIVE check → replay winner, charge once ────


@pytest.mark.asyncio
async def test_duplicate_live_check_replays_winner_without_second_charge(session, tmp_path):
    """A concurrent winner already holds a PENDING row for (order, file_sha).
    The second run's INSERT hits the unique index → IntegrityError → the service
    replays the winner and charges the trader's WORK ZERO times."""
    trader, provider, order, pdf = await _setup(session, tmp_path)

    # The concurrent "winner": already-committed PENDING live check for this
    # exact (order_id, file_sha256). (sha256 of the PDF is computed by the
    # service; we mirror it here so the unique key collides.)
    import hashlib
    file_sha = hashlib.sha256(open(pdf, "rb").read()).hexdigest()
    winner = ReceiptCheck(
        order_id=order.id, provider_id=provider.id, trader_user_id=trader.id,
        trigger=ReceiptCheckTrigger.AUTO.value, status=ReceiptCheckStatus.PENDING,
        file_path=pdf, file_sha256=file_sha, price_usdt=PRICE,
        charged=True, refunded=False,
    )
    session.add(winner)
    await session.flush()
    winner_id = winner.id

    work_before = await _work(session, user_id=trader.id)

    with patch(
        "app.modules.receipt_checks.service.build_client_for_provider",
        return_value=_fake_client(clean=True),
    ):
        result = await ReceiptCheckService(session).run_check_for_order(
            order, trader, ReceiptCheckTrigger.MANUAL,
        )

    # The loser replays the winner row, not a fresh one.
    assert result.id == winner_id
    # NO money moved by the losing run.
    assert await _work(session, user_id=trader.id) == work_before
    assert not await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK)
    # Still exactly ONE live check row for the file (the index held).
    assert await _live_check_count(session, order.id, file_sha) == 1


# ── adversarial #2: a prior FAILED check does NOT block a re-check ───────


@pytest.mark.asyncio
async def test_prior_failed_check_does_not_block_recheck(session, tmp_path):
    """FAILED rows are excluded from the partial index, so a re-check of the
    same file is allowed and charges the trader once."""
    trader, provider, order, pdf = await _setup(session, tmp_path)

    import hashlib
    file_sha = hashlib.sha256(open(pdf, "rb").read()).hexdigest()
    failed = ReceiptCheck(
        order_id=order.id, provider_id=provider.id, trader_user_id=trader.id,
        trigger=ReceiptCheckTrigger.MANUAL.value, status=ReceiptCheckStatus.FAILED,
        file_path=pdf, file_sha256=file_sha, price_usdt=PRICE,
        charged=False, refunded=False, error_code="upstream_error",
    )
    session.add(failed)
    await session.flush()

    work_before = await _work(session, user_id=trader.id)

    with patch(
        "app.modules.receipt_checks.service.build_client_for_provider",
        return_value=_fake_client(clean=True),
    ):
        check = await ReceiptCheckService(session).run_check_for_order(
            order, trader, ReceiptCheckTrigger.MANUAL,
        )

    # A brand-new SUCCESS row was created (not the FAILED one replayed).
    assert check.id != failed.id
    assert check.status == ReceiptCheckStatus.SUCCESS
    assert check.charged is True
    # Charged exactly once for the re-check.
    assert await _work(session, user_id=trader.id) == work_before - PRICE
    legs = await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK)
    assert len(legs) == 1
    assert legs[0].reference_id == f"receipt_check:{check.id}"


# ── adversarial #3: sequential idempotency — run TWICE → charge once ─────


@pytest.mark.asyncio
async def test_sequential_duplicate_runs_charge_trader_exactly_once(session, tmp_path):
    """SEQUENTIAL idempotency (the SQLite-friendly proxy for concurrency): run
    the SAME check twice in a row and assert money moves EXACTLY ONCE.

    This drives the FINISHED-dedup replay path (`find_reusable_for_file` →
    create CACHED, price 0), the no-charge happy path. Run WITHOUT the partial
    unique index to exercise the branch where the CACHED audit row is actually
    written (no collision), so we can assert second.status == CACHED. The
    index-present branch — where the CACHED insert collides and the guard
    replays the existing winner — is covered by
    `test_cached_replay_with_index_returns_winner_no_500`. Either way the *money*
    invariant holds: a second run does NOT charge the trader again (a
    double-charge regression would show as a 2nd RECEIPT_CHECK leg / doubled
    debit)."""
    # with_index=False: exercise the no-collision branch where the CACHED audit
    # row is actually written (the index-present branch is a separate test).
    trader, provider, order, _pdf = await _setup(session, tmp_path, with_index=False)

    svc = ReceiptCheckService(session)
    with patch(
        "app.modules.receipt_checks.service.build_client_for_provider",
        return_value=_fake_client(clean=True),
    ):
        first = await svc.run_check_for_order(order, trader, ReceiptCheckTrigger.MANUAL)
        second = await svc.run_check_for_order(order, trader, ReceiptCheckTrigger.MANUAL)

    assert first.status == ReceiptCheckStatus.SUCCESS
    assert first.charged is True
    # Second is a free cached replay of the verdict.
    assert second.status == ReceiptCheckStatus.CACHED
    assert second.charged is False
    assert second.price_usdt == Decimal("0")
    assert second.is_clean == first.is_clean

    # Money moved EXACTLY ONCE total.
    assert await _work(session, user_id=trader.id) == TRADER_WORK_START - PRICE
    assert await _work(session, is_system=True) == PRICE
    legs = await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK)
    assert len(legs) == 1                                   # not 2 → no double-charge
    assert legs[0].reference_id == f"receipt_check:{first.id}"


# ── CACHED replay with the index present: guarded, returns the winner ────


@pytest.mark.asyncio
async def test_cached_replay_with_index_returns_winner_no_500(session, tmp_path):
    """With the real migration-054 partial index present (as on Postgres), a
    second manual click on an already-verified file takes the dedup-replay path,
    whose CACHED insert reuses the SAME (order_id, file_sha256) as the SUCCESS
    row and so collides with the index. The CACHED create is now wrapped in the
    same IntegrityError→replay guard as the PENDING claim, so instead of a 500
    the call returns the existing live winner and charges nothing.

    (Previously this surfaced an UNHANDLED IntegrityError — a 500 on the common
    "click Проверить чек again on a verified file" path. The guard flips that to
    a clean idempotent replay.)"""
    # WITH the real partial index present (as on Postgres).
    trader, provider, order, _pdf = await _setup(session, tmp_path, with_index=True)

    svc = ReceiptCheckService(session)
    with patch(
        "app.modules.receipt_checks.service.build_client_for_provider",
        return_value=_fake_client(clean=True),
    ):
        first = await svc.run_check_for_order(order, trader, ReceiptCheckTrigger.MANUAL)
        assert first.status == ReceiptCheckStatus.SUCCESS
        # Second click → dedup hits the SUCCESS row → CACHED insert collides with
        # the index → guard catches it → returns the existing winner, no 500.
        second = await svc.run_check_for_order(order, trader, ReceiptCheckTrigger.MANUAL)

    # Replayed the live winner (the SUCCESS row), not a raised 500.
    assert second.id == first.id
    assert second.status == ReceiptCheckStatus.SUCCESS
    # Trader charged EXACTLY once across both clicks.
    assert await _work(session, user_id=trader.id) == TRADER_WORK_START - PRICE
    assert await _work(session, is_system=True) == PRICE
    legs = await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK)
    assert len(legs) == 1


# ── adversarial #4: refundable provider error → charge then refund nets 0 ─


@pytest.mark.asyncio
async def test_refundable_provider_error_nets_zero_charge(session, tmp_path):
    """When the provider tells us it didn't bill (refundable error), the WORK
    charge is reversed in the SAME run: one RECEIPT_CHECK leg + one
    RECEIPT_CHECK_REFUND leg, trader WORK back to start."""
    trader, provider, order, _pdf = await _setup(session, tmp_path)

    with patch(
        "app.modules.receipt_checks.service.build_client_for_provider",
        return_value=_fake_client(error_code="upstream_error", refundable=True),
    ):
        check = await ReceiptCheckService(session).run_check_for_order(
            order, trader, ReceiptCheckTrigger.MANUAL,
        )

    assert check.status == ReceiptCheckStatus.FAILED
    assert check.charged is True and check.refunded is True
    # Net zero: charged then refunded.
    assert await _work(session, user_id=trader.id) == TRADER_WORK_START
    assert await _work(session, is_system=True) == Decimal("0")
    assert len(await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK)) == 1
    assert len(await _ledger_legs(session, LedgerReferenceType.RECEIPT_CHECK_REFUND)) == 1


# ── sanity: the manually-created partial index actually rejects the dup ──


@pytest.mark.asyncio
async def test_partial_index_rejects_duplicate_live_but_allows_failed(session, tmp_path):
    """Direct DB-level proof the SQLite partial index matches the PG migration:
    a 2nd LIVE (pending) row for the same key is rejected; a FAILED dup is not."""
    from sqlalchemy.exc import IntegrityError

    await session.execute(text(_INDEX_SQL))
    trader, provider, order, pdf = await _setup(session, tmp_path, with_index=False)
    sha = "deadbeef" * 8

    async with session.begin_nested():
        session.add(ReceiptCheck(
            order_id=order.id, provider_id=provider.id, trader_user_id=trader.id,
            trigger="manual", status=ReceiptCheckStatus.PENDING, file_path=pdf,
            file_sha256=sha, price_usdt=PRICE, charged=False, refunded=False,
        ))

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(ReceiptCheck(
                order_id=order.id, provider_id=provider.id, trader_user_id=trader.id,
                trigger="manual", status=ReceiptCheckStatus.PENDING, file_path=pdf,
                file_sha256=sha, price_usdt=PRICE, charged=False, refunded=False,
            ))

    # FAILED dup for the same key is allowed (excluded from the partial index).
    async with session.begin_nested():
        session.add(ReceiptCheck(
            order_id=order.id, provider_id=provider.id, trader_user_id=trader.id,
            trigger="manual", status=ReceiptCheckStatus.FAILED, file_path=pdf,
            file_sha256=sha, price_usdt=Decimal("0"), charged=False, refunded=False,
        ))

    assert await _live_check_count(session, order.id, sha) == 1
