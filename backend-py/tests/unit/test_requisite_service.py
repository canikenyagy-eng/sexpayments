import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

# Importing User ensures the SQLAlchemy mapper can resolve the
# `Requisite.trader -> User` relationship at configure time.
from app.modules.users.models import User  # noqa: F401
# Importing Merchant lets `_attach_active_amounts` resolve the
# `Order -> Merchant` relationship when it lazy-imports Order. Without
# this, running this test file in isolation trips a mapper init error.
from app.modules.merchants.models import Merchant  # noqa: F401
from app.modules.requisites.service import RequisiteService
from app.modules.requisites.models import Requisite, RequisiteLimit
from app.modules.requisites.schemas import RequisiteCreate, RequisiteUpdate, RequisiteLimitBase, RequisiteLimitUpdate
from app.modules.payments.models import PaymentOption
from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.requisites import RequisiteStatus
from app.core.exceptions import NotFoundException, ForbiddenException, ValidationException


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
    session.get = AsyncMock()
    # `_attach_active_amounts` queries Order amounts via session.execute(),
    # and RequisiteService's PriorityService.recompute_group hooks (create /
    # update / delete / set_enabled) also query via session.execute() — the
    # trader's priority_bonus_percent (.scalar_one_or_none()) then the
    # poolable requisites in the group (.scalars().all()). Default all three
    # shapes to an empty/no-op result so unrelated tests don't have to wire
    # any of this up: no percent -> base=100, no requisites -> recompute is a
    # no-op (nothing to redistribute).
    empty_result = MagicMock()
    empty_result.all = MagicMock(return_value=[])
    empty_result.scalar_one_or_none = MagicMock(return_value=None)
    empty_result.scalars.return_value.all = MagicMock(return_value=[])
    session.execute = AsyncMock(return_value=empty_result)
    return session


@pytest.fixture
def mock_req_repo():
    return AsyncMock()


@pytest.fixture
def mock_limit_repo():
    return AsyncMock()


@pytest.fixture
def sample_payment_option():
    return PaymentOption(
        id=5,
        code="sber",
        name="Сбербанк",
        logo_url="/banks/sber.svg",
        supported_methods=["sbp", "card"],
        currency=Currency.RUB,
        is_active=True,
    )


@pytest.fixture
def service(mock_session, mock_req_repo, mock_limit_repo, sample_payment_option):
    svc = RequisiteService(mock_session)
    svc.requisite_repo = mock_req_repo
    svc.limit_repo = mock_limit_repo
    svc.audit_log = AsyncMock()
    mock_session.get.return_value = sample_payment_option
    return svc


@pytest.fixture
def sample_requisite():
    return Requisite(
        id=1,
        trader_id=10,
        payment_option_id=5,
        bank_name="Сбербанк",
        account_number="123456789",
        account_holder="Ivan Ivanov",
        payment_method=PaymentMethod.CARD,
        currency=Currency.RUB,
        status=RequisiteStatus.DISABLED,
        is_active=True,
        is_archived=False,
        limits=RequisiteLimit(
            id=1,
            requisite_id=1,
            limit_daily=100000,
            limit_monthly=1000000,
            limit_min_transaction=100,
            limit_max_transaction=50000,
            current_daily_turnover=0,
            current_monthly_turnover=0,
        ),
    )


def test_create_schema_defaults_and_validates_trader_priority():
    """RequisiteCreate (the actual trader-facing create schema — imported by
    both the trader `/me` POST endpoint and RequisiteService.create_requisite)
    defaults trader_priority to 1 and only accepts 1-3."""
    from pydantic import ValidationError

    base = dict(payment_option_id=1, account_number="12345", account_holder="holder", payment_method="sbp")
    assert RequisiteCreate(**base).trader_priority == 1
    assert RequisiteCreate(**base, trader_priority=3).trader_priority == 3
    for bad in (0, 4, 5):
        with pytest.raises(ValidationError):
            RequisiteCreate(**base, trader_priority=bad)


@pytest.mark.asyncio
async def test_create_requisite_via_payment_option(
    service, mock_req_repo, mock_limit_repo, sample_requisite
):
    create_data = RequisiteCreate(
        payment_option_id=5,
        account_number="123456789",
        account_holder="Ivan Ivanov",
        payment_method=PaymentMethod.CARD,
        limits=RequisiteLimitBase(
            limit_daily=100000,
            limit_monthly=1000000,
            limit_min_transaction=100,
            limit_max_transaction=50000,
        ),
    )

    mock_req_repo.create.return_value = sample_requisite
    mock_req_repo.get_with_limits.return_value = sample_requisite

    result = await service.create_requisite(10, create_data)

    assert result.id == 1
    assert result.payment_method == PaymentMethod.CARD
    mock_req_repo.create.assert_called_once()
    create_call_args = mock_req_repo.create.call_args[0][0]
    # bank_name and currency should be auto-derived from PaymentOption
    assert create_call_args["bank_name"] == "Сбербанк"
    assert create_call_args["currency"] == Currency.RUB
    assert create_call_args["payment_option_id"] == 5

    mock_limit_repo.create.assert_called_once()


@pytest.mark.asyncio
async def test_create_requisite_normalizes_phone_for_sbp(
    service, mock_req_repo, sample_requisite
):
    """A phone requisite (SBP) is stored as +7XXXXXXXXXX no matter how typed."""
    create_data = RequisiteCreate(
        payment_option_id=5,
        account_number="8 (999) 123-12-12",
        account_holder="Ivan Ivanov",
        payment_method=PaymentMethod.SBP,
        limits=RequisiteLimitBase(limit_daily=100000, limit_monthly=1000000),
    )
    mock_req_repo.create.return_value = sample_requisite
    mock_req_repo.get_with_limits.return_value = sample_requisite

    await service.create_requisite(10, create_data)

    stored = mock_req_repo.create.call_args[0][0]
    assert stored["account_number"] == "+79991231212"


@pytest.mark.asyncio
async def test_create_requisite_leaves_card_number_untouched(
    service, mock_req_repo, sample_requisite
):
    """CARD requisites keep their raw account_number (not a phone)."""
    create_data = RequisiteCreate(
        payment_option_id=5,
        account_number="2200 1234 5678 9010",
        account_holder="Ivan Ivanov",
        payment_method=PaymentMethod.CARD,
        limits=RequisiteLimitBase(limit_daily=100000, limit_monthly=1000000),
    )
    mock_req_repo.create.return_value = sample_requisite
    mock_req_repo.get_with_limits.return_value = sample_requisite

    await service.create_requisite(10, create_data)

    stored = mock_req_repo.create.call_args[0][0]
    assert stored["account_number"] == "2200 1234 5678 9010"


@pytest.mark.asyncio
async def test_create_requisite_rejects_unsupported_method(
    service, mock_session
):
    # Сбербанк supports only sbp/card; SIM should be rejected
    create_data = RequisiteCreate(
        payment_option_id=5,
        account_number="123456789",
        account_holder="Ivan Ivanov",
        payment_method=PaymentMethod.SIM,
    )

    with pytest.raises(ValidationException):
        await service.create_requisite(10, create_data)


@pytest.mark.asyncio
async def test_create_requisite_rejects_inactive_option(
    service, mock_session, sample_payment_option
):
    sample_payment_option.is_active = False
    create_data = RequisiteCreate(
        payment_option_id=5,
        account_number="123456789",
        account_holder="Ivan Ivanov",
        payment_method=PaymentMethod.CARD,
    )

    with pytest.raises(ValidationException):
        await service.create_requisite(10, create_data)


@pytest.mark.asyncio
async def test_get_trader_requisite_forbidden(service, mock_req_repo, sample_requisite):
    mock_req_repo.get_with_limits.return_value = sample_requisite

    # Try to access requisite belonging to trader 10 with trader_id 99
    with pytest.raises(ForbiddenException):
        await service.get_trader_requisite(1, 99)


@pytest.mark.asyncio
async def test_update_requisite_limits_only(
    service, mock_req_repo, mock_limit_repo, sample_requisite
):
    mock_req_repo.get_with_limits.return_value = sample_requisite

    update_data = RequisiteUpdate(
        limits=RequisiteLimitUpdate(limit_daily=200000)
    )
    mock_req_repo.update.return_value = sample_requisite

    await service.update_requisite(1, update_data, trader_id=10, user_id=42)

    mock_limit_repo.update.assert_called_once_with(1, {"limit_daily": 200000.0})


@pytest.mark.asyncio
async def test_update_requisite_normalizes_phone_by_existing_method(
    service, mock_req_repo, sample_requisite
):
    """Editing an SBP requisite's number normalizes it (effective method = the
    requisite's existing method, since the update doesn't change it)."""
    sample_requisite.payment_method = PaymentMethod.SBP
    mock_req_repo.get_with_limits.return_value = sample_requisite
    mock_req_repo.update.return_value = sample_requisite

    await service.update_requisite(
        1, RequisiteUpdate(account_number="8 999 123 12 12"), trader_id=10, user_id=42
    )

    stored = mock_req_repo.update.call_args[0][1]
    assert stored["account_number"] == "+79991231212"


@pytest.mark.asyncio
async def test_update_requisite_leaves_card_number_untouched(
    service, mock_req_repo, sample_requisite
):
    """Editing a CARD requisite's number leaves it as-is."""
    sample_requisite.payment_method = PaymentMethod.CARD
    mock_req_repo.get_with_limits.return_value = sample_requisite
    mock_req_repo.update.return_value = sample_requisite

    await service.update_requisite(
        1, RequisiteUpdate(account_number="2200 1234 5678 9010"), trader_id=10, user_id=42
    )

    stored = mock_req_repo.update.call_args[0][1]
    assert stored["account_number"] == "2200 1234 5678 9010"


@pytest.mark.asyncio
async def test_update_requisite_method_change_to_sbp_normalizes(
    service, mock_req_repo, sample_requisite
):
    """Switching a requisite CARD→SBP with a new number normalizes by the NEW
    (effective) method."""
    sample_requisite.payment_method = PaymentMethod.CARD
    mock_req_repo.get_with_limits.return_value = sample_requisite
    mock_req_repo.update.return_value = sample_requisite

    await service.update_requisite(
        1,
        RequisiteUpdate(
            payment_method=PaymentMethod.SBP,
            payment_option_id=5,
            account_number="89991231212",
        ),
        trader_id=10,
        user_id=42,
    )

    stored = mock_req_repo.update.call_args[0][1]
    assert stored["account_number"] == "+79991231212"


@pytest.mark.asyncio
async def test_delete_requisite(service, mock_req_repo, sample_requisite):
    mock_req_repo.get_with_limits.return_value = sample_requisite

    await service.delete_requisite(1, trader_id=10, user_id=42)

    call_args = mock_req_repo.update.call_args[0]
    assert call_args[0] == 1
    assert call_args[1]["is_archived"] is True
    assert call_args[1]["is_active"] is False
    assert "status_updated_at" in call_args[1]

    service.audit_log.assert_called_once_with(
        action="delete_requisite",
        entity_type="requisite",
        entity_id=1,
        user_id=42,
        old_values={"is_archived": False, "is_active": True},
        new_values={"is_archived": True, "is_active": False},
    )


# ────────────────────────────────────────────────────────────────
# set_enabled — trader-facing toggle. Flips status DISABLED↔ENABLED;
# refuses to touch archived or admin-blocked requisites.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_enabled_disabled_to_enabled(service, mock_req_repo, sample_requisite):
    mock_req_repo.get_with_limits.return_value = sample_requisite

    await service.set_enabled(1, True, trader_id=10, user_id=10)

    update_args = mock_req_repo.update.call_args[0]
    assert update_args[0] == 1
    assert update_args[1]["status"] == RequisiteStatus.ENABLED
    assert "status_updated_at" in update_args[1]

    service.audit_log.assert_called_once_with(
        action="toggle_requisite",
        entity_type="requisite",
        entity_id=1,
        user_id=10,
        old_values={"status": RequisiteStatus.DISABLED.value},
        new_values={"status": RequisiteStatus.ENABLED.value},
    )


@pytest.mark.asyncio
async def test_set_enabled_enabled_to_disabled(service, mock_req_repo, sample_requisite):
    sample_requisite.status = RequisiteStatus.ENABLED
    mock_req_repo.get_with_limits.return_value = sample_requisite

    await service.set_enabled(1, False, trader_id=10, user_id=10)

    update_args = mock_req_repo.update.call_args[0]
    assert update_args[1]["status"] == RequisiteStatus.DISABLED

    audit = service.audit_log.call_args[1]
    assert audit["old_values"]["status"] == RequisiteStatus.ENABLED.value
    assert audit["new_values"]["status"] == RequisiteStatus.DISABLED.value


@pytest.mark.asyncio
async def test_set_enabled_already_in_target_state_is_noop(
    service, mock_req_repo, sample_requisite
):
    """Toggle to current state — nothing to do, no audit, no DB write."""
    sample_requisite.status = RequisiteStatus.ENABLED
    mock_req_repo.get_with_limits.return_value = sample_requisite

    result = await service.set_enabled(1, True, trader_id=10, user_id=10)

    assert result is sample_requisite
    mock_req_repo.update.assert_not_called()
    service.audit_log.assert_not_called()


@pytest.mark.asyncio
async def test_set_enabled_forbidden_for_other_trader(
    service, mock_req_repo, sample_requisite
):
    mock_req_repo.get_with_limits.return_value = sample_requisite

    with pytest.raises(ForbiddenException):
        await service.set_enabled(1, True, trader_id=999, user_id=999)

    mock_req_repo.update.assert_not_called()
    service.audit_log.assert_not_called()


@pytest.mark.asyncio
async def test_set_enabled_archived_rejected(
    service, mock_req_repo, sample_requisite
):
    sample_requisite.is_archived = True
    mock_req_repo.get_with_limits.return_value = sample_requisite

    with pytest.raises(ValidationException, match="Archived"):
        await service.set_enabled(1, True, trader_id=10, user_id=10)

    mock_req_repo.update.assert_not_called()


@pytest.mark.asyncio
async def test_set_enabled_admin_blocked_rejected(
    service, mock_req_repo, sample_requisite
):
    """Trader cannot un-block an admin-blocked requisite."""
    sample_requisite.status = RequisiteStatus.BLOCKED
    mock_req_repo.get_with_limits.return_value = sample_requisite

    with pytest.raises(ForbiddenException, match="blocked by administration"):
        await service.set_enabled(1, True, trader_id=10, user_id=10)

    mock_req_repo.update.assert_not_called()


@pytest.mark.asyncio
async def test_trader_update_cannot_change_status_or_admin_flags(
    service, mock_req_repo, sample_requisite
):
    """A trader-initiated update (trader_id set) must NOT change status /
    is_active / is_archived — those are admin-only. Otherwise a trader could
    re-enable an admin-BLOCKED requisite via PATCH /me, bypassing the BLOCKED
    guard on the dedicated /enable route (traders change status only through
    /enable and /disable). A legit field in the same payload still applies."""
    sample_requisite.status = RequisiteStatus.BLOCKED
    mock_req_repo.get_with_limits.return_value = sample_requisite
    mock_req_repo.update.return_value = sample_requisite

    await service.update_requisite(
        1,
        RequisiteUpdate(
            nickname="new-label",
            status=RequisiteStatus.ENABLED,
            is_active=True,
            is_archived=True,
        ),
        trader_id=10,
        user_id=42,
    )

    stored = mock_req_repo.update.call_args[0][1]
    assert stored.get("nickname") == "new-label"   # legit field applied
    assert "status" not in stored                   # admin-only field stripped
    assert "is_active" not in stored
    assert "is_archived" not in stored


@pytest.mark.asyncio
async def test_set_enabled_admin_path_no_trader_id(
    service, mock_req_repo, sample_requisite
):
    """Admin-side toggle (no trader_id) must succeed regardless of ownership."""
    mock_req_repo.get_with_limits.return_value = sample_requisite

    await service.set_enabled(1, True, trader_id=None, user_id=42)

    update_args = mock_req_repo.update.call_args[0]
    assert update_args[1]["status"] == RequisiteStatus.ENABLED
    assert service.audit_log.call_args[1]["user_id"] == 42
