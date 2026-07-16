from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.users import UserRole
from app.modules.base.repository import BaseRepository
from app.modules.teamleaders.models import TeamleadLink


class TeamleadLinkRepository(BaseRepository[TeamleadLink]):
    def __init__(self, session: AsyncSession):
        super().__init__(TeamleadLink, session)

    async def get_active_by_merchant(self, merchant_id: int) -> List[TeamleadLink]:
        stmt = select(TeamleadLink).where(
            TeamleadLink.linked_entity_type == UserRole.MERCHANT,
            TeamleadLink.linked_entity_id == merchant_id,
            TeamleadLink.is_active == True
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_active_by_trader(self, trader_id: int) -> List[TeamleadLink]:
        stmt = select(TeamleadLink).where(
            TeamleadLink.linked_entity_type == UserRole.TRADER,
            TeamleadLink.linked_entity_id == trader_id,
            TeamleadLink.is_active == True
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_teamlead(self, teamlead_id: int) -> List[TeamleadLink]:
        stmt = select(TeamleadLink).where(
            TeamleadLink.teamlead_id == teamlead_id
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_teamlead_and_entity(
        self,
        teamlead_id: int,
        linked_entity_type: UserRole,
        linked_entity_id: int,
    ) -> TeamleadLink | None:
        stmt = select(TeamleadLink).where(
            TeamleadLink.teamlead_id == teamlead_id,
            TeamleadLink.linked_entity_type == linked_entity_type,
            TeamleadLink.linked_entity_id == linked_entity_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
