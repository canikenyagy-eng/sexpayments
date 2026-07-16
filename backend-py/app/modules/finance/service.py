import zlib
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency
from app.common.enums.users import UserRole
from app.common.types import utcnow
from app.core.config import get_settings
from app.core.exceptions import ConflictException, NotFoundException, ValidationException
from app.infrastructure.tron.client import TronGridClient, TronGridError
from app.modules.base.service import BaseService
from app.modules.finance.models import Balance, LedgerEntry, WithdrawalRequest
from app.modules.finance.repository import BalanceRepository, LedgerRepository, WithdrawalRequestRepository
from app.common.enums.finances import Currency, WithdrawalStatus
from app.modules.finance.schemas import (
    BalanceRefInfo,
    LedgerEntryResponse,
    WithdrawalRequestCreate,
)
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.users.models import User
from app.common.enums.payments import PaymentDirection
from app.common.enums.orders import OrderStatus

class FinanceService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.balance_repo = BalanceRepository(session)
        self.ledger_repo = LedgerRepository(session)
        self.withdrawal_repo = WithdrawalRequestRepository(session)

    async def get_or_create_user_balance(
        self, user: User, balance_type: BalanceType, currency: Currency
    ) -> Balance:
        """
        Get or create a balance for a user.
        Traders can have WORK, ESCROW, SAFE_DEPOSIT.
        Teamleads and Merchants (as users) can have WORK or ESCROW — the
        merchant user-level balance is used as the settlement account when
        the owner withdraws funds aggregated across all their terminals.
        """
        # Validate allowed balance types for user roles
        if user.role == UserRole.MERCHANT and balance_type not in (BalanceType.WORK, BalanceType.ESCROW):
            raise ValidationException(f"Role {user.role.value} can only have WORK or ESCROW balance")
        if user.role == UserRole.TEAMLEAD and balance_type not in (BalanceType.WORK, BalanceType.ESCROW):
            raise ValidationException(f"Role {user.role.value} can only have WORK or ESCROW balance")

        balance = await self.balance_repo.get_user_balance(user.id, balance_type, currency)
        if not balance:
            balance = await self.balance_repo.create(
                {
                    "user_id": user.id,
                    "type": balance_type,
                    "currency": currency,
                    "amount": 0,
                }
            )
        return balance

    async def get_or_create_merchant_balance(
        self, merchant: Merchant, currency: Currency, balance_type: BalanceType = BalanceType.WORK
    ) -> Balance:
        """
        Get or create a balance for a merchant entity.
        Merchants only have WORK balance.
        """
        balance = await self.balance_repo.get_merchant_balance(
            merchant.id, balance_type, currency
        )
        if not balance:
            balance = await self.balance_repo.create(
                {
                    "merchant_id": merchant.id,
                    "type": balance_type,
                    "currency": currency,
                    "amount": 0,
                }
            )
        return balance

    async def get_or_create_payout_terminal_balance(
        self, payout_terminal_id: int, currency: Currency, balance_type: BalanceType = BalanceType.WORK
    ) -> Balance:
        """Get or create a balance for a payout terminal (WORK or ESCROW). The
        terminal funds its own payouts; admin tops up its WORK balance."""
        balance = await self.balance_repo.get_payout_terminal_balance(
            payout_terminal_id, balance_type, currency
        )
        if not balance:
            balance = await self.balance_repo.create(
                {
                    "payout_terminal_id": payout_terminal_id,
                    "type": balance_type,
                    "currency": currency,
                    "amount": 0,
                }
            )
        return balance

    async def get_or_create_system_balance(
        self, currency: Currency, balance_type: BalanceType = BalanceType.WORK
    ) -> Balance:
        """
        Get or create a balance for the system/platform.
        System usually has WORK balance.
        """
        balance = await self.balance_repo.get_system_balance(balance_type, currency)
        if balance:
            return balance
        # The system float has no DB unique constraint yet (audit #22). Serialise
        # the one-time first creation with a DETERMINISTIC transaction advisory
        # lock so two concurrent callers can't both INSERT a duplicate
        # platform-float row (which would silently split the platform balance).
        bind = self.session.bind
        if bind is not None and bind.dialect.name == "postgresql":
            key = zlib.crc32(f"sysbal:{balance_type.value}:{currency.value}".encode())
            await self.session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": int(key)})
            balance = await self.balance_repo.get_system_balance(balance_type, currency)
            if balance:
                return balance
        return await self.balance_repo.create(
            {
                "is_system": True,
                "type": balance_type,
                "currency": currency,
                "amount": 0,
            }
        )

    async def get_my_balances(self, user: User) -> list[Balance]:
        """
        Get all balances for the current user.
        Traders/Teamleads — their own user-level balances.
        Merchants — aggregated over every terminal they own plus the
        merchant-owner (user-level) balance that acts as the settlement
        account for cross-terminal withdrawals.
        """
        if user.role == UserRole.MERCHANT:
            return await self.get_merchant_aggregate_balances(user.id)

        return await self.balance_repo.get_my_balances(user_id=user.id, merchant_id=None)

    async def get_bot_balances_for_tg_user(self, tg_user_id: int):
        """Aggregate USDT WORK/ESCROW for all terminals linked to a Telegram user.

        Returns a `BotBalancesResponse` containing totals, owner-level values
        summed across every distinct terminal owner (settlement accounts for
        sweep-all withdrawals) and a per-terminal breakdown. Targeted at the
        merchant Telegram bot.
        """
        from app.modules.merchants.repository import MerchantRepository
        from app.modules.merchants.schemas import (
            BotBalancesResponse,
            BotTerminalBalance,
        )

        merchants = await MerchantRepository(self.session).list_by_telegram_user_id(tg_user_id)

        currency = Currency.USDT
        terminals: list = []
        total_work = Decimal("0")
        total_escrow = Decimal("0")

        for m in merchants:
            work_b = await self.get_or_create_merchant_balance(m, currency, BalanceType.WORK)
            escrow_b = await self.get_or_create_merchant_balance(m, currency, BalanceType.ESCROW)
            w = Decimal(work_b.amount or 0)
            e = Decimal(escrow_b.amount or 0)
            total_work += w
            total_escrow += e
            terminals.append(
                BotTerminalBalance(
                    id=m.id,
                    name=m.name,
                    currency=currency.value,
                    work=float(w),
                    escrow=float(e),
                )
            )

        owner_work = Decimal("0")
        owner_escrow = Decimal("0")
        for owner_id in {m.user_id for m in merchants}:
            owner = await self.session.get(User, owner_id)
            if owner is None:
                continue
            ow = await self.get_or_create_user_balance(owner, BalanceType.WORK, currency)
            oe = await self.get_or_create_user_balance(owner, BalanceType.ESCROW, currency)
            owner_work += Decimal(ow.amount or 0)
            owner_escrow += Decimal(oe.amount or 0)
        total_work += owner_work
        total_escrow += owner_escrow

        return BotBalancesResponse(
            currency=currency.value,
            total_work=float(total_work),
            total_escrow=float(total_escrow),
            owner_work=float(owner_work),
            owner_escrow=float(owner_escrow),
            terminals=terminals,
        )

    async def get_merchant_aggregate_balances(self, user_id: int) -> list[Balance]:
        """Return synthetic Balance rows that aggregate every terminal of a merchant.

        One row per (type, currency) with summed amount. `id`, `user_id`,
        `merchant_id`, `is_system` are left empty because these rows are a
        virtual view rather than real ledger balances.

        The merchant-owner (user-level) balance — used as the settlement
        account when sweeping funds across terminals for a withdrawal — is
        folded into the same (type, currency) group so the UI sees a single
        total number.
        """
        merchant_ids = (
            (await self.session.execute(
                select(Merchant.id).where(Merchant.user_id == user_id)
            ))
            .scalars()
            .all()
        )

        totals: dict[tuple[BalanceType, Currency], Decimal] = {}

        if merchant_ids:
            stmt = (
                select(Balance.type, Balance.currency, func.coalesce(func.sum(Balance.amount), 0))
                .where(Balance.merchant_id.in_(merchant_ids))
                .group_by(Balance.type, Balance.currency)
            )
            for b_type, currency, amount in (await self.session.execute(stmt)).all():
                totals[(b_type, currency)] = Decimal(amount or 0)

        stmt_user = select(Balance).where(Balance.user_id == user_id)
        for b in (await self.session.execute(stmt_user)).scalars().all():
            key = (b.type, b.currency)
            totals[key] = totals.get(key, Decimal("0")) + Decimal(b.amount or 0)

        results: list[Balance] = []
        for (b_type, currency), amount in totals.items():
            synthetic = Balance(
                user_id=None,
                merchant_id=None,
                is_system=False,
                type=b_type,
                currency=currency,
                amount=amount,
            )
            results.append(synthetic)
        results.sort(key=lambda b: (b.type.value, b.currency.value))
        return results

    async def get_trader_finance_stats(
        self,
        user: User,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> dict:
        """Aggregate a trader's SUCCESS orders into processed/profit totals.

        ``completed_at`` falls back to ``updated_at`` when ``confirmed_at`` is
        absent (legacy rows). The returned dict matches
        ``TraderFinanceStatsResponse`` — the endpoint validates it.
        """
        completed_at = func.coalesce(Order.confirmed_at, Order.updated_at)
        stmt = select(Order).where(
            Order.trader_id == user.id,
            Order.status == OrderStatus.SUCCESS,
        )
        if date_from is not None:
            stmt = stmt.where(completed_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(completed_at <= date_to)
        stmt = stmt.order_by(completed_at.desc())

        orders = list((await self.session.execute(stmt)).scalars().all())
        rows = []
        processed = Decimal("0")
        profit = Decimal("0")

        for order in orders:
            amount_usdt = Decimal(order.amount_usdt or 0)
            trader_profit = Decimal(order.trader_fee_usdt or 0)
            processed += amount_usdt
            profit += trader_profit
            rows.append({
                "order_id": order.id,
                "order_date": order.confirmed_at or order.updated_at or order.created_at,
                "amount_usdt": float(amount_usdt),
                "profit_usdt": float(trader_profit),
            })

        return {
            "processed_usdt": float(processed),
            "profit_usdt": float(profit),
            "orders": rows,
        }

    async def list_user_ledger_entries(
        self,
        user: User,
        *,
        reference_type: Optional["LedgerReferenceType"] = None,
        skip: int = 0,
        limit: int = 100,
        order_search: Optional[str] = None,
        amount_from: Optional[float] = None,
        amount_to: Optional[float] = None,
    ) -> list[LedgerEntryResponse]:
        balances = await self.balance_repo.get_my_balances(user_id=user.id)
        balance_ids = [b.id for b in balances if b.id is not None]
        if not balance_ids:
            return []

        allowed_types = [
            LedgerReferenceType.ORDER_PAYIN,
            LedgerReferenceType.ORDER_PAYOUT,
            LedgerReferenceType.DEPOSIT,
            LedgerReferenceType.CRYPTO_DEPOSIT,
            LedgerReferenceType.WITHDRAWAL,
            LedgerReferenceType.INTERNAL_TRANSFER,
            LedgerReferenceType.TRADER_REWARD,
            LedgerReferenceType.DISPUTE_REFUND,
        ]
        if reference_type and reference_type not in allowed_types:
            return []
        effective_type = reference_type if reference_type in allowed_types else None

        return await self.list_ledger_entries(
            reference_type=effective_type,
            reference_types=None if effective_type else allowed_types,
            balance_ids=balance_ids,
            skip=skip,
            limit=limit,
            order_search=order_search,
            amount_from=amount_from,
            amount_to=amount_to,
        )

    async def transfer(
        self,
        amount: Decimal,
        currency: Currency,
        reference_type: LedgerReferenceType,
        reference_id: str,
        from_balance_id: Optional[int] = None,
        to_balance_id: Optional[int] = None,
        description: Optional[str] = None,
        allow_negative: bool = False,
    ) -> LedgerEntry:
        """
        Perform a double-entry ledger transfer.
        Must be called within an active session transaction (e.g. async with session.begin():).

        ``allow_negative`` lets the source balance go below zero — used ONLY by
        the dispute re-freeze/reversal (``reconcile_for_dispute``): opening a
        dispute MUST re-freeze the trader's collateral even if the trader already
        spent their WORK balance, so the funnel can settle/release on resolution.
        The overdraft is a real debt the trader carries, fully traceable in the
        ledger. Every other caller keeps the default (False) "no overdraft" rule.
        """
        if amount <= 0:
            raise ValidationException("Transfer amount must be positive")
        if not from_balance_id and not to_balance_id:
            raise ValidationException("At least one balance must be provided")

        from_balance = None
        to_balance = None

        # Lock balances for update to prevent race conditions.
        # Order of locking must be consistent to prevent deadlocks (e.g., lower ID first)
        balance_ids = [bid for bid in [from_balance_id, to_balance_id] if bid is not None]
        balance_ids.sort()

        locked_balances = {}
        for bid in balance_ids:
            b = await self.balance_repo.get_for_update(bid)
            if not b:
                raise ValidationException(f"Balance {bid} not found")
            locked_balances[bid] = b

        if from_balance_id:
            from_balance = locked_balances[from_balance_id]
            if from_balance.currency != currency:
                raise ValidationException("Currency mismatch on from_balance")
            if not allow_negative and from_balance.amount < amount:
                raise ValidationException("Insufficient funds")

            # Deduct amount (may go negative when allow_negative — dispute re-freeze).
            from_balance.amount -= amount

        if to_balance_id:
            to_balance = locked_balances[to_balance_id]
            if to_balance.currency != currency:
                raise ValidationException("Currency mismatch on to_balance")
            
            # Add amount
            to_balance.amount += amount

        # Create Ledger Entry
        entry_data = {
            "from_balance_id": from_balance_id,
            "to_balance_id": to_balance_id,
            "amount": amount,
            "currency": currency,
            "reference_type": reference_type,
            "reference_id": reference_id,
            "description": description,
        }

        entry = await self.ledger_repo.create(entry_data)
        return entry

    async def deposit(
        self,
        amount: Decimal,
        currency: Currency,
        reference_id: str,
        user: Optional[User] = None,
        merchant: Optional[Merchant] = None,
        fee_amount: Decimal = Decimal("0"),
    ) -> LedgerEntry:
        """
        Deposit funds from outside into WORK balance.
        """
        if user:
            balance = await self.get_or_create_user_balance(user, BalanceType.WORK, currency)
        elif merchant:
            balance = await self.get_or_create_merchant_balance(merchant, currency, BalanceType.WORK)
        else:
            raise ValidationException("Must provide user or merchant")

        # Deposit full amount
        entry = await self.transfer(
            amount=amount,
            currency=currency,
            reference_type=LedgerReferenceType.DEPOSIT,
            reference_id=reference_id,
            from_balance_id=None,
            to_balance_id=balance.id,
            description="Deposit",
        )

        if fee_amount > 0:
            system_balance = await self.get_or_create_system_balance(currency)
            await self.transfer(
                amount=fee_amount,
                currency=currency,
                reference_type=LedgerReferenceType.SYSTEM_COMMISSION,
                reference_id=reference_id,
                from_balance_id=balance.id,
                to_balance_id=system_balance.id,
                description="Deposit fee",
            )
        return entry

    # ── Crypto (TRC20) deposit by tx hash ──────────────────────────────
    # Admin "top-up by hash": verify a TRC20 (USDT) transfer on-chain via
    # TronGrid, then credit the trader's WORK/USDT balance. Two-phase — the admin
    # previews the parsed transfer (verify), eyeballs the destination wallet, then
    # commits (confirm). The tx hash is stored as the ledger ``reference_id`` under
    # ``CRYPTO_DEPOSIT``, which doubles as the "already used" guard (idempotency):
    # the same hash can never credit twice.

    @staticmethod
    def _normalize_tx_hash(tx_hash: str) -> str:
        h = (tx_hash or "").strip().lower().removeprefix("0x")
        if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
            raise ValidationException("Invalid TRC20 transaction hash")
        return h

    async def _hash_deposit_exists(self, tx_hash: str) -> bool:
        stmt = (
            select(LedgerEntry.id)
            .where(
                LedgerEntry.reference_type == LedgerReferenceType.CRYPTO_DEPOSIT,
                LedgerEntry.reference_id == tx_hash,
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).first() is not None

    async def verify_hash_deposit(self, tx_hash: str) -> dict:
        """Fetch + validate a TRC20 USDT deposit by hash. Does NOT credit.

        Raises ``ValidationException`` on any problem (bad hash, not found, not
        confirmed, not a USDT transfer, hash already used). Returns the parsed
        ``{tx_hash, amount, to_address, from_address}`` for the admin to confirm.
        """
        tx_hash = self._normalize_tx_hash(tx_hash)
        if await self._hash_deposit_exists(tx_hash):
            raise ValidationException("This transaction hash has already been used")

        try:
            info = await TronGridClient().get_transaction_info(tx_hash)
        except TronGridError as exc:
            raise ValidationException(f"Could not reach TRON network: {exc}")

        if not info.found:
            raise ValidationException("Transaction not found on TRON")
        if not info.confirmed:
            raise ValidationException("Transaction is not yet confirmed")
        if not info.success:
            raise ValidationException("Transaction did not succeed on-chain")

        usdt_contract = get_settings().USDT_TRC20_CONTRACT
        usdt_transfers = [t for t in info.transfers if t.contract_address == usdt_contract]
        if not usdt_transfers:
            raise ValidationException("Transaction has no USDT (TRC20) transfer")
        transfer = usdt_transfers[0]
        if transfer.amount <= 0:
            raise ValidationException("USDT transfer amount is zero")

        return {
            "tx_hash": tx_hash,
            "amount": transfer.amount,
            "to_address": transfer.to_address,
            "from_address": transfer.from_address,
        }

    async def confirm_hash_deposit(self, user: User, tx_hash: str, *, admin_id: int) -> LedgerEntry:
        """Re-verify the on-chain transfer and credit the trader's WORK/USDT
        balance. Atomic + idempotent (the tx hash is the ledger reference_id)."""
        verified = await self.verify_hash_deposit(tx_hash)
        amount: Decimal = verified["amount"]
        tx = verified["tx_hash"]

        try:
            async with self.session.begin_nested():
                # In-tx re-check for the common case; the partial UNIQUE index on
                # (reference_id WHERE CRYPTO_DEPOSIT) is the hard guard that also
                # covers two genuinely-concurrent confirms (→ IntegrityError below).
                if await self._hash_deposit_exists(tx):
                    raise ValidationException("This transaction hash has already been used")
                balance = await self.get_or_create_user_balance(user, BalanceType.WORK, Currency.USDT)
                entry = await self.transfer(
                    amount=amount,
                    currency=Currency.USDT,
                    reference_type=LedgerReferenceType.CRYPTO_DEPOSIT,
                    reference_id=tx,
                    from_balance_id=None,
                    to_balance_id=balance.id,
                    description="TRC20 deposit",
                )
                await self.audit_log(
                    action="admin_hash_deposit",
                    entity_type="user",
                    entity_id=user.id,
                    user_id=admin_id,
                    new_values={
                        "tx_hash": tx,
                        "amount": float(amount),
                        "currency": Currency.USDT.value,
                        "to_address": verified["to_address"],
                        "from_address": verified["from_address"],
                    },
                )
        except IntegrityError:
            raise ValidationException("This transaction hash has already been used")
        return entry

    async def list_merchant_withdrawals(
        self,
        merchant_id: int,
        *,
        status: Optional["WithdrawalStatus"] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[WithdrawalRequest]:
        return await self.withdrawal_repo.list_for_merchant(
            merchant_id, status=status, skip=skip, limit=limit,
        )

    async def list_merchant_owner_withdrawals(
        self,
        owner_user_id: int,
        *,
        status: Optional["WithdrawalStatus"] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[WithdrawalRequest]:
        return await self.withdrawal_repo.list_for_merchant_owner(
            owner_user_id, status=status, skip=skip, limit=limit,
        )

    async def create_withdrawal_request(
        self,
        data: WithdrawalRequestCreate,
        merchant: Optional[Merchant] = None,
        user: Optional[User] = None,
    ) -> WithdrawalRequest:
        """
        Create a single-terminal withdrawal request and freeze funds in ESCROW.
        The fee is withheld FROM the requested amount (see below). The
        cross-terminal sweep helper (`create_merchant_sweep_withdrawal`) is no
        longer exposed by the API — every withdrawal charges one terminal.
        """
        if not merchant and not user:
            raise ValidationException("Must provide merchant or user")

        merchant_id: Optional[int] = None
        if merchant:
            fee_amount = merchant.withdrawal_fee_fixed
            work_balance = await self.get_or_create_merchant_balance(merchant, data.currency, BalanceType.WORK)
            escrow_balance = await self.get_or_create_merchant_balance(merchant, data.currency, BalanceType.ESCROW)
            user_role = UserRole.MERCHANT
            user_id = merchant.user_id
            merchant_id = merchant.id
        else:
            if user.role == UserRole.ADMIN:
                raise ValidationException("Admins cannot withdraw funds")
                
            fee_amount = Decimal("0")
            if user.role == UserRole.TRADER:
                from app.modules.traders.models import Trader
                trader_stmt = select(Trader).where(Trader.user_id == user.id)
                trader_result = await self.session.execute(trader_stmt)
                trader_profile = trader_result.scalar_one_or_none()
                if trader_profile:
                    fee_amount = trader_profile.withdrawal_fee_fixed
                    
            work_balance = await self.get_or_create_user_balance(user, BalanceType.WORK, data.currency)
            escrow_balance = await self.get_or_create_user_balance(user, BalanceType.ESCROW, data.currency)
            user_role = user.role
            user_id = user.id

        # Fee is withheld FROM the requested amount (not added on top): the caller
        # enters the GROSS amount that leaves their balance, and the destination
        # receives ``amount − fee`` (the fee goes to the platform). So the frozen
        # total equals the entered amount, and the stored ``amount`` is the net
        # payout. ``approve``/``reject`` are unchanged — they still move ``amount``
        # (net) to the recipient plus ``fee_amount`` to the system, which sums back
        # to the frozen gross, so rows created under the old model stay correct.
        if data.amount <= fee_amount:
            raise ValidationException(
                f"Withdrawal amount must be greater than the fee ({fee_amount})"
            )
        net_amount = data.amount - fee_amount
        total_deduction = data.amount

        req_data = {
            "user_role": user_role,
            "user_id": user_id,
            "merchant_id": merchant_id,
            "amount": net_amount,
            "currency": data.currency,
            "destination_address": data.destination_address,
            "fee_amount": fee_amount,
            "status": WithdrawalStatus.PENDING,
        }

        async with self.session.begin_nested():
            withdrawal_req = await self.withdrawal_repo.create(req_data)

            await self.transfer(
                amount=total_deduction,
                currency=data.currency,
                reference_type=LedgerReferenceType.WITHDRAWAL,
                reference_id=str(withdrawal_req.id),
                from_balance_id=work_balance.id,
                to_balance_id=escrow_balance.id,
                description="Freeze funds for withdrawal request",
            )

        self._notify_withdrawal_created(withdrawal_req.id)
        return withdrawal_req

    @staticmethod
    def _notify_withdrawal_created(withdrawal_id: int) -> None:
        """Best-effort: tell the platform notifications group a new withdrawal
        request was created. The task itself short-circuits when the toggle is
        off / no chat configured, so we always enqueue. Broker errors are
        swallowed — the withdrawal is already committed and the notification is
        non-critical telemetry.
        """
        try:
            from app.workers.celery_app import celery_app
            celery_app.send_task(
                "app.workers.tasks.support_bot.notify_withdrawal_request",
                args=[withdrawal_id],
            )
        except Exception as exc:  # pragma: no cover — defensive
            import logging
            logging.getLogger(__name__).warning(
                "notify_withdrawal_request enqueue failed (id=%s): %s",
                withdrawal_id, exc,
            )

    @staticmethod
    def _notify_withdrawal_decided(withdrawal_id: int, decision: str) -> None:
        """Best-effort: дописать исход (approve/reject) в карточку уведомления о
        выводе. Таска сама no-op'ит, если карточку не слали, поэтому всегда
        enqueue. ``decision`` — ``"approved"`` или ``"rejected"``. Ошибки брокера
        глотаем — решение по выводу уже закоммичено, уведомление некритично.
        """
        try:
            from app.workers.celery_app import celery_app
            celery_app.send_task(
                "app.workers.tasks.support_bot.notify_withdrawal_decided",
                args=[withdrawal_id, decision],
            )
        except Exception as exc:  # pragma: no cover — defensive
            import logging
            logging.getLogger(__name__).warning(
                "notify_withdrawal_decided enqueue failed (id=%s): %s",
                withdrawal_id, exc,
            )

    async def create_merchant_sweep_withdrawal(
        self,
        data: WithdrawalRequestCreate,
        owner: User,
    ) -> WithdrawalRequest:
        """Create a withdrawal that sweeps WORK balance across every terminal
        of the merchant owner onto their user-level WORK balance, then freezes
        the total (amount + summed per-terminal fees) into user-level ESCROW.

        Rules (agreed with the owner):
          - terminals are drained FIFO by `merchants.id`;
          - each terminal contributes its own `withdrawal_fee_fixed` only when
            it actually participates (non-zero debit);
          - no terminal ever goes negative — if the requested amount exceeds
            the combined WORK across terminals we fail before touching the
            ledger.
        """
        if owner.role != UserRole.MERCHANT:
            raise ValidationException("Sweep withdrawal is only available for merchant owners")

        from app.modules.merchants.repository import MerchantRepository
        merchants = await MerchantRepository(self.session).list_by_user_id(owner.id)
        if not merchants:
            raise ValidationException("No terminals available to withdraw from")

        currency = data.currency

        owner_work = await self.get_or_create_user_balance(owner, BalanceType.WORK, currency)
        owner_escrow = await self.get_or_create_user_balance(owner, BalanceType.ESCROW, currency)

        remaining = data.amount
        plan: list[tuple[Merchant, Decimal, Decimal]] = []
        total_fee = Decimal("0")

        owner_work_available = Decimal(owner_work.amount or 0)
        if owner_work_available > 0:
            take_from_owner = min(owner_work_available, remaining)
            remaining -= take_from_owner

        for m in merchants:
            if remaining <= 0:
                break
            terminal_work = await self.get_or_create_merchant_balance(m, currency, BalanceType.WORK)
            fee = Decimal(m.withdrawal_fee_fixed or 0)
            available = Decimal(terminal_work.amount or 0)
            spendable = available - fee
            if spendable <= 0:
                continue
            take = min(spendable, remaining)
            plan.append((m, take, fee))
            remaining -= take
            total_fee += fee

        if remaining > 0:
            raise ValidationException(
                "Insufficient funds: aggregated terminal WORK balance does "
                "not cover the requested amount plus per-terminal fees"
            )

        req_data = {
            "user_role": UserRole.MERCHANT,
            "user_id": owner.id,
            "merchant_id": None,
            "amount": data.amount,
            "currency": currency,
            "destination_address": data.destination_address,
            "fee_amount": total_fee,
            "status": WithdrawalStatus.PENDING,
        }

        async with self.session.begin_nested():
            withdrawal_req = await self.withdrawal_repo.create(req_data)
            ref_id = str(withdrawal_req.id)

            for m, take, fee in plan:
                terminal_work = await self.get_or_create_merchant_balance(m, currency, BalanceType.WORK)
                await self.transfer(
                    amount=take + fee,
                    currency=currency,
                    reference_type=LedgerReferenceType.INTERNAL_TRANSFER,
                    reference_id=ref_id,
                    from_balance_id=terminal_work.id,
                    to_balance_id=owner_work.id,
                    description=f"Sweep terminal #{m.id} → merchant owner WORK (incl. fee reserve)",
                )

            await self.transfer(
                amount=data.amount + total_fee,
                currency=currency,
                reference_type=LedgerReferenceType.WITHDRAWAL,
                reference_id=ref_id,
                from_balance_id=owner_work.id,
                to_balance_id=owner_escrow.id,
                description="Freeze swept funds for merchant cross-terminal withdrawal",
            )

        self._notify_withdrawal_created(withdrawal_req.id)
        return withdrawal_req

    async def withdrawal(
        self,
        amount: Decimal,
        currency: Currency,
        reference_id: str,
        user: Optional[User] = None,
        merchant: Optional[Merchant] = None,
        fee_amount: Decimal = Decimal("0"),
    ) -> LedgerEntry:
        """
        Withdraw funds from WORK balance to outside.
        """
        if user:
            balance = await self.get_or_create_user_balance(user, BalanceType.WORK, currency)
        elif merchant:
            balance = await self.get_or_create_merchant_balance(merchant, currency, BalanceType.WORK)
        else:
            raise ValidationException("Must provide user or merchant")

        if fee_amount > 0:
            system_balance = await self.get_or_create_system_balance(currency)
            await self.transfer(
                amount=fee_amount,
                currency=currency,
                reference_type=LedgerReferenceType.SYSTEM_COMMISSION,
                reference_id=reference_id,
                from_balance_id=balance.id,
                to_balance_id=system_balance.id,
                description="Withdrawal fee",
            )

        entry = await self.transfer(
            amount=amount,
            currency=currency,
            reference_type=LedgerReferenceType.WITHDRAWAL,
            reference_id=reference_id,
            from_balance_id=balance.id,
            to_balance_id=None,
            description="Withdrawal",
        )
        return entry

    async def create_order(
        self,
        order: Order,
        trader: Optional[User] = None,
        merchant: Optional[Merchant] = None,
    ) -> LedgerEntry:
        """
        Freeze funds in ESCROW when an order is created.
        Payin: Freeze trader's funds.
        Payout: Freeze merchant's funds.
        """
        amount = order.amount_usdt
        currency = Currency.USDT
        
        if order.direction == PaymentDirection.PAYIN:
            if not trader:
                raise ValidationException("Trader is required for PAYIN order creation")
            work_balance = await self.get_or_create_user_balance(trader, BalanceType.WORK, currency)
            escrow_balance = await self.get_or_create_user_balance(trader, BalanceType.ESCROW, currency)
            ref_type = LedgerReferenceType.ORDER_PAYIN
            from_b = work_balance.id
            to_b = escrow_balance.id
        else:
            if not merchant:
                raise ValidationException("Merchant is required for PAYOUT order creation")
            work_balance = await self.get_or_create_merchant_balance(merchant, currency, BalanceType.WORK)
            escrow_balance = await self.get_or_create_merchant_balance(merchant, currency, BalanceType.ESCROW)
            ref_type = LedgerReferenceType.ORDER_PAYOUT
            from_b = work_balance.id
            to_b = escrow_balance.id

        return await self.transfer(
            amount=amount,
            currency=currency,
            reference_type=ref_type,
            reference_id=str(order.id),
            from_balance_id=from_b,
            to_balance_id=to_b,
            description=f"Create {order.direction.value} order",
        )

    async def cancel_order(
        self,
        order: Order,
        trader: Optional[User] = None,
        merchant: Optional[Merchant] = None,
    ) -> LedgerEntry:
        """
        Release funds from ESCROW back to WORK when an order is cancelled.
        """
        amount = order.amount_usdt
        currency = Currency.USDT
        
        if order.direction == PaymentDirection.PAYIN:
            if not trader:
                raise ValidationException("Trader is required for PAYIN order cancellation")
            work_balance = await self.get_or_create_user_balance(trader, BalanceType.WORK, currency)
            escrow_balance = await self.get_or_create_user_balance(trader, BalanceType.ESCROW, currency)
            ref_type = LedgerReferenceType.ORDER_PAYIN
            from_b = escrow_balance.id
            to_b = work_balance.id
        else:
            if not merchant:
                raise ValidationException("Merchant is required for PAYOUT order cancellation")
            work_balance = await self.get_or_create_merchant_balance(merchant, currency, BalanceType.WORK)
            escrow_balance = await self.get_or_create_merchant_balance(merchant, currency, BalanceType.ESCROW)
            ref_type = LedgerReferenceType.ORDER_PAYOUT
            from_b = escrow_balance.id
            to_b = work_balance.id

        return await self.transfer(
            amount=amount,
            currency=currency,
            reference_type=ref_type,
            reference_id=str(order.id),
            from_balance_id=from_b,
            to_balance_id=to_b,
            description=f"Cancel {order.direction.value} order",
        )

    async def recalculate_order(
        self,
        order: Order,
        old_amount_usdt: Decimal,
        new_amount_usdt: Decimal,
        trader: Optional[User] = None,
        merchant: Optional[Merchant] = None,
    ) -> Optional[LedgerEntry]:
        """
        Adjust ESCROW balance when order amount changes (e.g. dispute).
        old_amount_usdt must be captured before the order is updated in the DB.
        """
        delta = new_amount_usdt - old_amount_usdt
        currency = Currency.USDT
        
        if delta == 0:
            return None
            
        if order.direction == PaymentDirection.PAYIN:
            if not trader:
                raise ValidationException("Trader is required for PAYIN order recalculation")
            work_balance = await self.get_or_create_user_balance(trader, BalanceType.WORK, currency)
            escrow_balance = await self.get_or_create_user_balance(trader, BalanceType.ESCROW, currency)
            ref_type = LedgerReferenceType.ORDER_PAYIN
        else:
            if not merchant:
                raise ValidationException("Merchant is required for PAYOUT order recalculation")
            work_balance = await self.get_or_create_merchant_balance(merchant, currency, BalanceType.WORK)
            escrow_balance = await self.get_or_create_merchant_balance(merchant, currency, BalanceType.ESCROW)
            ref_type = LedgerReferenceType.ORDER_PAYOUT

        if delta > 0:
            # Need to freeze more
            from_b = work_balance.id
            to_b = escrow_balance.id
            transfer_amount = delta
        else:
            # Need to release some
            from_b = escrow_balance.id
            to_b = work_balance.id
            transfer_amount = abs(delta)

        return await self.transfer(
            amount=transfer_amount,
            currency=currency,
            reference_type=ref_type,
            reference_id=str(order.id),
            from_balance_id=from_b,
            to_balance_id=to_b,
            description=f"Recalculate {order.direction.value} order delta",
        )

    async def complete_order(
        self,
        order: Order,
        trader: User,
        merchant: Merchant,
    ) -> list[dict]:
        """Settle an order to SUCCESS — the single source of truth for ALL the
        money of a successful order:

          PAYIN : trader ESCROW → merchant WORK, system fee, trader reward.
          PAYOUT: merchant ESCROW → trader WORK, system fee, trader reward.

        then pays the teamlead rewards (system → teamlead WORK) and bumps the
        requisite turnover. Returns the per-teamlead reward breakdown (consumed by
        the order's financial snapshot). Callers must NOT pay teamlead rewards or
        turnover separately — settlement is complete here. The mirror op
        ``reconcile_for_dispute`` reverses all of it when a settled order is
        un-settled (dispute open / force).
        """
        currency = Currency.USDT
        amount = order.amount_usdt
        fee = order.fee_usdt or Decimal("0")
        entries = []
        
        merchant_work = await self.get_or_create_merchant_balance(merchant, currency, BalanceType.WORK)
        trader_work = await self.get_or_create_user_balance(trader, BalanceType.WORK, currency)
        
        if order.direction == PaymentDirection.PAYIN:
            # Trader ESCROW -> Merchant WORK
            trader_escrow = await self.get_or_create_user_balance(trader, BalanceType.ESCROW, currency)
            entries.append(await self.transfer(
                amount=amount,
                currency=currency,
                reference_type=LedgerReferenceType.ORDER_PAYIN,
                reference_id=str(order.id),
                from_balance_id=trader_escrow.id,
                to_balance_id=merchant_work.id,
                description="Complete PAYIN order",
            ))
            
            # Deduct fee from Merchant WORK -> System WORK
            if fee > 0:
                system_balance = await self.get_or_create_system_balance(currency)
                entries.append(await self.transfer(
                    amount=fee,
                    currency=currency,
                    reference_type=LedgerReferenceType.SYSTEM_COMMISSION,
                    reference_id=str(order.id),
                    from_balance_id=merchant_work.id,
                    to_balance_id=system_balance.id,
                    description="PAYIN order fee",
                ))
                
            # Pay trader fee from System WORK -> Trader WORK
            trader_fee = order.trader_fee_usdt or Decimal("0")
            if trader_fee > 0:
                system_balance = await self.get_or_create_system_balance(currency)
                entries.append(await self.transfer(
                    amount=trader_fee,
                    currency=currency,
                    reference_type=LedgerReferenceType.TRADER_REWARD,
                    reference_id=str(order.id),
                    from_balance_id=system_balance.id,
                    to_balance_id=trader_work.id,
                    description="Trader processing fee",
                ))
                
        else:
            # PAYOUT: Merchant ESCROW -> Trader WORK
            merchant_escrow = await self.get_or_create_merchant_balance(merchant, currency, BalanceType.ESCROW)
            entries.append(await self.transfer(
                amount=amount,
                currency=currency,
                reference_type=LedgerReferenceType.ORDER_PAYOUT,
                reference_id=str(order.id),
                from_balance_id=merchant_escrow.id,
                to_balance_id=trader_work.id,
                description="Complete PAYOUT order",
            ))
            
            # Deduct fee from Trader WORK -> System WORK (or Merchant? Usually Merchant pays fee, but it's already deducted from their balance if they sent amount+fee. Let's assume fee is deducted from the recipient or sender. If Merchant ESCROW had amount+fee, we'd need to handle it. Assuming `amount_usdt` is the gross amount, and `fee_usdt` is the fee.)
            # For PAYOUT, if merchant pays the fee, it should be deducted from Merchant WORK.
            if fee > 0:
                system_balance = await self.get_or_create_system_balance(currency)
                entries.append(await self.transfer(
                    amount=fee,
                    currency=currency,
                    reference_type=LedgerReferenceType.SYSTEM_COMMISSION,
                    reference_id=str(order.id),
                    from_balance_id=merchant_work.id,
                    to_balance_id=system_balance.id,
                    description="PAYOUT order fee",
                ))

            # Pay trader fee from System WORK -> Trader WORK
            trader_fee = order.trader_fee_usdt or Decimal("0")
            if trader_fee > 0:
                system_balance = await self.get_or_create_system_balance(currency)
                entries.append(await self.transfer(
                    amount=trader_fee,
                    currency=currency,
                    reference_type=LedgerReferenceType.TRADER_REWARD,
                    reference_id=str(order.id),
                    from_balance_id=system_balance.id,
                    to_balance_id=trader_work.id,
                    description="Trader processing fee",
                ))

        # Teamlead rewards + requisite turnover are PART OF a successful
        # settlement — folded in here so complete_order() is the only place that
        # PAYS them. (The reward *calculation* — which teamleads, what % — stays
        # in TeamleaderService.)
        from app.modules.teamleaders.service import TeamleaderService
        from app.modules.requisites.repository import RequisiteLimitRepository

        teamlead_breakdown = await TeamleaderService(self.session).calculate_and_pay_rewards(order)
        await RequisiteLimitRepository(self.session).increment_turnover_by_order(order)
        return teamlead_breakdown

    # NOTE: a dead ``refund_order`` lived here. It was never called (the
    # REFUNDED transition routes through change_status → reconcile + cancel_order,
    # which reverses by the ACTUAL settlement), and its body had inverted
    # conservation for PAYIN (debited merchant WORK to a trader who never held
    # the amount) with caller-supplied fee figures. Removed per the financial
    # audit so it can't be wired up and mint money. Reverse a settled order via
    # ``change_status(order, REFUNDED)`` instead.

    async def approve_withdrawal_request(
        self,
        request_id: int,
        admin_id: int,
    ) -> WithdrawalRequest:
        """
        Approve a pending withdrawal:
        Move funds from ESCROW to outside (debit ESCROW) and pay fee to system.
        """

        # Lock the request row + re-check status under the lock so a concurrent
        # approve/reject (double-click, retry, two admins) can't pay out twice.
        req = await self.withdrawal_repo.get_for_update(request_id)
        if not req:
            raise NotFoundException(f"Withdrawal request {request_id} not found")
        if req.status != WithdrawalStatus.PENDING:
            raise ConflictException(f"Cannot approve request in status {req.status.value}")

        currency = req.currency
        amount = req.amount
        fee = req.fee_amount or Decimal("0")

        if req.user_role == UserRole.MERCHANT:
            if req.merchant_id is not None:
                merchant = await self.session.get(Merchant, req.merchant_id)
                if not merchant:
                    raise ValidationException("Merchant not found")
                escrow_balance = await self.get_or_create_merchant_balance(merchant, currency, BalanceType.ESCROW)
            else:
                owner = await self.session.get(User, req.user_id)
                if not owner:
                    raise ValidationException("Merchant owner not found")
                escrow_balance = await self.get_or_create_user_balance(owner, BalanceType.ESCROW, currency)
        else:
            user = await self.session.get(User, req.user_id)
            if not user:
                raise ValidationException("User not found")
            escrow_balance = await self.get_or_create_user_balance(user, BalanceType.ESCROW, currency)

        async with self.session.begin_nested():
            await self.transfer(
                amount=amount,
                currency=currency,
                reference_type=LedgerReferenceType.WITHDRAWAL,
                reference_id=str(req.id),
                from_balance_id=escrow_balance.id,
                to_balance_id=None,
                description="Withdrawal approved",
            )

            if fee > 0:
                system_balance = await self.get_or_create_system_balance(currency)
                await self.transfer(
                    amount=fee,
                    currency=currency,
                    reference_type=LedgerReferenceType.SYSTEM_COMMISSION,
                    reference_id=str(req.id),
                    from_balance_id=escrow_balance.id,
                    to_balance_id=system_balance.id,
                    description="Withdrawal fee",
                )

            req = await self.withdrawal_repo.update(
                request_id,
                {
                    "status": WithdrawalStatus.SUCCESS,
                    "processed_at": utcnow(),
                    "processed_by_id": admin_id,
                },
            )

            await self.audit_log(
                action="approve_withdrawal",
                entity_type="withdrawal_request",
                entity_id=request_id,
                user_id=admin_id,
                new_values={"status": WithdrawalStatus.SUCCESS.value},
            )

        self._notify_withdrawal_decided(request_id, "approved")
        return req

    async def reject_withdrawal_request(
        self,
        request_id: int,
        admin_id: int,
        reason: str,
    ) -> WithdrawalRequest:
        """
        Reject a pending withdrawal:
        Return frozen funds from ESCROW back to WORK.
        """
        # Lock the request row + re-check status under the lock (mirror approve).
        req = await self.withdrawal_repo.get_for_update(request_id)
        if not req:
            raise NotFoundException(f"Withdrawal request {request_id} not found")
        if req.status != WithdrawalStatus.PENDING:
            raise ConflictException(f"Cannot reject request in status {req.status.value}")

        currency = req.currency
        total = req.amount + (req.fee_amount or Decimal("0"))

        if req.user_role == UserRole.MERCHANT:
            if req.merchant_id is not None:
                merchant = await self.session.get(Merchant, req.merchant_id)
                if not merchant:
                    raise ValidationException("Merchant not found")
                escrow_balance = await self.get_or_create_merchant_balance(merchant, currency, BalanceType.ESCROW)
                work_balance = await self.get_or_create_merchant_balance(merchant, currency, BalanceType.WORK)
            else:
                owner = await self.session.get(User, req.user_id)
                if not owner:
                    raise ValidationException("Merchant owner not found")
                escrow_balance = await self.get_or_create_user_balance(owner, BalanceType.ESCROW, currency)
                work_balance = await self.get_or_create_user_balance(owner, BalanceType.WORK, currency)
        else:
            user = await self.session.get(User, req.user_id)
            if not user:
                raise ValidationException("User not found")
            escrow_balance = await self.get_or_create_user_balance(user, BalanceType.ESCROW, currency)
            work_balance = await self.get_or_create_user_balance(user, BalanceType.WORK, currency)

        async with self.session.begin_nested():
            await self.transfer(
                amount=total,
                currency=currency,
                reference_type=LedgerReferenceType.WITHDRAWAL,
                reference_id=str(req.id),
                from_balance_id=escrow_balance.id,
                to_balance_id=work_balance.id,
                description="Withdrawal rejected — funds returned",
            )

            req = await self.withdrawal_repo.update(
                request_id,
                {
                    "status": WithdrawalStatus.REJECTED,
                    "processed_at": utcnow(),
                    "processed_by_id": admin_id,
                    "rejection_reason": reason,
                },
            )

            await self.audit_log(
                action="reject_withdrawal",
                entity_type="withdrawal_request",
                entity_id=request_id,
                user_id=admin_id,
                new_values={"status": WithdrawalStatus.REJECTED.value, "reason": reason},
            )

        self._notify_withdrawal_decided(request_id, "rejected")
        return req

    # ── Dispute finance helpers ────────────────────────────────

    async def reconcile_for_dispute(
        self,
        order: "Order",
        merchant: "Merchant",
        trader: Optional["User"],
        pre_status: "OrderStatus",
    ) -> None:
        """Freeze an order's money into the 'pending-like' state on dispute open.

        Disputes open from terminal statuses (SUCCESS/FAILED/CANCELED) and from
        active ones (PENDING/RECEIPT_UPLOADED). Whatever the prior state, we
        normalise the books so the trader's collateral (``amount_usdt``) sits in
        **trader ESCROW** with no settlement / fee / reward outstanding — exactly
        the balance shape an order has while PENDING. The two dispute exits then
        reuse the ordinary order flows verbatim:

          * resolve → ``complete_order`` (trader ESCROW → merchant WORK + fee + reward)
          * reject  → ``cancel_order``   (trader ESCROW → trader WORK)

        Ledger ``reference_id`` scheme over a SUCCESS→dispute→resolve lifecycle:
        the first settlement writes entries keyed ``str(order.id)``; opening the
        dispute writes REVERSAL entries keyed ``"{order.id}_dispute_reverse"``
        that neutralise them; resolving re-runs ``complete_order`` which writes a
        SECOND set keyed ``str(order.id)``. The ledger stays balanced (reversal
        cancels the first set), but note that ``reference_id`` alone is NOT a
        unique per-event key — it intentionally repeats for the re-settlement.
        Don't use ``reference_id`` as an idempotency key in isolation.

        Normalisation per prior status (PAYIN):
          * SUCCESS                    → reverse the settlement: undo the trader
                                         reward, undo the system fee, then move
                                         the settled amount back from merchant
                                         WORK to trader ESCROW.
          * FAILED / CANCELED          → collateral was already released to
                                         trader WORK; re-freeze it (trader WORK →
                                         trader ESCROW).
          * PENDING / RECEIPT_UPLOADED → collateral is ALREADY in trader ESCROW
                                         (frozen at create_order). No-op —
                                         re-freezing would double-charge the
                                         trader's WORK balance.

        No-op when the order has no trader (nothing was ever frozen) — the
        resolve/reject guards reject those orders explicitly.
        """
        from app.common.enums.orders import OrderStatus

        if order.direction != PaymentDirection.PAYIN:
            # The simplified dispute flow only models PAYIN collateral.
            raise ValidationException(
                "Dispute reconciliation only supports PAYIN orders"
            )

        currency = Currency.USDT
        amount = order.amount_usdt or Decimal("0")

        if trader is None or amount <= 0:
            # Nothing was frozen against a trader — leave the books untouched.
            return

        if pre_status in (OrderStatus.PENDING, OrderStatus.RECEIPT_UPLOADED):
            return

        trader_work = await self.get_or_create_user_balance(trader, BalanceType.WORK, currency)
        trader_escrow = await self.get_or_create_user_balance(trader, BalanceType.ESCROW, currency)

        if pre_status == OrderStatus.SUCCESS:
            # Reverse the three transfers complete_order made, in inverse order,
            # landing the full amount back in trader ESCROW.
            fee = order.fee_usdt or Decimal("0")
            trader_fee = order.trader_fee_usdt or Decimal("0")
            merchant_work = await self.get_or_create_merchant_balance(merchant, currency, BalanceType.WORK)
            system_balance = await self.get_or_create_system_balance(currency)

            # 0. Reverse teamlead rewards (teamlead WORK → system) + undo turnover
            #    FIRST, so the system balance is replenished before it funds the
            #    fee reversal below. Mirror of complete_order's teamlead payout —
            #    reconcile is the single place that un-does a full settlement.
            from app.modules.teamleaders.service import TeamleaderService
            from app.modules.requisites.repository import RequisiteLimitRepository

            await TeamleaderService(self.session).reverse_rewards(order)
            await RequisiteLimitRepository(self.session).decrement_turnover_by_order(order)

            # 1. Undo trader reward (trader WORK → system) FIRST, so the system
            #    balance can fund the fee reversal below.
            if trader_fee > 0:
                await self.transfer(
                    amount=trader_fee,
                    currency=currency,
                    reference_type=LedgerReferenceType.TRADER_REWARD,
                    reference_id=f"{order.id}_dispute_reverse",
                    from_balance_id=trader_work.id,
                    to_balance_id=system_balance.id,
                    description="Dispute open: reverse trader reward",
                    # The reward was paid into the trader's WORK; reverse it even
                    # if they've since spent it (the dispute MUST open) — the
                    # overdraft is a debt carried until the dispute resolves.
                    allow_negative=True,
                )
            # 2. Undo system fee (system → merchant WORK).
            if fee > 0:
                await self.transfer(
                    amount=fee,
                    currency=currency,
                    reference_type=LedgerReferenceType.SYSTEM_COMMISSION,
                    reference_id=f"{order.id}_dispute_reverse",
                    from_balance_id=system_balance.id,
                    to_balance_id=merchant_work.id,
                    description="Dispute open: reverse system fee",
                )
            # 3. Undo settlement (merchant WORK → trader ESCROW, full amount).
            await self.transfer(
                amount=amount,
                currency=currency,
                reference_type=LedgerReferenceType.ORDER_PAYIN,
                reference_id=f"{order.id}_dispute_reverse",
                from_balance_id=merchant_work.id,
                to_balance_id=trader_escrow.id,
                description="Dispute open: claw settled payout back to escrow",
            )
        else:
            # FAILED / CANCELED — collateral is back in trader WORK; re-freeze it
            # even if the trader already spent it (the dispute MUST open) — the
            # overdraft is a debt carried until the dispute resolves.
            await self.transfer(
                amount=amount,
                currency=currency,
                reference_type=LedgerReferenceType.ORDER_PAYIN,
                reference_id=f"{order.id}_dispute_freeze",
                from_balance_id=trader_work.id,
                to_balance_id=trader_escrow.id,
                description="Dispute open: re-freeze trader collateral",
                allow_negative=True,
            )

    async def adjust_balance(
        self,
        balance: "Balance",
        delta: Decimal,
        *,
        reason: str,
        reference_id: Optional[str] = None,
    ) -> Optional[LedgerEntry]:
        """Move a balance by a SIGNED ``delta`` through the ledger — a one-sided
        entry (credit via DEPOSIT when positive, debit via WITHDRAWAL when
        negative). The single place for "set/adjust a balance": every balance
        change is a traceable ledger record, never a raw ``balance.amount = …``
        mutation. Debits are funds-checked by ``transfer`` (no going negative).
        No-op (returns None) for a zero delta.
        """
        if delta == 0:
            return None
        ref_id = reference_id or f"balance:{balance.id}"
        if delta > 0:
            return await self.transfer(
                amount=delta, currency=balance.currency,
                reference_type=LedgerReferenceType.DEPOSIT,
                reference_id=ref_id,
                to_balance_id=balance.id, description=reason,
            )
        return await self.transfer(
            amount=-delta, currency=balance.currency,
            reference_type=LedgerReferenceType.WITHDRAWAL,
            reference_id=ref_id,
            from_balance_id=balance.id, description=reason,
        )

    async def set_provider_balance(
        self,
        user: User,
        target_amount: Decimal,
        *,
        reason: str = "Cascade provider balance set",
    ) -> Optional[LedgerEntry]:
        """Reconcile a cascade provider's (virtual user) WORK balance to an
        ABSOLUTE amount via the ledger (``adjust_balance`` by the delta)."""
        balance = await self.get_or_create_user_balance(user, BalanceType.WORK, Currency.USDT)
        delta = Decimal(str(target_amount)) - Decimal(balance.amount or 0)
        return await self.adjust_balance(balance, delta, reason=reason)

    async def adjust_provider_balance(
        self,
        user: User,
        delta: Decimal,
        *,
        reason: str = "Cascade provider balance adjust",
    ) -> Optional[LedgerEntry]:
        """Apply a signed delta to a cascade provider's WORK balance via the
        ledger. A debit beyond the balance raises (Insufficient funds)."""
        balance = await self.get_or_create_user_balance(user, BalanceType.WORK, Currency.USDT)
        return await self.adjust_balance(balance, Decimal(str(delta)), reason=reason)

    async def charge_receipt_check_fee(
        self, trader: User, amount: Decimal, check_id: int,
    ) -> LedgerEntry:
        """Charge a trader the antifraud receipt-check fee: trader WORK → system
        WORK (RECEIPT_CHECK). Single source of truth for the fee move."""
        if trader.role != UserRole.TRADER:
            raise ValidationException("Receipt check can only be charged to a trader account")
        currency = Currency.USDT
        trader_work = await self.get_or_create_user_balance(trader, BalanceType.WORK, currency)
        system_work = await self.get_or_create_system_balance(currency, BalanceType.WORK)
        if Decimal(trader_work.amount or 0) < amount:
            raise ValidationException("Insufficient WORK balance to pay for a receipt check")
        return await self.transfer(
            amount=amount, currency=currency,
            reference_type=LedgerReferenceType.RECEIPT_CHECK,
            reference_id=f"receipt_check:{check_id}",
            from_balance_id=trader_work.id, to_balance_id=system_work.id,
            description="Receipt verification fee",
        )

    async def refund_receipt_check_fee(
        self, trader: User, amount: Decimal, check_id: int,
    ) -> LedgerEntry:
        """Refund a receipt-check fee (provider error): system WORK → trader WORK."""
        currency = Currency.USDT
        trader_work = await self.get_or_create_user_balance(trader, BalanceType.WORK, currency)
        system_work = await self.get_or_create_system_balance(currency, BalanceType.WORK)
        return await self.transfer(
            amount=amount, currency=currency,
            reference_type=LedgerReferenceType.RECEIPT_CHECK_REFUND,
            reference_id=f"receipt_check:{check_id}",
            from_balance_id=system_work.id, to_balance_id=trader_work.id,
            description="Receipt verification refund (provider error)",
        )

    # ── Payout escrow business functions (terminal-funded; mirror the order ops:
    #    freeze ≈ create_order, settle ≈ complete_order, refund ≈ cancel_order) ──

    async def freeze_payout(self, payout, terminal) -> LedgerEntry:
        """Freeze the payout terminal's funds on creation: terminal WORK → ESCROW
        for amount_usdt + commission. Single source of truth for the freeze;
        raises on insufficient terminal balance."""
        currency = Currency.USDT
        tid = terminal.id
        total = Decimal(str(payout.amount_usdt or 0)) + Decimal(str(payout.merchant_fee_usdt or 0))
        work = await self.get_or_create_payout_terminal_balance(tid, currency, BalanceType.WORK)
        escrow = await self.get_or_create_payout_terminal_balance(tid, currency, BalanceType.ESCROW)
        if Decimal(str(work.amount)) < total:
            raise ValidationException("Insufficient payout terminal balance")
        return await self.transfer(
            amount=total, currency=currency,
            reference_type=LedgerReferenceType.ORDER_PAYOUT, reference_id=f"payout:{payout.id}",
            from_balance_id=work.id, to_balance_id=escrow.id,
            description="Freeze payout terminal funds",
        )

    async def settle_payout(self, payout, *, terminal, trader, hold_hours: int) -> None:
        """Settle a COMPLETED payout — the single source of truth for ALL its
        money: terminal ESCROW → trader (reimburse amount), terminal ESCROW →
        system (commission), system → trader (fee reward). The trader's earnings
        land in WORK, or in ESCROW (held) when hold_hours > 0. Platform profit =
        commission − trader fee."""
        currency = Currency.USDT
        amount_usdt = Decimal(str(payout.amount_usdt or 0))
        merchant_fee = Decimal(str(payout.merchant_fee_usdt or 0))
        trader_fee = Decimal(str(payout.trader_fee_usdt or 0))
        t_escrow = await self.get_or_create_payout_terminal_balance(terminal.id, currency, BalanceType.ESCROW)
        system = await self.get_or_create_system_balance(currency)
        target_type = BalanceType.ESCROW if hold_hours > 0 else BalanceType.WORK
        trader_target = await self.get_or_create_user_balance(trader, target_type, currency)

        await self.transfer(
            amount=amount_usdt, currency=currency,
            reference_type=LedgerReferenceType.ORDER_PAYOUT, reference_id=f"payout:{payout.id}",
            from_balance_id=t_escrow.id, to_balance_id=trader_target.id,
            description="Payout settle (amount)",
        )
        if merchant_fee > 0:
            await self.transfer(
                amount=merchant_fee, currency=currency,
                reference_type=LedgerReferenceType.SYSTEM_COMMISSION, reference_id=f"payout:{payout.id}",
                from_balance_id=t_escrow.id, to_balance_id=system.id,
                description="Payout commission",
            )
        if trader_fee > 0:
            await self.transfer(
                amount=trader_fee, currency=currency,
                reference_type=LedgerReferenceType.TRADER_REWARD, reference_id=f"payout:{payout.id}",
                from_balance_id=system.id, to_balance_id=trader_target.id,
                description="Payout trader fee",
            )

        # Teamlead rewards are PART OF a successful payout settlement — folded in
        # here so settle_payout() is the only place that pays them (mirrors
        # complete_order for orders). The reward calculation — which teamleads,
        # what % — stays in TeamleaderService, driven by payout_fee_percent.
        from app.modules.teamleaders.service import TeamleaderService

        await TeamleaderService(self.session).calculate_and_pay_payout_rewards(payout)

    async def refund_payout(self, payout, terminal) -> None:
        """Refund a canceled/expired payout: terminal ESCROW → WORK for the full
        frozen amount (amount_usdt + commission)."""
        currency = Currency.USDT
        total = Decimal(str(payout.amount_usdt or 0)) + Decimal(str(payout.merchant_fee_usdt or 0))
        if total <= 0:
            return
        work = await self.get_or_create_payout_terminal_balance(terminal.id, currency, BalanceType.WORK)
        escrow = await self.get_or_create_payout_terminal_balance(terminal.id, currency, BalanceType.ESCROW)
        await self.transfer(
            amount=total, currency=currency,
            reference_type=LedgerReferenceType.ORDER_PAYOUT, reference_id=f"payout:{payout.id}",
            from_balance_id=escrow.id, to_balance_id=work.id,
            description="Payout refund",
        )

    # ── долив (requisite refill) — funded by the requesting trader ──────────

    async def freeze_doliv(self, payout, requester: "User") -> LedgerEntry:
        """Freeze the REQUESTING trader's funds on долив creation: requester
        WORK → ESCROW for amount_usdt + доliv price. Single source of truth for
        the freeze; raises on insufficient WORK balance (so the долив is not
        created)."""
        currency = Currency.USDT
        total = Decimal(str(payout.amount_usdt or 0)) + Decimal(str(payout.doliv_price_usdt or 0))
        work = await self.get_or_create_user_balance(requester, BalanceType.WORK, currency)
        escrow = await self.get_or_create_user_balance(requester, BalanceType.ESCROW, currency)
        if Decimal(str(work.amount)) < total:
            raise ValidationException("Insufficient WORK balance to request a долив")
        return await self.transfer(
            amount=total, currency=currency,
            reference_type=LedgerReferenceType.DOLIV, reference_id=f"doliv:{payout.id}",
            from_balance_id=work.id, to_balance_id=escrow.id,
            description="Freeze долив (requester)",
        )

    async def settle_doliv(self, payout, *, requester: "User", executor: "User") -> None:
        """Settle a COMPLETED долив — the single source of truth for ALL its
        money: requester ESCROW → доливщик WORK (reimburse the amount the
        доливщик really sent to the card), requester ESCROW → system (доliv
        price), system → доливщик WORK (executor reward). Platform profit =
        price − reward."""
        currency = Currency.USDT
        amount = Decimal(str(payout.amount_usdt or 0))
        price = Decimal(str(payout.doliv_price_usdt or 0))
        reward = Decimal(str(payout.trader_fee_usdt or 0))
        req_escrow = await self.get_or_create_user_balance(requester, BalanceType.ESCROW, currency)
        exec_work = await self.get_or_create_user_balance(executor, BalanceType.WORK, currency)
        system = await self.get_or_create_system_balance(currency)

        await self.transfer(
            amount=amount, currency=currency,
            reference_type=LedgerReferenceType.DOLIV, reference_id=f"doliv:{payout.id}",
            from_balance_id=req_escrow.id, to_balance_id=exec_work.id,
            description="Долив settle (amount → доливщик)",
        )
        if price > 0:
            await self.transfer(
                amount=price, currency=currency,
                reference_type=LedgerReferenceType.SYSTEM_COMMISSION, reference_id=f"doliv:{payout.id}",
                from_balance_id=req_escrow.id, to_balance_id=system.id,
                description="Долив price",
            )
        if reward > 0:
            await self.transfer(
                amount=reward, currency=currency,
                reference_type=LedgerReferenceType.TRADER_REWARD, reference_id=f"doliv:{payout.id}",
                from_balance_id=system.id, to_balance_id=exec_work.id,
                description="Долив executor reward",
            )

    async def refund_doliv(self, payout, requester: "User") -> None:
        """Refund a canceled/expired долив: requester ESCROW → WORK for the full
        frozen amount (amount_usdt + доliv price). Turnover is NOT touched (the
        долив never completed)."""
        currency = Currency.USDT
        total = Decimal(str(payout.amount_usdt or 0)) + Decimal(str(payout.doliv_price_usdt or 0))
        if total <= 0 or requester is None:
            return
        work = await self.get_or_create_user_balance(requester, BalanceType.WORK, currency)
        escrow = await self.get_or_create_user_balance(requester, BalanceType.ESCROW, currency)
        await self.transfer(
            amount=total, currency=currency,
            reference_type=LedgerReferenceType.DOLIV, reference_id=f"doliv:{payout.id}",
            from_balance_id=escrow.id, to_balance_id=work.id,
            description="Долив refund",
        )

    async def reverse_settle_doliv(self, payout, *, requester: "User", executor: "User") -> None:
        """Undo a settled долив (COMPLETED → re-frozen) — the exact inverse of
        ``settle_doliv``, moving everything back to the requester's ESCROW so the
        долив returns to its frozen baseline. Used by the admin долив state
        machine when un-completing. Legs are ordered (reward → amount → price) so
        a lean system float never goes transiently negative. Raises (→ rollback,
        no status change) if the доливщик already spent the settled funds. Unlike
        refund_doliv's no-op-on-null, this RAISES on a missing party by design —
        a settled долив must have had both a requester and an executor."""
        currency = Currency.USDT
        amount = Decimal(str(payout.amount_usdt or 0))
        price = Decimal(str(payout.doliv_price_usdt or 0))
        reward = Decimal(str(payout.trader_fee_usdt or 0))
        if requester is None or executor is None:
            raise ValidationException("Cannot reverse долив without both requester and executor")
        req_escrow = await self.get_or_create_user_balance(requester, BalanceType.ESCROW, currency)
        exec_work = await self.get_or_create_user_balance(executor, BalanceType.WORK, currency)
        system = await self.get_or_create_system_balance(currency)
        if reward > 0:
            await self.transfer(
                amount=reward, currency=currency,
                reference_type=LedgerReferenceType.TRADER_REWARD, reference_id=f"doliv:{payout.id}",
                from_balance_id=exec_work.id, to_balance_id=system.id,
                description="Долив reverse (reward → system)",
            )
        await self.transfer(
            amount=amount, currency=currency,
            reference_type=LedgerReferenceType.DOLIV, reference_id=f"doliv:{payout.id}",
            from_balance_id=exec_work.id, to_balance_id=req_escrow.id,
            description="Долив reverse (amount → requester escrow)",
        )
        if price > 0:
            await self.transfer(
                amount=price, currency=currency,
                reference_type=LedgerReferenceType.SYSTEM_COMMISSION, reference_id=f"doliv:{payout.id}",
                from_balance_id=system.id, to_balance_id=req_escrow.id,
                description="Долив reverse (price → requester escrow)",
            )

    async def release_payout_hold(self, payout, trader) -> None:
        """Release a COMPLETED payout's held trader earnings: trader ESCROW → WORK
        for amount_usdt + trader fee."""
        currency = Currency.USDT
        amount = Decimal(str(payout.amount_usdt or 0)) + Decimal(str(payout.trader_fee_usdt or 0))
        if amount <= 0 or trader is None:
            return
        escrow = await self.get_or_create_user_balance(trader, BalanceType.ESCROW, currency)
        work = await self.get_or_create_user_balance(trader, BalanceType.WORK, currency)
        await self.transfer(
            amount=amount, currency=currency,
            reference_type=LedgerReferenceType.ORDER_PAYOUT, reference_id=f"payout:{payout.id}",
            from_balance_id=escrow.id, to_balance_id=work.id,
            description="Payout hold release",
        )

    # ── Read-only listing methods ─────────────────────────────

    async def list_user_withdrawals(
        self,
        user_id: int,
        user_role: "UserRole",
        *,
        status: Optional["WithdrawalStatus"] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[WithdrawalRequest]:
        return await self.withdrawal_repo.list_for_user(
            user_id, user_role, status=status, skip=skip, limit=limit,
        )

    async def list_all_withdrawals(
        self,
        *,
        status: Optional["WithdrawalStatus"] = None,
        skip: int = 0,
        limit: int = 100,
        user_role: Optional[UserRole] = None,
        user_login: Optional[str] = None,
    ) -> list[WithdrawalRequest]:
        return await self.withdrawal_repo.list_all(
            status=status,
            skip=skip,
            limit=limit,
            user_role=user_role,
            user_login=user_login,
        )

    async def list_all_withdrawals_enriched(
        self,
        *,
        status: Optional["WithdrawalStatus"] = None,
        skip: int = 0,
        limit: int = 100,
        user_role: Optional[UserRole] = None,
        user_login: Optional[str] = None,
    ) -> list[dict]:
        items = await self.list_all_withdrawals(
            status=status, skip=skip, limit=limit,
            user_role=user_role, user_login=user_login,
        )
        if not items:
            return []

        # After migration 016 `user_id` always references `users.id`, so we
        # can resolve usernames with a single query regardless of role.
        all_user_ids = {w.user_id for w in items}
        logins: dict[int, str] = {}
        if all_user_ids:
            res = await self.session.execute(
                select(User.id, User.username).where(User.id.in_(all_user_ids))
            )
            logins = {uid: uname for uid, uname in res.all()}

        merchant_ids = {w.merchant_id for w in items if w.merchant_id is not None}
        merchant_names: dict[int, Optional[str]] = {}
        if merchant_ids:
            res = await self.session.execute(
                select(Merchant.id, Merchant.name).where(Merchant.id.in_(merchant_ids))
            )
            merchant_names = {mid: name for mid, name in res.all()}

        enriched = []
        for w in items:
            enriched.append({
                "id": w.id,
                "uuid": w.uuid,
                "user_role": w.user_role,
                "user_id": w.user_id,
                "user_login": logins.get(w.user_id),
                "merchant_id": w.merchant_id,
                "merchant_name": merchant_names.get(w.merchant_id) if w.merchant_id else None,
                "amount": w.amount,
                "currency": w.currency,
                "destination_address": w.destination_address,
                "fee_amount": w.fee_amount,
                "status": w.status,
                "created_at": w.created_at,
                "updated_at": w.updated_at,
                "processed_at": w.processed_at,
                "processed_by_id": w.processed_by_id,
                "rejection_reason": w.rejection_reason,
            })
        return enriched

    async def list_all_balances(self, *, skip: int = 0, limit: int = 500) -> list[Balance]:
        return await self.balance_repo.list_all(skip=skip, limit=limit)

    async def list_ledger_entries(
        self,
        *,
        reference_type: Optional["LedgerReferenceType"] = None,
        reference_types: Optional[list["LedgerReferenceType"]] = None,
        balance_ids: Optional[list[int]] = None,
        skip: int = 0,
        limit: int = 100,
        order_search: Optional[str] = None,
        user_login: Optional[str] = None,
        amount_from: Optional[float] = None,
        amount_to: Optional[float] = None,
    ) -> list[LedgerEntryResponse]:
        """Fetch ledger entries and attach denormalised balance/owner info so
        the admin finances table can render counterparty badges without a
        separate lookup per row."""
        entries = await self.ledger_repo.list_entries(
            reference_type=reference_type,
            reference_types=reference_types,
            balance_ids=balance_ids,
            skip=skip,
            limit=limit,
            order_search=order_search,
            user_login=user_login,
            amount_from=amount_from,
            amount_to=amount_to,
        )

        balance_ids: set[int] = set()
        for e in entries:
            if e.from_balance_id:
                balance_ids.add(e.from_balance_id)
            if e.to_balance_id:
                balance_ids.add(e.to_balance_id)

        balance_map: dict[int, BalanceRefInfo] = {}
        if balance_ids:
            balances = (
                (await self.session.execute(
                    select(Balance).where(Balance.id.in_(balance_ids))
                ))
                .scalars()
                .all()
            )
            user_ids = {b.user_id for b in balances if b.user_id}
            merchant_ids = {b.merchant_id for b in balances if b.merchant_id}

            users_by_id: dict[int, User] = {}
            if user_ids:
                rows = (
                    (await self.session.execute(
                        select(User).where(User.id.in_(user_ids))
                    ))
                    .scalars()
                    .all()
                )
                users_by_id = {u.id: u for u in rows}

            merchants_by_id: dict[int, Merchant] = {}
            if merchant_ids:
                rows = (
                    (await self.session.execute(
                        select(Merchant).where(Merchant.id.in_(merchant_ids))
                    ))
                    .scalars()
                    .all()
                )
                merchants_by_id = {m.id: m for m in rows}

            for b in balances:
                # `owner_kind` is the narrowest role we can tell for the
                # balance owner: explicit user roles for user-backed balances,
                # "merchant" for merchant-backed balances, "system" for the
                # platform pool. The admin UI uses this to render a readable
                # role label (Trader / Agent / Merchant / System).
                if b.is_system:
                    owner_kind = "system"
                    owner_id = None
                    owner_label = "Система"
                elif b.merchant_id:
                    owner_kind = "merchant"
                    owner_id = b.merchant_id
                    merchant = merchants_by_id.get(b.merchant_id)
                    owner_label = (merchant.name if merchant and merchant.name else f"Мерч #{b.merchant_id}")
                elif b.user_id:
                    user = users_by_id.get(b.user_id)
                    owner_id = b.user_id
                    owner_kind = user.role.value if user and user.role else "user"
                    owner_label = user.username if user else f"User #{b.user_id}"
                else:
                    owner_kind = "system"
                    owner_id = None
                    owner_label = "—"

                balance_map[b.id] = BalanceRefInfo(
                    id=b.id,
                    type=b.type,
                    currency=b.currency,
                    owner_kind=owner_kind,
                    owner_id=owner_id,
                    owner_label=owner_label,
                )

        results: list[LedgerEntryResponse] = []
        for e in entries:
            results.append(
                LedgerEntryResponse(
                    id=e.id,
                    from_balance_id=e.from_balance_id,
                    to_balance_id=e.to_balance_id,
                    from_balance=balance_map.get(e.from_balance_id) if e.from_balance_id else None,
                    to_balance=balance_map.get(e.to_balance_id) if e.to_balance_id else None,
                    amount=e.amount,
                    currency=e.currency,
                    reference_type=e.reference_type,
                    reference_id=e.reference_id,
                    description=e.description,
                    created_at=e.created_at,
                )
            )
        return results