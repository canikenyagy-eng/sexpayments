from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.broadcasts import BroadcastAudience
from app.common.enums.traders import TraderStatus
from app.modules.base.repository import BaseRepository
from app.modules.broadcasts.models import Broadcast


def _recipient_chat_ids_select(audience: BroadcastAudience):
    """``select(Trader.telegram_group_id)`` for the broadcast audience.

    Only traders with a linked Telegram chat, excluding system/cascade virtual
    traders (same exclusion as ``TraderRepository.get_traders``). For
    ``EXCEPT_BLOCKED`` also drops ``status=BLOCKED``.
    """
    from app.modules.traders.models import Trader
    from app.modules.users.models import User

    stmt = select(Trader.telegram_group_id).where(
        Trader.telegram_group_id.is_not(None),
        Trader.user_id.in_(select(User.id).where(User.is_system.is_(False))),
    )
    if audience == BroadcastAudience.EXCEPT_BLOCKED:
        stmt = stmt.where(Trader.status != TraderStatus.BLOCKED)
    return stmt


class BroadcastRepository(BaseRepository[Broadcast]):
    def __init__(self, session: AsyncSession):
        super().__init__(Broadcast, session)

    async def list_recent(self, limit: int = 50) -> List[Broadcast]:
        stmt = select(Broadcast).order_by(Broadcast.id.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def recipient_chat_ids(self, audience: BroadcastAudience) -> List[int]:
        rows = (await self.session.execute(_recipient_chat_ids_select(audience))).scalars().all()
        return [int(c) for c in rows if c is not None]

    async def recipient_counts(self) -> dict:
        return {
            "all": len(await self.recipient_chat_ids(BroadcastAudience.ALL)),
            "except_blocked": len(
                await self.recipient_chat_ids(BroadcastAudience.EXCEPT_BLOCKED)
            ),
        }
