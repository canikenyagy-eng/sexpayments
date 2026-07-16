from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.base.repository import BaseRepository
from app.modules.settings.models import PlatformSetting


class PlatformSettingRepository(BaseRepository[PlatformSetting]):
    def __init__(self, session: AsyncSession):
        super().__init__(PlatformSetting, session)

    async def get_by_key(self, key: str) -> Optional[PlatformSetting]:
        result = await self.session.execute(
            select(PlatformSetting).where(PlatformSetting.key == key)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> List[PlatformSetting]:
        result = await self.session.execute(
            select(PlatformSetting).order_by(PlatformSetting.key.asc())
        )
        return list(result.scalars().all())

    async def upsert(self, key: str, value: Optional[str], description: Optional[str]) -> PlatformSetting:
        existing = await self.get_by_key(key)
        if existing is None:
            row = PlatformSetting(key=key, value=value, description=description)
            self.session.add(row)
            await self.session.flush()
            await self.session.refresh(row)
            return row

        existing.value = value
        if description is not None:
            existing.description = description
        self.session.add(existing)
        await self.session.flush()
        await self.session.refresh(existing)
        return existing
