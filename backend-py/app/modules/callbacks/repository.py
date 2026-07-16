from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.base.repository import BaseRepository
from app.modules.callbacks.models import CallbackAttempt


class CallbackAttemptRepository(BaseRepository[CallbackAttempt]):
    def __init__(self, session: AsyncSession):
        super().__init__(CallbackAttempt, session)

    async def list_with_filters(
        self,
        skip: int = 0,
        limit: int = 50,
        *,
        order_id: Optional[int] = None,
        is_successful: Optional[bool] = None,
        sort_order: str = "desc",
    ) -> List[CallbackAttempt]:
        query = select(self.model)

        if order_id is not None:
            query = query.where(self.model.order_id == order_id)
        if is_successful is not None:
            query = query.where(self.model.is_successful.is_(is_successful))

        order_col = self.model.created_at
        query = query.order_by(order_col.desc() if sort_order == "desc" else order_col.asc())
        query = query.offset(skip).limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())
