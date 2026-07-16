from datetime import date, datetime, time
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency, WithdrawalStatus
from app.common.enums.users import UserRole
from app.core.exceptions import NotFoundException, ValidationException
from app.modules.base.schemas import BaseSchema
from app.modules.finance.schemas import (
    LedgerEntryResponse,
    RejectWithdrawalRequest,
    WithdrawalRequestCreate,
    WithdrawalRequestResponse,
)
from app.modules.finance.schemas.admin import (
    AdminHashDepositConfirmResponse,
    AdminHashDepositRequest,
    AdminHashDepositVerifyResponse,
)
from app.modules.finance.schemas.trader import (
    BalanceTraderResponse,
    TraderFinanceStatsResponse,
    WithdrawalRequestTraderResponse,
)
from app.modules.finance.service import FinanceService
from app.modules.users.models import User
from app.modules.users.permissions import get_current_user, require_admin, require_trader_or_teamlead

router = APIRouter()


@router.get(
    "/my-withdrawals",
    response_model=List[WithdrawalRequestTraderResponse],
    summary="List current user's withdrawals (Trader/Teamlead)",
)
async def list_my_withdrawals(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    status: Optional[WithdrawalStatus] = None,
    current_user: User = Depends(require_trader_or_teamlead),
    session: AsyncSession = Depends(get_db),
):
    service = FinanceService(session)
    return await service.list_user_withdrawals(
        current_user.id, current_user.role, status=status, skip=skip, limit=limit,
    )


@router.post(
    "/my-withdrawals",
    response_model=WithdrawalRequestTraderResponse,
    summary="Create withdrawal request (Trader/Teamlead)",
)
async def create_my_withdrawal(
    data: WithdrawalRequestCreate,
    current_user: User = Depends(require_trader_or_teamlead),
    session: AsyncSession = Depends(get_db),
):
    service = FinanceService(session)
    return await service.create_withdrawal_request(data, user=current_user)


@router.get(
    "/withdrawals",
    response_model=List[WithdrawalRequestResponse],
    dependencies=[Depends(require_admin)],
    summary="List withdrawal requests (Admin)",
)
async def list_withdrawals(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    status: Optional[WithdrawalStatus] = None,
    user_role: Optional[UserRole] = Query(None, description="Filter by user role"),
    user_login: Optional[str] = Query(None, description="Search by user login"),
    session: AsyncSession = Depends(get_db),
):
    service = FinanceService(session)
    return await service.list_all_withdrawals_enriched(
        status=status,
        skip=skip,
        limit=limit,
        user_role=user_role,
        user_login=user_login,
    )


@router.post(
    "/withdrawals/{request_id}/approve",
    response_model=WithdrawalRequestResponse,
    dependencies=[Depends(require_admin)],
    summary="Approve withdrawal request (Admin)",
)
async def approve_withdrawal(
    request_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = FinanceService(session)
    return await service.approve_withdrawal_request(request_id=request_id, admin_id=current_user.id)


@router.post(
    "/withdrawals/{request_id}/reject",
    response_model=WithdrawalRequestResponse,
    dependencies=[Depends(require_admin)],
    summary="Reject withdrawal request (Admin)",
)
async def reject_withdrawal(
    request_id: int,
    data: RejectWithdrawalRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = FinanceService(session)
    return await service.reject_withdrawal_request(
        request_id=request_id,
        admin_id=current_user.id,
        reason=data.reason,
    )


@router.get(
    "/my-balances",
    summary="List current user balances",
)
async def list_my_balances(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Return the caller's balances.

    For traders/teamleads we strip ``user_id`` / ``merchant_id`` /
    ``is_system`` — they always equal "you" / None / False in this
    path, so the columns are noise that would leak the relational
    schema to DevTools. Merchant callers still get ``merchant_id``
    because their admin/merchant UI keys on it.
    """
    service = FinanceService(session)
    balances = await service.get_my_balances(current_user)
    is_trader_audience = current_user.role in (UserRole.TRADER, UserRole.TEAMLEAD)
    items = []
    # For merchants `get_my_balances` returns synthetic aggregated rows with
    # no primary key. The UI keys balance rows by `id`, so we fall back to a
    # stable negative value derived from (type, currency) to avoid collisions
    # with real balances (which always have positive ids).
    for idx, b in enumerate(balances):
        bid = b.id if b.id is not None else -(idx + 1)
        if is_trader_audience:
            # BalanceTraderResponse shape — only operational fields.
            items.append(
                {
                    "id": bid,
                    "type": b.type.value,
                    "currency": b.currency.value,
                    "amount": float(b.amount),
                }
            )
        else:
            items.append(
                {
                    "id": bid,
                    "user_id": b.user_id,
                    "merchant_id": b.merchant_id,
                    "is_system": b.is_system,
                    "type": b.type.value,
                    "currency": b.currency.value,
                    "amount": float(b.amount),
                }
            )
    return items


@router.get(
    "/my-stats",
    response_model=TraderFinanceStatsResponse,
    summary="Current trader finance statistics for a date range",
)
async def get_my_finance_stats(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    current_user: User = Depends(require_trader_or_teamlead),
    session: AsyncSession = Depends(get_db),
):
    service = FinanceService(session)
    start = datetime.combine(date_from, time.min) if date_from else None
    end = datetime.combine(date_to, time.max) if date_to else None
    return await service.get_trader_finance_stats(
        current_user,
        date_from=start,
        date_to=end,
    )


@router.get(
    "/my-ledger",
    response_model=List[LedgerEntryResponse],
    summary="List current user's finance ledger (Trader/Teamlead)",
)
async def list_my_ledger_entries(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    reference_type: Optional[LedgerReferenceType] = None,
    order_search: Optional[str] = Query(None, description="Search by order id/uuid/external id"),
    amount_from: Optional[float] = Query(None),
    amount_to: Optional[float] = Query(None),
    current_user: User = Depends(require_trader_or_teamlead),
    session: AsyncSession = Depends(get_db),
):
    service = FinanceService(session)
    return await service.list_user_ledger_entries(
        current_user,
        reference_type=reference_type,
        skip=skip,
        limit=limit,
        order_search=order_search,
        amount_from=amount_from,
        amount_to=amount_to,
    )


@router.get(
    "/balances",
    dependencies=[Depends(require_admin)],
    summary="List all balances (Admin)",
)
async def list_balances(
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    user_id: Optional[int] = Query(
        None,
        description="Filter balances by owning user (traders/teamleads); ignored when merchant_id is set",
    ),
    merchant_id: Optional[int] = Query(
        None,
        description="Filter balances by owning merchant; takes precedence over user_id",
    ),
    session: AsyncSession = Depends(get_db),
):
    service = FinanceService(session)
    if user_id is not None or merchant_id is not None:
        # Admin lookup for a single entity — reuse the same repo method that
        # powers /my-balances. `get_my_balances` prefers merchant_id when set,
        # otherwise filters by user_id, so this branch returns *just* that
        # entity's balances (not a paginated slice of the whole table).
        balances = await service.balance_repo.get_my_balances(
            user_id=user_id or 0, merchant_id=merchant_id,
        )
    else:
        balances = await service.list_all_balances(skip=skip, limit=limit)
    return [
        {
            "id": b.id,
            "user_id": b.user_id,
            "merchant_id": b.merchant_id,
            "is_system": b.is_system,
            "type": b.type.value,
            "currency": b.currency.value,
            "amount": float(b.amount),
        }
        for b in balances
    ]


class AdminBalanceAdjustRequest(BaseSchema):
    user_id: Optional[int] = Field(None, description="Target user ID (mutually exclusive with merchant_id)")
    merchant_id: Optional[int] = Field(None, description="Target merchant ID (mutually exclusive with user_id)")
    amount: float = Field(..., description="Amount to adjust: positive = credit, negative = debit")
    currency: Currency = Field(Currency.USDT, description="Currency of the adjustment")
    balance_type: BalanceType = Field(BalanceType.WORK, description="Balance type to adjust")
    reason: str = Field(..., min_length=1, max_length=500, description="Mandatory reason for the adjustment")

    @field_validator("amount")
    @classmethod
    def amount_not_zero(cls, v: float) -> float:
        if v == 0:
            raise ValueError("Amount must not be zero")
        return v


class AdminBalanceAdjustResponse(BaseSchema):
    ledger_entry_id: int
    balance_id: int
    amount: float
    currency: str
    balance_type: str
    entity: str


@router.post(
    "/admin/adjust",
    dependencies=[Depends(require_admin)],
    response_model=AdminBalanceAdjustResponse,
    summary="Admin balance adjustment (Admin) — credit or debit any user/merchant balance",
)
async def admin_adjust_balance(
    data: AdminBalanceAdjustRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    from app.modules.merchants.models import Merchant
    from app.modules.finance.models import Balance

    if not data.user_id and not data.merchant_id:
        raise ValidationException("Provide user_id or merchant_id")
    if data.user_id and data.merchant_id:
        raise ValidationException("Provide either user_id or merchant_id, not both")

    # amount != 0 is guaranteed by the schema's amount_not_zero validator.
    service = FinanceService(session)
    is_credit = data.amount > 0

    async with session.begin_nested():
        # Resolve entity and balance
        if data.merchant_id:
            merchant = await session.get(Merchant, data.merchant_id)
            if not merchant:
                raise NotFoundException(f"Merchant {data.merchant_id} not found")
            balance = await service.get_or_create_merchant_balance(
                merchant, data.currency, data.balance_type
            )
            entity_label = f"merchant:{data.merchant_id}"
        else:
            user = await session.get(User, data.user_id)
            if not user:
                raise NotFoundException(f"User {data.user_id} not found")
            balance = await service.get_or_create_user_balance(
                user, data.balance_type, data.currency
            )
            entity_label = f"user:{data.user_id}"

        # Single source of truth for moving a balance — credit/debit through the
        # ledger lives in FinanceService.adjust_balance, not inline here.
        entry = await service.adjust_balance(
            balance, Decimal(str(data.amount)),
            reason=f"[admin {'credit' if is_credit else 'debit'}] {data.reason}",
            reference_id=f"admin_adjust_{current_user.id}_{balance.id}",
        )

    await service.audit_log(
        action="admin_balance_adjust",
        entity_type="balance",
        entity_id=str(balance.id),
        user_id=current_user.id,
        new_values={
            "entity": entity_label,
            "amount": data.amount,
            "currency": data.currency.value,
            "balance_type": data.balance_type.value,
            "reason": data.reason,
        },
    )

    return AdminBalanceAdjustResponse(
        ledger_entry_id=entry.id,
        balance_id=balance.id,
        amount=data.amount,
        currency=data.currency.value,
        balance_type=data.balance_type.value,
        entity=entity_label,
    )


# ── Top-up by TRC20 tx hash (Admin) ─────────────────────────────────────────


async def _resolve_trader_target(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if not user:
        raise NotFoundException(f"User {user_id} not found")
    if user.role != UserRole.TRADER:
        raise ValidationException("Target user must be a trader")
    return user


@router.post(
    "/admin/hash-deposit/verify",
    dependencies=[Depends(require_admin)],
    response_model=AdminHashDepositVerifyResponse,
    summary="Preview a TRC20 (USDT) deposit by tx hash — verifies on-chain, does NOT credit (Admin)",
)
async def admin_hash_deposit_verify(
    data: AdminHashDepositRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    user = await _resolve_trader_target(session, data.user_id)
    service = FinanceService(session)
    verified = await service.verify_hash_deposit(data.tx_hash)
    return AdminHashDepositVerifyResponse(
        user_id=user.id,
        trader_login=user.username,
        tx_hash=verified["tx_hash"],
        amount=float(verified["amount"]),
        currency=Currency.USDT.value,
        to_address=verified["to_address"],
        from_address=verified["from_address"],
    )


@router.post(
    "/admin/hash-deposit/confirm",
    dependencies=[Depends(require_admin)],
    response_model=AdminHashDepositConfirmResponse,
    summary="Confirm a TRC20 (USDT) deposit by tx hash — credits the trader's WORK/USDT (Admin)",
)
async def admin_hash_deposit_confirm(
    data: AdminHashDepositRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    user = await _resolve_trader_target(session, data.user_id)
    service = FinanceService(session)
    entry = await service.confirm_hash_deposit(user, data.tx_hash, admin_id=current_user.id)
    return AdminHashDepositConfirmResponse(
        ledger_entry_id=entry.id,
        user_id=user.id,
        amount=float(entry.amount),
        currency=Currency.USDT.value,
        tx_hash=entry.reference_id,
    )


@router.get(
    "/ledger",
    response_model=List[LedgerEntryResponse],
    dependencies=[Depends(require_admin)],
    summary="List ledger entries (Admin)",
)
async def list_ledger_entries(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    reference_type: Optional[LedgerReferenceType] = None,
    order_search: Optional[str] = Query(None, description="Search by order id/uuid/external id"),
    user_login: Optional[str] = Query(None, description="Search by user login"),
    amount_from: Optional[float] = Query(None),
    amount_to: Optional[float] = Query(None),
    session: AsyncSession = Depends(get_db),
):
    service = FinanceService(session)
    return await service.list_ledger_entries(
        reference_type=reference_type,
        skip=skip,
        limit=limit,
        order_search=order_search,
        user_login=user_login,
        amount_from=amount_from,
        amount_to=amount_to,
    )
