from typing import List
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.base.service import BaseService
from app.modules.payments.models import PaymentOption
from app.modules.payments.repository import PaymentOptionRepository

# TODO add get active methods for @router.get "/methods", ??


class PaymentOptionService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.repository = PaymentOptionRepository(session)

    async def get_active_options(self) -> List[PaymentOption]:
        return await self.repository.get_active_options()
