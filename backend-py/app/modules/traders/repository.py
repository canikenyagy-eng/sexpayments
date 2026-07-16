from typing import List, Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.base.repository import BaseRepository
from app.modules.traders.models import Trader, TraderGroup
from app.modules.users.models import User


class TraderRepository(BaseRepository[Trader]):
    def __init__(self, session: AsyncSession):
        super().__init__(Trader, session)

    async def get_by_user_id(self, user_id: int) -> Optional[Trader]:
        stmt = select(self.model).options(selectinload(self.model.groups), selectinload(self.model.method_configs)).where(self.model.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_traders(self, skip: int = 0, limit: int = 100, search: Optional[str] = None) -> Tuple[List[Trader], int]:
        # System users (cascade providers' virtual traders) are excluded from
        # admin trader listings — they're managed under /api/v1/cascade.
        non_system_user_ids = select(User.id).where(User.is_system.is_(False))

        query = select(self.model).options(selectinload(self.model.groups), selectinload(self.model.method_configs)).where(
            self.model.user_id.in_(non_system_user_ids)
        )
        count_query = select(func.count()).select_from(self.model).where(
            self.model.user_id.in_(non_system_user_ids)
        )

        if search:
            if search.isdigit():
                query = query.where(self.model.id == int(search))
                count_query = count_query.where(self.model.id == int(search))
            else:
                user_ids = select(User.id).where(
                    User.username.ilike(f"%{search}%"),
                    User.is_system.is_(False),
                )
                query = query.where(self.model.user_id.in_(user_ids))
                count_query = count_query.where(self.model.user_id.in_(user_ids))

        query = query.order_by(self.model.id.desc()).offset(skip).limit(limit)

        result = await self.session.execute(query)
        count_result = await self.session.execute(count_query)

        return list(result.scalars().all()), count_result.scalar_one()

    async def get(self, id: int) -> Optional[Trader]:
        stmt = select(self.model).options(selectinload(self.model.groups), selectinload(self.model.method_configs)).where(self.model.id == id)
        result = await self.session.execute(stmt)
        return result.scalars().first()


class TraderGroupRepository(BaseRepository[TraderGroup]):
    def __init__(self, session: AsyncSession):
        super().__init__(TraderGroup, session)

    async def get_by_name(self, name: str) -> Optional[TraderGroup]:
        stmt = select(self.model).options(selectinload(self.model.traders), selectinload(self.model.merchants)).where(self.model.name == name)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get(self, id: int) -> Optional[TraderGroup]:
        stmt = select(self.model).options(selectinload(self.model.traders), selectinload(self.model.merchants)).where(self.model.id == id)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_all(self) -> List[TraderGroup]:
        stmt = select(self.model).options(selectinload(self.model.traders), selectinload(self.model.merchants))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
