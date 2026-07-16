"""Slot mini-app HTTP router (config + spin settlement).

NOTE — integration-parked. This module is the *integration* half of the
service: it deliberately imports the host monorepo's finance/auth/DB layer
(``app.*`` from ``backend-py``). It is NOT mounted anywhere right now — the
game was carved out and disconnected on purpose (see ``../README.md``). To
reconnect, mount ``router`` into ``backend-py``'s v1 API router and ensure
the ``miniapp_casino_spins`` table exists (``migrations/001_create_casino_spins.sql``).

Pure game maths live in ``game.py`` (standalone + unit-tested). Schemas and
config are local to this service (``schemas.py`` / ``config.py``).
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Host-backend imports (resolved only when mounted inside backend-py).
from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency
from app.infrastructure.db.session import get_db
from app.modules.finance.service import FinanceService
from app.modules.users.models import User
from app.modules.users.permissions import require_trader

from . import game
from .config import get_settings
from .schemas import (
    CasinoBalanceSchema,
    CasinoConfigResponse,
    CasinoConfigSchema,
    CasinoSpinRequest,
    CasinoSpinResponse,
)

router = APIRouter()


def _round_usdt(value: float) -> float:
    return round(float(value or 0), 4)


def _decimal_usdt(value: float) -> Decimal:
    return Decimal(str(_round_usdt(value)))


def _format_balance(value: float) -> str:
    return f"{value:.2f} USDT"


async def _table_exists(session: AsyncSession, table_name: str) -> bool:
    row = (
        await session.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_name = :table_name
                LIMIT 1
                """
            ),
            {"table_name": table_name},
        )
    ).first()
    return bool(row)


async def _insert_spin_log(
    session: AsyncSession,
    *,
    trader_id: int,
    bet_amount: float,
    payout_amount: float,
    multiplier: float,
    is_win: bool,
    symbols: list[str],
    balance_before: float,
    balance_after: float,
) -> None:
    if not await _table_exists(session, "miniapp_casino_spins"):
        return
    await session.execute(
        text(
            """
            INSERT INTO miniapp_casino_spins (
                user_id, bet_usdt, payout_usdt, multiplier, is_win, symbols,
                balance_before_usdt, balance_after_usdt, created_at
            ) VALUES (
                :trader_id, :bet_amount, :payout_amount, :multiplier, :is_win, :symbols,
                :balance_before, :balance_after, NOW()
            )
            """
        ),
        {
            "trader_id": trader_id,
            "bet_amount": bet_amount,
            "payout_amount": payout_amount,
            "multiplier": multiplier,
            "is_win": is_win,
            "symbols": ",".join(symbols),
            "balance_before": balance_before,
            "balance_after": balance_after,
        },
    )


@router.get(
    "/miniapp/casino/config",
    response_model=CasinoConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Telegram slot mini app config",
)
async def get_casino_config() -> CasinoConfigResponse:
    settings = get_settings()
    return CasinoConfigResponse(
        config=CasinoConfigSchema(
            rtp=settings.RTP,
            minBet=settings.MIN_BET,
            maxBet=settings.MAX_BET,
            betStepMin=settings.BET_STEP_MIN,
            betStepMax=settings.BET_STEP_MAX,
        )
    )


@router.post(
    "/miniapp/casino/spin",
    response_model=CasinoSpinResponse,
    status_code=status.HTTP_200_OK,
    summary="Spin Telegram slot mini app and settle trader balance",
)
async def spin_casino(
    payload: CasinoSpinRequest,
    current_user: User = Depends(require_trader),
    session: AsyncSession = Depends(get_db),
) -> CasinoSpinResponse:
    settings = get_settings()
    bet_amount = _round_usdt(float(payload.betAmount))

    if bet_amount < settings.MIN_BET or bet_amount > settings.MAX_BET:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Bet must be from {settings.MIN_BET} to {settings.MAX_BET} USDT.",
        )

    finance = FinanceService(session)
    reference_id = f"miniapp-casino-{uuid4().hex[:20]}"
    currency = Currency.USDT

    async with session.begin_nested():
        work_balance = await finance.get_or_create_user_balance(current_user, BalanceType.WORK, currency)
        balance_before = _round_usdt(float(work_balance.amount or 0))

        if bet_amount > balance_before:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Not enough available balance for this bet.",
            )

        outcome = game.roll(settings.RTP)
        payout_amount = _round_usdt(bet_amount * outcome.multiplier) if outcome.is_win else 0.0
        profit_amount = _round_usdt(payout_amount - bet_amount)

        await finance.transfer(
            amount=_decimal_usdt(bet_amount),
            currency=currency,
            reference_type=LedgerReferenceType.INTERNAL_TRANSFER,
            reference_id=reference_id,
            from_balance_id=work_balance.id,
            to_balance_id=None,
            description="Prime Casino bet",
        )

        if payout_amount > 0:
            await finance.transfer(
                amount=_decimal_usdt(payout_amount),
                currency=currency,
                reference_type=LedgerReferenceType.DEPOSIT,
                reference_id=reference_id,
                from_balance_id=None,
                to_balance_id=work_balance.id,
                description="Prime Casino payout",
            )

        await session.flush()
        await session.refresh(work_balance)
        balance_after = _round_usdt(float(work_balance.amount or 0))

        await _insert_spin_log(
            session,
            trader_id=int(current_user.id),
            bet_amount=bet_amount,
            payout_amount=payout_amount,
            multiplier=outcome.multiplier,
            is_win=outcome.is_win,
            symbols=outcome.symbols,
            balance_before=balance_before,
            balance_after=balance_after,
        )

    return CasinoSpinResponse(
        symbols={"left": outcome.symbols[0], "center": outcome.symbols[1], "right": outcome.symbols[2]},
        isWin=outcome.is_win,
        multiplier=outcome.multiplier,
        betAmount=bet_amount,
        payoutAmount=payout_amount,
        profitAmount=profit_amount,
        balance=CasinoBalanceSchema(
            balanceUsdt=balance_after,
            balanceText=_format_balance(balance_after),
            totalBalanceUsdt=balance_after,
            frozenBalanceUsdt=0.0,
            availableBalanceUsdt=balance_after,
        ),
    )
