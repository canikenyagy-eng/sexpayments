"""Integration test: creating a withdrawal request fires the platform
notification hook (FinanceService._notify_withdrawal_created) with the new
withdrawal's id — against a real ledger.
"""
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.common.enums.balances import BalanceType
from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.common.enums.users import UserRole
from app.core.security import get_password_hash
from app.modules.finance.models import Balance
from app.modules.finance.schemas.admin import WithdrawalRequestCreate
from app.modules.finance.service import FinanceService
from app.modules.merchants.models import Merchant
from app.modules.users.models import User


async def _mk_merchant_with_funds(session, *, amount):
    user = User(
        username=f"m_{uuid4().hex[:6]}",
        password=get_password_hash("pass12345"),
        role=UserRole.MERCHANT,
        totp_enabled=False,
        is_blocked=False,
        use_shared_balance=True,
    )
    session.add(user)
    await session.flush()

    suffix = uuid4().hex[:6]
    merchant = Merchant(
        user_id=user.id,
        name=f"M-{suffix}",
        status=TerminalStatus.ENABLED,
        currency=Currency.RUB,
        api_key=f"key-{suffix}",
        api_secret=f"secret-{suffix}",
        withdrawal_fee_fixed=Decimal("0"),
    )
    session.add(merchant)
    await session.flush()

    session.add(Balance(
        merchant_id=merchant.id, is_system=False,
        type=BalanceType.WORK, currency=Currency.USDT, amount=Decimal(amount),
    ))
    await session.flush()
    return merchant


@pytest.mark.asyncio
async def test_create_withdrawal_fires_notification_hook(session):
    merchant = await _mk_merchant_with_funds(session, amount="500")
    service = FinanceService(session)
    data = WithdrawalRequestCreate(
        amount=Decimal("100"),
        currency=Currency.USDT,
        destination_address="T-destination-address-xyz",
    )

    with patch.object(FinanceService, "_notify_withdrawal_created") as notify:
        wr = await service.create_withdrawal_request(data, merchant=merchant)

    notify.assert_called_once_with(wr.id)
    # Sanity: funds were actually frozen (WORK 500 → 400, ESCROW 100).
    work = (await session.execute(
        Balance.__table__.select().where(
            (Balance.merchant_id == merchant.id) & (Balance.type == BalanceType.WORK)
        )
    )).first()
    assert work.amount == Decimal("400")
