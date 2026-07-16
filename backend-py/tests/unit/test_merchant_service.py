import pytest
from unittest.mock import AsyncMock, MagicMock
from app.modules.merchants.service import MerchantService
from app.core.exceptions import NotFoundException
from app.core.security import decrypt_api_secret


class AsyncContextManagerMock:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        pass


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.begin = MagicMock(return_value=AsyncContextManagerMock())
    session.begin_nested = MagicMock(return_value=AsyncContextManagerMock())
    session.flush = AsyncMock()
    return session

@pytest.fixture
def merchant_service(mock_session):
    svc = MerchantService(mock_session)
    svc.audit_log = AsyncMock()
    return svc

@pytest.mark.asyncio
async def test_reset_api_key_success(merchant_service):
    merchant_id = 1
    
    # Mock repository
    merchant_service.repository = MagicMock()
    
    mock_merchant = MagicMock()
    mock_merchant.id = merchant_id
    merchant_service.repository.get = AsyncMock(return_value=mock_merchant)
    merchant_service.repository.update = AsyncMock()
    
    new_api_key, new_api_secret = await merchant_service.reset_api_key(merchant_id)
    
    assert isinstance(new_api_key, str)
    assert len(new_api_key) > 0
    assert isinstance(new_api_secret, str)
    assert len(new_api_secret) > 0
    
    merchant_service.repository.get.assert_called_once_with(merchant_id)
    merchant_service.repository.update.assert_called_once()
    
    # Check that update was called with correct data
    call_args = merchant_service.repository.update.call_args[0]
    assert call_args[0] == merchant_id
    update_data = call_args[1]
    
    assert update_data["api_key"] == new_api_key
    assert update_data["api_secret"] != new_api_secret  # It should be encrypted
    
    # Verify we can decrypt it back
    decrypted_secret = decrypt_api_secret(update_data["api_secret"])
    assert decrypted_secret == new_api_secret
    
    merchant_service.audit_log.assert_called_once_with(
        action="reset_api_key",
        entity_type="merchant",
        entity_id=merchant_id,
        user_id=None,
    )

@pytest.mark.asyncio
async def test_reset_api_key_with_admin(merchant_service):
    merchant_id = 1
    admin_id = 42
    
    merchant_service.repository = MagicMock()
    mock_merchant = MagicMock()
    mock_merchant.id = merchant_id
    merchant_service.repository.get = AsyncMock(return_value=mock_merchant)
    merchant_service.repository.update = AsyncMock()
    
    await merchant_service.reset_api_key(merchant_id, admin_user_id=admin_id)
    
    merchant_service.audit_log.assert_called_once_with(
        action="reset_api_key",
        entity_type="merchant",
        entity_id=merchant_id,
        user_id=admin_id,
    )

@pytest.mark.asyncio
async def test_get_by_api_key(merchant_service):
    merchant_service.repository = MagicMock()
    mock_merchant = MagicMock()
    merchant_service.repository.get_by_api_key = AsyncMock(return_value=mock_merchant)
    
    result = await merchant_service.get_by_api_key("some-key")
    
    assert result == mock_merchant
    merchant_service.repository.get_by_api_key.assert_called_once_with("some-key")

@pytest.mark.asyncio
async def test_reset_api_key_not_found(merchant_service):
    merchant_id = 999

    merchant_service.repository = MagicMock()
    merchant_service.repository.get = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException, match=f"Merchant {merchant_id} not found"):
        await merchant_service.reset_api_key(merchant_id)


# ── update_settings: trader/group bindings ──────────────────

def _scalars_result(items):
    res = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = items
    res.scalars.return_value = scalars
    return res


@pytest.mark.asyncio
async def test_update_settings_replaces_traders(merchant_service):
    merchant = MagicMock()
    merchant.id = 1
    existing = MagicMock(); existing.id = 5
    merchant.traders = [existing]
    merchant.trader_groups = []

    merchant_service.repository = MagicMock()
    merchant_service.repository.get = AsyncMock(return_value=merchant)
    merchant_service.repository.update = AsyncMock(return_value=merchant)
    merchant_service.session.refresh = AsyncMock()

    new_t1 = MagicMock(); new_t1.id = 7
    new_t2 = MagicMock(); new_t2.id = 8
    merchant_service.session.execute = AsyncMock(return_value=_scalars_result([new_t1, new_t2]))

    await merchant_service.update_settings(1, {"trader_ids": [7, 8]}, user_id=42)

    assert [t.id for t in merchant.traders] == [7, 8]
    # No scalar settings → repo.update NOT called
    merchant_service.repository.update.assert_not_called()

    audit = merchant_service.audit_log.call_args[1]
    assert audit["action"] == "update_merchant_settings"
    assert audit["old_values"]["trader_ids"] == [5]
    assert audit["new_values"]["trader_ids"] == [7, 8]


@pytest.mark.asyncio
async def test_update_settings_clears_traders_with_empty_list(merchant_service):
    merchant = MagicMock()
    merchant.id = 1
    existing = MagicMock(); existing.id = 5
    merchant.traders = [existing]
    merchant.trader_groups = []

    merchant_service.repository = MagicMock()
    merchant_service.repository.get = AsyncMock(return_value=merchant)
    merchant_service.session.refresh = AsyncMock()
    merchant_service.session.execute = AsyncMock()

    await merchant_service.update_settings(1, {"trader_ids": []}, user_id=42)

    # Empty list short-circuits — no execute call
    merchant_service.session.execute.assert_not_called()
    assert merchant.traders == []
    audit = merchant_service.audit_log.call_args[1]
    assert audit["new_values"]["trader_ids"] == []


@pytest.mark.asyncio
async def test_update_settings_replaces_trader_groups(merchant_service):
    merchant = MagicMock()
    merchant.id = 1
    existing_group = MagicMock(); existing_group.id = 3
    merchant.traders = []
    merchant.trader_groups = [existing_group]

    merchant_service.repository = MagicMock()
    merchant_service.repository.get = AsyncMock(return_value=merchant)
    merchant_service.session.refresh = AsyncMock()

    g_new = MagicMock(); g_new.id = 9
    merchant_service.session.execute = AsyncMock(return_value=_scalars_result([g_new]))

    await merchant_service.update_settings(1, {"group_ids": [9]}, user_id=42)

    assert [g.id for g in merchant.trader_groups] == [9]
    audit = merchant_service.audit_log.call_args[1]
    assert audit["old_values"]["group_ids"] == [3]
    assert audit["new_values"]["group_ids"] == [9]


@pytest.mark.asyncio
async def test_update_settings_combines_scalar_and_bindings(merchant_service):
    """Scalar fields and bindings can be updated in a single call."""
    merchant = MagicMock()
    merchant.id = 1
    merchant.webhook_url = "https://old.example.com"
    merchant.traders = []
    merchant.trader_groups = []
    merchant.telegram_user_ids = []

    merchant_service.repository = MagicMock()
    merchant_service.repository.get = AsyncMock(return_value=merchant)
    merchant_service.repository.update = AsyncMock(return_value=merchant)
    merchant_service.session.refresh = AsyncMock()

    new_t = MagicMock(); new_t.id = 11
    merchant_service.session.execute = AsyncMock(return_value=_scalars_result([new_t]))

    await merchant_service.update_settings(
        1,
        {"webhook_url": "https://new.example.com", "trader_ids": [11]},
        user_id=42,
    )

    # Scalar update went through repo
    update_args = merchant_service.repository.update.call_args[0]
    assert update_args[0] == 1
    assert update_args[1]["webhook_url"] == "https://new.example.com"
    # And bindings replaced via relationship
    assert [t.id for t in merchant.traders] == [11]

    audit = merchant_service.audit_log.call_args[1]
    assert audit["new_values"]["webhook_url"] == "https://new.example.com"
    assert audit["new_values"]["trader_ids"] == [11]


# ────────────────────────────────────────────────────────────────
# Pure read pass-throughs (admin / merchant-self listings).
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_all_passes_filters(merchant_service):
    expected = [MagicMock()]
    merchant_service.repository = MagicMock()
    merchant_service.repository.get_all = AsyncMock(return_value=expected)

    result = await merchant_service.list_all(
        skip=10, limit=25, search="acme",
        status="enabled", is_active=True, payment_method="card",
    )

    assert result is expected
    merchant_service.repository.get_all.assert_called_once_with(
        skip=10, limit=25, search="acme",
        status="enabled", is_active=True, payment_method="card",
    )


@pytest.mark.asyncio
async def test_list_user_merchants_passthrough(merchant_service):
    merchant_service.repository = MagicMock()
    merchant_service.repository.list_by_user_id = AsyncMock(return_value=[])

    await merchant_service.list_user_merchants(7)

    merchant_service.repository.list_by_user_id.assert_called_once_with(7)


@pytest.mark.asyncio
async def test_list_by_telegram_user_id_passthrough(merchant_service):
    merchant_service.repository = MagicMock()
    merchant_service.repository.list_by_telegram_user_id = AsyncMock(return_value=[])

    await merchant_service.list_by_telegram_user_id(99)

    merchant_service.repository.list_by_telegram_user_id.assert_called_once_with(99)


@pytest.mark.asyncio
async def test_get_active_terminal_for_tg_passthrough(merchant_service):
    merchant_service.repository = MagicMock()
    expected = MagicMock()
    merchant_service.repository.get_active_terminal_for_tg = AsyncMock(return_value=expected)

    result = await merchant_service.get_active_terminal_for_tg(123)

    assert result is expected
    merchant_service.repository.get_active_terminal_for_tg.assert_called_once_with(123)


@pytest.mark.asyncio
async def test_get_by_id_success(merchant_service):
    merchant_service.repository = MagicMock()
    expected = MagicMock(); expected.id = 1
    merchant_service.repository.get = AsyncMock(return_value=expected)

    result = await merchant_service.get_by_id(1)
    assert result is expected


@pytest.mark.asyncio
async def test_get_by_id_not_found_raises(merchant_service):
    merchant_service.repository = MagicMock()
    merchant_service.repository.get = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException, match="Merchant 999 not found"):
        await merchant_service.get_by_id(999)


# ────────────────────────────────────────────────────────────────
# get_merchant_for_user — resolves which merchant terminal to use,
# auto-provisions when the user has none.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_merchant_for_user_with_explicit_id(merchant_service):
    expected = MagicMock(); expected.id = 5
    merchant_service.repository = MagicMock()
    merchant_service.repository.get_by_id_and_user = AsyncMock(return_value=expected)

    result = await merchant_service.get_merchant_for_user(user_id=10, merchant_id=5)

    assert result is expected
    merchant_service.repository.get_by_id_and_user.assert_called_once_with(5, 10)


@pytest.mark.asyncio
async def test_get_merchant_for_user_explicit_id_not_owned_raises(merchant_service):
    merchant_service.repository = MagicMock()
    merchant_service.repository.get_by_id_and_user = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await merchant_service.get_merchant_for_user(user_id=10, merchant_id=5)


@pytest.mark.asyncio
async def test_get_merchant_for_user_returns_first_when_no_id(merchant_service):
    expected = MagicMock(); expected.id = 5
    merchant_service.repository = MagicMock()
    merchant_service.repository.get_by_user_id = AsyncMock(return_value=expected)
    merchant_service._provision_merchant = AsyncMock()

    result = await merchant_service.get_merchant_for_user(user_id=10)

    assert result is expected
    # When user already has a merchant, no auto-provision call.
    merchant_service._provision_merchant.assert_not_called()


@pytest.mark.asyncio
async def test_get_merchant_for_user_auto_provisions_when_none(merchant_service):
    provisioned = MagicMock(); provisioned.id = 17
    merchant_service.repository = MagicMock()
    merchant_service.repository.get_by_user_id = AsyncMock(return_value=None)
    merchant_service._provision_merchant = AsyncMock(return_value=provisioned)

    result = await merchant_service.get_merchant_for_user(user_id=10)

    assert result is provisioned
    merchant_service._provision_merchant.assert_called_once_with(10)


# ────────────────────────────────────────────────────────────────
# create_merchant — must return (merchant, api_key, plain_secret),
# generate USDT WORK + ESCROW balances, and reject non-merchant users.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_merchant_success(merchant_service, monkeypatch):
    from app.common.enums.users import UserRole

    user = MagicMock()
    user.role = UserRole.MERCHANT
    merchant_service.session.get = AsyncMock(return_value=user)

    new_merchant = MagicMock(); new_merchant.id = 5
    merchant_service.repository = MagicMock()
    merchant_service.repository.create = AsyncMock(return_value=new_merchant)

    fake_finance = MagicMock()
    fake_finance.get_or_create_merchant_balance = AsyncMock()
    monkeypatch.setattr(
        "app.modules.finance.service.FinanceService",
        MagicMock(return_value=fake_finance),
    )

    merchant, api_key, secret_plain = await merchant_service.create_merchant(7, name="acme")

    assert merchant is new_merchant
    # api_key is hex(16) → 32 chars; secret is urlsafe(32)
    assert isinstance(api_key, str) and len(api_key) == 32
    assert isinstance(secret_plain, str) and len(secret_plain) >= 32
    # Two balance creates for USDT — WORK and ESCROW.
    assert fake_finance.get_or_create_merchant_balance.await_count == 2

    audit = merchant_service.audit_log.call_args[1]
    assert audit["action"] == "create_merchant"
    assert audit["entity_id"] == 5
    assert audit["new_values"]["name"] == "acme"


@pytest.mark.asyncio
async def test_create_merchant_rejects_non_merchant_role(merchant_service):
    from app.common.enums.users import UserRole

    user = MagicMock()
    user.role = UserRole.TRADER
    merchant_service.session.get = AsyncMock(return_value=user)

    from app.core.exceptions import ValidationException
    with pytest.raises(ValidationException, match="merchant role"):
        await merchant_service.create_merchant(7, name="acme")


@pytest.mark.asyncio
async def test_create_merchant_rejects_missing_user(merchant_service):
    merchant_service.session.get = AsyncMock(return_value=None)

    from app.core.exceptions import ValidationException
    with pytest.raises(ValidationException):
        await merchant_service.create_merchant(7)


# ────────────────────────────────────────────────────────────────
# set_active_terminal_for_tg — flips `active=True` on exactly one
# entry and `active=False` on the rest, only across merchants this
# TG user owns.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_active_terminal_for_tg_flips_active_flag(merchant_service):
    target = MagicMock()
    target.id = 7
    target.telegram_user_ids = [{"id": 999, "label": "Bot", "active": False}]
    other = MagicMock()
    other.id = 8
    other.telegram_user_ids = [{"id": 999, "label": "Bot", "active": True}]

    merchant_service.repository = MagicMock()

    async def _get(mid):
        return target if mid == 7 else other

    merchant_service.repository.get = AsyncMock(side_effect=_get)
    merchant_service.repository.list_by_telegram_user_id = AsyncMock(
        return_value=[target, other]
    )
    merchant_service.repository.update = AsyncMock()

    result = await merchant_service.set_active_terminal_for_tg(999, 7)

    assert result is target  # final repository.get(7) is what's returned
    # Two updates: target gets active=True, other gets active=False
    assert merchant_service.repository.update.await_count == 2

    calls = {c.args[0]: c.args[1] for c in merchant_service.repository.update.await_args_list}
    target_update = calls[7]["telegram_user_ids"]
    other_update = calls[8]["telegram_user_ids"]
    assert any(e["id"] == 999 and e["active"] is True for e in target_update)
    assert any(e["id"] == 999 and e["active"] is False for e in other_update)


@pytest.mark.asyncio
async def test_set_active_terminal_for_tg_target_not_authorized(merchant_service):
    """User must own the merchant they're trying to activate."""
    target = MagicMock()
    target.id = 7
    target.telegram_user_ids = [{"id": 111, "active": False}]  # 999 not in list
    merchant_service.repository = MagicMock()
    merchant_service.repository.get = AsyncMock(return_value=target)

    from app.core.exceptions import ValidationException
    with pytest.raises(ValidationException, match="not authorized"):
        await merchant_service.set_active_terminal_for_tg(999, 7)


@pytest.mark.asyncio
async def test_set_active_terminal_for_tg_target_not_found(merchant_service):
    merchant_service.repository = MagicMock()
    merchant_service.repository.get = AsyncMock(return_value=None)

    with pytest.raises(NotFoundException):
        await merchant_service.set_active_terminal_for_tg(999, 7)


# ────────────────────────────────────────────────────────────────
# mask_api_key — pure helper. Long keys → first 8 + last 4; short
# keys (≤12 chars) → first 4 + ****.
# ────────────────────────────────────────────────────────────────


def test_mask_api_key_short():
    from app.modules.merchants.service import MerchantService
    assert MerchantService.mask_api_key("abcd1234") == "abcd****"


def test_mask_api_key_exactly_twelve_chars_uses_short_form():
    from app.modules.merchants.service import MerchantService
    # Edge: len == 12 → still short form
    masked = MerchantService.mask_api_key("abcdefghijkl")
    assert masked == "abcd****"


def test_mask_api_key_long_keeps_head_and_tail():
    from app.modules.merchants.service import MerchantService
    # 32-char key like real API key
    full = "0123456789abcdef0123456789abcdef"
    masked = MerchantService.mask_api_key(full)
    assert masked.startswith("01234567")
    assert masked.endswith("cdef")
    assert "****" in masked


# ────────────────────────────────────────────────────────────────
# get_bot_limits_for_tg_user — aggregates per-(terminal, method)
# pool capacity. We mock the SQL aggregator rows the service expects.
# ────────────────────────────────────────────────────────────────


class _RowAttrDict:
    """Behaves like a SQLAlchemy Row with attribute access."""
    def __init__(self, **fields):
        self.__dict__.update(fields)


def _all_result(rows):
    res = MagicMock()
    res.all = MagicMock(return_value=rows)
    return res


@pytest.mark.asyncio
async def test_get_bot_limits_for_tg_user_aggregates_per_terminal(merchant_service):
    """Two terminals, one method each — service produces a per-terminal view
    with method-level rows in the order the SQL aggregator returned them."""
    from app.common.enums.finances import Currency
    from app.common.enums.payments import PaymentMethod

    # Mock terminals owned by TG user.
    m1 = MagicMock()
    m1.id = 1
    m1.name = "Acme Main"
    m1.currency = Currency.RUB
    m2 = MagicMock()
    m2.id = 2
    m2.name = "Acme Side"
    m2.currency = Currency.RUB

    merchant_service.repository = MagicMock()
    merchant_service.repository.list_by_telegram_user_id = AsyncMock(
        return_value=[m1, m2]
    )

    # First execute() — query for m1 — returns one SBP row with limited slots.
    # Second execute() — query for m2 — returns one CARD row with NULL slots.
    sbp_row = _RowAttrDict(
        payment_method=PaymentMethod.SBP,
        currency=Currency.RUB,
        # SUM of MIN(daily_room, monthly_room) per requisite — see service docstring
        available=250000,
        min_amount=100,
        max_amount=50000,
        slots_sum=8,
        any_unlimited=False,
        requisites_count=3,
    )
    card_row = _RowAttrDict(
        payment_method=PaymentMethod.CARD,
        currency=Currency.RUB,
        available=400000,
        min_amount=500,
        max_amount=100000,
        slots_sum=None,  # at least one requisite is unlimited
        any_unlimited=True,
        requisites_count=2,
    )

    merchant_service.session.execute = AsyncMock(
        side_effect=[_all_result([sbp_row]), _all_result([card_row])]
    )

    result = await merchant_service.get_bot_limits_for_tg_user(tg_user_id=999)

    assert len(result.terminals) == 2

    t1 = result.terminals[0]
    assert t1.id == 1 and t1.name == "Acme Main" and t1.currency == "RUB"
    assert len(t1.methods) == 1
    sbp = t1.methods[0]
    assert sbp.payment_method == "sbp"
    assert sbp.available == 250000.0
    assert sbp.min_amount == 100.0
    assert sbp.max_amount == 50000.0
    assert sbp.concurrent_slots == 8
    assert sbp.requisites_count == 3

    t2 = result.terminals[1]
    assert t2.id == 2
    [card] = t2.methods
    assert card.payment_method == "card"
    # any_unlimited==True ⇒ concurrent_slots collapses to None.
    assert card.concurrent_slots is None
    assert card.requisites_count == 2


@pytest.mark.asyncio
async def test_get_bot_limits_for_tg_user_empty_terminals(merchant_service):
    """TG user with no merchant terminals — empty response, no SQL fired."""
    merchant_service.repository = MagicMock()
    merchant_service.repository.list_by_telegram_user_id = AsyncMock(return_value=[])
    merchant_service.session.execute = AsyncMock()

    result = await merchant_service.get_bot_limits_for_tg_user(tg_user_id=999)

    assert result.terminals == []
    merchant_service.session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_bot_limits_for_tg_user_terminal_with_no_eligible_requisites(
    merchant_service,
):
    """Terminal exists but pooling has no eligible requisites — methods list empty."""
    from app.common.enums.finances import Currency

    m1 = MagicMock()
    m1.id = 1
    m1.name = "Empty"
    m1.currency = Currency.RUB

    merchant_service.repository = MagicMock()
    merchant_service.repository.list_by_telegram_user_id = AsyncMock(return_value=[m1])
    merchant_service.session.execute = AsyncMock(return_value=_all_result([]))

    result = await merchant_service.get_bot_limits_for_tg_user(tg_user_id=999)

    assert len(result.terminals) == 1
    assert result.terminals[0].methods == []


@pytest.mark.asyncio
async def test_get_bot_limits_for_tg_user_zero_slots_when_all_consumed(
    merchant_service,
):
    """All eligible requisites have a concurrent limit AND it's already
    saturated → slots_sum = 0, not None."""
    from app.common.enums.finances import Currency
    from app.common.enums.payments import PaymentMethod

    m = MagicMock()
    m.id = 1
    m.name = "T"
    m.currency = Currency.RUB

    merchant_service.repository = MagicMock()
    merchant_service.repository.list_by_telegram_user_id = AsyncMock(return_value=[m])

    saturated = _RowAttrDict(
        payment_method=PaymentMethod.SBP,
        currency=Currency.RUB,
        # daily room saturated → MIN(daily, monthly) per requisite is 0
        # for everyone → SUM is 0
        available=0,
        min_amount=100,
        max_amount=50000,
        slots_sum=0,
        any_unlimited=False,
        requisites_count=2,
    )
    merchant_service.session.execute = AsyncMock(return_value=_all_result([saturated]))

    result = await merchant_service.get_bot_limits_for_tg_user(tg_user_id=999)
    [method] = result.terminals[0].methods
    assert method.concurrent_slots == 0
    assert method.available == 0.0


@pytest.mark.asyncio
async def test_get_bot_limits_sql_uses_least_for_combined_daily_monthly(
    merchant_service,
):
    """Regression: the per-requisite ``available`` must be
    ``LEAST(daily_room, monthly_room)`` so that the binding cap is
    honoured. A regression to ``SUM(daily_room)`` would silently
    overstate capacity whenever monthly is the tighter constraint.

    We compile the actual SQL the service emits and assert ``LEAST``
    appears in the GREATEST/LEAST stack rather than the previous
    pair of ``GREATEST`` SUMs.
    """
    from sqlalchemy.dialects import postgresql
    # Importing these makes Order's mapper resolve its string-based
    # relationships when SQLAlchemy compiles the statement.
    from app.modules.payments.models import PaymentOption  # noqa: F401
    from app.modules.requisites.models import Requisite  # noqa: F401
    from app.modules.users.models import User  # noqa: F401
    from app.common.enums.finances import Currency

    captured: list = []

    async def _capture_execute(stmt):
        compiled = stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
        captured.append(str(compiled))
        result = MagicMock()
        result.all = MagicMock(return_value=[])
        return result

    m = MagicMock()
    m.id = 1
    m.name = "Capture"
    m.currency = Currency.RUB
    merchant_service.repository = MagicMock()
    merchant_service.repository.list_by_telegram_user_id = AsyncMock(return_value=[m])
    merchant_service.session.execute = _capture_execute

    await merchant_service.get_bot_limits_for_tg_user(tg_user_id=1)

    sql = captured[0].lower()
    # LEAST() of daily_room and monthly_room is the binding amount per
    # requisite (so that whichever cap is tighter wins). Used for both
    # ``available`` (SUM) and ``max_amount`` (MAX) — i.e. LEAST appears
    # at least twice in the compiled SQL.
    assert sql.count("least(") >= 2, (
        f"expected LEAST() at least twice (for available + max_amount), got:\n{sql}"
    )
    # Both rooms are still expressed via GREATEST(..., 0) for clamping.
    assert "greatest(" in sql
    # Both labels present — available (SUM) and max_amount (MAX of per-req cap).
    assert "as available" in sql
    assert "as max_amount" in sql
    # max_amount must be a MAX aggregate (not SUM) because it's the largest
    # single-order amount that fits, not a pool sum.
    assert "max(least(" in sql, (
        "max_amount should be MAX(LEAST(...)), got:\n" + sql
    )


@pytest.mark.asyncio
async def test_get_bot_limits_for_tg_user_handles_string_payment_method(
    merchant_service,
):
    """Some DB drivers return raw strings rather than enum members for the
    GROUP BY column. Service must coerce gracefully."""
    from app.common.enums.finances import Currency

    m = MagicMock()
    m.id = 1
    m.name = "T"
    m.currency = Currency.RUB

    merchant_service.repository = MagicMock()
    merchant_service.repository.list_by_telegram_user_id = AsyncMock(return_value=[m])

    plain_row = _RowAttrDict(
        payment_method="sbp",  # raw string, no .value
        currency="RUB",
        available=100,
        min_amount=100,
        max_amount=100,
        slots_sum=None,
        any_unlimited=True,
        requisites_count=1,
    )
    merchant_service.session.execute = AsyncMock(return_value=_all_result([plain_row]))

    result = await merchant_service.get_bot_limits_for_tg_user(tg_user_id=999)
    [method] = result.terminals[0].methods
    assert method.payment_method == "sbp"
    assert method.currency == "RUB"
