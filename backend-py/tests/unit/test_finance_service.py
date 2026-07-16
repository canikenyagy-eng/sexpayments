import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal
from app.modules.finance.service import FinanceService
from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency
from app.common.enums.users import UserRole
from app.core.exceptions import ValidationException
from app.modules.users.models import User
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.requisites.models import Requisite  # noqa: F401
from app.modules.payments.models import PaymentOption  # noqa: F401
from app.common.enums.payments import PaymentDirection
from app.common.enums.orders import OrderStatus

@pytest.fixture
def mock_session():
    return AsyncMock()

@pytest.fixture
def finance_service(mock_session):
    return FinanceService(mock_session)

@pytest.mark.asyncio
async def test_get_my_balances_trader(finance_service):
    user = MagicMock(spec=User)
    user.id = 1
    user.role = UserRole.TRADER
    
    finance_service.balance_repo = MagicMock()
    finance_service.balance_repo.get_my_balances = AsyncMock(return_value=[])
    
    await finance_service.get_my_balances(user)
    
    finance_service.balance_repo.get_my_balances.assert_called_once_with(user_id=1, merchant_id=None)

def _mock_session_execute(*results):
    """Return an AsyncMock that yields each `result` from `session.execute` in turn.

    `result` is whatever you'd get from `await session.execute(stmt)` — i.e. a
    SQLAlchemy `Result`. We expose `.scalars().all()` and `.all()` chains so the
    same mock satisfies code that does either.
    """
    canned_results = []
    for r in results:
        result_obj = MagicMock()
        if isinstance(r, list) and r and isinstance(r[0], tuple):
            # rows already in (col, col, ...) form, e.g. for (type, currency, sum)
            result_obj.all.return_value = r
            result_obj.scalars.return_value.all.return_value = [row[0] for row in r]
        else:
            scalars_obj = MagicMock()
            scalars_obj.all.return_value = r
            result_obj.scalars.return_value = scalars_obj
            result_obj.all.return_value = r
        canned_results.append(result_obj)
    return AsyncMock(side_effect=canned_results)


@pytest.mark.asyncio
async def test_get_my_balances_merchant(finance_service):
    user = MagicMock(spec=User)
    user.id = 2
    user.role = UserRole.MERCHANT

    # 1st execute: list merchant_ids for the user.
    # 2nd execute: GROUP BY (type, currency) sum from `Balance` for those terminals.
    # 3rd execute: list user-level balances.
    finance_service.session.execute = _mock_session_execute(
        [10, 11],
        [],
        [],
    )

    result = await finance_service.get_my_balances(user)

    assert result == []
    # 3 queries inside get_merchant_aggregate_balances when user has terminals.
    assert finance_service.session.execute.await_count == 3


@pytest.mark.asyncio
async def test_get_my_balances_merchant_no_merchant_record(finance_service):
    user = MagicMock(spec=User)
    user.id = 2
    user.role = UserRole.MERCHANT

    # No terminals owned: only the user-level balance lookup runs.
    finance_service.session.execute = _mock_session_execute([], [])

    result = await finance_service.get_my_balances(user)

    assert result == []
    # 2 queries: list merchant_ids (empty) + list user-level balances.
    assert finance_service.session.execute.await_count == 2

# --- Transfer Tests ---

@pytest.mark.asyncio
async def test_transfer_success(finance_service):
    # Mock balances
    from_balance = MagicMock()
    from_balance.id = 1
    from_balance.amount = Decimal("1000.0")
    from_balance.currency = Currency.RUB
    
    to_balance = MagicMock()
    to_balance.id = 2
    to_balance.amount = Decimal("500.0")
    to_balance.currency = Currency.RUB

    # Mock repository methods
    finance_service.balance_repo = MagicMock()
    finance_service.balance_repo.get_for_update = AsyncMock(side_effect=lambda bid: from_balance if bid == 1 else to_balance)
    finance_service.ledger_repo = MagicMock()
    finance_service.ledger_repo.create = AsyncMock()

    # Execute transfer
    await finance_service.transfer(
        from_balance_id=1,
        to_balance_id=2,
        amount=Decimal("200.0"),
        currency=Currency.RUB,
        reference_type=LedgerReferenceType.INTERNAL_TRANSFER,
        reference_id="test-123"
    )

    # Assertions
    assert from_balance.amount == Decimal("800.0")
    assert to_balance.amount == Decimal("700.0")
    finance_service.ledger_repo.create.assert_called_once()
    
    # Check ledger entry data
    call_args = finance_service.ledger_repo.create.call_args[0][0]
    assert call_args["from_balance_id"] == 1
    assert call_args["to_balance_id"] == 2
    assert call_args["amount"] == Decimal("200.0")
    assert call_args["currency"] == Currency.RUB
    assert call_args["reference_type"] == LedgerReferenceType.INTERNAL_TRANSFER
    assert call_args["reference_id"] == "test-123"

@pytest.mark.asyncio
async def test_transfer_insufficient_funds(finance_service):
    from_balance = MagicMock()
    from_balance.id = 1
    from_balance.amount = Decimal("100.0")  # Less than transfer amount
    from_balance.currency = Currency.RUB
    
    to_balance = MagicMock()
    to_balance.id = 2
    to_balance.amount = Decimal("500.0")
    to_balance.currency = Currency.RUB

    finance_service.balance_repo = MagicMock()
    finance_service.balance_repo.get_for_update = AsyncMock(side_effect=lambda bid: from_balance if bid == 1 else to_balance)

    with pytest.raises(ValidationException, match="Insufficient funds"):
        await finance_service.transfer(
            from_balance_id=1,
            to_balance_id=2,
            amount=Decimal("200.0"),
            currency=Currency.RUB,
            reference_type=LedgerReferenceType.INTERNAL_TRANSFER,
            reference_id="test-123"
        )

@pytest.mark.asyncio
async def test_transfer_currency_mismatch(finance_service):
    from_balance = MagicMock()
    from_balance.id = 1
    from_balance.amount = Decimal("1000.0")
    from_balance.currency = Currency.RUB
    
    to_balance = MagicMock()
    to_balance.id = 2
    to_balance.amount = Decimal("500.0")
    to_balance.currency = Currency.AZN  # Different currency

    finance_service.balance_repo = MagicMock()
    finance_service.balance_repo.get_for_update = AsyncMock(side_effect=lambda bid: from_balance if bid == 1 else to_balance)

    with pytest.raises(ValidationException, match="Currency mismatch"):
        await finance_service.transfer(
            from_balance_id=1,
            to_balance_id=2,
            amount=Decimal("200.0"),
            currency=Currency.RUB,
            reference_type=LedgerReferenceType.INTERNAL_TRANSFER,
            reference_id="test-123"
        )

@pytest.mark.asyncio
async def test_transfer_negative_amount(finance_service):
    with pytest.raises(ValidationException, match="Transfer amount must be positive"):
        await finance_service.transfer(
            from_balance_id=1,
            to_balance_id=2,
            amount=Decimal("-100.0"),
            currency=Currency.RUB,
            reference_type=LedgerReferenceType.INTERNAL_TRANSFER,
            reference_id="test-123"
        )

@pytest.mark.asyncio
async def test_transfer_no_balances(finance_service):
    with pytest.raises(ValidationException, match="At least one balance must be provided"):
        await finance_service.transfer(
            from_balance_id=None,
            to_balance_id=None,
            amount=Decimal("100.0"),
            currency=Currency.RUB,
            reference_type=LedgerReferenceType.INTERNAL_TRANSFER,
            reference_id="test-123"
        )

# --- Balance Creation Tests ---

@pytest.mark.asyncio
async def test_get_or_create_user_balance_existing(finance_service):
    user = MagicMock(spec=User)
    user.id = 1
    user.role = UserRole.TRADER
    
    mock_balance = MagicMock()
    finance_service.balance_repo = MagicMock()
    finance_service.balance_repo.get_user_balance = AsyncMock(return_value=mock_balance)
    
    result = await finance_service.get_or_create_user_balance(user, BalanceType.ESCROW, Currency.RUB)
    
    assert result == mock_balance
    finance_service.balance_repo.get_user_balance.assert_called_once_with(1, BalanceType.ESCROW, Currency.RUB)
    finance_service.balance_repo.create.assert_not_called()

@pytest.mark.asyncio
async def test_get_or_create_user_balance_new(finance_service):
    user = MagicMock(spec=User)
    user.id = 1
    user.role = UserRole.TRADER
    
    mock_balance = MagicMock()
    finance_service.balance_repo = MagicMock()
    finance_service.balance_repo.get_user_balance = AsyncMock(return_value=None)
    finance_service.balance_repo.create = AsyncMock(return_value=mock_balance)
    
    result = await finance_service.get_or_create_user_balance(user, BalanceType.ESCROW, Currency.RUB)
    
    assert result == mock_balance
    finance_service.balance_repo.create.assert_called_once_with({
        "user_id": 1,
        "type": BalanceType.ESCROW,
        "currency": Currency.RUB,
        "amount": 0,
    })

@pytest.mark.asyncio
async def test_get_or_create_user_balance_merchant_invalid(finance_service):
    user = MagicMock(spec=User)
    user.id = 1
    user.role = UserRole.MERCHANT

    # Merchant (as user) is allowed WORK and ESCROW. Other balance types
    # remain forbidden — SAFE_DEPOSIT is the canonical negative case here.
    with pytest.raises(
        ValidationException, match="Role merchant can only have WORK or ESCROW balance"
    ):
        await finance_service.get_or_create_user_balance(
            user, BalanceType.SAFE_DEPOSIT, Currency.RUB
        )


@pytest.mark.asyncio
async def test_get_or_create_user_balance_teamlead_escrow_allowed(finance_service):
    user = MagicMock(spec=User)
    user.id = 1
    user.role = UserRole.TEAMLEAD

    mock_balance = MagicMock()
    finance_service.balance_repo = MagicMock()
    finance_service.balance_repo.get_user_balance = AsyncMock(return_value=None)
    finance_service.balance_repo.create = AsyncMock(return_value=mock_balance)

    # Teamlead is allowed WORK and ESCROW (to support withdrawal flow)
    result = await finance_service.get_or_create_user_balance(user, BalanceType.ESCROW, Currency.USDT)
    assert result == mock_balance


@pytest.mark.asyncio
async def test_get_or_create_user_balance_teamlead_safe_deposit_forbidden(finance_service):
    user = MagicMock(spec=User)
    user.id = 1
    user.role = UserRole.TEAMLEAD

    with pytest.raises(ValidationException, match="Role teamlead can only have WORK or ESCROW balance"):
        await finance_service.get_or_create_user_balance(user, BalanceType.SAFE_DEPOSIT, Currency.USDT)

@pytest.mark.asyncio
async def test_get_or_create_merchant_balance(finance_service):
    merchant = MagicMock(spec=Merchant)
    merchant.id = 1
    
    mock_balance = MagicMock()
    finance_service.balance_repo = MagicMock()
    finance_service.balance_repo.get_merchant_balance = AsyncMock(return_value=None)
    finance_service.balance_repo.create = AsyncMock(return_value=mock_balance)
    
    result = await finance_service.get_or_create_merchant_balance(merchant, Currency.RUB)
    
    assert result == mock_balance
    finance_service.balance_repo.create.assert_called_once_with({
        "merchant_id": 1,
        "type": BalanceType.WORK,
        "currency": Currency.RUB,
        "amount": 0,
    })

@pytest.mark.asyncio
async def test_get_or_create_system_balance(finance_service):
    mock_balance = MagicMock()
    finance_service.balance_repo = MagicMock()
    finance_service.balance_repo.get_system_balance = AsyncMock(return_value=None)
    finance_service.balance_repo.create = AsyncMock(return_value=mock_balance)
    
    result = await finance_service.get_or_create_system_balance(Currency.RUB, BalanceType.WORK)
    
    assert result == mock_balance
    finance_service.balance_repo.create.assert_called_once_with({
        "is_system": True,
        "type": BalanceType.WORK,
        "currency": Currency.RUB,
        "amount": 0,
    })

# --- Deposit and Withdrawal Tests ---

@pytest.mark.asyncio
async def test_deposit_user(finance_service):
    user = MagicMock(spec=User)
    user.id = 1
    user.role = UserRole.TRADER
    
    work_balance = MagicMock()
    work_balance.id = 10
    
    finance_service.get_or_create_user_balance = AsyncMock(return_value=work_balance)
    finance_service.transfer = AsyncMock()
    
    await finance_service.deposit(
        amount=Decimal("100.0"),
        currency=Currency.USDT,
        reference_id="dep-1",
        user=user
    )
    
    finance_service.get_or_create_user_balance.assert_called_once_with(user, BalanceType.WORK, Currency.USDT)
    finance_service.transfer.assert_called_once_with(
        amount=Decimal("100.0"),
        currency=Currency.USDT,
        reference_type=LedgerReferenceType.DEPOSIT,
        reference_id="dep-1",
        from_balance_id=None,
        to_balance_id=10,
        description="Deposit"
    )

@pytest.mark.asyncio
async def test_create_order_payin(finance_service):
    order = MagicMock(spec=Order)
    order.id = 100
    order.amount_usdt = Decimal("50.0")
    order.direction = PaymentDirection.PAYIN
    
    trader = MagicMock(spec=User)
    trader.id = 1
    
    work_balance = MagicMock()
    work_balance.id = 10
    escrow_balance = MagicMock()
    escrow_balance.id = 11
    
    # Mock get_or_create_user_balance to return work then escrow
    async def mock_get_balance(user, b_type, currency):
        if b_type == BalanceType.WORK:
            return work_balance
        return escrow_balance
        
    finance_service.get_or_create_user_balance = AsyncMock(side_effect=mock_get_balance)
    finance_service.transfer = AsyncMock()
    
    await finance_service.create_order(order=order, trader=trader)
    
    finance_service.transfer.assert_called_once_with(
        amount=Decimal("50.0"),
        currency=Currency.USDT,
        reference_type=LedgerReferenceType.ORDER_PAYIN,
        reference_id="100",
        from_balance_id=10,
        to_balance_id=11,
        description="Create payin order"
    )

@pytest.mark.asyncio
async def test_cancel_order_payin(finance_service):
    order = MagicMock(spec=Order)
    order.id = 100
    order.amount_usdt = Decimal("50.0")
    order.direction = PaymentDirection.PAYIN
    
    trader = MagicMock(spec=User)
    trader.id = 1
    
    work_balance = MagicMock()
    work_balance.id = 10
    escrow_balance = MagicMock()
    escrow_balance.id = 11
    
    async def mock_get_balance(user, b_type, currency):
        if b_type == BalanceType.WORK:
            return work_balance
        return escrow_balance
        
    finance_service.get_or_create_user_balance = AsyncMock(side_effect=mock_get_balance)
    finance_service.transfer = AsyncMock()
    
    await finance_service.cancel_order(order=order, trader=trader)
    
    finance_service.transfer.assert_called_once_with(
        amount=Decimal("50.0"),
        currency=Currency.USDT,
        reference_type=LedgerReferenceType.ORDER_PAYIN,
        reference_id="100",
        from_balance_id=11,
        to_balance_id=10,
        description="Cancel payin order"
    )

@pytest.mark.asyncio
async def test_withdrawal_merchant(finance_service):
    merchant = MagicMock(spec=Merchant)
    merchant.id = 1
    
    work_balance = MagicMock()
    work_balance.id = 10
    system_balance = MagicMock()
    system_balance.id = 99
    
    finance_service.get_or_create_merchant_balance = AsyncMock(return_value=work_balance)
    finance_service.get_or_create_system_balance = AsyncMock(return_value=system_balance)
    finance_service.transfer = AsyncMock()
    
    await finance_service.withdrawal(
        amount=Decimal("500.0"),
        currency=Currency.USDT,
        reference_id="with-1",
        merchant=merchant,
        fee_amount=Decimal("5.0")
    )
    
    finance_service.get_or_create_merchant_balance.assert_called_once_with(merchant, Currency.USDT, BalanceType.WORK)
    finance_service.get_or_create_system_balance.assert_called_once_with(Currency.USDT)
    
    assert finance_service.transfer.call_count == 2
    # Fee transfer
    finance_service.transfer.assert_any_call(
        amount=Decimal("5.0"),
        currency=Currency.USDT,
        reference_type=LedgerReferenceType.SYSTEM_COMMISSION,
        reference_id="with-1",
        from_balance_id=10,
        to_balance_id=99,
        description="Withdrawal fee"
    )
    # Main withdrawal transfer
    finance_service.transfer.assert_any_call(
        amount=Decimal("500.0"),
        currency=Currency.USDT,
        reference_type=LedgerReferenceType.WITHDRAWAL,
        reference_id="with-1",
        from_balance_id=10,
        to_balance_id=None,
        description="Withdrawal"
    )

@pytest.mark.asyncio
async def test_recalculate_order_increase(finance_service):
    order = MagicMock(spec=Order)
    order.id = 100
    order.amount_usdt = Decimal("50.0")
    order.direction = PaymentDirection.PAYIN
    
    trader = MagicMock(spec=User)
    trader.id = 1
    
    work_balance = MagicMock()
    work_balance.id = 10
    escrow_balance = MagicMock()
    escrow_balance.id = 11
    
    async def mock_get_balance(user, b_type, currency):
        if b_type == BalanceType.WORK:
            return work_balance
        return escrow_balance
        
    finance_service.get_or_create_user_balance = AsyncMock(side_effect=mock_get_balance)
    finance_service.transfer = AsyncMock()
    
    # Increase amount by 10
    await finance_service.recalculate_order(
        order=order,
        old_amount_usdt=Decimal("50.0"),
        new_amount_usdt=Decimal("60.0"),
        trader=trader
    )
    
    finance_service.transfer.assert_called_once_with(
        amount=Decimal("10.0"),
        currency=Currency.USDT,
        reference_type=LedgerReferenceType.ORDER_PAYIN,
        reference_id="100",
        from_balance_id=10,
        to_balance_id=11,
        description="Recalculate payin order delta"
    )

# ────────────────────────────────────────────────────────────────
# get_merchant_aggregate_balances — synthetic per-(type, currency)
# rows summed across every terminal of the user, plus user-level
# balances merged in.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_merchant_aggregate_balances_sums_terminal_and_user_balances(finance_service):
    # 1st execute: list merchant_ids for user (terminal_ids = [10, 11]).
    # 2nd execute: GROUP BY (type, currency) sum from `Balance` rows for those terminals.
    # 3rd execute: list user-level Balance rows.

    user_balance = MagicMock()
    user_balance.type = BalanceType.WORK
    user_balance.currency = Currency.USDT
    user_balance.amount = Decimal("5")

    finance_service.session.execute = _mock_session_execute(
        [10, 11],
        [(BalanceType.WORK, Currency.USDT, Decimal("100"))],
        [user_balance],
    )

    result = await finance_service.get_merchant_aggregate_balances(user_id=42)

    # Single synthetic row covering BalanceType.WORK + USDT, summed across
    # the merchant terminals AND the user-level balance.
    assert len(result) == 1
    [row] = result
    assert row.type == BalanceType.WORK
    assert row.currency == Currency.USDT
    assert row.amount == Decimal("105")  # 100 from terminals + 5 from owner
    # Synthetic rows: no concrete owner, no merchant id, never system.
    assert row.user_id is None
    assert row.merchant_id is None
    assert row.is_system is False


@pytest.mark.asyncio
async def test_get_merchant_aggregate_balances_no_terminals_only_user_balance(finance_service):
    """User with merchant role but no terminals yet — only user-level balances counted."""
    user_balance = MagicMock()
    user_balance.type = BalanceType.WORK
    user_balance.currency = Currency.USDT
    user_balance.amount = Decimal("3.5")

    finance_service.session.execute = _mock_session_execute(
        [],            # no terminals
        [user_balance],  # user-level balance only
    )

    result = await finance_service.get_merchant_aggregate_balances(user_id=42)

    assert len(result) == 1
    assert result[0].amount == Decimal("3.5")
    # Only TWO queries when there are no terminals (skip the GROUP BY one).
    assert finance_service.session.execute.await_count == 2


@pytest.mark.asyncio
async def test_get_merchant_aggregate_balances_groups_by_type_and_currency(finance_service):
    """Multiple (type, currency) buckets → multiple synthetic rows, each
    independent of the others."""
    user_balance_escrow = MagicMock()
    user_balance_escrow.type = BalanceType.ESCROW
    user_balance_escrow.currency = Currency.USDT
    user_balance_escrow.amount = Decimal("0.25")

    finance_service.session.execute = _mock_session_execute(
        [7],
        [
            (BalanceType.WORK, Currency.USDT, Decimal("100")),
            (BalanceType.ESCROW, Currency.USDT, Decimal("50")),
        ],
        [user_balance_escrow],
    )

    result = await finance_service.get_merchant_aggregate_balances(user_id=42)

    by_type = {(r.type, r.currency): r for r in result}
    assert by_type[(BalanceType.WORK, Currency.USDT)].amount == Decimal("100")
    # ESCROW totals = 50 (from terminals) + 0.25 (user-level)
    assert by_type[(BalanceType.ESCROW, Currency.USDT)].amount == Decimal("50.25")


# ────────────────────────────────────────────────────────────────
# get_bot_balances_for_tg_user — aggregate WORK/ESCROW for every
# terminal a Telegram user owns, plus owner-level balances.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_bot_balances_for_tg_user_aggregates_terminals_and_owner(finance_service):
    """Two terminals owned by the same TG user; each has WORK and ESCROW.
    The owner has a separate user-level balance which is folded into totals."""

    m1 = MagicMock(spec=Merchant); m1.id = 1; m1.user_id = 100; m1.name = "T1"
    m2 = MagicMock(spec=Merchant); m2.id = 2; m2.user_id = 100; m2.name = "T2"

    # Mock the lazy-imported MerchantRepository.list_by_telegram_user_id.
    with patch(
        "app.modules.merchants.repository.MerchantRepository"
    ) as mock_repo_cls:
        repo_inst = mock_repo_cls.return_value
        repo_inst.list_by_telegram_user_id = AsyncMock(return_value=[m1, m2])

        # WORK balance: 10 USDT each terminal; ESCROW: 5 USDT each terminal.
        def _make_balance(amount):
            b = MagicMock()
            b.amount = Decimal(str(amount))
            return b

        async def mock_get_merchant_balance(merchant, currency, b_type):
            if b_type == BalanceType.WORK:
                return _make_balance("10")
            return _make_balance("5")

        async def mock_get_user_balance(user, b_type, currency):
            if b_type == BalanceType.WORK:
                return _make_balance("3")
            return _make_balance("1")

        finance_service.get_or_create_merchant_balance = AsyncMock(
            side_effect=mock_get_merchant_balance
        )
        finance_service.get_or_create_user_balance = AsyncMock(
            side_effect=mock_get_user_balance
        )
        # Owner User lookup
        owner_user = MagicMock(spec=User)
        owner_user.id = 100
        finance_service.session.get = AsyncMock(return_value=owner_user)

        result = await finance_service.get_bot_balances_for_tg_user(tg_user_id=999)

    assert result.currency == Currency.USDT.value
    # 2 terminals × WORK 10 = 20, plus owner WORK 3 = 23
    assert result.total_work == 23.0
    # 2 terminals × ESCROW 5 = 10, plus owner ESCROW 1 = 11
    assert result.total_escrow == 11.0
    assert result.owner_work == 3.0
    assert result.owner_escrow == 1.0
    assert len(result.terminals) == 2
    assert {t.id for t in result.terminals} == {1, 2}
    for t in result.terminals:
        assert t.work == 10.0
        assert t.escrow == 5.0
        assert t.currency == Currency.USDT.value


@pytest.mark.asyncio
async def test_get_bot_balances_for_tg_user_no_terminals_returns_empty_zeros(finance_service):
    """TG user with zero terminals — totals zeroed, terminals empty."""
    with patch(
        "app.modules.merchants.repository.MerchantRepository"
    ) as mock_repo_cls:
        repo_inst = mock_repo_cls.return_value
        repo_inst.list_by_telegram_user_id = AsyncMock(return_value=[])

        result = await finance_service.get_bot_balances_for_tg_user(tg_user_id=999)

    assert result.terminals == []
    assert result.total_work == 0.0
    assert result.total_escrow == 0.0
    assert result.owner_work == 0.0
    assert result.owner_escrow == 0.0


# ────────────────────────────────────────────────────────────────
# reconcile_for_dispute — money normalisation on dispute open.
# Disputes now open from ACTIVE statuses (PENDING / RECEIPT_UPLOADED)
# too; their collateral is already in ESCROW so reconcile must be a
# strict no-op — re-freezing would double-charge the trader's WORK.
# ────────────────────────────────────────────────────────────────


def _dispute_order(amount_usdt="100.0"):
    order = MagicMock(spec=Order)
    order.id = 500
    order.direction = PaymentDirection.PAYIN
    order.amount_usdt = Decimal(amount_usdt)
    order.fee_usdt = Decimal("2.0")
    order.trader_fee_usdt = Decimal("1.0")
    return order


@pytest.mark.asyncio
@pytest.mark.parametrize("pre_status", [
    OrderStatus.PENDING,
    OrderStatus.RECEIPT_UPLOADED,
])
async def test_reconcile_for_dispute_active_status_is_noop(finance_service, pre_status):
    """PENDING / RECEIPT_UPLOADED — collateral already frozen in ESCROW.
    reconcile must NOT issue any transfer (no re-freeze, no double-charge)."""
    order = _dispute_order()
    trader = MagicMock(spec=User)
    trader.id = 7
    merchant = MagicMock(spec=Merchant)
    merchant.id = 1

    finance_service.transfer = AsyncMock()
    finance_service.get_or_create_user_balance = AsyncMock()
    finance_service.get_or_create_merchant_balance = AsyncMock()

    await finance_service.reconcile_for_dispute(
        order=order, merchant=merchant, trader=trader, pre_status=pre_status,
    )

    # Strict no-op: zero transfers, zero balance fetches (we bail before them).
    finance_service.transfer.assert_not_awaited()
    finance_service.get_or_create_user_balance.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_for_dispute_failed_status_refreezes(finance_service):
    """FAILED — collateral was released to trader WORK; reconcile re-freezes
    it back to ESCROW (single transfer). Guards against regressing the no-op
    branch into swallowing the terminal-status path."""
    order = _dispute_order()
    trader = MagicMock(spec=User)
    trader.id = 7
    merchant = MagicMock(spec=Merchant)
    merchant.id = 1

    work = MagicMock(); work.id = 10
    escrow = MagicMock(); escrow.id = 11

    async def _bal(user, b_type, currency):
        return work if b_type == BalanceType.WORK else escrow

    finance_service.get_or_create_user_balance = AsyncMock(side_effect=_bal)
    finance_service.transfer = AsyncMock()

    await finance_service.reconcile_for_dispute(
        order=order, merchant=merchant, trader=trader, pre_status=OrderStatus.FAILED,
    )

    # Exactly one transfer: WORK → ESCROW for the full collateral.
    finance_service.transfer.assert_awaited_once()
    call = finance_service.transfer.await_args.kwargs
    assert call["from_balance_id"] == 10  # trader WORK
    assert call["to_balance_id"] == 11    # trader ESCROW
    assert call["amount"] == Decimal("100.0")


@pytest.mark.asyncio
async def test_reconcile_for_dispute_no_trader_is_noop(finance_service):
    """No trader → nothing was ever frozen → no-op regardless of status."""
    order = _dispute_order()
    finance_service.transfer = AsyncMock()

    await finance_service.reconcile_for_dispute(
        order=order, merchant=MagicMock(), trader=None, pre_status=OrderStatus.FAILED,
    )

    finance_service.transfer.assert_not_awaited()
