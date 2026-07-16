from decimal import Decimal
import pytest
from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.requisites import RequisiteStatus
from app.modules.payments.models import PaymentOption
from app.modules.requisites.models import Requisite
from app.modules.requisites.schemas.admin import RequisiteUpdate
from app.modules.requisites.service import RequisiteService
from app.modules.traders.models import Trader


async def _enabled_req(session, uid, weight, *, currency=Currency.RUB,
                        method=PaymentMethod.SBP, payment_option_id=None):
    r = Requisite(trader_id=uid, bank_name="b", account_number="1", account_holder="h",
                  payment_method=method, status=RequisiteStatus.ENABLED,
                  currency=currency, is_active=True, is_archived=False,
                  payment_option_id=payment_option_id,
                  trader_priority=weight, priority_score=Decimal("100"))
    session.add(r); await session.flush(); return r


async def _payment_option(session, code, currency, methods=(PaymentMethod.SBP,)):
    opt = PaymentOption(code=code, name=code, supported_methods=[m.value for m in methods],
                         currency=currency, is_active=True)
    session.add(opt); await session.flush(); return opt


@pytest.mark.asyncio
async def test_delete_recomputes_group(session):
    # priority_bonus_percent=50 -> base=150, distinct from the dummy 100
    # constructed below, so this actually distinguishes "recompute ran" from
    # "recompute didn't run" (with bonus%=0 a singleton group's recomputed
    # score is also 100, which passes trivially even with no wiring at all).
    session.add(Trader(user_id=1, priority_bonus_percent=Decimal("50")))
    a = await _enabled_req(session, 1, 2)
    b = await _enabled_req(session, 1, 1)
    await RequisiteService(session).delete_requisite(a.id, user_id=1)
    await session.refresh(b)
    assert b.priority_score == Decimal("150.0000")   # group now {b}: N=1, Σ=1, base=150 → 150


@pytest.mark.asyncio
async def test_set_enabled_recomputes_group(session):
    session.add(Trader(user_id=1, priority_bonus_percent=Decimal("50")))
    a = await _enabled_req(session, 1, 1)
    b = Requisite(trader_id=1, bank_name="b", account_number="2", account_holder="h",
                  payment_method=PaymentMethod.SBP, status=RequisiteStatus.DISABLED,
                  currency=Currency.RUB, is_active=True, is_archived=False,
                  trader_priority=1, priority_score=Decimal("100"))
    session.add(b); await session.flush()
    # Enable b → group N 1→2, equal weights, base=150 → both 150
    await RequisiteService(session).set_enabled(b.id, True, user_id=1)
    await session.refresh(a); await session.refresh(b)
    assert a.priority_score == Decimal("150.0000") and b.priority_score == Decimal("150.0000")


@pytest.mark.asyncio
async def test_update_currency_swap_recomputes_old_group(session):
    """A payment_option_id swap silently changes currency (no "payment_method"
    key in the payload at all — the method derives currency from the payment
    option). The requisite's OLD (currency, method) group must still be
    recomputed so its remaining sibling's stale priority_score is corrected."""
    session.add(Trader(user_id=1, priority_bonus_percent=Decimal("50")))  # base=150
    opt_rub = await _payment_option(session, "rub_bank", Currency.RUB)
    opt_azn = await _payment_option(session, "azn_bank", Currency.AZN)

    moving = await _enabled_req(session, 1, 1, payment_option_id=opt_rub.id)
    sibling = await _enabled_req(session, 1, 1, payment_option_id=opt_rub.id)

    # Only payment_option_id is in the payload — payment_method is untouched.
    await RequisiteService(session).update_requisite(
        moving.id, RequisiteUpdate(payment_option_id=opt_azn.id), user_id=1
    )

    await session.refresh(sibling)
    # Old group (RUB, SBP) now has only `sibling` left: N=1, Σ=1, base=150 → 150.
    # Bug: old-group recompute only fired when "payment_method" was explicitly
    # in the payload, so `sibling` would incorrectly keep its stale 100.
    assert sibling.priority_score == Decimal("150.0000")

    moved = await session.get(Requisite, moving.id)
    assert moved.currency == Currency.AZN
    # New group (AZN, SBP) has only `moving`: N=1, Σ=1, base=150 → 150.
    assert moved.priority_score == Decimal("150.0000")


@pytest.mark.asyncio
async def test_update_currency_and_method_change_uses_old_currency_for_old_group(session):
    """When BOTH currency and payment_method change together, the OLD-group
    recompute must use the coordinates captured BEFORE the update, not the
    post-update currency paired with the pre-update method — otherwise it
    targets a group the requisite was never even a member of."""
    session.add(Trader(user_id=1, priority_bonus_percent=Decimal("50")))  # base=150
    opt_rub_sbp = await _payment_option(session, "rub_sbp_bank", Currency.RUB, (PaymentMethod.SBP,))
    opt_azn_card = await _payment_option(session, "azn_card_bank", Currency.AZN, (PaymentMethod.CARD,))

    moving = await _enabled_req(session, 1, 1, payment_option_id=opt_rub_sbp.id)
    sibling = await _enabled_req(session, 1, 1, payment_option_id=opt_rub_sbp.id)

    await RequisiteService(session).update_requisite(
        moving.id,
        RequisiteUpdate(payment_option_id=opt_azn_card.id, payment_method=PaymentMethod.CARD),
        user_id=1,
    )

    await session.refresh(sibling)
    # Old group (RUB, SBP) now has only `sibling` left → recomputed to 150.
    # Bug: old code called recompute_group(trader, requisite.currency=AZN, _old_method=SBP)
    # — the (AZN, SBP) group is empty, so `sibling` in the real old group (RUB, SBP)
    # would incorrectly keep its stale 100.
    assert sibling.priority_score == Decimal("150.0000")
