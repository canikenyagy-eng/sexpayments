from typing import List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, ValidationException
from app.modules.base.service import BaseService
from app.modules.requisites.priority_service import PriorityService
from app.modules.traders.models import Trader, TraderGroup
from app.modules.receipt_checks.repository import ReceiptCheckProviderRepository
from app.modules.traders.repository import TraderRepository, TraderGroupRepository
from app.modules.traders.schemas import (
    TraderGroupCreate,
    TraderGroupUpdate,
    TraderUpdateAdmin,
    TraderToggleRequest,
)
from app.modules.merchants.repository import MerchantRepository

# TODO cache trader groups


class TraderService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.trader_repo = TraderRepository(session)
        self.group_repo = TraderGroupRepository(session)
        self.merchant_repo = MerchantRepository(session)
        self.priority_service = PriorityService(session)

    async def get_or_create_trader(self, user_id: int) -> Trader:
        trader = await self.trader_repo.get_by_user_id(user_id)
        if not trader:
            async with self.session.begin_nested():
                trader = await self.trader_repo.create({"user_id": user_id})
        return trader

    async def get_trader_by_id(self, trader_id: int) -> Trader:
        trader = await self.trader_repo.get(trader_id)
        if not trader:
            raise NotFoundException(f"Trader {trader_id} not found")
        return trader

    async def get_traders(self, skip: int = 0, limit: int = 100, search: Optional[str] = None) -> Tuple[List[Trader], int]:
        return await self.trader_repo.get_traders(skip=skip, limit=limit, search=search)

    async def toggle_payin(self, user_id: int, active: bool) -> Trader:
        from app.common.enums.traders import TraderStatus
        trader = await self.get_or_create_trader(user_id)
        if trader.status == TraderStatus.BLOCKED and active:
            raise ValidationException("Trader is blocked and cannot enable payin")
        old_values = {"is_payin_active": trader.is_payin_active}
        async with self.session.begin_nested():
            trader = await self.trader_repo.update(trader.id, {"is_payin_active": active})
            await self.audit_log(
                action="toggle_payin",
                entity_type="trader",
                entity_id=trader.id,
                user_id=user_id,
                old_values=old_values,
                new_values={"is_payin_active": active},
            )
        return trader

    async def toggle_payout(self, user_id: int, active: bool) -> Trader:
        from app.common.enums.traders import TraderStatus
        trader = await self.get_or_create_trader(user_id)
        if trader.status == TraderStatus.BLOCKED and active:
            raise ValidationException("Trader is blocked and cannot enable payout")
        old_values = {"is_payout_active": trader.is_payout_active}
        async with self.session.begin_nested():
            trader = await self.trader_repo.update(trader.id, {"is_payout_active": active})
            await self.audit_log(
                action="toggle_payout",
                entity_type="trader",
                entity_id=trader.id,
                user_id=user_id,
                old_values=old_values,
                new_values={"is_payout_active": active},
            )
        return trader

    async def toggle_receipt_auto_check(self, user_id: int, enabled: bool) -> Trader:
        """Per-trader switch for automatic receipt verification.

        We don't pre-validate that a provider exists or that the trader has
        balance — the toggle only sets intent. The Celery auto-check task
        silently skips when no provider is active, and surfaces an error in
        the trader UI when balance runs out.
        """
        trader = await self.get_or_create_trader(user_id)
        old_values = {"receipt_auto_check": trader.receipt_auto_check}
        async with self.session.begin_nested():
            trader = await self.trader_repo.update(
                trader.id, {"receipt_auto_check": enabled}
            )
            await self.audit_log(
                action="toggle_receipt_auto_check",
                entity_type="trader",
                entity_id=trader.id,
                user_id=user_id,
                old_values=old_values,
                new_values={"receipt_auto_check": enabled},
            )
        return trader

    async def set_default_receipt_provider(
        self, user_id: int, provider_id: Optional[int]
    ) -> Trader:
        """Set (or clear with ``None``) the trader's default receipt-check
        provider.

        A non-null ``provider_id`` must reference an ACTIVE provider — traders
        may only default to providers the admin marked active (mirrors the
        selection guard in ``ReceiptCheckService.resolve_provider_for_trader``).
        """
        trader = await self.get_or_create_trader(user_id)
        if provider_id is not None:
            provider = await ReceiptCheckProviderRepository(self.session).get(provider_id)
            if provider is None or not provider.is_active:
                raise ValidationException(
                    "Selected receipt-check provider is not available"
                )
        old_values = {
            "default_receipt_check_provider_id": trader.default_receipt_check_provider_id
        }
        async with self.session.begin_nested():
            trader = await self.trader_repo.update(
                trader.id, {"default_receipt_check_provider_id": provider_id}
            )
            await self.audit_log(
                action="set_default_receipt_provider",
                entity_type="trader",
                entity_id=trader.id,
                user_id=user_id,
                old_values=old_values,
                new_values={"default_receipt_check_provider_id": provider_id},
            )
        return trader

    async def update_trader_admin(self, trader_id: int, data: TraderUpdateAdmin, admin_user_id: Optional[int] = None) -> Trader:
        trader = await self.get_trader_by_id(trader_id)

        update_data = data.model_dump(exclude_unset=True)
        methods_config = update_data.pop("methods_config", None)
        merchant_ids = update_data.pop("merchant_ids", None)
        group_ids = update_data.pop("group_ids", None)

        any_change = bool(update_data) or methods_config is not None \
            or merchant_ids is not None or group_ids is not None

        if any_change:
            old_values = {}
            if update_data:
                old_values.update({k: getattr(trader, k) for k in update_data.keys()})
            if methods_config is not None:
                old_values["methods_config"] = {
                    mc.payment_method.value: {
                        "fee": float(mc.fee),
                        "min_amount": float(mc.min_amount),
                        "max_amount": float(mc.max_amount)
                    } for mc in trader.method_configs
                }
            if merchant_ids is not None:
                old_values["merchant_ids"] = sorted([m.id for m in (trader.merchants or [])])
            if group_ids is not None:
                old_values["group_ids"] = sorted([g.id for g in (trader.groups or [])])

            async with self.session.begin_nested():
                if update_data:
                    trader = await self.trader_repo.update(trader.id, update_data)

                if methods_config is not None:
                    from sqlalchemy import delete
                    from app.modules.traders.models import TraderMethodConfig

                    # Remove existing configs
                    await self.session.execute(
                        delete(TraderMethodConfig).where(TraderMethodConfig.trader_id == trader.id)
                    )

                    # Add new configs
                    for method, config in methods_config.items():
                        new_config = TraderMethodConfig(
                            trader_id=trader.id,
                            payment_method=method,
                            fee=config["fee"],
                            min_amount=config["min_amount"],
                            max_amount=config["max_amount"],
                            is_active=bool(config.get("is_active", True)),
                        )
                        self.session.add(new_config)

                    await self.session.flush()

                if merchant_ids is not None:
                    from app.modules.merchants.models import Merchant
                    from sqlalchemy import select
                    if merchant_ids:
                        result = await self.session.execute(
                            select(Merchant).where(Merchant.id.in_(merchant_ids))
                        )
                        new_merchants = list(result.scalars().all())
                    else:
                        new_merchants = []
                    trader.merchants = new_merchants
                    await self.session.flush()

                if group_ids is not None:
                    if group_ids:
                        from sqlalchemy import select
                        result = await self.session.execute(
                            select(TraderGroup).where(TraderGroup.id.in_(group_ids))
                        )
                        new_groups = list(result.scalars().all())
                    else:
                        new_groups = []
                    trader.groups = new_groups
                    await self.session.flush()

                await self.session.refresh(trader, ["method_configs", "merchants", "groups"])

                new_values = update_data.copy()
                if methods_config is not None:
                    new_values["methods_config"] = {
                        k.value if hasattr(k, 'value') else k: v
                        for k, v in methods_config.items()
                    }
                if merchant_ids is not None:
                    new_values["merchant_ids"] = sorted([m.id for m in (trader.merchants or [])])
                if group_ids is not None:
                    new_values["group_ids"] = sorted([g.id for g in (trader.groups or [])])

                await self.audit_log(
                    action="update_trader",
                    entity_type="trader",
                    entity_id=trader.id,
                    user_id=admin_user_id,
                    old_values=old_values,
                    new_values=new_values,
                )

            if "priority_bonus_percent" in update_data:
                # Off the hot path (admin action, not order creation): recompute
                # every poolable (currency, method) group for this trader so
                # priority_score reflects the new bonus. Same transaction as the
                # update above (no commit in between). recompute_all_for_trader
                # takes the user id — Requisite.trader_id is a FK to users.id.
                await self.priority_service.recompute_all_for_trader(trader.user_id)
                # recompute mutates ORM attributes in-session without flushing;
                # flush explicitly so the new scores are durable even if the
                # caller's next session op is a bare refresh() (which does NOT
                # autoflush and would otherwise silently discard the change).
                await self.session.flush()

        return trader

    # Group Management
    async def create_group(self, data: TraderGroupCreate, admin_user_id: Optional[int] = None) -> TraderGroup:
        existing = await self.group_repo.get_by_name(data.name)
        if existing:
            raise ValidationException(f"Group with name {data.name} already exists")
            
        async with self.session.begin_nested():
            group = await self.group_repo.create(data.model_dump())
            
            await self.audit_log(
                action="create_trader_group",
                entity_type="trader_group",
                entity_id=group.id,
                user_id=admin_user_id,
                new_values=data.model_dump(),
            )
            
        return group

    async def get_groups(self) -> List[TraderGroup]:
        return await self.group_repo.get_all()

    async def get_group(self, group_id: int) -> TraderGroup:
        group = await self.group_repo.get(group_id)
        if not group:
            raise NotFoundException(f"Group {group_id} not found")
        return group

    async def update_group(self, group_id: int, data: TraderGroupUpdate, admin_user_id: Optional[int] = None) -> TraderGroup:
        group = await self.get_group(group_id)

        if data.name and data.name != group.name:
            existing = await self.group_repo.get_by_name(data.name)
            if existing:
                raise ValidationException(f"Group with name {data.name} already exists")

        update_data = data.model_dump(exclude_unset=True)
        trader_ids = update_data.pop("trader_ids", None)
        merchant_ids = update_data.pop("merchant_ids", None)

        if not update_data and trader_ids is None and merchant_ids is None:
            return group

        old_values = {k: getattr(group, k) for k in update_data.keys()}
        if trader_ids is not None:
            old_values["trader_ids"] = sorted([t.id for t in (group.traders or [])])
        if merchant_ids is not None:
            old_values["merchant_ids"] = sorted([m.id for m in (group.merchants or [])])

        async with self.session.begin_nested():
            if update_data:
                group = await self.group_repo.update(group_id, update_data)

            if trader_ids is not None:
                from sqlalchemy import select
                from app.modules.traders.models import Trader
                if trader_ids:
                    result = await self.session.execute(
                        select(Trader).where(Trader.id.in_(trader_ids))
                    )
                    new_traders = list(result.scalars().all())
                else:
                    new_traders = []
                group.traders = new_traders
                await self.session.flush()

            if merchant_ids is not None:
                from sqlalchemy import select
                from app.modules.merchants.models import Merchant
                if merchant_ids:
                    result = await self.session.execute(
                        select(Merchant).where(Merchant.id.in_(merchant_ids))
                    )
                    new_merchants = list(result.scalars().all())
                else:
                    new_merchants = []
                group.merchants = new_merchants
                await self.session.flush()

            await self.session.refresh(group, ["traders", "merchants"])

            new_values = dict(update_data)
            if trader_ids is not None:
                new_values["trader_ids"] = sorted([t.id for t in (group.traders or [])])
            if merchant_ids is not None:
                new_values["merchant_ids"] = sorted([m.id for m in (group.merchants or [])])

            await self.audit_log(
                action="update_trader_group",
                entity_type="trader_group",
                entity_id=group.id,
                user_id=admin_user_id,
                old_values=old_values,
                new_values=new_values,
            )

        return group

    async def add_trader_to_group(self, group_id: int, trader_id: int, admin_user_id: Optional[int] = None) -> TraderGroup:
        group = await self.get_group(group_id)
        trader = await self.get_trader_by_id(trader_id)
        
        if any(t.id == trader.id for t in group.traders):
            raise ValidationException("Trader is already in this group")
            
        async with self.session.begin_nested():
            group.traders.append(trader)
            await self.session.flush()
            
            await self.audit_log(
                action="add_trader_to_group",
                entity_type="trader_group",
                entity_id=group.id,
                user_id=admin_user_id,
                new_values={"trader_id": trader.id},
            )
            
        return group

    async def add_merchant_to_group(self, group_id: int, merchant_id: int, admin_user_id: Optional[int] = None) -> TraderGroup:
        group = await self.get_group(group_id)
        merchant = await self.merchant_repo.get(merchant_id)
        if not merchant:
            raise NotFoundException(f"Merchant {merchant_id} not found")

        if any(m.id == merchant.id for m in group.merchants):
            raise ValidationException("Merchant is already in this group")

        async with self.session.begin_nested():
            group.merchants.append(merchant)
            await self.session.flush()

            await self.audit_log(
                action="add_merchant_to_group",
                entity_type="trader_group",
                entity_id=group.id,
                user_id=admin_user_id,
                new_values={"merchant_id": merchant.id},
            )

        return group

    async def remove_trader_from_group(self, group_id: int, trader_id: int, admin_user_id: Optional[int] = None) -> TraderGroup:
        group = await self.get_group(group_id)
        trader = await self.get_trader_by_id(trader_id)

        if not any(t.id == trader.id for t in group.traders):
            raise NotFoundException("Trader is not in this group")

        async with self.session.begin_nested():
            group.traders = [t for t in group.traders if t.id != trader.id]
            await self.session.flush()

            await self.audit_log(
                action="remove_trader_from_group",
                entity_type="trader_group",
                entity_id=group.id,
                user_id=admin_user_id,
                old_values={"trader_id": trader.id},
            )

        return group

    async def remove_merchant_from_group(self, group_id: int, merchant_id: int, admin_user_id: Optional[int] = None) -> TraderGroup:
        group = await self.get_group(group_id)
        merchant = await self.merchant_repo.get(merchant_id)
        if not merchant:
            raise NotFoundException(f"Merchant {merchant_id} not found")

        if not any(m.id == merchant.id for m in group.merchants):
            raise NotFoundException("Merchant is not in this group")

        async with self.session.begin_nested():
            group.merchants = [m for m in group.merchants if m.id != merchant.id]
            await self.session.flush()

            await self.audit_log(
                action="remove_merchant_from_group",
                entity_type="trader_group",
                entity_id=group.id,
                user_id=admin_user_id,
                old_values={"merchant_id": merchant.id},
            )

        return group

    async def delete_group(self, group_id: int, admin_user_id: Optional[int] = None) -> None:
        group = await self.get_group(group_id)
        snapshot = {
            "name": group.name,
            "trader_ids": sorted([t.id for t in (group.traders or [])]),
            "merchant_ids": sorted([m.id for m in (group.merchants or [])]),
        }

        async with self.session.begin_nested():
            group.traders = []
            group.merchants = []
            await self.session.flush()
            await self.session.delete(group)
            await self.session.flush()

            await self.audit_log(
                action="delete_trader_group",
                entity_type="trader_group",
                entity_id=group_id,
                user_id=admin_user_id,
                old_values=snapshot,
            )
