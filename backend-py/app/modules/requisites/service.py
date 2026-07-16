from typing import Iterable, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.orders import OrderStatus
from app.common.enums.requisites import RequisiteStatus
from app.common.phone import is_phone_method, normalize_phone
from app.common.types import utcnow
from app.core.exceptions import NotFoundException, ValidationException, ForbiddenException
from app.modules.base.service import BaseService
from app.modules.payments.models import PaymentOption
from app.modules.requisites.models import Requisite, RequisiteLimit
from app.modules.requisites.priority_service import PriorityService
from app.modules.requisites.repository import RequisiteRepository, RequisiteLimitRepository
from app.modules.requisites.schemas import RequisiteCreate, RequisiteUpdate, RequisiteLimitUpdate


_ACTIVE_ORDER_STATUSES = (
    OrderStatus.PENDING,
    OrderStatus.RECEIPT_UPLOADED,
    OrderStatus.DISPUTED,
)


class RequisiteService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.requisite_repo = RequisiteRepository(session)
        self.limit_repo = RequisiteLimitRepository(session)
        self.priority_service = PriorityService(session)

    async def _attach_active_amounts(self, requisites: Iterable[Requisite]) -> None:
        """Populate ``requisite.limits.active_amount`` with the SUM of amounts
        from orders currently in flight (PENDING / RECEIPT_UPLOADED / DISPUTED)
        for each requisite. Pydantic ``RequisiteLimitResponse`` reads this as
        a transient attribute via ``from_attributes=True``."""
        # Lazy-import Order so unit tests that don't pull the orders module
        # don't trip the SQLAlchemy mapper (Order.relationship('Merchant')).
        from app.modules.orders.models import Order

        ids: list[int] = []
        for r in requisites:
            if r.limits is not None:
                ids.append(r.id)
                # Default to 0 so the response field is always populated.
                setattr(r.limits, "active_amount", 0.0)
        if not ids:
            return

        stmt = (
            select(
                Order.requisite_id,
                func.coalesce(func.sum(Order.amount), 0).label("total"),
            )
            .where(
                Order.requisite_id.in_(ids),
                Order.status.in_(_ACTIVE_ORDER_STATUSES),
            )
            .group_by(Order.requisite_id)
        )
        rows = (await self.session.execute(stmt)).all()
        by_req: dict[int, float] = {row.requisite_id: float(row.total or 0) for row in rows}
        for r in requisites:
            if r.limits is not None and r.id in by_req:
                setattr(r.limits, "active_amount", by_req[r.id])

    async def _resolve_payment_option(
        self,
        payment_option_id: int,
        payment_method: str,
        currency: Optional[str],
    ) -> PaymentOption:
        """Validate that the payment_option exists, is active, supports the payment_method
        and matches currency. Returns the loaded PaymentOption."""
        option = await self.session.get(PaymentOption, payment_option_id)
        if not option:
            raise ValidationException(f"Payment option {payment_option_id} not found")
        if not option.is_active:
            raise ValidationException("Payment option is inactive")
        if payment_method not in (option.supported_methods or []):
            raise ValidationException(
                f"Payment method '{payment_method}' is not supported by '{option.name}'"
            )
        if currency is not None and option.currency.value != currency:
            raise ValidationException(
                f"Currency '{currency}' does not match payment option currency '{option.currency.value}'"
            )
        return option

    async def create_requisite(self, trader_id: int, data: RequisiteCreate) -> Requisite:
        """Create a new requisite for a trader.

        Admin-side activation flag (`is_active`) is not exposed to the trader
        and is always set to True at creation time so admins don't have to
        manually flip it for every new requisite.

        bank_name is auto-derived from the selected PaymentOption.name to keep
        a denormalised display value (cheap to read, no extra joins).
        """
        currency_value = data.currency.value if data.currency else None
        option = await self._resolve_payment_option(
            data.payment_option_id, data.payment_method.value, currency_value
        )

        req_data = data.model_dump(exclude={"limits", "currency"})
        if is_phone_method(data.payment_method):
            req_data["account_number"] = normalize_phone(req_data["account_number"])
        req_data["trader_id"] = trader_id
        req_data["is_active"] = True
        req_data["status"] = RequisiteStatus.DISABLED
        req_data["bank_name"] = option.name
        req_data["currency"] = option.currency

        limits_data = data.limits.model_dump() if data.limits else {}
        # Stamp last_reset_at so the periodic reset task starts the cycle
        # from creation time (otherwise the next run would zero a freshly
        # accumulated counter).
        if limits_data.get("reset_enabled"):
            limits_data["last_reset_at"] = utcnow()

        async with self.session.begin_nested():
            requisite = await self.requisite_repo.create(req_data)

            # Create limits
            limits_data["requisite_id"] = requisite.id
            await self.limit_repo.create(limits_data)
            
            await self.audit_log(
                action="create_requisite",
                entity_type="requisite",
                entity_id=requisite.id,
                user_id=trader_id,
                new_values={"requisite": req_data, "limits": limits_data},
            )

        # Off the hot path (requisite CRUD, not order creation): recompute the
        # (trader, currency, method) group so priority_score reflects the new
        # member. Same transaction as the create above (no commit in between).
        await self.priority_service.recompute_group(
            requisite.trader_id, requisite.currency, requisite.payment_method
        )
        # recompute_group mutates ORM attributes in-session without flushing;
        # flush explicitly so the new scores are durable even if the caller's
        # next session op is a bare refresh() (which does NOT autoflush and
        # would otherwise silently discard the pending change).
        await self.session.flush()

        return await self.requisite_repo.get_with_limits(requisite.id)

    async def get_requisite(self, id: int) -> Requisite:
        requisite = await self.requisite_repo.get_with_limits(id)
        if not requisite:
            raise NotFoundException(f"Requisite {id} not found")
        await self._attach_active_amounts([requisite])
        return requisite

    async def get_trader_requisite(self, id: int, trader_id: int) -> Requisite:
        requisite = await self.get_requisite(id)
        if requisite.trader_id != trader_id:
            raise ForbiddenException("You don't have access to this requisite")
        return requisite

    async def get_trader_requisites(
        self,
        trader_id: int,
        skip: int = 0,
        limit: int = 100,
        *,
        nickname: Optional[str] = None,
        bank: Optional[str] = None,
        payment_method: Optional[str] = None,
        status: Optional[str] = None,
        is_active: Optional[bool] = None,
        include_archived: bool = False,
        state: Optional[str] = None,
        ready_only: bool = False,
    ) -> Tuple[List[Requisite], int]:
        items, total = await self.requisite_repo.get_by_trader(
            trader_id,
            skip,
            limit,
            nickname=nickname,
            bank=bank,
            payment_method=payment_method,
            status=status,
            is_active=is_active,
            include_archived=include_archived,
            state=state,
            ready_only=ready_only,
        )
        await self._attach_active_amounts(items)
        return items, total

    async def get_all_requisites(
        self,
        skip: int = 0,
        limit: int = 100,
        *,
        trader_login: Optional[str] = None,
        payment_method: Optional[str] = None,
        is_active: Optional[bool] = None,
        is_enabled: Optional[bool] = None,
        bank: Optional[str] = None,
    ) -> Tuple[List[Requisite], int]:
        items, total = await self.requisite_repo.get_all_with_limits(
            skip=skip,
            limit=limit,
            trader_login=trader_login,
            payment_method=payment_method,
            is_active=is_active,
            is_enabled=is_enabled,
            bank=bank,
        )
        await self._attach_active_amounts(items)
        return items, total

    async def update_requisite(self, id: int, data: RequisiteUpdate, trader_id: Optional[int] = None, user_id: Optional[int] = None) -> Requisite:
        requisite = await self.get_requisite(id)
        
        if trader_id and requisite.trader_id != trader_id:
            raise ForbiddenException("You don't have access to this requisite")

        if trader_id is not None and requisite.is_archived:
            raise ValidationException("Archived requisite cannot be modified")
            
        update_data = data.model_dump(exclude_unset=True, exclude={"limits"})

        # status / is_active / is_archived are admin-only — strip them from
        # trader-initiated updates so a trader can't flip an admin flag (e.g.
        # re-enable an admin-BLOCKED requisite) via their own PATCH /me,
        # bypassing the BLOCKED guard on the dedicated /enable route. Traders
        # change status only through /enable and /disable. The trader endpoint
        # also binds RequisiteTraderUpdate (which omits these fields entirely),
        # so this service-level strip is defence in depth.
        if trader_id is not None:
            update_data.pop("status", None)
            update_data.pop("is_active", None)
            update_data.pop("is_archived", None)

        # Normalise a phone requisite's account_number to +7XXXXXXXXXX when it
        # changes — using the effective method (a new one from the update, else
        # the requisite's current one).
        if update_data.get("account_number"):
            eff_method = update_data.get("payment_method") or requisite.payment_method
            if is_phone_method(eff_method):
                update_data["account_number"] = normalize_phone(update_data["account_number"])

        # If payment_option_id or payment_method is changing — re-validate the pair
        # and refresh the denormalised bank_name and currency.
        new_option_id = update_data.get("payment_option_id", requisite.payment_option_id)
        new_method = (
            update_data.get("payment_method", requisite.payment_method).value
            if isinstance(update_data.get("payment_method", requisite.payment_method), object)
            and update_data.get("payment_method", requisite.payment_method) is not None
            else None
        )
        if "payment_option_id" in update_data or "payment_method" in update_data:
            if new_option_id is not None and new_method is not None:
                option = await self._resolve_payment_option(
                    new_option_id, new_method, None
                )
                update_data["bank_name"] = option.name
                update_data["currency"] = option.currency

        # If status or is_active is changing, update status_updated_at
        if "status" in update_data or "is_active" in update_data:
            update_data["status_updated_at"] = utcnow()
            
        limits_data = data.limits.model_dump(exclude_unset=True) if data.limits else None

        # Captured before any mutation so we can also recompute the OLD
        # (trader, currency, method) group below if currency and/or
        # payment_method changes. Currency can change silently via a
        # payment_option_id swap (the payment_method may not be in the
        # payload at all — see _resolve_payment_option above), so both
        # coordinates must be snapshotted, not just the method.
        _old_currency = requisite.currency
        _old_method = requisite.payment_method

        # When the reset flag flips from False → True, stamp last_reset_at to
        # "now" so the periodic task starts the cycle from this moment instead
        # of immediately wiping accumulated turnover.
        if limits_data and requisite.limits is not None:
            old_lim = requisite.limits
            if (
                limits_data.get("reset_enabled") is True
                and not getattr(old_lim, "reset_enabled", False)
            ):
                limits_data["last_reset_at"] = utcnow()

        if update_data or limits_data:
            old_values = {}
            if update_data:
                old_values.update({k: getattr(requisite, k) for k in update_data.keys()})
            if limits_data and requisite.limits:
                old_values.update({f"limit_{k}": getattr(requisite.limits, k) for k in limits_data.keys()})
                
            new_values = {}
            if update_data:
                new_values.update(update_data)
            if limits_data:
                new_values.update({f"limit_{k}": v for k, v in limits_data.items()})

            async with self.session.begin_nested():
                if update_data:
                    requisite = await self.requisite_repo.update(id, update_data)
                if limits_data and requisite.limits:
                    await self.limit_repo.update(requisite.limits.id, limits_data)
                    
                await self.audit_log(
                    action="update_requisite",
                    entity_type="requisite",
                    entity_id=id,
                    user_id=user_id,
                    old_values=old_values,
                    new_values=new_values,
                )

            # Off the hot path: recompute the affected group(s), same
            # transaction as the write above. Always recompute the CURRENT
            # (post-update) group. If either coordinate moved — currency
            # (which can change silently via a payment_option_id swap, with
            # no "payment_method" key in the payload at all) or method —
            # also recompute the OLD group using the coordinates captured
            # BEFORE the update, so its remaining members get a new split.
            await self.priority_service.recompute_group(
                requisite.trader_id, requisite.currency, requisite.payment_method
            )
            if (requisite.currency, requisite.payment_method) != (_old_currency, _old_method):
                await self.priority_service.recompute_group(
                    requisite.trader_id, _old_currency, _old_method
                )
            # See create_requisite: flush so recompute survives a bare refresh().
            await self.session.flush()

        return await self.requisite_repo.get_with_limits(id)

    async def delete_requisite(self, id: int, trader_id: Optional[int] = None, user_id: Optional[int] = None) -> None:
        requisite = await self.get_requisite(id)
        
        if trader_id and requisite.trader_id != trader_id:
            raise ForbiddenException("You don't have access to this requisite")
            
        # We use soft delete (archiving) instead of hard delete
        async with self.session.begin_nested():
            await self.requisite_repo.update(id, {
                "is_archived": True,
                "is_active": False,
                "status_updated_at": utcnow()
            })
            
            await self.audit_log(
                action="delete_requisite",
                entity_type="requisite",
                entity_id=id,
                user_id=user_id,
                old_values={"is_archived": False, "is_active": requisite.is_active},
                new_values={"is_archived": True, "is_active": False},
            )

        # Off the hot path, same transaction: the archived requisite is no
        # longer poolable (fails is_active/is_archived), so its old group's
        # remaining members must be redistributed.
        await self.priority_service.recompute_group(
            requisite.trader_id, requisite.currency, requisite.payment_method
        )
        # delete_requisite returns None with no trailing query of its own, so
        # without an explicit flush a caller whose next session op is a bare
        # refresh() (which does NOT autoflush) would silently lose this
        # recompute — refresh() expires + reloads from the DB, discarding any
        # unflushed in-memory change. Confirmed by test_delete_recomputes_group
        # failing intermittently-by-code-path before this flush was added.
        await self.session.flush()

    async def set_enabled(
        self,
        id: int,
        enabled: bool,
        *,
        trader_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> Requisite:
        """Trader-facing toggle: flip requisite status between ENABLED/DISABLED.

        Does not touch the admin-only `is_active` flag. Archived requisites
        cannot be toggled.
        """
        requisite = await self.get_requisite(id)

        if trader_id is not None and requisite.trader_id != trader_id:
            raise ForbiddenException("You don't have access to this requisite")

        if requisite.is_archived:
            raise ValidationException("Archived requisite cannot be toggled")

        if requisite.status == RequisiteStatus.BLOCKED:
            raise ForbiddenException("Requisite is blocked by administration")

        new_status = RequisiteStatus.ENABLED if enabled else RequisiteStatus.DISABLED
        if requisite.status == new_status:
            return requisite

        async with self.session.begin_nested():
            await self.requisite_repo.update(id, {
                "status": new_status,
                "status_updated_at": utcnow(),
            })
            await self.audit_log(
                action="toggle_requisite",
                entity_type="requisite",
                entity_id=id,
                user_id=user_id,
                old_values={"status": requisite.status.value},
                new_values={"status": new_status.value},
            )

        # Off the hot path, same transaction: enabling/disabling flips the
        # requisite's poolable membership in its (trader, currency, method)
        # group, so the whole group's split must be redistributed.
        await self.priority_service.recompute_group(
            requisite.trader_id, requisite.currency, requisite.payment_method
        )
        # See create_requisite: flush so recompute survives a bare refresh().
        await self.session.flush()

        return await self.requisite_repo.get_with_limits(id)