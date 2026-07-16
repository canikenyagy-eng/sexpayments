from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.modules.base.repository import BaseRepository
from app.modules.payments.models import PaymentOption


class PaymentOptionRepository(BaseRepository[PaymentOption]):
    def __init__(self, session: AsyncSession):
        super().__init__(PaymentOption, session)

    async def get_active_options(self) -> List[PaymentOption]:
        stmt = select(PaymentOption).where(PaymentOption.is_active == True)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
