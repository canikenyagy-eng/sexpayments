import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency
from app.common.enums.users import UserRole
from app.core.exceptions import NotFoundException, ValidationException
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.teamleaders.models import TeamleadLink
from app.modules.teamleaders.schemas import TeamleadLinkCreate, TeamleadLinkUpdate
from app.modules.teamleaders.service import TeamleaderService
from app.modules.users.models import User


@pytest.fixture
def mock_session():
    session = MagicMock()
    
    class AsyncContextManagerMock:
        async def __aenter__(self):
            return session
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
            
    session.begin.return_value = AsyncContextManagerMock()
    session.get = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def service(mock_session):
    svc = TeamleaderService(mock_session)
    svc.repository = AsyncMock()
    svc.repository.get_by_teamlead_and_entity = AsyncMock(return_value=None)
    svc.finance_service = AsyncMock()
    svc.audit_log = AsyncMock()
    return svc


@pytest.fixture
def mock_teamlead():
    user = MagicMock(spec=User)
    user.id = 1
    user.role = UserRole.TEAMLEAD
    return user


@pytest.fixture
def mock_merchant():
    merchant = MagicMock(spec=Merchant)
    merchant.id = 10
    return merchant


@pytest.fixture
def mock_trader():
    user = MagicMock(spec=User)
    user.id = 20
    user.role = UserRole.TRADER
    return user


@pytest.mark.asyncio
async def test_create_link_merchant_success(service, mock_session, mock_teamlead, mock_merchant):
    # Setup session.get side effect
    async def mock_get(model, id):
        if model == User and id == 1:
            return mock_teamlead
        elif model == Merchant and id == 10:
            return mock_merchant
        return None
    mock_session.get.side_effect = mock_get

    mock_link = MagicMock(spec=TeamleadLink)
    service.repository.create.return_value = mock_link

    data = TeamleadLinkCreate(
        teamlead_id=1,
        linked_entity_type=UserRole.MERCHANT,
        linked_entity_id=10,
        fee_percent=Decimal("1.50")
    )

    result = await service.create_link(data, admin_user_id=42)

    assert result == mock_link
    service.repository.create.assert_called_once()
    service.audit_log.assert_called_once_with(
        action="create_teamlead_link",
        entity_type="teamlead_link",
        entity_id=mock_link.id,
        user_id=42,
        new_values=data.model_dump(),
    )


@pytest.mark.asyncio
async def test_create_link_trader_success(service, mock_session, mock_teamlead, mock_trader):
    async def mock_get(model, id):
        if model == User and id == 1:
            return mock_teamlead
        elif model == User and id == 20:
            return mock_trader
        return None
    mock_session.get.side_effect = mock_get

    mock_link = MagicMock(spec=TeamleadLink)
    service.repository.create.return_value = mock_link

    data = TeamleadLinkCreate(
        teamlead_id=1,
        linked_entity_type=UserRole.TRADER,
        linked_entity_id=20,
        fee_percent=Decimal("0.50")
    )

    result = await service.create_link(data)

    assert result == mock_link
    service.repository.create.assert_called_once()


@pytest.mark.asyncio
async def test_create_link_invalid_teamlead(service, mock_session):
    mock_session.get.return_value = None  # Teamlead not found

    data = TeamleadLinkCreate(
        teamlead_id=999,
        linked_entity_type=UserRole.MERCHANT,
        linked_entity_id=10
    )

    with pytest.raises(ValidationException, match="User 999 is not a valid teamlead"):
        await service.create_link(data)


@pytest.mark.asyncio
async def test_create_link_invalid_merchant(service, mock_session, mock_teamlead):
    async def mock_get(model, id):
        if model == User and id == 1:
            return mock_teamlead
        return None  # Merchant not found
    mock_session.get.side_effect = mock_get

    data = TeamleadLinkCreate(
        teamlead_id=1,
        linked_entity_type=UserRole.MERCHANT,
        linked_entity_id=999
    )

    with pytest.raises(NotFoundException, match="Merchant 999 not found"):
        await service.create_link(data)


@pytest.mark.asyncio
async def test_update_link_success(service):
    mock_link = MagicMock(spec=TeamleadLink)
    service.repository.get.return_value = mock_link
    service.repository.update.return_value = mock_link

    data = TeamleadLinkUpdate(fee_percent=Decimal("2.00"))
    result = await service.update_link(1, data, admin_user_id=42)

    assert result == mock_link
    service.repository.update.assert_called_once_with(1, {"fee_percent": Decimal("2.00")})
    
    service.audit_log.assert_called_once()
    call_args = service.audit_log.call_args[1]
    assert call_args["action"] == "update_teamlead_link"
    assert call_args["entity_id"] == 1
    assert call_args["user_id"] == 42
    assert call_args["new_values"]["fee_percent"] == Decimal("2.00")


@pytest.mark.asyncio
async def test_update_link_not_found(service):
    service.repository.get.return_value = None

    data = TeamleadLinkUpdate(fee_percent=Decimal("2.00"))
    with pytest.raises(NotFoundException, match="TeamleadLink 1 not found"):
        await service.update_link(1, data)


@pytest.mark.asyncio
async def test_calculate_and_pay_rewards_success(service, mock_session, mock_teamlead):
    # Setup order
    order = MagicMock(spec=Order)
    order.id = 100
    order.merchant_id = 10
    order.trader_id = 20
    order.amount_usdt = Decimal("1000.00")

    # Setup links
    merchant_link = MagicMock(spec=TeamleadLink)
    merchant_link.fee_percent = Decimal("1.00")
    merchant_link.teamlead_id = 1

    trader_link = MagicMock(spec=TeamleadLink)
    trader_link.fee_percent = Decimal("0.50")
    trader_link.teamlead_id = 2

    service.repository.get_active_by_merchant.return_value = [merchant_link]
    service.repository.get_active_by_trader.return_value = [trader_link]

    # Mock _pay_reward internally or mock its dependencies
    # We will mock the dependencies of _pay_reward
    mock_session.get.return_value = mock_teamlead
    
    mock_teamlead_balance = MagicMock(spec=Balance)
    mock_teamlead_balance.id = 50
    service.finance_service.get_or_create_user_balance.return_value = mock_teamlead_balance
    
    mock_system_balance = MagicMock(spec=Balance)
    mock_system_balance.id = 99
    service.finance_service.get_or_create_system_balance.return_value = mock_system_balance

    await service.calculate_and_pay_rewards(order)

    # 1% of 1000 = 10.00, 0.5% of 1000 = 5.00
    assert service.finance_service.transfer.call_count == 2
    
    # Check first call (Merchant)
    call1_args = service.finance_service.transfer.call_args_list[0][1]
    assert call1_args["amount"] == Decimal("10.0000")
    assert call1_args["from_balance_id"] == 99
    assert call1_args["to_balance_id"] == 50
    assert call1_args["reference_type"] == LedgerReferenceType.TEAMLEAD_REWARD

    # Check second call (Trader)
    call2_args = service.finance_service.transfer.call_args_list[1][1]
    assert call2_args["amount"] == Decimal("5.0000")


@pytest.mark.asyncio
async def test_calculate_and_pay_rewards_no_usdt(service):
    order = MagicMock(spec=Order)
    order.amount_usdt = None

    await service.calculate_and_pay_rewards(order)

    service.repository.get_active_by_merchant.assert_not_called()




@pytest.mark.asyncio
async def test_get_teamlead_reward_history_success(service, mock_session, mock_teamlead):
    mock_session.get.return_value = mock_teamlead
    
    mock_balance = MagicMock(spec=Balance)
    mock_balance.id = 50
    service.finance_service.get_or_create_user_balance.return_value = mock_balance

    mock_entry = MagicMock(spec=LedgerEntry)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_entry]
    mock_session.execute.return_value = mock_result

    result = await service.get_teamlead_reward_history(1)

    assert result == [mock_entry]
    mock_session.get.assert_called_once_with(User, 1)
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_teamlead_reward_history_not_found(service, mock_session):
    mock_session.get.return_value = None

    with pytest.raises(NotFoundException, match="Teamlead not found"):
        await service.get_teamlead_reward_history(1)


# ────────────────────────────────────────────────────────────────
# get_teamlead_stats
# ────────────────────────────────────────────────────────────────

def _make_entry(amount: str, ref_id: str):
    entry = MagicMock(spec=LedgerEntry)
    entry.amount = Decimal(amount)
    entry.currency = Currency.USDT
    entry.reference_id = ref_id
    return entry


@pytest.mark.asyncio
async def test_get_teamlead_stats_nets_reversals_and_counts_orders(
    service, mock_session, mock_teamlead
):
    mock_session.get.return_value = mock_teamlead

    mock_balance = MagicMock(spec=Balance)
    mock_balance.id = 50
    service.finance_service.get_or_create_user_balance.return_value = mock_balance

    # 3 entries: two distinct orders + one reversal of the first
    entries = [
        _make_entry("10.00", "100"),
        _make_entry("5.00", "200_trader"),
        _make_entry("10.00", "100_reversal"),
    ]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = entries
    mock_session.execute.return_value = mock_result

    # 2 links, one of them inactive
    link_active = MagicMock(spec=TeamleadLink)
    link_active.is_active = True
    link_inactive = MagicMock(spec=TeamleadLink)
    link_inactive.is_active = False
    service.repository.get_by_teamlead.return_value = [link_active, link_inactive]

    result = await service.get_teamlead_stats(1)

    assert result["total_earned_usdt"] == Decimal("5.00")
    assert result["orders_count"] == 2
    assert result["active_links_count"] == 1


@pytest.mark.asyncio
async def test_get_teamlead_stats_no_rewards(service, mock_session, mock_teamlead):
    mock_session.get.return_value = mock_teamlead

    mock_balance = MagicMock(spec=Balance)
    mock_balance.id = 50
    service.finance_service.get_or_create_user_balance.return_value = mock_balance

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result

    service.repository.get_by_teamlead.return_value = []

    result = await service.get_teamlead_stats(1)

    assert result["total_earned_usdt"] == Decimal("0")
    assert result["orders_count"] == 0
    assert result["active_links_count"] == 0


# ────────────────────────────────────────────────────────────────
# get_teamlead_links_enriched
# ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_teamlead_links_enriched_empty(service):
    service.repository.get_by_teamlead.return_value = []
    result = await service.get_teamlead_links_enriched(1)
    assert result == []


@pytest.mark.asyncio
async def test_get_teamlead_links_enriched_merchant_and_trader(
    service, mock_session, mock_teamlead
):
    mock_session.get.return_value = mock_teamlead

    # Two links: one merchant, one trader
    merchant_link = MagicMock(spec=TeamleadLink)
    merchant_link.id = 1
    merchant_link.linked_entity_type = UserRole.MERCHANT
    merchant_link.linked_entity_id = 10
    merchant_link.fee_percent = Decimal("1.00")
    merchant_link.is_active = True
    merchant_link.created_at = None

    trader_link = MagicMock(spec=TeamleadLink)
    trader_link.id = 2
    trader_link.linked_entity_type = UserRole.TRADER
    trader_link.linked_entity_id = 20
    trader_link.fee_percent = Decimal("0.50")
    trader_link.is_active = True
    trader_link.created_at = None

    service.repository.get_by_teamlead.return_value = [merchant_link, trader_link]

    # Balance used by _get_teamlead_reward_entries
    mock_balance = MagicMock(spec=Balance)
    mock_balance.id = 50
    service.finance_service.get_or_create_user_balance.return_value = mock_balance

    # Use two separate orders so each reward is attributed only to one link
    order_merchant_only = MagicMock(spec=Order)
    order_merchant_only.id = 100
    order_merchant_only.merchant_id = 10
    order_merchant_only.trader_id = 999  # not linked to our teamlead

    order_trader_only = MagicMock(spec=Order)
    order_trader_only.id = 200
    order_trader_only.merchant_id = 888  # not linked to our teamlead
    order_trader_only.trader_id = 20

    entries = [
        _make_entry("10.00", "100"),    # → merchant link (order 100)
        _make_entry("5.00", "200"),     # → trader link (order 200)
    ]

    # mock_session.execute is called four times here (in order):
    #   1) fetch merchant logins (all)
    #   2) fetch trader logins (all)
    #   3) fetch ledger entries (scalars)
    #   4) fetch orders (scalars)
    results_iter = iter([
        _row_result([(10, "merchant_login")]),
        _row_result([(20, "trader_login")]),
        _scalar_result(entries),
        _scalar_result([order_merchant_only, order_trader_only]),
    ])
    mock_session.execute.side_effect = lambda *a, **kw: next(results_iter)

    result = await service.get_teamlead_links_enriched(1)

    assert len(result) == 2
    merchant_info = next(r for r in result if r["linked_entity_type"] == UserRole.MERCHANT)
    trader_info = next(r for r in result if r["linked_entity_type"] == UserRole.TRADER)

    assert merchant_info["login"] == "merchant_login"
    assert merchant_info["income_usdt"] == Decimal("10.00")
    assert trader_info["login"] == "trader_login"
    assert trader_info["income_usdt"] == Decimal("5.00")


def _scalar_result(items):
    r = MagicMock()
    r.scalars.return_value.all.return_value = items
    return r


def _row_result(rows):
    r = MagicMock()
    r.all.return_value = rows
    return r


# ────────────────────────────────────────────────────────────────
# create_link: duplicate detection
# ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_link_rejects_duplicate(
    service, mock_session, mock_teamlead, mock_merchant
):
    async def mock_get(model, id):
        if model == User and id == 1:
            return mock_teamlead
        if model == Merchant and id == 10:
            return mock_merchant
        return None

    mock_session.get.side_effect = mock_get
    service.repository.get_by_teamlead_and_entity.return_value = MagicMock(spec=TeamleadLink)

    data = TeamleadLinkCreate(
        teamlead_id=1,
        linked_entity_type=UserRole.MERCHANT,
        linked_entity_id=10,
        fee_percent=Decimal("1.50"),
    )

    with pytest.raises(ValidationException, match="already exists"):
        await service.create_link(data)

    service.repository.create.assert_not_called()


# ────────────────────────────────────────────────────────────────
# delete_link
# ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_link_success(service):
    link = MagicMock(spec=TeamleadLink)
    link.teamlead_id = 1
    link.linked_entity_type = UserRole.MERCHANT
    link.linked_entity_id = 10
    link.fee_percent = Decimal("1.00")
    link.is_active = True
    service.repository.get.return_value = link
    service.repository.delete.return_value = True

    await service.delete_link(42, admin_user_id=7)

    service.repository.delete.assert_awaited_once_with(42)
    service.audit_log.assert_awaited_once()
    args = service.audit_log.call_args.kwargs
    assert args["action"] == "delete_teamlead_link"
    assert args["entity_id"] == 42
    assert args["user_id"] == 7
    assert args["old_values"]["teamlead_id"] == 1


@pytest.mark.asyncio
async def test_delete_link_not_found(service):
    service.repository.get.return_value = None

    with pytest.raises(NotFoundException, match="TeamleadLink 999 not found"):
        await service.delete_link(999)


# ────────────────────────────────────────────────────────────────
# reverse_rewards
# ────────────────────────────────────────────────────────────────

def _mk_payout_entry(*, ref_id, amount="10.00", system_id=99, teamlead_id=50):
    entry = MagicMock(spec=LedgerEntry)
    entry.amount = Decimal(amount)
    entry.currency = Currency.USDT
    entry.reference_id = ref_id
    entry.from_balance_id = system_id
    entry.to_balance_id = teamlead_id
    return entry


def _mk_reversal_entry(*, ref_id, amount="10.00", system_id=99, teamlead_id=50):
    entry = MagicMock(spec=LedgerEntry)
    entry.amount = Decimal(amount)
    entry.currency = Currency.USDT
    entry.reference_id = ref_id
    entry.from_balance_id = teamlead_id
    entry.to_balance_id = system_id
    return entry


@pytest.mark.asyncio
async def test_reverse_rewards_moves_balance_back_to_system(service, mock_session):
    order = MagicMock(spec=Order)
    order.id = 500

    entries = [
        _mk_payout_entry(ref_id="500", amount="10.00"),
        _mk_payout_entry(ref_id="500_trader", amount="5.00"),
    ]
    mock_session.execute.return_value = _scalar_result(entries)

    await service.reverse_rewards(order)

    assert service.finance_service.transfer.await_count == 2

    first_call = service.finance_service.transfer.call_args_list[0].kwargs
    assert first_call["amount"] == Decimal("10.00")
    assert first_call["reference_id"] == "500_reversal"
    assert first_call["from_balance_id"] == 50
    assert first_call["to_balance_id"] == 99
    assert first_call["reference_type"] == LedgerReferenceType.TEAMLEAD_REWARD

    second_call = service.finance_service.transfer.call_args_list[1].kwargs
    assert second_call["reference_id"] == "500_trader_reversal"


@pytest.mark.asyncio
async def test_reverse_rewards_is_idempotent(service, mock_session):
    order = MagicMock(spec=Order)
    order.id = 500

    entries = [
        _mk_payout_entry(ref_id="500", amount="10.00"),
        _mk_reversal_entry(ref_id="500_reversal", amount="10.00"),
    ]
    mock_session.execute.return_value = _scalar_result(entries)

    await service.reverse_rewards(order)

    service.finance_service.transfer.assert_not_awaited()


@pytest.mark.asyncio
async def test_reverse_rewards_skips_reversal_entries_from_iteration(
    service, mock_session
):
    order = MagicMock(spec=Order)
    order.id = 700

    entries = [
        _mk_payout_entry(ref_id="700", amount="8.00"),
        _mk_payout_entry(ref_id="700_recalc_1", amount="3.00"),
    ]
    mock_session.execute.return_value = _scalar_result(entries)

    await service.reverse_rewards(order)

    refs = [
        call.kwargs["reference_id"]
        for call in service.finance_service.transfer.call_args_list
    ]
    assert refs == ["700_reversal", "700_recalc_1_reversal"]


@pytest.mark.asyncio
async def test_reverse_rewards_does_not_process_unrelated_order_entries(
    service, mock_session
):
    """
    The DB query now uses `reference_id = '5' OR reference_id LIKE '5\_%'`
    instead of bare `LIKE '5%'`, so entries for orders 50, 51, 500 are excluded
    at the SQL level. This test ensures that even if the mock session returns
    only the correct entries (as the fixed query would), reverse_rewards only
    reverses those entries and does not accidentally touch anything else.
    """
    order = MagicMock(spec=Order)
    order.id = 5

    # Simulate what the fixed SQL returns: only entries for order 5
    entries = [
        _mk_payout_entry(ref_id="5", amount="10.00"),
        _mk_payout_entry(ref_id="5_trader", amount="4.00"),
    ]
    mock_session.execute.return_value = _scalar_result(entries)

    await service.reverse_rewards(order)

    assert service.finance_service.transfer.await_count == 2
    refs = [c.kwargs["reference_id"] for c in service.finance_service.transfer.call_args_list]
    assert "5_reversal" in refs
    assert "5_trader_reversal" in refs
    # Entries for orders 50, 500, 51 must not appear
    for ref in refs:
        assert ref.startswith("5_"), f"Unexpected reference_id: {ref}"


# ────────────────────────────────────────────────────────────────
# recalculate_rewards — used when an order amount changes mid-dispute.
# Should: (1) reverse every prior reward entry, (2) compute new
# rewards with a unique recalc suffix.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recalculate_rewards_reverses_then_pays_new(
    service, mock_session, mock_teamlead, mock_merchant, mock_trader
):
    order = MagicMock(spec=Order)
    order.id = 500
    order.merchant_id = 10
    order.trader_id = 20
    order.amount_usdt = Decimal("100.00")

    # Existing reward entries from prior calculation
    entries = [
        _mk_payout_entry(ref_id="500", amount="1.00"),
        _mk_payout_entry(ref_id="500_trader", amount="0.50"),
    ]
    mock_session.execute.return_value = _scalar_result(entries)

    # New links for the recalculation
    merchant_link = MagicMock(spec=TeamleadLink)
    merchant_link.fee_percent = Decimal("2.00")
    merchant_link.teamlead_id = 1
    trader_link = MagicMock(spec=TeamleadLink)
    trader_link.fee_percent = Decimal("0.75")
    trader_link.teamlead_id = 2
    service.repository.get_active_by_merchant.return_value = [merchant_link]
    service.repository.get_active_by_trader.return_value = [trader_link]

    mock_session.get.return_value = mock_teamlead
    teamlead_balance = MagicMock(spec=Balance); teamlead_balance.id = 50
    system_balance = MagicMock(spec=Balance); system_balance.id = 99
    service.finance_service.get_or_create_user_balance.return_value = teamlead_balance
    service.finance_service.get_or_create_system_balance.return_value = system_balance

    await service.recalculate_rewards(order)

    # 2 reversals (existing entries) + 2 new payouts (merchant + trader)
    assert service.finance_service.transfer.await_count == 4

    refs = [c.kwargs["reference_id"] for c in service.finance_service.transfer.call_args_list]
    # Reversals come first
    assert refs[0] == "500_reversal"
    assert refs[1] == "500_trader_reversal"
    # New payouts have `_recalc_<timestamp>` suffix
    assert refs[2].startswith("500_recalc_")
    assert refs[3].startswith("500_recalc_")
    # And the new amounts reflect the new fee percents
    new_payouts = service.finance_service.transfer.call_args_list[2:]
    assert new_payouts[0].kwargs["amount"] == Decimal("2.0000")  # 2% of 100
    assert new_payouts[1].kwargs["amount"] == Decimal("0.7500")  # 0.75% of 100


@pytest.mark.asyncio
async def test_recalculate_rewards_no_amount_just_reverses(service, mock_session):
    """If the new order has no amount_usdt, only reverse — don't try to compute."""
    order = MagicMock(spec=Order)
    order.id = 500
    order.merchant_id = 10
    order.trader_id = None
    order.amount_usdt = None  # missing

    entries = [_mk_payout_entry(ref_id="500", amount="1.00")]
    mock_session.execute.return_value = _scalar_result(entries)

    await service.recalculate_rewards(order)

    # Only the single reversal — no new payouts because amount_usdt is None.
    assert service.finance_service.transfer.await_count == 1
    refs = [c.kwargs["reference_id"] for c in service.finance_service.transfer.call_args_list]
    assert refs == ["500_reversal"]


# ────────────────────────────────────────────────────────────────
# list_admin_teamleads — admin user listing with USDT balances.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_admin_teamleads_enriches_with_balances(service, mock_session, mocker):
    from datetime import datetime as _dt

    teamlead_user = MagicMock(spec=User)
    teamlead_user.id = 7
    teamlead_user.username = "tl"
    teamlead_user.is_blocked = False
    teamlead_user.created_at = _dt(2026, 5, 1)

    fake_repo = MagicMock()
    fake_repo.get_users = AsyncMock(return_value=([teamlead_user], 1))
    fake_repo.get_usdt_balances_by_user_ids = AsyncMock(return_value={7: 12.5})
    mocker.patch("app.modules.users.repository.UserRepository", return_value=fake_repo)

    result = await service.list_admin_teamleads(skip=0, limit=10, search="tl")

    assert len(result) == 1
    item = result[0]
    assert item.id == 7
    assert item.username == "tl"
    assert item.is_active is True
    assert item.is_blocked is False
    assert item.balance_usdt == 12.5

    fake_repo.get_users.assert_awaited_once()
    # The role filter must be hard-coded to TEAMLEAD even if the caller doesn't pass one.
    kwargs = fake_repo.get_users.await_args.kwargs
    assert kwargs["role"] == UserRole.TEAMLEAD
    assert kwargs["search"] == "tl"


@pytest.mark.asyncio
async def test_list_admin_teamleads_empty(service, mocker):
    fake_repo = MagicMock()
    fake_repo.get_users = AsyncMock(return_value=([], 0))
    fake_repo.get_usdt_balances_by_user_ids = AsyncMock()
    mocker.patch("app.modules.users.repository.UserRepository", return_value=fake_repo)

    result = await service.list_admin_teamleads()

    assert result == []
    # Balance lookup must be skipped when there are no users.
    fake_repo.get_usdt_balances_by_user_ids.assert_not_called()


# ────────────────────────────────────────────────────────────────
# list_all_links — fetch all (paginated) or scoped by teamlead.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_all_links_no_filter_uses_get_all(service):
    expected = [MagicMock(spec=TeamleadLink), MagicMock(spec=TeamleadLink)]
    service.repository.get_all = AsyncMock(return_value=expected)
    service.repository.get_by_teamlead = AsyncMock()

    result = await service.list_all_links(skip=10, limit=25)

    assert result is expected
    service.repository.get_all.assert_awaited_once_with(skip=10, limit=25)
    service.repository.get_by_teamlead.assert_not_called()


@pytest.mark.asyncio
async def test_list_all_links_with_teamlead_filter_uses_get_by_teamlead(service):
    expected = [MagicMock(spec=TeamleadLink)]
    service.repository.get_by_teamlead = AsyncMock(return_value=expected)
    service.repository.get_all = AsyncMock()

    result = await service.list_all_links(teamlead_id=42)

    assert result is expected
    service.repository.get_by_teamlead.assert_awaited_once_with(42)
    service.repository.get_all.assert_not_called()


# ────────────────────────────────────────────────────────────────
# get_teamlead_links — pure delegate to repo.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_teamlead_links_passthrough(service):
    expected = [MagicMock(spec=TeamleadLink)]
    service.repository.get_by_teamlead = AsyncMock(return_value=expected)

    result = await service.get_teamlead_links(7)

    assert result is expected
    service.repository.get_by_teamlead.assert_awaited_once_with(7)
