from decimal import Decimal
import pytest
from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.requisites import RequisiteStatus
from app.modules.requisites.models import Requisite
from app.modules.requisites.priority_service import PriorityService
from app.modules.traders.models import Trader


async def _mk_req(session, uid, weight, *, status=RequisiteStatus.ENABLED,
                  currency=Currency.RUB, method=PaymentMethod.SBP):
    r = Requisite(trader_id=uid, bank_name="b", account_number="1", account_holder="h",
                  payment_method=method, status=status, currency=currency,
                  is_active=True, is_archived=False, trader_priority=weight, priority_score=Decimal("100"))
    session.add(r)
    await session.flush()
    return r


@pytest.mark.asyncio
async def test_recompute_group_redistributes(session):
    session.add(Trader(user_id=1, priority_bonus_percent=Decimal("0")))
    a = await _mk_req(session, 1, 2)
    b = await _mk_req(session, 1, 1)
    c = await _mk_req(session, 1, 1)
    await PriorityService(session).recompute_group(1, Currency.RUB, PaymentMethod.SBP)
    assert (a.priority_score, b.priority_score, c.priority_score) == (
        Decimal("150.0000"), Decimal("75.0000"), Decimal("75.0000"))


@pytest.mark.asyncio
async def test_recompute_excludes_disabled_and_admin_bonus(session):
    session.add(Trader(user_id=1, priority_bonus_percent=Decimal("50")))
    a = await _mk_req(session, 1, 1)
    b = await _mk_req(session, 1, 1)
    disabled = await _mk_req(session, 1, 3, status=RequisiteStatus.DISABLED)
    await PriorityService(session).recompute_group(1, Currency.RUB, PaymentMethod.SBP)
    # base 150, N=2 (disabled excluded), Σ=2 → each 150; disabled untouched (still 100)
    assert a.priority_score == Decimal("150.0000") and b.priority_score == Decimal("150.0000")
    assert disabled.priority_score == Decimal("100.0000")


@pytest.mark.asyncio
async def test_recompute_all_for_trader_covers_each_group(session):
    session.add(Trader(user_id=1, priority_bonus_percent=Decimal("0")))
    sbp = await _mk_req(session, 1, 1, method=PaymentMethod.SBP)
    card = await _mk_req(session, 1, 1, method=PaymentMethod.CARD)
    await PriorityService(session).recompute_all_for_trader(1)
    assert sbp.priority_score == Decimal("100.0000") and card.priority_score == Decimal("100.0000")


@pytest.mark.asyncio
async def test_admin_percent_change_recomputes(session):
    from app.modules.traders.service import TraderService
    from app.modules.traders.schemas.admin import TraderUpdateAdmin
    t = Trader(user_id=1, priority_bonus_percent=Decimal("0"))
    session.add(t); await session.flush()
    r = await _mk_req(session, 1, 1)  # base 100 → score 100 after recompute
    await TraderService(session).update_trader_admin(t.id, TraderUpdateAdmin(priority_bonus_percent=Decimal("100")))
    await session.refresh(r)
    assert r.priority_score == Decimal("200.0000")   # base 200, N=1, Σ=1
