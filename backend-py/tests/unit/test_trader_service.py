import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.modules.traders.service import TraderService
from app.modules.traders.models import Trader, TraderGroup
from app.modules.merchants.models import Merchant
from app.common.enums.traders import TraderStatus
from app.modules.traders.schemas import TraderUpdateAdmin, TraderMethodConfig, TraderGroupCreate


class AsyncContextManagerMock:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        pass


@pytest.fixture
def mock_session():
    session = MagicMock()
    session.begin.return_value = AsyncContextManagerMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    session.refresh = AsyncMock()
    return session


@pytest.fixture
def mock_trader_repo():
    return AsyncMock()


@pytest.fixture
def mock_group_repo():
    return AsyncMock()


@pytest.fixture
def mock_merchant_repo():
    return AsyncMock()

@pytest.fixture
def service(mock_session, mock_trader_repo, mock_group_repo, mock_merchant_repo):
    svc = TraderService(mock_session)
    svc.trader_repo = mock_trader_repo
    svc.group_repo = mock_group_repo
    svc.merchant_repo = mock_merchant_repo
    svc.audit_log = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_get_or_create_trader_existing(service, mock_trader_repo):
    mock_trader = Trader(id=1, user_id=10)
    mock_trader_repo.get_by_user_id.return_value = mock_trader
    
    result = await service.get_or_create_trader(10)
    
    assert result.id == 1
    mock_trader_repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_get_or_create_trader_new(service, mock_trader_repo):
    mock_trader_repo.get_by_user_id.return_value = None
    mock_trader_repo.create.return_value = Trader(id=2, user_id=20)
    
    result = await service.get_or_create_trader(20)
    
    assert result.id == 2
    mock_trader_repo.create.assert_called_once_with({"user_id": 20})


@pytest.mark.asyncio
async def test_toggle_payin(service, mock_trader_repo):
    mock_trader = Trader(id=1, user_id=10, is_payin_active=False)
    mock_trader_repo.get_by_user_id.return_value = mock_trader
    mock_trader_repo.update.return_value = Trader(id=1, user_id=10, is_payin_active=True)

    result = await service.toggle_payin(10, True)

    assert result.is_payin_active is True
    mock_trader_repo.update.assert_called_once_with(1, {"is_payin_active": True})
    service.audit_log.assert_called_once_with(
        action="toggle_payin",
        entity_type="trader",
        entity_id=1,
        user_id=10,
        old_values={"is_payin_active": False},
        new_values={"is_payin_active": True},
    )


@pytest.mark.asyncio
async def test_toggle_payout(service, mock_trader_repo):
    mock_trader = Trader(id=1, user_id=10, is_payout_active=False)
    mock_trader_repo.get_by_user_id.return_value = mock_trader
    mock_trader_repo.update.return_value = Trader(id=1, user_id=10, is_payout_active=True)

    result = await service.toggle_payout(10, True)

    assert result.is_payout_active is True
    mock_trader_repo.update.assert_called_once_with(1, {"is_payout_active": True})
    service.audit_log.assert_called_once_with(
        action="toggle_payout",
        entity_type="trader",
        entity_id=1,
        user_id=10,
        old_values={"is_payout_active": False},
        new_values={"is_payout_active": True},
    )


@pytest.mark.asyncio
async def test_toggle_payout_blocked_trader_rejected(service, mock_trader_repo):
    """Blocked trader cannot enable payout — symmetric with toggle_payin."""
    blocked = Trader(id=1, user_id=10, is_payout_active=False, status=TraderStatus.BLOCKED)
    mock_trader_repo.get_by_user_id.return_value = blocked

    from app.core.exceptions import ValidationException
    with pytest.raises(ValidationException, match="blocked"):
        await service.toggle_payout(10, True)

    mock_trader_repo.update.assert_not_called()


@pytest.mark.asyncio
async def test_toggle_payout_blocked_trader_can_disable(service, mock_trader_repo):
    """Disabling payout on a blocked trader is allowed (only enable is blocked)."""
    blocked = Trader(id=1, user_id=10, is_payout_active=True, status=TraderStatus.BLOCKED)
    mock_trader_repo.get_by_user_id.return_value = blocked
    mock_trader_repo.update.return_value = Trader(id=1, user_id=10, is_payout_active=False, status=TraderStatus.BLOCKED)

    result = await service.toggle_payout(10, False)
    assert result.is_payout_active is False


@pytest.mark.asyncio
async def test_get_group_success(service, mock_group_repo):
    mock_group = TraderGroup(id=1, name="VIP")
    mock_group_repo.get.return_value = mock_group

    result = await service.get_group(1)

    assert result is mock_group
    mock_group_repo.get.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_get_group_not_found(service, mock_group_repo):
    from app.core.exceptions import NotFoundException
    mock_group_repo.get.return_value = None

    with pytest.raises(NotFoundException, match="Group 999 not found"):
        await service.get_group(999)


@pytest.mark.asyncio
async def test_get_traders_passthrough(service, mock_trader_repo):
    expected_pair = ([Trader(id=1, user_id=10)], 1)
    mock_trader_repo.get_traders.return_value = expected_pair

    traders, total = await service.get_traders(skip=0, limit=10, search="alice")

    assert (traders, total) == expected_pair
    mock_trader_repo.get_traders.assert_called_once_with(skip=0, limit=10, search="alice")


@pytest.mark.asyncio
async def test_get_trader_by_id_success(service, mock_trader_repo):
    mock_trader = Trader(id=42, user_id=10)
    mock_trader_repo.get.return_value = mock_trader

    result = await service.get_trader_by_id(42)

    assert result is mock_trader


@pytest.mark.asyncio
async def test_get_trader_by_id_not_found(service, mock_trader_repo):
    from app.core.exceptions import NotFoundException
    mock_trader_repo.get.return_value = None

    with pytest.raises(NotFoundException, match="Trader 99 not found"):
        await service.get_trader_by_id(99)


@pytest.mark.asyncio
async def test_get_groups_passthrough(service, mock_group_repo):
    groups = [TraderGroup(id=1, name="A"), TraderGroup(id=2, name="B")]
    mock_group_repo.get_all.return_value = groups

    result = await service.get_groups()
    assert result is groups


@pytest.mark.asyncio
async def test_update_trader_admin(service, mock_trader_repo, mock_session):
    mock_trader = Trader(id=1, user_id=10)
    mock_trader.method_configs = []
    mock_trader_repo.get.return_value = mock_trader
    mock_trader_repo.update.return_value = mock_trader
    
    from app.common.enums.payments import PaymentMethod
    
    update_data = TraderUpdateAdmin(
        status=TraderStatus.ENABLED,
        methods_config={PaymentMethod.CARD: TraderMethodConfig(fee=1.5, min_amount=100, max_amount=5000)}
    )
    
    await service.update_trader_admin(1, update_data, admin_user_id=42)
    
    # Verify update was called for status
    call_args = mock_trader_repo.update.call_args[0]
    assert call_args[0] == 1
    assert call_args[1]["status"] == TraderStatus.ENABLED
    
    # Verify audit log
    service.audit_log.assert_called_once()
    audit_args = service.audit_log.call_args[1]
    assert audit_args["action"] == "update_trader"
    assert audit_args["entity_id"] == 1
    assert audit_args["user_id"] == 42
    assert audit_args["new_values"]["status"] == TraderStatus.ENABLED
    assert audit_args["new_values"]["methods_config"]["card"]["fee"] == 1.5


@pytest.mark.asyncio
async def test_add_trader_to_group(service, mock_trader_repo, mock_group_repo):
    mock_group = TraderGroup(id=1, name="VIP", traders=[])
    mock_trader = Trader(id=5, user_id=10)

    mock_group_repo.get.return_value = mock_group
    mock_trader_repo.get.return_value = mock_trader

    result = await service.add_trader_to_group(1, 5, admin_user_id=42)

    assert len(result.traders) == 1
    assert result.traders[0].id == 5

    service.audit_log.assert_called_once_with(
        action="add_trader_to_group",
        entity_type="trader_group",
        entity_id=1,
        user_id=42,
        new_values={"trader_id": 5},
    )


def _scalars_result(items):
    """Mimic session.execute(...).scalars().all() chain."""
    res = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = items
    res.scalars.return_value = scalars
    return res


@pytest.mark.asyncio
async def test_update_trader_admin_replaces_merchants(service, mock_trader_repo, mock_session):
    """merchant_ids = full replacement; relationship and audit log reflect new state."""
    trader = MagicMock(spec=Trader)
    trader.id = 1
    trader.user_id = 10
    trader.method_configs = []
    # Existing merchants: M1 only
    existing_m = MagicMock(spec=Merchant)
    existing_m.id = 1
    trader.merchants = [existing_m]
    trader.groups = []
    trader.accept_all_merchants = False

    mock_trader_repo.get.return_value = trader

    new_m2 = MagicMock(spec=Merchant); new_m2.id = 2
    new_m3 = MagicMock(spec=Merchant); new_m3.id = 3
    mock_session.execute.return_value = _scalars_result([new_m2, new_m3])

    update = TraderUpdateAdmin(merchant_ids=[2, 3])
    await service.update_trader_admin(1, update, admin_user_id=42)

    # After update trader.merchants reassigned
    assert [m.id for m in trader.merchants] == [2, 3]

    # No call to repo.update because no scalar fields changed
    mock_trader_repo.update.assert_not_called()

    audit = service.audit_log.call_args[1]
    assert audit["old_values"]["merchant_ids"] == [1]
    assert audit["new_values"]["merchant_ids"] == [2, 3]


@pytest.mark.asyncio
async def test_update_trader_admin_clears_merchants_with_empty_list(service, mock_trader_repo, mock_session):
    trader = MagicMock(spec=Trader)
    trader.id = 1
    trader.user_id = 10
    trader.method_configs = []
    existing = MagicMock(spec=Merchant); existing.id = 7
    trader.merchants = [existing]
    trader.groups = []

    mock_trader_repo.get.return_value = trader

    update = TraderUpdateAdmin(merchant_ids=[])
    await service.update_trader_admin(1, update, admin_user_id=None)

    # session.execute NOT called for empty list — service short-circuits
    mock_session.execute.assert_not_called()
    assert trader.merchants == []
    audit = service.audit_log.call_args[1]
    assert audit["old_values"]["merchant_ids"] == [7]
    assert audit["new_values"]["merchant_ids"] == []


@pytest.mark.asyncio
async def test_update_trader_admin_replaces_groups(service, mock_trader_repo, mock_session):
    trader = MagicMock(spec=Trader)
    trader.id = 1
    trader.method_configs = []
    trader.merchants = []
    g_old = MagicMock(spec=TraderGroup); g_old.id = 5
    trader.groups = [g_old]

    mock_trader_repo.get.return_value = trader

    g_new1 = MagicMock(spec=TraderGroup); g_new1.id = 10
    g_new2 = MagicMock(spec=TraderGroup); g_new2.id = 11
    mock_session.execute.return_value = _scalars_result([g_new1, g_new2])

    update = TraderUpdateAdmin(group_ids=[10, 11])
    await service.update_trader_admin(1, update, admin_user_id=42)

    assert [g.id for g in trader.groups] == [10, 11]
    audit = service.audit_log.call_args[1]
    assert audit["old_values"]["group_ids"] == [5]
    assert audit["new_values"]["group_ids"] == [10, 11]


@pytest.mark.asyncio
async def test_update_trader_admin_accept_all_merchants(service, mock_trader_repo):
    trader = MagicMock(spec=Trader)
    trader.id = 1
    trader.method_configs = []
    trader.merchants = []
    trader.groups = []
    trader.accept_all_merchants = False

    mock_trader_repo.get.return_value = trader
    mock_trader_repo.update.return_value = trader

    update = TraderUpdateAdmin(accept_all_merchants=True)
    await service.update_trader_admin(1, update, admin_user_id=42)

    call_args = mock_trader_repo.update.call_args[0]
    assert call_args[1] == {"accept_all_merchants": True}

    audit = service.audit_log.call_args[1]
    assert audit["old_values"]["accept_all_merchants"] is False
    assert audit["new_values"]["accept_all_merchants"] is True


@pytest.mark.asyncio
async def test_update_group_replaces_traders(service, mock_group_repo, mock_session):
    from app.modules.traders.schemas import TraderGroupUpdate

    group = MagicMock(spec=TraderGroup)
    group.id = 1
    group.name = "VIP"
    group.description = None
    t_old = MagicMock(spec=Trader); t_old.id = 1
    group.traders = [t_old]
    group.merchants = []

    mock_group_repo.get.return_value = group

    t_new1 = MagicMock(spec=Trader); t_new1.id = 5
    t_new2 = MagicMock(spec=Trader); t_new2.id = 6
    mock_session.execute.return_value = _scalars_result([t_new1, t_new2])

    update = TraderGroupUpdate(trader_ids=[5, 6])
    await service.update_group(1, update, admin_user_id=42)

    assert [t.id for t in group.traders] == [5, 6]
    # name/description not changed → group_repo.update NOT called
    mock_group_repo.update.assert_not_called()

    audit = service.audit_log.call_args[1]
    assert audit["action"] == "update_trader_group"
    assert audit["old_values"]["trader_ids"] == [1]
    assert audit["new_values"]["trader_ids"] == [5, 6]


@pytest.mark.asyncio
async def test_update_group_replaces_merchants(service, mock_group_repo, mock_session):
    from app.modules.traders.schemas import TraderGroupUpdate

    group = MagicMock(spec=TraderGroup)
    group.id = 1
    group.name = "VIP"
    group.description = None
    group.traders = []
    m_old = MagicMock(spec=Merchant); m_old.id = 9
    group.merchants = [m_old]

    mock_group_repo.get.return_value = group

    m_new = MagicMock(spec=Merchant); m_new.id = 10
    mock_session.execute.return_value = _scalars_result([m_new])

    update = TraderGroupUpdate(merchant_ids=[10])
    await service.update_group(1, update, admin_user_id=42)

    assert [m.id for m in group.merchants] == [10]
    audit = service.audit_log.call_args[1]
    assert audit["old_values"]["merchant_ids"] == [9]
    assert audit["new_values"]["merchant_ids"] == [10]


@pytest.mark.asyncio
async def test_remove_trader_from_group_success(service, mock_trader_repo, mock_group_repo):
    trader = MagicMock(spec=Trader); trader.id = 5
    keep = MagicMock(spec=Trader); keep.id = 6
    group = MagicMock(spec=TraderGroup)
    group.id = 1
    group.traders = [trader, keep]

    mock_group_repo.get.return_value = group
    mock_trader_repo.get.return_value = trader

    result = await service.remove_trader_from_group(1, 5, admin_user_id=42)

    assert [t.id for t in result.traders] == [6]
    service.audit_log.assert_called_once_with(
        action="remove_trader_from_group",
        entity_type="trader_group",
        entity_id=1,
        user_id=42,
        old_values={"trader_id": 5},
    )


@pytest.mark.asyncio
async def test_remove_trader_from_group_not_member_raises(service, mock_trader_repo, mock_group_repo):
    from app.core.exceptions import NotFoundException

    trader = MagicMock(spec=Trader); trader.id = 5
    group = MagicMock(spec=TraderGroup)
    group.id = 1
    group.traders = []

    mock_group_repo.get.return_value = group
    mock_trader_repo.get.return_value = trader

    with pytest.raises(NotFoundException):
        await service.remove_trader_from_group(1, 5, admin_user_id=42)


@pytest.mark.asyncio
async def test_remove_merchant_from_group_success(service, mock_merchant_repo, mock_group_repo):
    merchant = MagicMock(spec=Merchant); merchant.id = 9
    keep = MagicMock(spec=Merchant); keep.id = 10
    group = MagicMock(spec=TraderGroup)
    group.id = 1
    group.merchants = [merchant, keep]

    mock_group_repo.get.return_value = group
    mock_merchant_repo.get.return_value = merchant

    result = await service.remove_merchant_from_group(1, 9, admin_user_id=42)

    assert [m.id for m in result.merchants] == [10]
    service.audit_log.assert_called_once_with(
        action="remove_merchant_from_group",
        entity_type="trader_group",
        entity_id=1,
        user_id=42,
        old_values={"merchant_id": 9},
    )


# ── receipt-auto-check toggle ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_toggle_receipt_auto_check_enables_for_existing_trader(
    service, mock_trader_repo,
):
    existing = MagicMock(spec=Trader)
    existing.id = 5
    existing.user_id = 10
    existing.receipt_auto_check = False
    mock_trader_repo.get_by_user_id.return_value = existing

    updated = MagicMock(spec=Trader)
    updated.id = 5
    updated.user_id = 10
    updated.receipt_auto_check = True
    mock_trader_repo.update.return_value = updated

    result = await service.toggle_receipt_auto_check(user_id=10, enabled=True)

    mock_trader_repo.update.assert_awaited_once_with(5, {"receipt_auto_check": True})
    assert result.receipt_auto_check is True

    audit = service.audit_log.call_args[1]
    assert audit["action"] == "toggle_receipt_auto_check"
    assert audit["old_values"] == {"receipt_auto_check": False}
    assert audit["new_values"] == {"receipt_auto_check": True}


@pytest.mark.asyncio
async def test_toggle_receipt_auto_check_creates_trader_if_missing(
    service, mock_trader_repo,
):
    # First lookup returns no trader profile → service should create one,
    # then write the toggle on the freshly-created row.
    mock_trader_repo.get_by_user_id.return_value = None
    new_trader = MagicMock(spec=Trader)
    new_trader.id = 22
    new_trader.user_id = 30
    new_trader.receipt_auto_check = False
    mock_trader_repo.create.return_value = new_trader

    updated = MagicMock(spec=Trader)
    updated.id = 22
    updated.user_id = 30
    updated.receipt_auto_check = True
    mock_trader_repo.update.return_value = updated

    result = await service.toggle_receipt_auto_check(user_id=30, enabled=True)

    mock_trader_repo.create.assert_awaited_once()
    mock_trader_repo.update.assert_awaited_once_with(22, {"receipt_auto_check": True})
    assert result.receipt_auto_check is True


@pytest.mark.asyncio
async def test_toggle_receipt_auto_check_disables(service, mock_trader_repo):
    existing = MagicMock(spec=Trader)
    existing.id = 5
    existing.user_id = 10
    existing.receipt_auto_check = True
    mock_trader_repo.get_by_user_id.return_value = existing

    disabled = MagicMock(spec=Trader)
    disabled.id = 5
    disabled.user_id = 10
    disabled.receipt_auto_check = False
    mock_trader_repo.update.return_value = disabled

    result = await service.toggle_receipt_auto_check(user_id=10, enabled=False)

    mock_trader_repo.update.assert_awaited_once_with(5, {"receipt_auto_check": False})
    assert result.receipt_auto_check is False


# ── default receipt-check provider ────────────────────────────────────


@pytest.mark.asyncio
async def test_set_default_receipt_provider_active(service, mock_trader_repo):
    existing = MagicMock(spec=Trader)
    existing.id = 5
    existing.user_id = 10
    existing.default_receipt_check_provider_id = None
    mock_trader_repo.get_by_user_id.return_value = existing

    updated = MagicMock(spec=Trader)
    updated.id = 5
    updated.default_receipt_check_provider_id = 3
    mock_trader_repo.update.return_value = updated

    with patch("app.modules.traders.service.ReceiptCheckProviderRepository") as Repo:
        provider = MagicMock()
        provider.id = 3
        provider.is_active = True
        Repo.return_value.get = AsyncMock(return_value=provider)
        result = await service.set_default_receipt_provider(user_id=10, provider_id=3)

    mock_trader_repo.update.assert_awaited_once_with(
        5, {"default_receipt_check_provider_id": 3}
    )
    assert result.default_receipt_check_provider_id == 3
    audit = service.audit_log.call_args[1]
    assert audit["action"] == "set_default_receipt_provider"
    assert audit["old_values"] == {"default_receipt_check_provider_id": None}
    assert audit["new_values"] == {"default_receipt_check_provider_id": 3}


@pytest.mark.asyncio
async def test_set_default_receipt_provider_inactive_rejected(service, mock_trader_repo):
    from app.core.exceptions import ValidationException

    existing = MagicMock(spec=Trader)
    existing.id = 5
    existing.user_id = 10
    existing.default_receipt_check_provider_id = None
    mock_trader_repo.get_by_user_id.return_value = existing

    with patch("app.modules.traders.service.ReceiptCheckProviderRepository") as Repo:
        provider = MagicMock()
        provider.id = 3
        provider.is_active = False
        Repo.return_value.get = AsyncMock(return_value=provider)
        with pytest.raises(ValidationException):
            await service.set_default_receipt_provider(user_id=10, provider_id=3)

    mock_trader_repo.update.assert_not_called()


@pytest.mark.asyncio
async def test_set_default_receipt_provider_missing_rejected(service, mock_trader_repo):
    from app.core.exceptions import ValidationException

    existing = MagicMock(spec=Trader)
    existing.id = 5
    existing.user_id = 10
    existing.default_receipt_check_provider_id = None
    mock_trader_repo.get_by_user_id.return_value = existing

    with patch("app.modules.traders.service.ReceiptCheckProviderRepository") as Repo:
        Repo.return_value.get = AsyncMock(return_value=None)
        with pytest.raises(ValidationException):
            await service.set_default_receipt_provider(user_id=10, provider_id=999)

    mock_trader_repo.update.assert_not_called()


@pytest.mark.asyncio
async def test_set_default_receipt_provider_clear_with_none(service, mock_trader_repo):
    existing = MagicMock(spec=Trader)
    existing.id = 5
    existing.user_id = 10
    existing.default_receipt_check_provider_id = 3
    mock_trader_repo.get_by_user_id.return_value = existing

    updated = MagicMock(spec=Trader)
    updated.id = 5
    updated.default_receipt_check_provider_id = None
    mock_trader_repo.update.return_value = updated

    with patch("app.modules.traders.service.ReceiptCheckProviderRepository") as Repo:
        result = await service.set_default_receipt_provider(user_id=10, provider_id=None)
        Repo.return_value.get.assert_not_called()

    mock_trader_repo.update.assert_awaited_once_with(
        5, {"default_receipt_check_provider_id": None}
    )
    assert result.default_receipt_check_provider_id is None


@pytest.mark.asyncio
async def test_delete_group_success(service, mock_group_repo, mock_session):
    t = MagicMock(spec=Trader); t.id = 5
    m = MagicMock(spec=Merchant); m.id = 9
    group = MagicMock(spec=TraderGroup)
    group.id = 1
    group.name = "VIP"
    group.traders = [t]
    group.merchants = [m]

    mock_group_repo.get.return_value = group
    mock_session.delete = AsyncMock()

    await service.delete_group(1, admin_user_id=42)

    # Caches cleared before delete
    assert group.traders == []
    assert group.merchants == []
    mock_session.delete.assert_awaited_once_with(group)

    audit = service.audit_log.call_args[1]
    assert audit["action"] == "delete_trader_group"
    assert audit["entity_id"] == 1
    assert audit["old_values"] == {
        "name": "VIP",
        "trader_ids": [5],
        "merchant_ids": [9],
    }
