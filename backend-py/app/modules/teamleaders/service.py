from decimal import Decimal
from typing import List, Optional, Union

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency
from app.common.enums.orders import OrderStatus
from app.common.enums.users import UserRole
from app.core.exceptions import NotFoundException, ValidationException
from app.modules.base.service import BaseService
from app.modules.finance.models import LedgerEntry
from app.modules.finance.repository import LedgerRepository
from app.modules.finance.service import FinanceService
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.teamleaders.models import TeamleadLink
from app.modules.teamleaders.repository import TeamleadLinkRepository
from app.modules.teamleaders.schemas import TeamleadLinkCreate, TeamleadLinkUpdate
from app.modules.users.models import User


class TeamleaderService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.repository = TeamleadLinkRepository(session)
        self.ledger_repository = LedgerRepository(session)
        self.finance_service = FinanceService(session)

    async def list_all_links(self, skip: int = 0, limit: int = 100, teamlead_id: Optional[int] = None) -> List[TeamleadLink]:
        """List all teamlead links (admin)."""
        if teamlead_id is not None:
            return await self.repository.get_by_teamlead(teamlead_id)
        return await self.repository.get_all(skip=skip, limit=limit)

    async def list_admin_teamleads(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
        search: Optional[str] = None,
        is_active: Optional[bool] = None,
        balance_from: Optional[float] = None,
        balance_to: Optional[float] = None,
    ) -> List["TeamleadAdminItem"]:
        """List teamlead users with their USDT balances (admin)."""
        from app.modules.teamleaders.schemas import TeamleadAdminItem
        from app.modules.users.repository import UserRepository

        user_repo = UserRepository(self.session)
        users, _ = await user_repo.get_users(
            skip=skip,
            limit=limit,
            role=UserRole.TEAMLEAD,
            is_active=is_active,
            balance_from=balance_from,
            balance_to=balance_to,
            search=search,
        )
        if not users:
            return []
        balances = await user_repo.get_usdt_balances_by_user_ids([u.id for u in users])
        return [
            TeamleadAdminItem(
                id=u.id,
                username=u.username,
                is_active=not u.is_blocked,
                is_blocked=u.is_blocked,
                balance_usdt=balances.get(u.id, 0.0),
                created_at=u.created_at,
            )
            for u in users
        ]

    async def create_link(self, data: TeamleadLinkCreate, admin_user_id: Optional[int] = None) -> TeamleadLink:
        """
        Create a new link between a teamlead and a merchant or trader.
        """
        # Validate teamlead exists and has TEAMLEAD role
        teamlead = await self.session.get(User, data.teamlead_id)
        if not teamlead or teamlead.role != UserRole.TEAMLEAD:
            raise ValidationException(f"User {data.teamlead_id} is not a valid teamlead")

        if data.linked_entity_type == UserRole.MERCHANT:
            merchant = await self.session.get(Merchant, data.linked_entity_id)
            if not merchant:
                raise NotFoundException(f"Merchant {data.linked_entity_id} not found")
        elif data.linked_entity_type == UserRole.TRADER:
            trader = await self.session.get(User, data.linked_entity_id)
            if not trader or trader.role != UserRole.TRADER:
                raise ValidationException(f"User {data.linked_entity_id} is not a valid trader")
        else:
            raise ValidationException("linked_entity_type must be merchant or trader")

        existing = await self.repository.get_by_teamlead_and_entity(
            data.teamlead_id, data.linked_entity_type, data.linked_entity_id
        )
        if existing:
            raise ValidationException(
                "Teamlead link already exists for this merchant/trader"
            )

        async with self.session.begin_nested():
            link = await self.repository.create(data.model_dump())
            
            await self.audit_log(
                action="create_teamlead_link",
                entity_type="teamlead_link",
                entity_id=link.id,
                user_id=admin_user_id,
                new_values=data.model_dump(),
            )
            
        return link

    async def update_link(self, link_id: int, data: TeamleadLinkUpdate, admin_user_id: Optional[int] = None) -> TeamleadLink:
        """
        Update commission percentages or status of a link.
        """
        link = await self.repository.get(link_id)
        if not link:
            raise NotFoundException(f"TeamleadLink {link_id} not found")

        update_data = data.model_dump(exclude_unset=True)
        if update_data:
            old_values = {k: getattr(link, k) for k in update_data.keys()}
            
            async with self.session.begin_nested():
                updated_link = await self.repository.update(link_id, update_data)
                
                await self.audit_log(
                    action="update_teamlead_link",
                    entity_type="teamlead_link",
                    entity_id=link_id,
                    user_id=admin_user_id,
                    old_values=old_values,
                    new_values=update_data,
                )
        else:
            updated_link = link
            
        return updated_link

    async def delete_link(self, link_id: int, admin_user_id: Optional[int] = None) -> None:
        """Hard-delete a teamlead link. Past reward ledger entries are kept
        for audit/history."""
        link = await self.repository.get(link_id)
        if not link:
            raise NotFoundException(f"TeamleadLink {link_id} not found")

        old_values = {
            "teamlead_id": link.teamlead_id,
            "linked_entity_type": link.linked_entity_type,
            "linked_entity_id": link.linked_entity_id,
            "fee_percent": link.fee_percent,
            "is_active": link.is_active,
        }

        async with self.session.begin_nested():
            await self.repository.delete(link_id)

            await self.audit_log(
                action="delete_teamlead_link",
                entity_type="teamlead_link",
                entity_id=link_id,
                user_id=admin_user_id,
                old_values=old_values,
            )

    async def get_teamlead_links(self, teamlead_id: int) -> List[TeamleadLink]:
        """
        Get all links for a specific teamlead.
        """
        return await self.repository.get_by_teamlead(teamlead_id)

    async def calculate_and_pay_rewards(self, order: Order) -> List[dict]:
        """
        Calculate and pay rewards to teamleads for a successful order.
        This must be called within an active session transaction (e.g., when confirming an order).

        Returns the per-teamlead reward breakdown — a list of
        ``{teamlead_id, side, fee_percent, reward_usdt}`` (JSON-serialisable) —
        which the order financial snapshot reuses verbatim (no extra queries).
        Empty list when nothing is paid.
        """
        breakdown: List[dict] = []
        if not order.amount_usdt:
            return breakdown  # Can't calculate percentage without USDT amount

        # 1. Get active links for the merchant
        merchant_links = await self.repository.get_active_by_merchant(order.merchant_id)

        # 2. Get active links for the trader (if any)
        trader_links = []
        if order.trader_id:
            trader_links = await self.repository.get_active_by_trader(order.trader_id)

        # 3. Process Merchant Teamleads
        for link in merchant_links:
            if link.fee_percent > 0:
                reward_amount = (order.amount_usdt * (link.fee_percent / Decimal("100"))).quantize(Decimal("0.0000"))
                if reward_amount > 0:
                    await self._pay_reward(link.teamlead_id, reward_amount, order.id, "Merchant Teamlead Reward")
                    breakdown.append({
                        "teamlead_id": link.teamlead_id, "side": "merchant",
                        "fee_percent": float(link.fee_percent), "reward_usdt": str(reward_amount),
                    })

        # 4. Process Trader Teamleads
        for link in trader_links:
            if link.fee_percent > 0:
                reward_amount = (order.amount_usdt * (link.fee_percent / Decimal("100"))).quantize(Decimal("0.0000"))
                if reward_amount > 0:
                    await self._pay_reward(link.teamlead_id, reward_amount, order.id, "Trader Teamlead Reward")
                    breakdown.append({
                        "teamlead_id": link.teamlead_id, "side": "trader",
                        "fee_percent": float(link.fee_percent), "reward_usdt": str(reward_amount),
                    })

        return breakdown

    async def calculate_and_pay_payout_rewards(self, payout) -> List[dict]:
        """Calculate and pay teamlead rewards for a COMPLETED payout.

        Payouts run on payout TERMINALS (no merchant entity), so only the
        TRADER-side teamlead links apply — each active link of the executing
        trader with a positive ``payout_fee_percent`` is paid ``amount_usdt × %``
        from the system balance → teamlead WORK.

        Reference ids are namespaced ``payout:{id}`` so payout rewards never
        collide with the order-id-keyed reward stats / reversal logic (which parse
        a numeric order id out of ``reference_id``). Returns the per-teamlead
        breakdown. Must run inside an active transaction (called from
        ``FinanceService.settle_payout`` — the single place payouts are settled).

        No reversal counterpart: payouts have no disputes and ``COMPLETED`` is
        terminal, so a payout reward is paid exactly once and never undone.
        """
        breakdown: List[dict] = []
        amount_usdt = Decimal(str(payout.amount_usdt or 0))
        if amount_usdt <= 0 or not payout.trader_id:
            return breakdown

        trader_links = await self.repository.get_active_by_trader(payout.trader_id)
        reference_id = f"payout:{payout.id}"
        for link in trader_links:
            pct = Decimal(str(link.payout_fee_percent or 0))
            if pct <= 0:
                continue
            reward_amount = (amount_usdt * pct / Decimal("100")).quantize(Decimal("0.0000"))
            if reward_amount > 0:
                await self._pay_payout_reward(
                    link.teamlead_id, reward_amount, reference_id, "Trader Teamlead Payout Reward"
                )
                breakdown.append({
                    "teamlead_id": link.teamlead_id, "side": "trader",
                    "fee_percent": float(pct), "reward_usdt": str(reward_amount),
                })

        return breakdown

    async def reverse_rewards(self, order: Order) -> None:
        """
        Reverse all teamlead rewards paid for an order (used when a successful
        order is refunded via dispute). Moves previously paid amounts back from
        each teamlead's WORK balance to the system balance via new ledger
        entries with a `_reversal` suffix on the reference_id.

        Must be called within an active session transaction. Idempotent and
        repeat-safe: only payouts that don't already have a matching reversal are
        reversed — so an order disputed→resolved (re-paid) →disputed again
        reverses just the live rewards, never the already-undone ones. Counts
        reversals per reference_id and consumes one per payout (oldest first), so
        repeated pay/reverse rounds that reuse the same reference_id stay balanced.
        """
        from collections import Counter

        order_id_str = str(order.id)
        stmt = select(LedgerEntry).where(
            LedgerEntry.reference_type == LedgerReferenceType.TEAMLEAD_REWARD,
            or_(
                LedgerEntry.reference_id == order_id_str,
                # autoescape: `_` is a LIKE single-char wildcard, so an
                # unescaped "5_%" would also match sibling orders 50/51/500…
                # and reverse THEIR teamlead rewards (cross-order clawback).
                LedgerEntry.reference_id.startswith(f"{order_id_str}_", autoescape=True),
            ),
        ).order_by(LedgerEntry.id)
        result = await self.session.execute(stmt)
        all_entries = list(result.scalars().all())

        # How many reversals already exist for each base reference_id. A payout is
        # only reversed if it has no unconsumed matching reversal.
        reversal_counts = Counter(
            e.reference_id[: -len("_reversal")]
            for e in all_entries
            if e.reference_id and e.reference_id.endswith("_reversal")
        )

        for entry in all_entries:
            if not entry.reference_id or entry.reference_id.endswith("_reversal"):
                continue
            if reversal_counts.get(entry.reference_id, 0) > 0:
                reversal_counts[entry.reference_id] -= 1  # already reversed once
                continue

            await self.finance_service.transfer(
                amount=entry.amount,
                currency=entry.currency,
                reference_type=LedgerReferenceType.TEAMLEAD_REWARD,
                reference_id=f"{entry.reference_id}_reversal",
                from_balance_id=entry.to_balance_id,
                to_balance_id=entry.from_balance_id,
                description=f"Reversal of Teamlead Reward for Order {order.id} (refund)",
            )

    async def recalculate_rewards(self, order: Order) -> None:
        """
        Recalculate rewards for an order (e.g., if the order amount changed during a dispute).
        This reverses previous rewards and calculates new ones.
        Must be called within an active session transaction.
        """
        # 1. Find all previous reward entries for this order
        # We need to find ALL previous rewards and reversals to calculate the net amount paid
        order_id_str = str(order.id)
        stmt = select(LedgerEntry).where(
            LedgerEntry.reference_type == LedgerReferenceType.TEAMLEAD_REWARD,
            or_(
                LedgerEntry.reference_id == order_id_str,
                # autoescape: `_` is a LIKE single-char wildcard, so an
                # unescaped "5_%" would also match sibling orders 50/51/500…
                # and reverse THEIR teamlead rewards (cross-order clawback).
                LedgerEntry.reference_id.startswith(f"{order_id_str}_", autoescape=True),
            ),
        )
        result = await self.session.execute(stmt)
        all_entries = result.scalars().all()

        # Calculate net amount paid to each teamlead
        # Positive if transferred to teamlead, negative if transferred from teamlead
        net_paid = {}
        for entry in all_entries:
            # If to_balance is teamlead (from_balance is system), it's a payout
            # If from_balance is teamlead (to_balance is system), it's a reversal
            # We need to figure out which balance belongs to the teamlead.
            # Since we know the system balance is the other side, we can just look at the amounts.
            # A simpler way: we just reverse ALL previous entries that were payouts, and reverse ALL previous reversals.
            # Actually, the simplest and most robust way is to just calculate the net balance change for each teamlead's balance id.
            pass

        # Let's use a simpler approach: 
        # Just find the original payout entries (reference_id == str(order.id))
        # and any previous recalculation entries (reference_id.startswith(f"{order.id}_recalc_"))
        # and reverse them all.
        
        previous_entries = [e for e in all_entries if not e.reference_id.endswith("_reversal")]

        # 2. Reverse previous entries
        for entry in previous_entries:
            # Reversing: transfer from Teamlead back to System
            await self.finance_service.transfer(
                amount=entry.amount,
                currency=entry.currency,
                reference_type=LedgerReferenceType.TEAMLEAD_REWARD,
                reference_id=f"{entry.reference_id}_reversal",
                from_balance_id=entry.to_balance_id,  # Was teamlead
                to_balance_id=entry.from_balance_id,  # Was system
                description=f"Reversal of Teamlead Reward for Order {order.id}"
            )

        # 3. Calculate and pay new rewards
        import time
        recalc_suffix = f"_recalc_{int(time.time())}"
        
        if not order.amount_usdt:
            return

        merchant_links = await self.repository.get_active_by_merchant(order.merchant_id)
        trader_links = []
        if order.trader_id:
            trader_links = await self.repository.get_active_by_trader(order.trader_id)

        for link in merchant_links:
            if link.fee_percent > 0:
                reward_amount = (order.amount_usdt * (link.fee_percent / Decimal("100"))).quantize(Decimal("0.0000"))
                if reward_amount > 0:
                    await self._pay_reward(link.teamlead_id, reward_amount, f"{order.id}{recalc_suffix}", "Merchant Teamlead Reward (Recalculated)")

        for link in trader_links:
            if link.fee_percent > 0:
                reward_amount = (order.amount_usdt * (link.fee_percent / Decimal("100"))).quantize(Decimal("0.0000"))
                if reward_amount > 0:
                    await self._pay_reward(link.teamlead_id, reward_amount, f"{order.id}{recalc_suffix}", "Trader Teamlead Reward (Recalculated)")

    async def _pay_reward(self, teamlead_id: int, amount: Decimal, order_id: Union[int, str], description: str) -> None:
        """
        Internal method to execute the transfer from System to Teamlead.
        """
        teamlead = await self.session.get(User, teamlead_id)
        if not teamlead:
            return

        # Get Teamlead balance
        teamlead_balance = await self.finance_service.get_or_create_user_balance(
            teamlead, BalanceType.WORK, Currency.USDT
        )

        # Get System balance
        system_balance = await self.finance_service.get_or_create_system_balance(Currency.USDT)

        # Transfer
        await self.finance_service.transfer(
            amount=amount,
            currency=Currency.USDT,
            reference_type=LedgerReferenceType.TEAMLEAD_REWARD,
            reference_id=str(order_id),
            from_balance_id=system_balance.id,
            to_balance_id=teamlead_balance.id,
            description=f"{description} for Order {order_id}"
        )

    async def _pay_payout_reward(
        self, teamlead_id: int, amount: Decimal, reference_id: str, description: str
    ) -> None:
        """Execute a payout teamlead reward transfer (System WORK → Teamlead WORK).

        Same money move as ``_pay_reward`` but takes the full namespaced
        ``reference_id`` (``payout:{id}``) and a verbatim description, keeping
        payout rewards off the order-id reference scheme.
        """
        teamlead = await self.session.get(User, teamlead_id)
        if not teamlead:
            return

        teamlead_balance = await self.finance_service.get_or_create_user_balance(
            teamlead, BalanceType.WORK, Currency.USDT
        )
        system_balance = await self.finance_service.get_or_create_system_balance(Currency.USDT)

        await self.finance_service.transfer(
            amount=amount,
            currency=Currency.USDT,
            reference_type=LedgerReferenceType.TEAMLEAD_REWARD,
            reference_id=reference_id,
            from_balance_id=system_balance.id,
            to_balance_id=teamlead_balance.id,
            description=description,
        )

    async def _get_teamlead_reward_entries(self, teamlead_id: int) -> List[LedgerEntry]:
        """Return all reward ledger entries for a teamlead (WORK, USDT).

        Includes BOTH payout direction (system -> teamlead, to_balance_id) AND
        reversal direction (teamlead -> system, from_balance_id). Reversals
        are produced by recalculate_rewards with swapped balance IDs, so a
        filter on to_balance_id alone would silently drop them and cause the
        `_reversal` logic in get_teamlead_stats / get_teamlead_links_enriched
        to never fire, overstating totals.
        """
        teamlead = await self.session.get(User, teamlead_id)
        if not teamlead:
            raise NotFoundException("Teamlead not found")

        teamlead_balance = await self.finance_service.get_or_create_user_balance(
            teamlead, BalanceType.WORK, Currency.USDT
        )

        stmt = select(LedgerEntry).where(
            LedgerEntry.reference_type == LedgerReferenceType.TEAMLEAD_REWARD,
            or_(
                LedgerEntry.to_balance_id == teamlead_balance.id,
                LedgerEntry.from_balance_id == teamlead_balance.id,
            ),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    def _parse_order_id(reference_id: str) -> Optional[int]:
        """Extract numeric order id prefix from a reward reference_id."""
        if not reference_id:
            return None
        head = reference_id.split("_", 1)[0]
        try:
            return int(head)
        except ValueError:
            return None

    async def get_teamlead_stats(self, teamlead_id: int) -> dict:
        """
        Aggregate teamlead stats: total earned USDT (net of reversals) and distinct orders count.
        """
        entries = await self._get_teamlead_reward_entries(teamlead_id)

        total_earned = Decimal("0")
        order_ids: set[int] = set()

        for entry in entries:
            # Reversal entries end with "_reversal" and effectively cancel a previous payout.
            # We count them as negative contributions to the net earned amount,
            # but we don't use them to mark additional orders.
            if entry.reference_id and entry.reference_id.endswith("_reversal"):
                total_earned -= entry.amount
            else:
                total_earned += entry.amount
                order_id = self._parse_order_id(entry.reference_id)
                if order_id is not None:
                    order_ids.add(order_id)

        active_links = await self.repository.get_by_teamlead(teamlead_id)
        active_count = sum(1 for l in active_links if l.is_active)

        return {
            "total_earned_usdt": total_earned if total_earned >= 0 else Decimal("0"),
            "orders_count": len(order_ids),
            "active_links_count": active_count,
        }

    async def get_teamlead_links_enriched(self, teamlead_id: int) -> List[dict]:
        """
        Return teamlead links enriched with linked user login and aggregated income per link.
        """
        links = await self.repository.get_by_teamlead(teamlead_id)
        if not links:
            return []

        # Resolve usernames for linked entities
        merchant_ids = [l.linked_entity_id for l in links if l.linked_entity_type == UserRole.MERCHANT]
        trader_ids = [l.linked_entity_id for l in links if l.linked_entity_type == UserRole.TRADER]

        merchant_logins: dict[int, str] = {}
        if merchant_ids:
            stmt = (
                select(Merchant.id, User.username)
                .join(User, User.id == Merchant.user_id)
                .where(Merchant.id.in_(merchant_ids))
            )
            result = await self.session.execute(stmt)
            for mid, username in result.all():
                merchant_logins[mid] = username

        trader_logins: dict[int, str] = {}
        if trader_ids:
            stmt = select(User.id, User.username).where(User.id.in_(trader_ids))
            result = await self.session.execute(stmt)
            for uid, username in result.all():
                trader_logins[uid] = username

        # Aggregate rewards per (linked_entity_type, linked_entity_id)
        entries = await self._get_teamlead_reward_entries(teamlead_id)
        order_ids: set[int] = set()
        for e in entries:
            oid = self._parse_order_id(e.reference_id)
            if oid is not None:
                order_ids.add(oid)

        orders_by_id: dict[int, Order] = {}
        if order_ids:
            stmt = select(Order).where(Order.id.in_(order_ids))
            result = await self.session.execute(stmt)
            for o in result.scalars().all():
                orders_by_id[o.id] = o

        income_by_key: dict[tuple[str, int], Decimal] = {}
        for entry in entries:
            oid = self._parse_order_id(entry.reference_id)
            order = orders_by_id.get(oid) if oid is not None else None
            if order is None:
                continue

            delta = -entry.amount if entry.reference_id.endswith("_reversal") else entry.amount

            # Reward may come from a merchant-link OR a trader-link match on the same order.
            # Attribute to matching links of this teamlead.
            for link in links:
                if link.linked_entity_type == UserRole.MERCHANT and link.linked_entity_id == order.merchant_id:
                    key = ("merchant", link.linked_entity_id)
                    income_by_key[key] = income_by_key.get(key, Decimal("0")) + delta
                elif (
                    link.linked_entity_type == UserRole.TRADER
                    and order.trader_id is not None
                    and link.linked_entity_id == order.trader_id
                ):
                    key = ("trader", link.linked_entity_id)
                    income_by_key[key] = income_by_key.get(key, Decimal("0")) + delta

        enriched: List[dict] = []
        for link in links:
            if link.linked_entity_type == UserRole.MERCHANT:
                login = merchant_logins.get(link.linked_entity_id, f"merchant#{link.linked_entity_id}")
                income = income_by_key.get(("merchant", link.linked_entity_id), Decimal("0"))
            else:
                login = trader_logins.get(link.linked_entity_id, f"trader#{link.linked_entity_id}")
                income = income_by_key.get(("trader", link.linked_entity_id), Decimal("0"))

            enriched.append({
                "id": link.id,
                "linked_entity_type": link.linked_entity_type,
                "linked_entity_id": link.linked_entity_id,
                "login": login,
                "fee_percent": link.fee_percent,
                "payout_fee_percent": link.payout_fee_percent,
                "is_active": link.is_active,
                "income_usdt": income if income >= 0 else Decimal("0"),
                "created_at": link.created_at,
            })

        return enriched


    async def get_teamlead_trader_order_finances(
        self,
        teamlead_id: int,
        *,
        skip: int = 0,
        limit: int = 50,
    ) -> List[dict]:
        links = await self.repository.get_by_teamlead(teamlead_id)
        trader_ids = {
            link.linked_entity_id
            for link in links
            if link.is_active and link.linked_entity_type == UserRole.TRADER
        }
        if not trader_ids:
            return []

        entries = await self._get_teamlead_reward_entries(teamlead_id)
        trader_reward_entries = [
            entry for entry in entries
            if entry.description and "Trader Teamlead Reward" in entry.description
        ]
        trader_reward_reference_ids = {
            entry.reference_id
            for entry in trader_reward_entries
            if entry.reference_id and not entry.reference_id.endswith("_reversal")
        }

        order_ids: set[int] = set()
        for reference_id in trader_reward_reference_ids:
            order_id = self._parse_order_id(reference_id)
            if order_id is not None:
                order_ids.add(order_id)
        if not order_ids:
            return []

        stmt = select(Order).where(
            Order.id.in_(order_ids),
            Order.status == OrderStatus.SUCCESS,
            Order.trader_id.in_(trader_ids),
        )
        orders = list((await self.session.execute(stmt)).scalars().all())
        if not orders:
            return []

        orders_by_id = {order.id: order for order in orders}
        reward_by_order: dict[int, Decimal] = {}
        for entry in entries:
            if not entry.reference_id:
                continue
            is_reversal = entry.reference_id.endswith("_reversal")
            base_reference_id = (
                entry.reference_id[: -len("_reversal")]
                if is_reversal
                else entry.reference_id
            )
            if base_reference_id not in trader_reward_reference_ids:
                continue
            order_id = self._parse_order_id(base_reference_id)
            if order_id not in orders_by_id:
                continue
            delta = -entry.amount if is_reversal else entry.amount
            reward_by_order[order_id] = reward_by_order.get(order_id, Decimal("0")) + delta

        users = list((await self.session.execute(
            select(User.id, User.username).where(User.id.in_(trader_ids))
        )).all())
        trader_login_by_id = {user_id: username for user_id, username in users}

        rows = []
        for order in orders:
            reward = reward_by_order.get(order.id, Decimal("0"))
            if reward <= 0 or order.trader_id is None:
                continue
            rows.append({
                "order_id": order.id,
                "closed_at": order.confirmed_at or order.updated_at or order.created_at,
                "amount_usdt": order.amount_usdt or Decimal("0"),
                "teamlead_profit_usdt": reward,
                "trader_id": order.trader_id,
                "trader_login": trader_login_by_id.get(order.trader_id, f"trader#{order.trader_id}"),
            })

        rows.sort(key=lambda row: row["closed_at"], reverse=True)
        return rows[skip:skip + limit]

    async def get_teamlead_reward_history(self, teamlead_id: int) -> List[LedgerEntry]:
        """
        Get the reward history for a specific teamlead.

        Returns BOTH payouts (system → teamlead) and reversals
        (teamlead → system). The latter are written when an order is
        refunded/recalculated and have a `_reversal` suffix on
        `reference_id`. Filtering them out hid the actual net history
        from the teamlead UI and broke `_reversal` detection.
        """
        teamlead = await self.session.get(User, teamlead_id)
        if not teamlead:
            raise NotFoundException("Teamlead not found")

        teamlead_balance = await self.finance_service.get_or_create_user_balance(
            teamlead, BalanceType.WORK, Currency.USDT
        )

        return await self.ledger_repository.list_by_balance_and_reference(
            balance_id=teamlead_balance.id,
            reference_type=LedgerReferenceType.TEAMLEAD_REWARD,
        )