from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.balances import BalanceType
from app.common.enums.finances import Currency
from app.common.enums.users import UserRole
from app.modules.base.repository import BaseRepository
from app.modules.finance.models import Balance
from app.modules.users.models import User


class UserRepository(BaseRepository[User]):
    def __init__(self, session: AsyncSession):
        super().__init__(User, session)

    async def get_by_username(self, username: str) -> Optional[User]:
        result = await self.session.execute(
            select(self.model).where(self.model.username == username)
        )
        return result.scalars().first()

    async def get_users(
        self,
        skip: int = 0,
        limit: int = 100,
        role: Optional[UserRole] = None,
        is_blocked: Optional[bool] = None,
        is_active: Optional[bool] = None,
        balance_from: Optional[float] = None,
        balance_to: Optional[float] = None,
        search: Optional[str] = None,
    ) -> Tuple[List[User], int]:
        query = select(self.model)
        count_query = select(func.count()).select_from(self.model)

        if role is not None:
            query = query.where(self.model.role == role)
            count_query = count_query.where(self.model.role == role)

        if is_blocked is not None:
            query = query.where(self.model.is_blocked == is_blocked)
            count_query = count_query.where(self.model.is_blocked == is_blocked)

        if is_active is not None:
            query = query.where(self.model.is_blocked == (not is_active))
            count_query = count_query.where(self.model.is_blocked == (not is_active))

        if search:
            if search.isdigit():
                query = query.where(self.model.id == int(search))
                count_query = count_query.where(self.model.id == int(search))
            else:
                query = query.where(self.model.username.ilike(f"%{search}%"))
                count_query = count_query.where(self.model.username.ilike(f"%{search}%"))

        if balance_from is not None or balance_to is not None:
            balance_subq = (
                select(
                    Balance.user_id,
                    func.coalesce(func.sum(Balance.amount), 0).label("total"),
                )
                .where(
                    Balance.user_id.isnot(None),
                    Balance.currency == Currency.USDT,
                    Balance.type == BalanceType.WORK,
                )
                .group_by(Balance.user_id)
                .subquery()
            )
            # Use OUTER JOIN so that users without a USDT WORK balance record
            # (implicit balance = 0) are still considered and correctly filtered
            # via COALESCE(total, 0).
            query = query.outerjoin(balance_subq, balance_subq.c.user_id == self.model.id)
            count_query = count_query.outerjoin(
                balance_subq, balance_subq.c.user_id == self.model.id
            )
            balance_expr = func.coalesce(balance_subq.c.total, 0)
            if balance_from is not None:
                query = query.where(balance_expr >= balance_from)
                count_query = count_query.where(balance_expr >= balance_from)
            if balance_to is not None:
                query = query.where(balance_expr <= balance_to)
                count_query = count_query.where(balance_expr <= balance_to)

        query = query.offset(skip).limit(limit).order_by(self.model.id.desc())

        result = await self.session.execute(query)
        count_result = await self.session.execute(count_query)

        return list(result.scalars().all()), count_result.scalar_one()

    async def get_usdt_balances_by_user_ids(
        self, user_ids: List[int]
    ) -> Dict[int, float]:
        if not user_ids:
            return {}
        result = await self.session.execute(
            select(
                Balance.user_id,
                func.coalesce(func.sum(Balance.amount), 0),
            )
            .where(
                Balance.user_id.in_(user_ids),
                Balance.currency == Currency.USDT,
                Balance.type == BalanceType.WORK,
            )
            .group_by(Balance.user_id)
        )
        return {row[0]: float(row[1]) for row in result.all()}
