"""Payout business logic: create → pool → claim → execute → settle.

Money settles in USDT, mirroring payin. At creation the merchant's WORK→ESCROW
is frozen for amount_usdt + merchant_fee_usdt; on completion that escrow pays
the trader (amount, reimbursement) and the platform (commission), and the
platform pays the trader's fee. No trader collateral. Optional per-trader hold
parks the trader's earnings in ESCROW for X hours (released by a worker).

All money lives in FinanceService payout primitives (``freeze_payout`` /
``settle_payout`` / ``refund_payout`` / ``release_payout_hold``); PayoutService
never moves money itself. Every status transition goes through the single
``change_status`` funnel (aggregate state machine, mirrors OrderService) which
routes the matching escrow op + audit + merchant callback in one atomic nested
tx. ``create_payout`` is the "create" core fn (freeze on creation).
"""
import os
import uuid as uuid_lib
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from fastapi import UploadFile
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutReceiptStatus, PayoutStatus
from app.common.types import utcnow
from app.core.config import get_settings
from app.core.exceptions import ForbiddenException, ValidationException
from app.modules.base.service import BaseService
from app.modules.finance.service import FinanceService
from app.modules.payouts.exceptions import PayoutConflict, PayoutNotFound
from app.modules.payouts.models import Payout, PayoutTerminal
from app.modules.payouts.repository import PayoutRepository
from app.modules.rates.service import RateService
from app.modules.traders.models import Trader
from app.modules.users.models import User

settings = get_settings()

_USDT = Currency.USDT
_Q = Decimal("0.0000")

# Global defaults (overridable via PlatformSetting — wired in a later step).
DEFAULT_CLAIM_TTL_SECONDS = 900   # claimed-but-unconfirmed → auto-return to pool
DEFAULT_RECEIPT_AUTO = True       # auto-complete on receipt unless trader overrides

# Payout state machine — the ONLY legal status transitions. ``change_status``
# validates every move against this graph (single source of transition
# legality). Money per target: COMPLETED → settle, CANCELED/EXPIRED → refund,
# all others (CLAIMED / AWAITING_CHECK / back-to-CREATED) move no money.
_PAYOUT_TRANSITIONS: dict = {
    PayoutStatus.CREATED: frozenset({
        PayoutStatus.CLAIMED, PayoutStatus.CANCELED, PayoutStatus.EXPIRED,
    }),
    PayoutStatus.CLAIMED: frozenset({
        PayoutStatus.AWAITING_CHECK, PayoutStatus.COMPLETED,
        PayoutStatus.CREATED, PayoutStatus.CANCELED, PayoutStatus.EXPIRED,
    }),
    PayoutStatus.AWAITING_CHECK: frozenset({
        PayoutStatus.COMPLETED, PayoutStatus.CLAIMED,
        PayoutStatus.CANCELED, PayoutStatus.EXPIRED,
    }),
}


class PayoutService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.repository = PayoutRepository(session)
        self.finance = FinanceService(session)

    # ── small helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _q(v) -> Decimal:
        return Decimal(str(v)).quantize(_Q)

    def _claim_ttl(self) -> int:
        return DEFAULT_CLAIM_TTL_SECONDS

    @staticmethod
    def _effective_receipt_auto(trader: Optional[Trader]) -> bool:
        if trader is not None and trader.payout_receipt_auto is not None:
            return bool(trader.payout_receipt_auto)
        return DEFAULT_RECEIPT_AUTO

    def _calc_trader_fee(self, trader: Optional[Trader], amount_usdt: Decimal) -> Decimal:
        pct = Decimal(str(trader.payout_fee_percent or 0)) if trader else Decimal("0")
        return self._q(amount_usdt * pct / Decimal("100"))

    async def _load_trader(self, user_id: Optional[int]) -> Optional[Trader]:
        if user_id is None:
            return None
        res = await self.session.execute(select(Trader).where(Trader.user_id == user_id))
        return res.scalars().first()

    async def _rate_for(self, terminal: PayoutTerminal, currency: Currency) -> Decimal:
        """Fiat→USDT rate for the terminal: its pinned rate_config if active,
        else the active config for the currency."""
        rate_service = RateService(self.session)
        config = None
        if terminal.rate_config_id:
            try:
                config = await rate_service.get_config(terminal.rate_config_id)
                if not config.is_active or not config.current_rate:
                    config = None
            except Exception:
                config = None
        if not config:
            active = await rate_service.get_active_configs()
            config = next((c for c in active if c.fiat_currency == currency), None)
        if not config or not config.current_rate:
            raise ValidationException(f"Payouts in {currency.value} are temporarily unavailable")
        return Decimal(str(config.current_rate))

    async def _save_receipt(self, payout: Payout, attachment: UploadFile) -> str:
        max_bytes = settings.MAX_RECEIPT_SIZE_MB * 1024 * 1024
        content = await attachment.read()
        if len(content) > max_bytes:
            raise ValidationException(f"File size exceeds {settings.MAX_RECEIPT_SIZE_MB}MB")
        # Content gate (extension allowlist + magic-byte match) — the same guard
        # every other receipt/evidence upload path applies (orders/doliv). Without
        # it a renamed HTML/SVG would be stored as a "receipt".
        from app.modules.receipts.storage import ReceiptStorage
        ReceiptStorage.validate_format(content, attachment.filename)
        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
        ext = os.path.splitext(attachment.filename or "")[1][:10]
        filename = f"payout_{payout.uuid}_{uuid_lib.uuid4().hex[:8]}{ext}"
        filepath = os.path.join(settings.UPLOAD_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(content)
        return filepath

    def _notify(self, payout: Payout) -> None:
        """Enqueue a merchant callback for the current payout state (fail-safe)."""
        try:
            from app.workers.celery_app import celery_app
            celery_app.send_task("app.workers.tasks.callbacks.send_payout_callback", args=[payout.id])
        except Exception:
            pass

    # ── the single status funnel (aggregate state machine) ─────────────────

    async def change_status(
        self,
        payout: Payout,
        new_status: PayoutStatus,
        *,
        actor_id: Optional[int] = None,
        reason: Optional[str] = None,
        extra_fields: Optional[dict] = None,
        audit_action: Optional[str] = "payout_status_change",
        fire_callback: bool = True,
    ) -> Payout:
        """The single funnel for EVERY payout status transition. Validates the
        move against ``_PAYOUT_TRANSITIONS``, writes the status (+ matching
        timestamps and any caller ``extra_fields``), routes the matching escrow
        op through FinanceService, audits, and notifies the merchant after commit.
        Status + money + audit are ONE atomic nested tx.

        Money per target status (FinanceService is the source of truth):
          * COMPLETED          → settle_payout (merchant ESCROW → trader + commission
                                  + trader fee; held to trader ESCROW if hold > 0)
          * CANCELED / EXPIRED → refund_payout (merchant ESCROW → WORK, full)
          * CLAIMED / AWAITING_CHECK / back-to-CREATED → no money

        (``change_amount`` for payouts is intentionally not implemented yet.)
        """
        # Cheap early-out (re-checked authoritatively under the row lock below).
        if new_status == payout.status:
            return payout

        fields: dict = dict(extra_fields or {})
        fields["status"] = new_status
        if reason is not None:
            fields["rejection_reason"] = reason
        if new_status == PayoutStatus.COMPLETED:
            fields["completed_at"] = utcnow()
        elif new_status in (PayoutStatus.CANCELED, PayoutStatus.EXPIRED):
            fields["canceled_at"] = utcnow()

        async with self.session.begin_nested():
            # Serialize ALL transitions on the payout row: lock it FOR UPDATE and
            # re-read the authoritative status, so concurrent transitions (e.g. two
            # admins approving the last receipt, or merchant-cancel racing a claim)
            # can't double-settle / strand the payout. The money is also guarded by
            # the balance FOR UPDATE in transfer(), but this keeps STATE consistent.
            locked = await self.repository.lock(payout.id)
            if locked is None:
                raise PayoutNotFound("Payout not found")
            old_status = locked.status
            if new_status == old_status:
                return locked  # another tx already applied this transition
            if new_status not in _PAYOUT_TRANSITIONS.get(old_status, frozenset()):
                raise PayoutConflict(
                    f"Illegal payout transition {old_status.value} → {new_status.value}"
                )

            terminal = await self.session.get(PayoutTerminal, locked.payout_terminal_id)
            if new_status == PayoutStatus.COMPLETED:
                trader_user = await self.session.get(User, locked.trader_id)
                trader = await self._load_trader(locked.trader_id)
                hold_hours = int(trader.payout_hold_hours or 0) if trader else 0
                await self.finance.settle_payout(
                    locked, terminal=terminal, trader=trader_user, hold_hours=hold_hours,
                )
                if hold_hours > 0:
                    fields["trader_hold_until"] = utcnow() + timedelta(hours=hold_hours)
            elif new_status in (PayoutStatus.CANCELED, PayoutStatus.EXPIRED):
                await self.finance.refund_payout(locked, terminal)
                # Void any live receipts so a dead payout doesn't show pending/
                # approved ones in admin tooling.
                for r in await self.repository.list_receipts(locked.id):
                    if r.status != PayoutReceiptStatus.REJECTED:
                        await self.repository.update_receipt(
                            r.id, {"status": PayoutReceiptStatus.REJECTED,
                                    "rejection_reason": f"payout {new_status.value}"}
                        )

            payout = await self.repository.update(locked.id, fields)
            if audit_action is not None:
                new_values: dict = {"status": new_status.value}
                if reason is not None:
                    new_values["reason"] = reason
                await self.audit_log(
                    action=audit_action, entity_type="payout", entity_id=payout.id,
                    user_id=actor_id, new_values=new_values,
                )

        if fire_callback:
            self._notify(payout)
        return payout

    # ── merchant: create / cancel ──────────────────────────────────────────

    # Per-payout TTL override clamps (minutes).
    _TTL_OVERRIDE_MIN = 1
    _TTL_OVERRIDE_MAX = 7 * 24 * 60  # 7 days

    async def create_payout(self, terminal_id: int, data, *, ttl_override_minutes: Optional[int] = None) -> Payout:
        """Create a payout against a payout terminal: convert at the terminal's
        rate, charge the terminal's commission, freeze the terminal's balance,
        and set the deadline from the terminal TTL (minutes) or the per-payout
        override (clamped). Idempotent on (terminal, external_id)."""
        terminal = await self.session.get(PayoutTerminal, terminal_id)
        if not terminal:
            raise PayoutNotFound("Payout terminal not found")

        amount = Decimal(str(data.amount))
        if terminal.min_amount is not None and amount < Decimal(str(terminal.min_amount)):
            raise ValidationException(f"Amount below terminal minimum ({terminal.min_amount})")
        if terminal.max_amount is not None and amount > Decimal(str(terminal.max_amount)):
            raise ValidationException(f"Amount above terminal maximum ({terminal.max_amount})")

        if await self.repository.get_by_external_and_terminal(data.external_id, terminal_id):
            raise ValidationException(
                f"Payout with external_id '{data.external_id}' already exists"
            )

        rate = await self._rate_for(terminal, data.currency)
        amount_usdt = self._q(amount / rate)
        commission_pct = Decimal(str(terminal.commission_percent or 0))
        merchant_fee_usdt = self._q(amount_usdt * commission_pct / Decimal("100"))

        ttl_minutes = int(terminal.ttl_minutes or 60)
        if ttl_override_minutes is not None:
            ttl_minutes = max(self._TTL_OVERRIDE_MIN, min(self._TTL_OVERRIDE_MAX, int(ttl_override_minutes)))

        # create_payout is the "create" core fn (the row is born CREATED with the
        # terminal funds frozen); subsequent moves go through change_status. The
        # soft external_id check above is a friendly pre-empt; the unique
        # constraint (terminal, external_id) is authoritative and we translate its
        # race-condition IntegrityError into the same clean idempotency error.
        try:
            async with self.session.begin_nested():
                payout = await self.repository.create({
                    "external_id": data.external_id,
                    "payout_terminal_id": terminal_id,
                    "client_user_id": data.user_id,
                    "payment_method": data.payment_method,
                    "payment_option_id": data.payment_option,
                    "amount": amount,
                    "currency": data.currency,
                    "amount_usdt": amount_usdt,
                    "exchange_rate": rate,
                    "merchant_fee_usdt": merchant_fee_usdt,
                    "req_holder": data.payment_requisites.holder,
                    "req_number": data.payment_requisites.number,
                    "req_extra": data.payment_requisites.extra,
                    "webhook_url": data.notification_url,
                    "status": PayoutStatus.CREATED,
                    "expires_at": utcnow() + timedelta(minutes=ttl_minutes),
                })
                # Freeze terminal funds (WORK → ESCROW) through the single primitive;
                # raises Insufficient funds if the terminal can't cover amount + fee.
                await self.finance.freeze_payout(payout, terminal)
                await self.audit_log(
                    action="payout_created", entity_type="payout", entity_id=payout.id,
                    user_id=terminal.user_id,
                    new_values={"amount": float(payout.amount), "currency": data.currency.value},
                )
        except IntegrityError:
            raise ValidationException(
                f"Payout with external_id '{data.external_id}' already exists"
            )
        self._notify(payout)
        return payout

    async def cancel_by_merchant(self, uuid: str, terminal_id: int) -> Payout:
        payout = await self.repository.get_by_uuid_and_terminal(uuid, terminal_id)
        if not payout:
            raise PayoutNotFound("Payout not found")
        # Lock the row and verify it's STILL unclaimed before canceling — the row
        # lock is held to the request-tx commit, so a concurrent trader claim can't
        # interleave (else the merchant could cancel an already-claimed payout).
        async with self.session.begin_nested():
            locked = await self.repository.lock(payout.id)
            if not locked or locked.status != PayoutStatus.CREATED:
                raise PayoutConflict("Only unclaimed payouts can be canceled")
        return await self.change_status(
            payout, PayoutStatus.CANCELED, audit_action="payout_canceled",
        )

    # ── merchant cabinet: list own payouts / cancel ─────────────────────────

    @staticmethod
    def to_merchant_list_item(payout: Payout, terminal_name: Optional[str] = None):
        """Map a Payout ORM row to the merchant-cabinet list schema. The owner
        sees the financials + the destination requisite they provided, never the
        trader's internal ids."""
        from app.modules.payouts.schemas.merchant import MerchantPayoutListItem

        def _f(v):
            return float(v) if v is not None else None

        return MerchantPayoutListItem(
            id=str(payout.uuid),
            external_id=payout.external_id,
            payout_terminal_id=payout.payout_terminal_id,
            terminal_name=terminal_name,
            payment_method=payout.payment_method,
            amount=float(payout.amount),
            currency=payout.currency,
            amount_usdt=_f(payout.amount_usdt),
            merchant_fee_usdt=_f(payout.merchant_fee_usdt),
            status=payout.status,
            client_user_id=payout.client_user_id,
            req_holder=payout.req_holder,
            req_number=payout.req_number,
            req_extra=payout.req_extra,
            rejection_reason=payout.rejection_reason,
            created_at=payout.created_at,
            expires_at=payout.expires_at,
            completed_at=payout.completed_at,
            canceled_at=payout.canceled_at,
        )

    async def list_for_owner(
        self, owner_user_id: int, *, status: Optional[PayoutStatus] = None,
        payment_method: Optional[PaymentMethod] = None, search: Optional[str] = None,
        skip: int = 0, limit: int = 50,
    ) -> tuple[list, int]:
        """Paginated payouts across every payout terminal the merchant owns.
        Mirrors ``OrderService.list_orders_for_user_merchants``. Owns no terminals
        → empty list (a fast, correct empty page)."""
        from app.modules.payouts.terminal_repository import PayoutTerminalRepository

        terminals = await PayoutTerminalRepository(self.session).list_all(
            owner_id=owner_user_id, limit=1000,
        )
        name_by_id = {t.id: t.name for t in terminals}
        terminal_ids = list(name_by_id.keys())
        if not terminal_ids:
            return [], 0
        payouts = await self.repository.list_for_terminals(
            terminal_ids, status=status, payment_method=payment_method,
            search=search, skip=skip, limit=limit,
        )
        total = await self.repository.count_for_terminals(
            terminal_ids, status=status, payment_method=payment_method, search=search,
        )
        items = [
            self.to_merchant_list_item(p, name_by_id.get(p.payout_terminal_id))
            for p in payouts
        ]
        return items, total

    async def cancel_for_owner(self, owner_user_id: int, uuid: str):
        """Cancel an unclaimed payout the merchant owns. Verifies the payout
        belongs to one of the caller's terminals before delegating to
        ``cancel_by_merchant`` (which enforces the unclaimed guard)."""
        payout = await self.repository.get_by_uuid(uuid)
        if not payout:
            raise PayoutNotFound("Payout not found")
        terminal = await self.session.get(PayoutTerminal, payout.payout_terminal_id)
        if not terminal or terminal.user_id != owner_user_id:
            raise PayoutNotFound("Payout not found")
        canceled = await self.cancel_by_merchant(str(payout.uuid), terminal.id)
        return self.to_merchant_list_item(canceled, terminal.name)

    # ── trader: claim / receipt ─────────────────────────────────────────────

    async def _allowed_terminal_ids(self, trader_id: int) -> list[int]:
        from app.modules.payouts.terminal_repository import PayoutTerminalRepository
        return await PayoutTerminalRepository(self.session).allowed_terminal_ids_for_trader(trader_id)

    async def list_pool_for_trader(self, current_user: User, *, skip: int = 0, limit: int = 50) -> list[Payout]:
        """Claimable payouts the trader may serve — only terminals whose ACL
        includes them (no terminals bound → empty pool). Soonest-expiring first."""
        trader = await self._load_trader(current_user.id)
        if not trader or not trader.is_payout_active:
            return []
        terminal_ids = await self._allowed_terminal_ids(current_user.id)
        if not terminal_ids:
            return []
        return await self.repository.list_pool(terminal_ids=terminal_ids, skip=skip, limit=limit)

    async def list_trader_payouts(self, trader_id: int, *, skip: int = 0, limit: int = 50) -> list[Payout]:
        return await self.repository.list_for_trader(trader_id, skip=skip, limit=limit)

    async def claim_payout(self, uuid: str, current_user: User) -> Payout:
        trader = await self._load_trader(current_user.id)
        if not trader or not trader.is_payout_active:
            raise ForbiddenException("Payouts are not enabled for this trader")
        payout = await self.repository.get_by_uuid(uuid)
        if not payout:
            raise PayoutNotFound("Payout not found")

        # ACL: the trader must be bound to the payout's terminal.
        allowed = await self._allowed_terminal_ids(current_user.id)
        if payout.payout_terminal_id not in allowed:
            raise ForbiddenException("You are not assigned to this payout terminal")

        # Exclusive claim: lock the row FOR UPDATE (lock held for the request tx,
        # so two traders can't both claim) and verify it's still in the pool.
        async with self.session.begin_nested():
            bind = self.session.bind
            if bind is not None and bind.dialect.name == "postgresql":
                await self.session.execute(text("SET LOCAL lock_timeout = '2000ms'"))
            try:
                locked = await self.repository.lock(payout.id)
            except (OperationalError, DBAPIError):
                raise PayoutConflict("Payout is busy; try another one")
            if not locked or locked.status != PayoutStatus.CREATED:
                raise PayoutConflict("Payout is no longer available")
            trader_fee = self._calc_trader_fee(trader, Decimal(str(locked.amount_usdt or 0)))

        # CREATED → CLAIMED through the funnel (no money; sets the trader fields).
        return await self.change_status(
            locked, PayoutStatus.CLAIMED, actor_id=current_user.id, audit_action="payout_claimed",
            extra_fields={
                "trader_id": current_user.id,
                "trader_fee_usdt": trader_fee,
                "claimed_at": utcnow(),
                "claim_expires_at": utcnow() + timedelta(seconds=self._claim_ttl()),
            },
        )

    # Statuses that count toward the payout's coverage / receipt cap (a REJECTED
    # receipt is voided and frees its slot + amount for a re-upload).
    _LIVE_RECEIPTS = (PayoutReceiptStatus.PENDING, PayoutReceiptStatus.APPROVED)

    async def add_receipt(
        self, uuid: str, current_user: User, attachment: UploadFile, amount,
    ) -> Payout:
        """Add ONE partial-payment receipt to a claimed payout. Each receipt has
        its own fiat ``amount`` ≤ the remaining (no overpay); up to the terminal's
        ``receipts_to_close`` receipts may be used. Auto-approves (per the trader's
        receipt mode) or lands PENDING for admin check. After adding, the payout
        is re-reconciled: it COMPLETES once the APPROVED amounts sum to EXACTLY the
        full amount with nothing pending; settlement happens once, on the full
        amount."""
        payout = await self.repository.get_by_uuid(uuid)
        if not payout:
            raise PayoutNotFound("Payout not found")
        if payout.trader_id != current_user.id:
            raise ForbiddenException("This payout is not assigned to you")
        if payout.status not in (PayoutStatus.CLAIMED, PayoutStatus.AWAITING_CHECK):
            raise PayoutConflict("Payout is not accepting receipts")

        amt = self._q(amount)
        if amt <= 0:
            raise ValidationException("Receipt amount must be positive")

        # File IO before taking the DB row lock (don't hold a lock across disk).
        filepath = await self._save_receipt(payout, attachment)
        trader = await self._load_trader(current_user.id)
        auto = self._effective_receipt_auto(trader)
        rstatus = PayoutReceiptStatus.APPROVED if auto else PayoutReceiptStatus.PENDING

        # Lock the payout so the remaining/count checks + the insert are ATOMIC —
        # otherwise two concurrent add_receipt calls could each pass `amt <=
        # remaining` against a stale sum and overpay / exceed the cap.
        async with self.session.begin_nested():
            locked = await self.repository.lock(payout.id)
            if not locked or locked.status not in (PayoutStatus.CLAIMED, PayoutStatus.AWAITING_CHECK):
                raise PayoutConflict("Payout is not accepting receipts")
            terminal = await self.session.get(PayoutTerminal, locked.payout_terminal_id)
            max_n = int(terminal.receipts_to_close or 1) if terminal else 1
            used = await self.repository.sum_receipts(locked.id, statuses=self._LIVE_RECEIPTS)
            remaining = Decimal(str(locked.amount)) - used
            if amt > remaining:
                raise ValidationException(f"Receipt amount exceeds remaining ({remaining})")
            count = await self.repository.count_receipts(locked.id, statuses=self._LIVE_RECEIPTS)
            if count >= max_n:
                raise PayoutConflict(f"Max receipts reached ({max_n})")
            await self.repository.create_receipt({
                "payout_id": locked.id, "trader_id": current_user.id,
                "amount": amt, "file": filepath, "status": rstatus,
                "moderated_at": (utcnow() if auto else None),
            })
            await self.repository.update(
                locked.id, {"receipt_file": filepath, "receipt_uploaded_at": utcnow()}
            )
        return await self._reconcile_payout(payout.id, actor_id=current_user.id)

    async def _reconcile_payout(self, payout_id: int, *, actor_id: Optional[int] = None) -> Payout:
        """Move the payout to the status implied by its receipts: COMPLETED when
        APPROVED amounts == the full amount and nothing is pending; AWAITING_CHECK
        while any receipt is pending; otherwise CLAIMED (more receipts needed).
        ``change_status`` no-ops when the status is unchanged."""
        payout = await self.repository.get(payout_id)
        approved = await self.repository.sum_receipts(payout_id, statuses=[PayoutReceiptStatus.APPROVED])
        pending = await self.repository.count_receipts(payout_id, statuses=[PayoutReceiptStatus.PENDING])
        full = Decimal(str(payout.amount))

        if approved == full and pending == 0:
            return await self.change_status(
                payout, PayoutStatus.COMPLETED, actor_id=actor_id, audit_action="payout_completed",
            )
        if pending > 0:
            return await self.change_status(
                payout, PayoutStatus.AWAITING_CHECK, actor_id=actor_id,
                audit_action="payout_awaiting_check",
            )
        return await self.change_status(
            payout, PayoutStatus.CLAIMED, actor_id=actor_id, audit_action="payout_receipt_added",
        )

    # ── admin ───────────────────────────────────────────────────────────────

    async def admin_approve_receipt(self, receipt_id: int, admin_id: int) -> Payout:
        """Approve one PENDING receipt; the payout completes if this closes it."""
        receipt = await self.repository.get_receipt(receipt_id)
        if not receipt:
            raise PayoutNotFound("Receipt not found")
        if receipt.status != PayoutReceiptStatus.PENDING:
            raise PayoutConflict("Receipt is not pending verification")
        async with self.session.begin_nested():
            await self.repository.update_receipt(receipt_id, {
                "status": PayoutReceiptStatus.APPROVED,
                "moderated_at": utcnow(), "moderated_by": admin_id,
            })
        return await self._reconcile_payout(receipt.payout_id, actor_id=admin_id)

    async def admin_reject_receipt(self, receipt_id: int, admin_id: int, reason: str) -> Payout:
        """Reject one PENDING receipt → voided; the trader re-uploads that
        installment (the payout drops back to CLAIMED if nothing else pends)."""
        receipt = await self.repository.get_receipt(receipt_id)
        if not receipt:
            raise PayoutNotFound("Receipt not found")
        if receipt.status != PayoutReceiptStatus.PENDING:
            raise PayoutConflict("Receipt is not pending verification")
        async with self.session.begin_nested():
            await self.repository.update_receipt(receipt_id, {
                "status": PayoutReceiptStatus.REJECTED, "rejection_reason": reason,
                "moderated_at": utcnow(), "moderated_by": admin_id,
            })
        return await self._reconcile_payout(receipt.payout_id, actor_id=admin_id)

    async def admin_cancel(self, uuid: str, admin_id: int, reason: Optional[str] = None) -> Payout:
        payout = await self.repository.get_by_uuid(uuid)
        if not payout:
            raise PayoutNotFound("Payout not found")
        if payout.status in (PayoutStatus.COMPLETED, PayoutStatus.CANCELED, PayoutStatus.EXPIRED):
            raise PayoutConflict("Payout is already in a terminal state")
        return await self.change_status(
            payout, PayoutStatus.CANCELED, actor_id=admin_id, reason=reason,
            audit_action="payout_admin_canceled",
        )

    async def admin_complete(self, uuid: str, admin_id: int) -> Payout:
        payout = await self.repository.get_by_uuid(uuid)
        if not payout:
            raise PayoutNotFound("Payout not found")
        if payout.status not in (PayoutStatus.CLAIMED, PayoutStatus.AWAITING_CHECK):
            raise PayoutConflict("Payout cannot be force-completed from its current state")
        return await self.change_status(
            payout, PayoutStatus.COMPLETED, actor_id=admin_id, audit_action="payout_completed",
        )

    # ── worker per-item operations (caller manages session/commit) ──────────

    async def expire_payout(self, payout: Payout) -> None:
        await self.change_status(payout, PayoutStatus.EXPIRED, audit_action="payout_expired")

    async def return_stale_claim(self, payout: Payout) -> None:
        # Void any partial receipts from the lapsed claim so the old trader's
        # progress can't carry over to whoever claims next from the pool.
        async with self.session.begin_nested():
            for r in await self.repository.list_receipts(payout.id):
                if r.status != PayoutReceiptStatus.REJECTED:
                    await self.repository.update_receipt(
                        r.id, {"status": PayoutReceiptStatus.REJECTED,
                                "rejection_reason": "claim returned to pool"}
                    )
        await self.change_status(
            payout, PayoutStatus.CREATED, audit_action="payout_returned_to_pool",
            extra_fields={
                "trader_id": None, "trader_fee_usdt": None,
                "claimed_at": None, "claim_expires_at": None,
                "receipt_file": None, "receipt_uploaded_at": None,
            },
        )

    async def release_hold(self, payout: Payout) -> None:
        """Release a COMPLETED payout's held trader earnings (worker; NOT a status
        change — the payout stays COMPLETED). Money via FinanceService."""
        # Defensive: only release a real, not-yet-released hold (the worker query
        # already filters these, but guard direct calls so we never try to move
        # from an empty trader ESCROW).
        if not payout.trader_hold_until or payout.hold_released_at:
            return
        async with self.session.begin_nested():
            # Lock the row + re-check hold_released_at UNDER the lock — two worker
            # runs / a retry both read hold_released_at=None on the unlocked object
            # otherwise and double-release ESCROW→WORK to the trader.
            locked = await self.repository.lock(payout.id)
            if not locked or not locked.trader_hold_until or locked.hold_released_at:
                return
            trader_user = await self.session.get(User, locked.trader_id)
            await self.finance.release_payout_hold(locked, trader_user)
            await self.repository.update(locked.id, {"hold_released_at": utcnow()})
