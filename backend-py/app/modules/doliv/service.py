"""Долив (requisite refill) business logic.

A долив reuses the ``Payout`` entity (flagged ``is_doliv``) so it shows up in the
existing payout pool / "my payouts" views, but its money flow and lifecycle live
here — the money-critical ``PayoutService`` is left untouched.

Lifecycle: CREATED (pool, requester funds frozen) → CLAIMED (доливщик takes it)
→ COMPLETED (доливщик confirms the real transfer → requester debited, доливщик
reimbursed + reward, requisite turnover filled). CREATED → CANCELED (requester
cancels an unclaimed долив → refund).

Money (USDT, at the order's fixed ``exchange_rate``):
  freeze  : requester WORK → ESCROW for amount_usdt + price_usdt
  settle  : requester ESCROW → доливщик WORK (amount); requester ESCROW → system
            (price); system → доливщик WORK (executor reward); turnover += amount
  refund  : requester ESCROW → WORK for amount_usdt + price_usdt
"""
from datetime import timedelta
from decimal import Decimal
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.payouts import PayoutStatus
from app.common.enums.users import UserRole
from app.common.types import utcnow
from app.core.exceptions import ValidationException
from app.modules.base.service import BaseService
from app.modules.doliv.exceptions import DolivConflict, DolivForbidden, DolivNotFound
from app.modules.finance.service import FinanceService
from app.modules.payments.models import PaymentOption
from app.modules.payouts.models import Payout
from app.modules.receipts.storage import ReceiptStorage
from app.workers.celery_app import celery_app
from app.modules.payouts.repository import PayoutRepository
from app.modules.requisites.models import Requisite
from app.modules.requisites.repository import RequisiteLimitRepository
from app.modules.settings.service import SettingsService
from app.modules.users.models import User

_Q = Decimal("0.0000")

# ── долив money-states (drive the admin state machine) ─────────────────────
# Which balance bucket a долив's funds sit in, per status:
#   FROZEN   — requester WORK→ESCROW held (created/claimed/awaiting), no turnover
#   SETTLED  — paid out to the доливщик, requisite turnover filled (completed)
#   REFUNDED — returned to the requester's WORK (canceled/expired)
# admin_change_status normalises the CURRENT state back to FROZEN, then applies
# the TARGET state's money — so ANY status→status move stays money-correct.
_DOLIV_SETTLED = frozenset({PayoutStatus.COMPLETED})
_DOLIV_REFUNDED = frozenset({PayoutStatus.CANCELED, PayoutStatus.EXPIRED})
# everything else (CREATED / CLAIMED / AWAITING_CHECK) is FROZEN.

# Transition guard (NOT a money-state): these target statuses imply a доливщик
# took the долив, so admin_change_status refuses them when there's no executor.
_NEEDS_EXECUTOR = frozenset({PayoutStatus.CLAIMED, PayoutStatus.COMPLETED})


def _doliv_money_state(status: PayoutStatus) -> str:
    if status in _DOLIV_SETTLED:
        return "settled"
    if status in _DOLIV_REFUNDED:
        return "refunded"
    return "frozen"


# A долив lives this long before it expires (and the freeze is refunded); a claim
# that isn't executed within the claim TTL returns the долив to the pool. Mirrors
# the payout defaults; the expiry/return sweeps are wired into the payout workers.
DEFAULT_DOLIV_TTL_MINUTES = 60
DEFAULT_DOLIV_CLAIM_TTL_SECONDS = 900


class DolivService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.finance = FinanceService(session)
        self.payouts = PayoutRepository(session)
        self.settings = SettingsService(session)

    # ── helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _q(v) -> Decimal:
        return Decimal(str(v)).quantize(_Q)

    @staticmethod
    def _parse_ids(csv: str) -> set:
        out = set()
        for part in (csv or "").replace(";", ",").split(","):
            part = part.strip()
            if part:
                try:
                    out.add(int(part))
                except (TypeError, ValueError):
                    continue
        return out

    async def executor_ids(self) -> set:
        return self._parse_ids(await self.settings.get_str("doliv_executor_user_ids"))

    async def is_executor(self, user_id: int) -> bool:
        return user_id in await self.executor_ids()

    # ── requester: create / cancel / list mine ────────────────────────────

    async def create(self, requester: User, requisite_id: int, amount) -> Payout:
        """Request a долив for ``amount`` (fiat) against one of the requester's
        own requisites. The долив fills that requisite's turnover; it is anchored
        to the requisite (not a specific order) and priced at the CURRENT platform
        rate for the requisite's currency. Validates, freezes the requester's
        funds, and adds the долив to the pool. Raises if the requisite isn't
        theirs, there's no active rate, the amount is out of the min/max or the
        remaining-to-limit window, or the balance can't cover amount + price."""
        requisite = await self.session.get(Requisite, requisite_id)
        if requisite is None:
            raise DolivNotFound(f"Requisite {requisite_id} not found")
        if requisite.trader_id != requester.id:
            raise DolivForbidden("Это не ваш реквизит")

        amt = self._q(amount)
        if amt <= 0:
            raise ValidationException("Сумма долива должна быть положительной")

        cfg_min = await self.settings.get_decimal("doliv_min_amount")
        cfg_max = await self.settings.get_decimal("doliv_max_amount")
        price_pct = await self.settings.get_decimal("doliv_price_percent")
        reward_pct = await self.settings.get_decimal("doliv_executor_reward_percent")
        if cfg_min > 0 and amt < cfg_min:
            raise ValidationException(f"Минимальная сумма долива — {cfg_min}")
        if cfg_max > 0 and amt > cfg_max:
            raise ValidationException(f"Максимальная сумма долива — {cfg_max}")

        # Cap at the remaining capacity up to the requisite's DAILY limit. Lock
        # the limit row + subtract already-pending доливы (turnover isn't bumped
        # until execute) so concurrent доливы on one requisite can't both pass
        # and over-commit the cap.
        limit = await RequisiteLimitRepository(self.session).get_by_requisite_id_for_update(requisite.id)
        if limit is not None:
            pending = await self.payouts.sum_pending_doliv_for_requisite(requisite.id)
            remaining = (
                Decimal(str(limit.limit_daily))
                - Decimal(str(limit.current_daily_turnover))
                - pending
            )
            if remaining <= 0:
                raise ValidationException("Дневной лимит реквизита уже заполнен")
            if amt > remaining:
                raise ValidationException(f"Сумма долива больше остатка до лимита ({remaining})")

        # Current platform rate for the requisite's fiat currency (долив is
        # requisite-anchored — there's no order with a fixed rate to inherit).
        rate = await self._current_rate(requisite.currency)
        amount_usdt = self._q(amt / rate)
        price_usdt = self._q(amount_usdt * price_pct / Decimal("100"))
        reward_usdt = self._q(amount_usdt * reward_pct / Decimal("100"))

        async with self.session.begin_nested():
            payout = Payout(
                uuid=uuid4(),
                external_id=f"doliv-{uuid4().hex}",
                is_doliv=True,
                payout_terminal_id=None,
                requester_trader_id=requester.id,
                refill_order_id=None,
                refill_requisite_id=requisite.id,
                trader_id=None,
                payment_method=requisite.payment_method,
                payment_option_id=requisite.payment_option_id,
                amount=amt,
                currency=requisite.currency,
                amount_usdt=amount_usdt,
                exchange_rate=rate,
                doliv_price_usdt=price_usdt,
                trader_fee_usdt=reward_usdt,  # доливщик reward, fixed at create (deterministic)
                req_holder=requisite.account_holder,
                req_number=requisite.account_number,
                req_extra=requisite.bank_name,
                status=PayoutStatus.CREATED,
                expires_at=utcnow() + timedelta(minutes=DEFAULT_DOLIV_TTL_MINUTES),
            )
            self.session.add(payout)
            await self.session.flush()
            # Freeze the requester's funds; raises (→ rollback, no долив) if short.
            await self.finance.freeze_doliv(payout, requester)
            await self.audit_log(
                action="doliv_created", entity_type="doliv", entity_id=payout.id,
                user_id=requester.id,
                new_values={
                    "requisite_id": requisite.id, "amount": float(amt),
                    "amount_usdt": float(amount_usdt), "price_usdt": float(price_usdt),
                },
            )
        await self.session.refresh(payout)
        return payout

    async def _current_rate(self, currency) -> Decimal:
        """Active platform rate for ``currency`` (USDT per fiat). Raises if none
        is configured — без курса долив посчитать нельзя."""
        from app.modules.rates.service import RateService

        configs = await RateService(self.session).get_active_configs()
        config = next((c for c in configs if c.fiat_currency == currency), None)
        if config is None or not config.current_rate:
            raise ValidationException("Нет активного курса для валюты реквизита")
        return Decimal(str(config.current_rate))

    async def cancel(self, requester: User, uuid: str) -> Payout:
        """Cancel a долив (requester only) → refund the freeze. Allowed ONLY while
        it's still CREATED (in the pool); once a доливщик takes it («В работе»)
        the trader can't cancel — only an admin can via the state machine."""
        payout = await self._get_doliv(uuid)
        if payout.requester_trader_id != requester.id:
            raise DolivForbidden("Это не ваш долив")
        async with self.session.begin_nested():
            locked = await self.payouts.lock(payout.id)
            if not locked or not locked.is_doliv or locked.status != PayoutStatus.CREATED:
                raise DolivConflict("Долив уже взят в работу или закрыт")
            await self.finance.refund_doliv(locked, requester)
            updated = await self.payouts.update(
                locked.id, {"status": PayoutStatus.CANCELED, "canceled_at": utcnow()}
            )
            await self.audit_log(
                action="doliv_canceled", entity_type="doliv", entity_id=locked.id,
                user_id=requester.id,
            )
        return updated

    async def list_mine(self, requester: User, *, skip: int = 0, limit: int = 50) -> List[Payout]:
        stmt = (
            select(Payout)
            .where(Payout.is_doliv.is_(True), Payout.requester_trader_id == requester.id)
            .order_by(Payout.created_at.desc())
            .offset(skip).limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    # ── доливщик: pool / claim / execute ──────────────────────────────────

    async def list_pool(self, executor: User, *, skip: int = 0, limit: int = 50) -> List[Payout]:
        """Claimable доливы — only for authorised доливщики; soonest-expiring first."""
        if not await self.is_executor(executor.id):
            return []
        stmt = (
            select(Payout)
            .where(Payout.is_doliv.is_(True), Payout.status == PayoutStatus.CREATED)
            .order_by(Payout.expires_at.asc(), Payout.created_at.asc())
            .offset(skip).limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_for_executor(self, executor: User, *, skip: int = 0, limit: int = 100) -> List[Payout]:
        """Everything a доливщик needs in the «Долив» tab: the open pool
        (claimable) PLUS every долив this доливщик has taken (claimed → executed
        / expired) as history-with-result. Newest first. Empty for non-executors
        (the tab is hidden for them)."""
        if not await self.is_executor(executor.id):
            return []
        stmt = (
            select(Payout)
            .where(
                Payout.is_doliv.is_(True),
                or_(
                    Payout.status == PayoutStatus.CREATED,        # open pool
                    Payout.trader_id == executor.id,              # mine (any status)
                ),
            )
            .order_by(Payout.created_at.desc())
            .offset(skip).limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    @staticmethod
    def _admin_filter_conds(
        *, status=None, requester_trader_id=None, executor_trader_id=None,
        created_from=None, created_to=None, search=None,
    ) -> list:
        """Shared WHERE clauses for the admin доливы list + count."""
        conds = [Payout.is_doliv.is_(True)]
        if status is not None:
            conds.append(Payout.status == status)
        if requester_trader_id is not None:
            conds.append(Payout.requester_trader_id == requester_trader_id)
        if executor_trader_id is not None:
            conds.append(Payout.trader_id == executor_trader_id)
        if created_from is not None:
            conds.append(Payout.created_at >= created_from)
        if created_to is not None:
            conds.append(Payout.created_at <= created_to)
        if search:
            s = search.strip()
            sub = [Payout.external_id.ilike(f"%{s}%")]
            if s.isdigit():
                n = int(s)
                sub += [Payout.id == n, Payout.refill_requisite_id == n, Payout.refill_order_id == n]
            conds.append(or_(*sub))
        return conds

    async def list_all(
        self, *, status=None, requester_trader_id=None, executor_trader_id=None,
        created_from=None, created_to=None, search=None, skip: int = 0, limit: int = 100,
    ) -> List[Payout]:
        """Filtered доливы list, newest first — the admin «Доливы» page."""
        conds = self._admin_filter_conds(
            status=status, requester_trader_id=requester_trader_id,
            executor_trader_id=executor_trader_id, created_from=created_from,
            created_to=created_to, search=search,
        )
        stmt = (
            select(Payout).where(and_(*conds))
            .order_by(Payout.created_at.desc()).offset(skip).limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def count_all(
        self, *, status=None, requester_trader_id=None, executor_trader_id=None,
        created_from=None, created_to=None, search=None,
    ) -> int:
        """Total доливы matching the admin filters (for pagination)."""
        conds = self._admin_filter_conds(
            status=status, requester_trader_id=requester_trader_id,
            executor_trader_id=executor_trader_id, created_from=created_from,
            created_to=created_to, search=search,
        )
        stmt = select(func.count()).select_from(Payout).where(and_(*conds))
        return int((await self.session.execute(stmt)).scalar_one())

    async def resolve_usernames(self, payouts: List[Payout]) -> dict:
        """Map trader-id → username for the requester/executor of the given
        доливы — the admin «Доливы» page shows names, not ids."""
        ids = {p.requester_trader_id for p in payouts} | {p.trader_id for p in payouts}
        ids.discard(None)
        if not ids:
            return {}
        rows = (await self.session.execute(
            select(User.id, User.username).where(User.id.in_(ids))
        )).all()
        return {uid: uname for uid, uname in rows}

    async def resolve_payment_options(self, payouts: List[Payout]) -> dict:
        """Map payment-option-id → (name, logo_url) for the долив tables — the
        «Банк» column shows the bank logo + payment-option name (как на реквизитах)."""
        ids = {p.payment_option_id for p in payouts}
        ids.discard(None)
        if not ids:
            return {}
        rows = (await self.session.execute(
            select(PaymentOption.id, PaymentOption.name, PaymentOption.logo_url)
            .where(PaymentOption.id.in_(ids))
        )).all()
        return {pid: (name, logo) for pid, name, logo in rows}

    async def amount_bounds(self) -> tuple:
        """(min, max, price_percent) долив config from settings — drives the
        create-modal hints («Доступный долив», «Стоимость»). 0 = no bound."""
        return (
            await self.settings.get_decimal("doliv_min_amount"),
            await self.settings.get_decimal("doliv_max_amount"),
            await self.settings.get_decimal("doliv_price_percent"),
        )

    async def claim(self, executor: User, uuid: str) -> Payout:
        """A доливщик takes a долив from the pool (exclusive, first-wins)."""
        if not await self.is_executor(executor.id):
            raise DolivForbidden("Вы не доливщик")
        payout = await self._get_doliv(uuid)
        async with self.session.begin_nested():
            bind = self.session.bind
            if bind is not None and bind.dialect.name == "postgresql":
                await self.session.execute(text("SET LOCAL lock_timeout = '2000ms'"))
            try:
                locked = await self.payouts.lock(payout.id)
            except (OperationalError, DBAPIError):
                raise DolivConflict("Долив занят; попробуйте другой")
            if not locked or not locked.is_doliv or locked.status != PayoutStatus.CREATED:
                raise DolivConflict("Долив больше недоступен")
            updated = await self.payouts.update(locked.id, {
                "status": PayoutStatus.CLAIMED,
                "trader_id": executor.id,
                "claimed_at": utcnow(),
                "claim_expires_at": utcnow() + timedelta(seconds=DEFAULT_DOLIV_CLAIM_TTL_SECONDS),
            })
            await self.audit_log(
                action="doliv_claimed", entity_type="doliv", entity_id=locked.id,
                user_id=executor.id,
            )
        return updated

    async def execute(self, executor: User, uuid: str, *, receipt_content: bytes,
                      receipt_filename: str) -> Payout:
        """The доливщик confirms the real transfer by ATTACHING a receipt (photo/
        PDF — validated + stored here) → settle + fill the requisite turnover →
        COMPLETED. The receipt is pushed to the requester (visible on the site +
        delivered to their telegram bot)."""
        payout = await self._get_doliv(uuid)
        # Validate + persist the proof before settling (mirrors the payout/receipt
        # save flow: bytes in, the SERVICE validates the format/size and stores it).
        ReceiptStorage.validate_size(receipt_content)
        ReceiptStorage.validate_format(receipt_content, receipt_filename)
        receipt_file = ReceiptStorage.save(payout.uuid, receipt_content, receipt_filename)
        async with self.session.begin_nested():
            locked = await self.payouts.lock(payout.id)
            if not locked or not locked.is_doliv or locked.status != PayoutStatus.CLAIMED:
                raise DolivConflict("Долив не в статусе «взят»")
            if locked.trader_id != executor.id:
                raise DolivForbidden("Этот долив взят не вами")
            requester = await self.session.get(User, locked.requester_trader_id)
            executor_user = await self.session.get(User, executor.id)
            await self.finance.settle_doliv(locked, requester=requester, executor=executor_user)
            await RequisiteLimitRepository(self.session).increment_turnover_by_amount(
                locked.refill_requisite_id, locked.amount,
            )
            updated = await self.payouts.update(
                locked.id, {
                    "status": PayoutStatus.COMPLETED, "completed_at": utcnow(),
                    "receipt_file": receipt_file, "receipt_uploaded_at": utcnow(),
                }
            )
            await self.audit_log(
                action="doliv_executed", entity_type="doliv", entity_id=locked.id,
                user_id=executor.id,
            )
        # Deliver the receipt to the requester's telegram bot (fire-and-forget;
        # the task re-reads the committed row and retries, so firing pre-commit
        # is safe).
        celery_app.send_task(
            "app.workers.tasks.trader_bot.notify_requester_doliv_receipt",
            args=[updated.id],
        )
        return updated

    async def get_with_receipt_access(self, uuid: str, user: User) -> Payout:
        """Fetch a долив for receipt download — the requester, the доливщик, or an
        admin may pull the file, and only after it's attached."""
        payout = await self._get_doliv(uuid)
        if user.role != UserRole.ADMIN and user.id not in (payout.requester_trader_id, payout.trader_id):
            raise DolivForbidden("Нет доступа к этому чеку")
        if not payout.receipt_file or not ReceiptStorage.is_within_upload_dir(payout.receipt_file):
            raise DolivNotFound("Чек не прикреплён")
        return payout

    # ── admin: free status machine ────────────────────────────────────────

    async def admin_change_status(self, uuid: str, target: PayoutStatus, admin: User) -> Payout:
        """Move a долив to ANY status (admin override) keeping the money correct.

        State machine: each status maps to a money-state (frozen / settled /
        refunded). The transition NORMALISES the current money back to the frozen
        baseline (reverse a settle, or re-freeze a refund), then APPLIES the
        target money-state (settle, or refund) — so any status→status move moves
        money exactly once and never mints/loses it. Locks the row + re-reads the
        committed status (idempotent: target==current is a no-op), and audits the
        admin action. Money steps raise → whole transition rolls back (e.g. the
        доливщик already spent settled funds, or the requester lacks WORK to
        re-freeze)."""
        payout = await self._get_doliv(uuid)
        async with self.session.begin_nested():
            locked = await self.payouts.lock(payout.id)
            if not locked or not locked.is_doliv:
                raise DolivNotFound("Долив не найден")
            old = locked.status
            if target == old:
                return locked  # idempotent no-op
            if target in _NEEDS_EXECUTOR and locked.trader_id is None:
                raise DolivConflict("Долив ещё не взят исполнителем — этот статус недоступен")

            requester = (
                await self.session.get(User, locked.requester_trader_id)
                if locked.requester_trader_id else None
            )
            executor = (
                await self.session.get(User, locked.trader_id)
                if locked.trader_id else None
            )
            limits = RequisiteLimitRepository(self.session)
            m_old, m_new = _doliv_money_state(old), _doliv_money_state(target)

            # 1) normalise current money → FROZEN baseline
            if m_old == "settled":
                await self.finance.reverse_settle_doliv(locked, requester=requester, executor=executor)
                await limits.decrement_turnover_by_amount(locked.refill_requisite_id, locked.amount)
            elif m_old == "refunded":
                await self.finance.freeze_doliv(locked, requester)  # re-freeze from WORK
            # 2) apply target money from FROZEN baseline
            if m_new == "settled":
                await self.finance.settle_doliv(locked, requester=requester, executor=executor)
                await limits.increment_turnover_by_amount(locked.refill_requisite_id, locked.amount)
            elif m_new == "refunded":
                await self.finance.refund_doliv(locked, requester)

            now = utcnow()
            fields = {"status": target}
            if target == PayoutStatus.COMPLETED:
                fields.update({"completed_at": now, "canceled_at": None})
            elif target in (PayoutStatus.CANCELED, PayoutStatus.EXPIRED):
                fields.update({"canceled_at": now, "completed_at": None})
            elif target == PayoutStatus.CREATED:
                # back to the unclaimed pool — clear executor + lifecycle stamps
                fields.update({
                    "trader_id": None, "claimed_at": None, "claim_expires_at": None,
                    "completed_at": None, "canceled_at": None,
                })
            elif target == PayoutStatus.CLAIMED and not locked.claimed_at:
                fields["claimed_at"] = now

            updated = await self.payouts.update(locked.id, fields)
            await self.audit_log(
                action="doliv_admin_changed", entity_type="doliv", entity_id=locked.id,
                user_id=admin.id,
                old_values={"status": old.value},
                new_values={"status": target.value},
            )
        return updated

    # ── internal ──────────────────────────────────────────────────────────

    async def _get_doliv(self, uuid: str) -> Payout:
        payout = await self.payouts.get_by_uuid(uuid)
        if not payout or not payout.is_doliv:
            raise DolivNotFound("Долив не найден")
        return payout
