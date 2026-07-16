from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.base.repository import BaseRepository
from app.modules.rates.models import RateConfig


class RateConfigRepository(BaseRepository[RateConfig]):
    def __init__(self, session: AsyncSession):
        super().__init__(RateConfig, session)

    async def get_active_configs(self) -> List[RateConfig]:
        stmt = select(RateConfig).where(RateConfig.is_active == True)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
