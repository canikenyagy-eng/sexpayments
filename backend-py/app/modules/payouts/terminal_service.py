"""Payout terminal management (admin) + auth lookup.

A payout terminal is the payout-side analogue of a payin Merchant: own API key,
own USDT balance (funded by admin top-up), rate config and economics. This
service handles terminal CRUD, the trader ACL, and balance top-ups; payouts
themselves are created/served by ``PayoutService``.
"""
import secrets
import uuid as uuid_lib
from decimal import Decimal
from typing import List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.balances import BalanceType
from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.core.exceptions import NotFoundException, ValidationException
from app.core.security import encrypt_api_secret
from app.modules.base.service import BaseService
from app.modules.finance.service import FinanceService
from app.modules.payouts.models import PayoutTerminal
from app.modules.payouts.terminal_repository import PayoutTerminalRepository


class PayoutTerminalService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.repository = PayoutTerminalRepository(session)
        self.finance = FinanceService(session)

    async def get_by_api_key(self, api_key: str) -> Optional[PayoutTerminal]:
        return await self.repository.get_by_api_key(api_key)

    async def list_terminals(self, *, owner_id: Optional[int] = None, skip: int = 0, limit: int = 100) -> List[PayoutTerminal]:
        return await self.repository.list_all(owner_id=owner_id, skip=skip, limit=limit)

    async def get_terminal(self, terminal_id: int) -> PayoutTerminal:
        terminal = await self.repository.get(terminal_id)
        if not terminal:
            raise NotFoundException("Payout terminal not found")
        return terminal

    async def create_terminal(self, data, *, admin_user_id: Optional[int] = None) -> Tuple[PayoutTerminal, str, str]:
        """Create a payout terminal: generate api_key/secret, seed WORK+ESCROW
        balances. Returns (terminal, api_key, api_secret_plain). The plaintext
        secret is shown ONCE to the admin; only the encrypted form is stored."""
        api_key = secrets.token_hex(16)
        api_secret = secrets.token_urlsafe(32)

        async with self.session.begin_nested():
            terminal = await self.repository.create({
                "user_id": data.owner_user_id,
                "name": data.name,
                "status": data.status or TerminalStatus.ENABLED,
                "currency": data.currency or Currency.RUB,
                "api_key": api_key,
                "api_secret": encrypt_api_secret(api_secret),
                "rate_config_id": data.rate_config_id,
                "commission_percent": Decimal(str(data.commission_percent or 0)),
                "ttl_minutes": int(data.ttl_minutes or 60),
                "receipts_to_close": int(data.receipts_to_close or 1),
                "min_amount": (Decimal(str(data.min_amount)) if data.min_amount is not None else None),
                "max_amount": (Decimal(str(data.max_amount)) if data.max_amount is not None else None),
                "webhook_url": data.webhook_url,
            })
            await self.finance.get_or_create_payout_terminal_balance(terminal.id, Currency.USDT, BalanceType.WORK)
            await self.finance.get_or_create_payout_terminal_balance(terminal.id, Currency.USDT, BalanceType.ESCROW)
            if data.trader_ids is not None:
                await self.repository.set_traders(terminal.id, list(data.trader_ids))
            await self.audit_log(
                action="create_payout_terminal", entity_type="payout_terminal",
                entity_id=terminal.id, user_id=admin_user_id, new_values={"name": data.name},
            )
        return terminal, api_key, api_secret

    async def update_terminal(self, terminal_id: int, config, *, admin_user_id: Optional[int] = None) -> PayoutTerminal:
        terminal = await self.get_terminal(terminal_id)
        fields = config.model_dump(exclude_unset=True, exclude={"trader_ids"})
        # Coerce status string → enum if provided.
        if "status" in fields and fields["status"] is not None:
            fields["status"] = TerminalStatus(fields["status"])
        async with self.session.begin_nested():
            if fields:
                await self.repository.update(terminal_id, fields)
            trader_ids = getattr(config, "trader_ids", None)
            if trader_ids is not None:
                await self.repository.set_traders(terminal_id, list(trader_ids))
            await self.audit_log(
                action="update_payout_terminal", entity_type="payout_terminal",
                entity_id=terminal_id, user_id=admin_user_id, new_values=fields,
            )
        return await self.get_terminal(terminal_id)

    async def topup(self, terminal_id: int, amount, *, admin_user_id: Optional[int] = None) -> Decimal:
        """Credit the terminal's WORK balance (admin). Returns the new balance."""
        amt = Decimal(str(amount))
        if amt <= 0:
            raise ValidationException("Top-up amount must be positive")
        terminal = await self.get_terminal(terminal_id)
        async with self.session.begin_nested():
            balance = await self.finance.get_or_create_payout_terminal_balance(
                terminal.id, Currency.USDT, BalanceType.WORK
            )
            await self.finance.adjust_balance(
                balance, amt, reason="Payout terminal top-up",
                reference_id=f"payout_terminal_topup:{terminal_id}:{uuid_lib.uuid4().hex[:12]}",
            )
            await self.audit_log(
                action="payout_terminal_topup", entity_type="payout_terminal",
                entity_id=terminal_id, user_id=admin_user_id, new_values={"amount": float(amt)},
            )
        balance = await self.finance.get_or_create_payout_terminal_balance(
            terminal.id, Currency.USDT, BalanceType.WORK
        )
        return Decimal(str(balance.amount or 0))

    async def get_balances(self, terminal_id: int) -> dict:
        work = await self.finance.get_or_create_payout_terminal_balance(terminal_id, Currency.USDT, BalanceType.WORK)
        escrow = await self.finance.get_or_create_payout_terminal_balance(terminal_id, Currency.USDT, BalanceType.ESCROW)
        return {"work_usdt": Decimal(str(work.amount or 0)), "escrow_usdt": Decimal(str(escrow.amount or 0))}

    async def trader_ids(self, terminal_id: int) -> List[int]:
        return await self.repository.trader_ids_for_terminal(terminal_id)
