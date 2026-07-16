from typing import List, Optional

from sqlalchemy import cast, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.modules.base.repository import BaseRepository
from app.modules.merchants.models import Merchant
from app.modules.users.models import User


class MerchantRepository(BaseRepository[Merchant]):
    def __init__(self, session: AsyncSession):
        super().__init__(Merchant, session)

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
        search: Optional[str] = None,
        *,
        status: Optional[str] = None,
        is_active: Optional[bool] = None,
        payment_method: Optional[str] = None,
    ) -> List[Merchant]:
        stmt = select(self.model)
        if search:
            if search.isdigit():
                stmt = stmt.where(self.model.id == int(search))
            else:
                user_ids = select(User.id).where(User.username.ilike(f"%{search}%"))
                stmt = stmt.where(self.model.user_id.in_(user_ids))
        if status:
            stmt = stmt.where(self.model.status == status)
        if is_active is not None:
            from app.common.enums.merchants import TerminalStatus
            if is_active:
                stmt = stmt.where(
                    self.model.status.in_([TerminalStatus.ENABLED, TerminalStatus.TEST])
                )
            else:
                stmt = stmt.where(
                    self.model.status.notin_([TerminalStatus.ENABLED, TerminalStatus.TEST])
                )
        if payment_method:
            stmt = stmt.where(cast(self.model.fees, JSONB).has_key(payment_method))
        stmt = stmt.order_by(self.model.id.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_api_key(self, api_key: str) -> Optional[Merchant]:
        result = await self.session.execute(
            select(self.model).where(self.model.api_key == api_key)
        )
        return result.scalars().first()

    async def get_by_user_id(self, user_id: int) -> Optional[Merchant]:
        result = await self.session.execute(
            select(self.model).where(self.model.user_id == user_id)
        )
        return result.scalars().first()

    async def list_by_user_id(self, user_id: int) -> List[Merchant]:
        result = await self.session.execute(
            select(self.model)
            .where(self.model.user_id == user_id)
            .order_by(self.model.id)
        )
        return list(result.scalars().all())

    async def get_by_id_and_user(self, merchant_id: int, user_id: int) -> Optional[Merchant]:
        result = await self.session.execute(
            select(self.model).where(
                self.model.id == merchant_id,
                self.model.user_id == user_id,
            )
        )
        return result.scalars().first()

    async def update(self, id, data) -> Optional[Merchant]:
        db_obj = await self.get(id)
        if not db_obj:
            return None
        for key, value in data.items():
            setattr(db_obj, key, value)
        if "telegram_user_ids" in data:
            flag_modified(db_obj, "telegram_user_ids")
        self.session.add(db_obj)
        await self.session.flush()
        await self.session.refresh(db_obj)
        return db_obj

    async def list_by_telegram_user_id(self, telegram_user_id: int) -> List[Merchant]:
        """Return merchants whose telegram_user_ids list contains the given TG user.

        Supports both legacy (int[]) and new ({id, label, active}[]) storage formats.
        """
        col = cast(self.model.telegram_user_ids, JSONB)
        result = await self.session.execute(
            select(self.model)
            .where(
                or_(
                    col.contains([telegram_user_id]),
                    col.contains([{"id": telegram_user_id}]),
                )
            )
            .order_by(self.model.id)
        )
        return list(result.scalars().all())

    async def get_active_terminal_for_tg(
        self, telegram_user_id: int
    ) -> Optional[Merchant]:
        """Return the merchant currently marked as active for the given TG user, if any."""
        col = cast(self.model.telegram_user_ids, JSONB)
        result = await self.session.execute(
            select(self.model)
            .where(
                col.contains([{"id": telegram_user_id, "active": True}]),
            )
            .order_by(self.model.id)
            .limit(1)
        )
        return result.scalars().first()
