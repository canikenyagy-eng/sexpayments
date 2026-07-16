from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import Field, model_validator

from app.common.enums.users import UserRole
from app.modules.base.schemas import BaseResponseSchema, BaseSchema


class TeamleadLinkBase(BaseSchema):
    teamlead_id: int
    linked_entity_type: UserRole
    linked_entity_id: int
    fee_percent: Decimal = Field(default=Decimal("0.00"), ge=0, le=100)
    payout_fee_percent: Decimal = Field(default=Decimal("0.00"), ge=0, le=100)
    is_active: bool = True

    @model_validator(mode="after")
    def check_linked_entity(self) -> "TeamleadLinkBase":
        if self.linked_entity_type not in [UserRole.MERCHANT, UserRole.TRADER]:
            raise ValueError("Teamlead can only be linked to a merchant or a trader")
        return self


class TeamleadLinkCreate(TeamleadLinkBase):
    pass


class TeamleadLinkUpdate(BaseSchema):
    fee_percent: Optional[Decimal] = Field(None, ge=0, le=100)
    payout_fee_percent: Optional[Decimal] = Field(None, ge=0, le=100)
    is_active: Optional[bool] = None


class TeamleadLinkResponse(BaseResponseSchema, TeamleadLinkBase):
    id: int
    created_at: datetime
    updated_at: datetime


class TeamleadRewardHistoryResponse(BaseResponseSchema):
    id: int
    amount: Decimal
    currency: str
    reference_type: str
    reference_id: str
    description: Optional[str]
    created_at: datetime


class TeamleadAdminItem(BaseResponseSchema):
    id: int
    username: str
    is_active: bool
    is_blocked: bool
    balance_usdt: float
    created_at: datetime


class TeamleadLinkEnrichedResponse(BaseResponseSchema):
    id: int
    linked_entity_type: UserRole
    linked_entity_id: int
    login: str
    fee_percent: Decimal
    payout_fee_percent: Decimal
    is_active: bool
    income_usdt: Decimal
    created_at: datetime


class TeamleadTraderOrderFinanceResponse(BaseResponseSchema):
    order_id: int
    closed_at: datetime
    amount_usdt: Decimal
    teamlead_profit_usdt: Decimal
    trader_id: int
    trader_login: str


class TeamleadStatsResponse(BaseResponseSchema):
    total_earned_usdt: Decimal
    orders_count: int
    active_links_count: int
