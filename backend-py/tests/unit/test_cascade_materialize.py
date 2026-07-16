"""Integration tests for the cascade "winner materialization" path.

`_persist_attempt` writes one CascadeOrderAttempt row per provider call;
`_materialize_winner` follows up on the winning attempt by creating a
one-shot Requisite pinned to the provider's virtual trader, linking it
back to the attempt, and computing per-call financial fields
(provider_fee_usdt, our_profit_usdt, provider_rate).

These are exercised end-to-end inside `try_cascade`-based tests in
`test_cascading_service.py`, but those tests only spot-check
`result.requisite.source == CASCADE`. The DB-level details (the
trader binding, the snapshot, the rate fallback) need their own
focused coverage so a future refactor of the attempt/requisite model
breaks tests instead of silently misrouting money.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict

import pytest

from app.common.enums.cascading import (
    CascadeAttemptStatus,
    CascadeRateSource,
    RequisiteSource,
)
from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.requisites import RequisiteStatus
from app.common.types import utcnow
from app.modules.cascading.integrations.base import ProviderRequisiteResponse
from app.modules.cascading.models import CascadeOrderAttempt, CascadeProvider
from app.modules.cascading.service import CascadingService, _RawAttemptOutcome
from app.modules.requisites.models import Requisite
from app.modules.users.models import User


# ─── fixture helpers (mirrors test_cascading_service.py shape) ─────────


async def _mk_provider(
    session,
    code: str = "matp",
    *,
    fees: Dict[str, float] | None = None,
    rate_source: CascadeRateSource = CascadeRateSource.PROVIDER,
    rate_config_id: int | None = None,
) -> CascadeProvider:
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
        adapter_type="mock",
        is_active=True,
        base_url="https://example.com",
        virtual_user_id=virtual_user.id,
        virtual_trader_id=trader.id,
        rates={"RUB": 95.0},
        fees=fees if fees is not None else {"sbp": 1.0},
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


def _response(
    *,
    external_order_id: str = "EXT-1",
    payment_method: PaymentMethod = PaymentMethod.SBP,
    amount_fiat: Decimal = Decimal("1000"),
    provider_rate: Decimal | None = Decimal("96.0"),
    payment_option_code: str | None = None,
) -> ProviderRequisiteResponse:
    from datetime import timedelta
    return ProviderRequisiteResponse(
        external_order_id=external_order_id,
        bank_name="SBER",
        account_number="4111222233334444",
        account_holder="Ivan Ivanov",
        payment_method=payment_method,
        payment_option_code=payment_option_code,
        amount_fiat=amount_fiat,
        expires_at=utcnow() + timedelta(minutes=10),
        raw={"event": "issued", "public_id": external_order_id},
        provider_rate=provider_rate,
    )


def _outcome(
    *,
    provider_id: int,
    response: ProviderRequisiteResponse,
    idempotency_key: str = "idem-mat-1",
    status: CascadeAttemptStatus = CascadeAttemptStatus.WON,
) -> _RawAttemptOutcome:
    now = utcnow()
    return _RawAttemptOutcome(
        provider_id=provider_id,
        started_at=now,
        finished_at=now,
        latency_ms=42,
        status=status,
        idempotency_key=idempotency_key,
        response=response,
    )


def _order_data(amount_usdt: Decimal = Decimal("10.5263")) -> Dict[str, Any]:
    return {
        "amount": Decimal("1000"),
        "amount_usdt": amount_usdt,
        "exchange_rate": Decimal("95.0"),
        "fee_usdt": Decimal("0.2632"),
        "currency": Currency.RUB,
        "payment_method": PaymentMethod.SBP,
        "payment_option_id": None,
        "payment_option_code": None,
    }


# ─── _persist_attempt ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_attempt_snapshots_response_into_jsonb(session):
    """The attempt row must carry a full snapshot of the requisite the
    provider issued. Operators rely on this for disputes / debug long
    after the live Requisite row has been archived."""
    provider = await _mk_provider(session, "att1")
    response = _response(external_order_id="SNAP-1")

    service = CascadingService(session)
    attempt = await service._persist_attempt(
        provider=provider,
        group=None,
        order_id=None,
        outcome=_outcome(provider_id=provider.id, response=response, idempotency_key="i-snap"),
    )

    assert attempt.id is not None
    assert attempt.external_order_id == "SNAP-1"
    assert attempt.status == CascadeAttemptStatus.WON
    assert attempt.latency_ms == 42
    snap = attempt.requisite_snapshot
    assert snap is not None
    assert snap["bank_name"] == "SBER"
    assert snap["account_number"] == "4111222233334444"
    assert snap["account_holder"] == "Ivan Ivanov"
    assert snap["payment_method"] == PaymentMethod.SBP.value
    assert snap["amount_fiat"] == "1000"
    assert "expires_at" in snap
    assert snap["raw"]["event"] == "issued"


@pytest.mark.asyncio
async def test_persist_attempt_refusal_has_no_snapshot(session):
    """Refused attempts (no_capacity / bad_request) carry no response;
    the snapshot column must stay NULL so admins can filter them out
    easily in the debug UI."""
    provider = await _mk_provider(session, "att2")

    service = CascadingService(session)
    outcome = _RawAttemptOutcome(
        provider_id=provider.id,
        started_at=utcnow(),
        finished_at=utcnow(),
        latency_ms=15,
        status=CascadeAttemptStatus.REFUSED,
        idempotency_key="i-ref",
        refusal_reason="no_capacity",
        response=None,
    )
    attempt = await service._persist_attempt(
        provider=provider, group=None, order_id=None, outcome=outcome
    )

    assert attempt.status == CascadeAttemptStatus.REFUSED
    assert attempt.refusal_reason == "no_capacity"
    assert attempt.external_order_id is None
    assert attempt.requisite_snapshot is None
    assert attempt.provider_rate is None


@pytest.mark.asyncio
async def test_persist_attempt_records_error_outcome(session):
    """Error path (adapter raised / network timeout) — capture
    error_code+message so circuit-breaker math has structured input."""
    provider = await _mk_provider(session, "att3")

    service = CascadingService(session)
    outcome = _RawAttemptOutcome(
        provider_id=provider.id,
        started_at=utcnow(),
        finished_at=utcnow(),
        latency_ms=5000,
        status=CascadeAttemptStatus.TIMEOUT,
        idempotency_key="i-err",
        error_code="timeout",
        error_message="adapter timed out after 5000ms",
    )
    attempt = await service._persist_attempt(
        provider=provider, group=None, order_id=None, outcome=outcome
    )

    assert attempt.status == CascadeAttemptStatus.TIMEOUT
    assert attempt.error_code == "timeout"
    assert "5000ms" in attempt.error_message


# ─── _materialize_winner ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_materialize_winner_creates_requisite_pinned_to_virtual_trader(session):
    """Critical: the new Requisite MUST belong to the provider's virtual
    user. The pooling/order layer routes incoming payins by requisite
    trader_id, so a wrong binding here means real traders see the
    cascade requisite as if it were theirs."""
    provider = await _mk_provider(session, "wm1")
    response = _response()
    outcome = _outcome(provider_id=provider.id, response=response)

    service = CascadingService(session)
    attempt = await service._persist_attempt(
        provider=provider, group=None, order_id=None, outcome=outcome
    )
    result = await service._materialize_winner(
        provider=provider,
        outcome=outcome,
        order_data=_order_data(),
        attempt=attempt,
    )

    assert result.success is True
    assert result.requisite is not None
    assert result.requisite.trader_id == provider.virtual_user_id
    assert result.requisite.source == RequisiteSource.CASCADE
    assert result.requisite.nickname == f"cascade:{provider.code}"
    assert result.requisite.payment_method == PaymentMethod.SBP
    assert result.requisite.account_number == "4111222233334444"
    assert result.requisite.account_holder == "Ivan Ivanov"
    assert result.requisite.status == RequisiteStatus.ENABLED
    assert result.requisite.is_active is True
    assert result.requisite.is_archived is False

    # Attempt got linked to the new requisite.
    assert attempt.requisite_id == result.requisite.id


@pytest.mark.asyncio
async def test_materialize_winner_persists_requisite_in_db(session):
    """Belt-and-suspenders: not just an in-memory object but a real DB
    row. A future change that forgets to `session.add(requisite)` would
    pass the relationship check but fail this query."""
    provider = await _mk_provider(session, "wm2")
    response = _response(external_order_id="DB-1")
    outcome = _outcome(provider_id=provider.id, response=response, idempotency_key="i-db")

    service = CascadingService(session)
    attempt = await service._persist_attempt(
        provider=provider, group=None, order_id=None, outcome=outcome
    )
    result = await service._materialize_winner(
        provider=provider,
        outcome=outcome,
        order_data=_order_data(),
        attempt=attempt,
    )

    fresh = await session.get(Requisite, result.requisite.id)
    assert fresh is not None
    assert fresh.trader_id == provider.virtual_user_id
    assert fresh.source == RequisiteSource.CASCADE


@pytest.mark.asyncio
async def test_materialize_winner_computes_provider_fee_and_profit(session):
    """provider.fees['sbp']=1.0%, merchant fee=0.2632 USDT, amount_usdt=10.5263:
    provider_fee = 10.5263 * 1% = 0.1053
    our_profit  = 0.2632 - 0.1053 = 0.1579
    These land on the Attempt and are used by OrderService when stamping
    the Order's profit_usdt."""
    provider = await _mk_provider(session, "wm3", fees={"sbp": 1.0})
    response = _response()
    outcome = _outcome(provider_id=provider.id, response=response, idempotency_key="i-fee")

    service = CascadingService(session)
    attempt = await service._persist_attempt(
        provider=provider, group=None, order_id=None, outcome=outcome
    )
    result = await service._materialize_winner(
        provider=provider,
        outcome=outcome,
        order_data=_order_data(),
        attempt=attempt,
    )

    assert result.provider_fee_usdt == Decimal("0.1053")
    assert result.our_profit_usdt == Decimal("0.1579")
    assert attempt.provider_fee_usdt == Decimal("0.1053")
    assert attempt.our_profit_usdt == Decimal("0.1579")


@pytest.mark.asyncio
async def test_materialize_winner_uses_provider_rate_in_provider_mode(session):
    """rate_source=PROVIDER (default): trust whatever the adapter parsed
    out of the response (rate_with_commission, etc). Attempt's
    provider_rate must equal response.provider_rate."""
    provider = await _mk_provider(session, "wm4")
    response = _response(provider_rate=Decimal("97.50"))
    outcome = _outcome(provider_id=provider.id, response=response, idempotency_key="i-rate-p")

    service = CascadingService(session)
    attempt = await service._persist_attempt(
        provider=provider, group=None, order_id=None, outcome=outcome
    )
    await service._materialize_winner(
        provider=provider,
        outcome=outcome,
        order_data=_order_data(),
        attempt=attempt,
    )
    assert attempt.provider_rate == Decimal("97.50")


@pytest.mark.asyncio
async def test_materialize_winner_falls_back_to_fiat_div_usdt_when_no_provider_rate(
    session,
):
    """Some adapters (LegacyCrypto) don't carry an explicit
    rate_with_commission — the response leaves provider_rate=None and
    only payload.rub_amount / payload.usdt_amount are populated. The
    service must derive a rate from order_data.amount/amount_usdt so
    the ledger has a non-null exchange rate to stamp."""
    provider = await _mk_provider(session, "wm5")
    response = _response(provider_rate=None)  # no explicit rate
    outcome = _outcome(provider_id=provider.id, response=response, idempotency_key="i-rate-fb")

    service = CascadingService(session)
    attempt = await service._persist_attempt(
        provider=provider, group=None, order_id=None, outcome=outcome
    )
    await service._materialize_winner(
        provider=provider,
        outcome=outcome,
        order_data=_order_data(amount_usdt=Decimal("10.5263")),
        attempt=attempt,
    )
    # 1000 / 10.5263 ≈ 94.9998… → quantized to 4 dp.
    assert attempt.provider_rate is not None
    assert abs(attempt.provider_rate - Decimal("95.0")) < Decimal("0.01")
