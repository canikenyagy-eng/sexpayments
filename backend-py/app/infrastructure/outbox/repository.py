from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.outbox.models import OutboxEvent
from app.modules.base.repository import BaseRepository


class OutboxRepository(BaseRepository[OutboxEvent]):
    def __init__(self, session: AsyncSession):
        super().__init__(OutboxEvent, session)

    async def get_unprocessed_events(self, limit: int = 100) -> List[OutboxEvent]:
        result = await self.session.execute(
            select(self.model)
            .where(self.model.processed == False)
            .order_by(self.model.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())
