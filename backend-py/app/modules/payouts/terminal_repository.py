from typing import List, Optional

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.base.repository import BaseRepository
from app.modules.payouts.models import PayoutTerminal, payout_terminal_traders


class PayoutTerminalRepository(BaseRepository[PayoutTerminal]):
    def __init__(self, session: AsyncSession):
        super().__init__(PayoutTerminal, session)

    async def get_by_api_key(self, api_key: str) -> Optional[PayoutTerminal]:
        res = await self.session.execute(
            select(PayoutTerminal).where(PayoutTerminal.api_key == api_key)
        )
        return res.scalars().first()

    async def list_all(self, *, owner_id: Optional[int] = None, skip: int = 0, limit: int = 100) -> List[PayoutTerminal]:
        stmt = select(PayoutTerminal)
        if owner_id is not None:
            stmt = stmt.where(PayoutTerminal.user_id == owner_id)
        stmt = stmt.order_by(PayoutTerminal.id.desc()).offset(skip).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def allowed_terminal_ids_for_trader(self, trader_id: int) -> List[int]:
        """Terminal ids whose ACL includes this trader (the trader's visible pool)."""
        res = await self.session.execute(
            select(payout_terminal_traders.c.payout_terminal_id).where(
                payout_terminal_traders.c.trader_id == trader_id
            )
        )
        return [row[0] for row in res.all()]

    async def trader_ids_for_terminal(self, terminal_id: int) -> List[int]:
        """ACL trader ids for a terminal (queried directly — robust to the
        relationship cache being stale after ``set_traders``)."""
        res = await self.session.execute(
            select(payout_terminal_traders.c.trader_id).where(
                payout_terminal_traders.c.payout_terminal_id == terminal_id
            )
        )
        return [row[0] for row in res.all()]

    async def set_traders(self, terminal_id: int, trader_ids: List[int]) -> None:
        """Replace the terminal's ACL with the given trader ids."""
        await self.session.execute(
            delete(payout_terminal_traders).where(
                payout_terminal_traders.c.payout_terminal_id == terminal_id
            )
        )
        if trader_ids:
            await self.session.execute(
                insert(payout_terminal_traders),
                [{"payout_terminal_id": terminal_id, "trader_id": tid} for tid in set(trader_ids)],
            )
