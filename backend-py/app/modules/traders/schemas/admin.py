from decimal import Decimal
from typing import List, Optional, Dict, Any
from pydantic import Field, model_validator

from app.common.enums.traders import TraderStatus
from app.common.enums.payments import PaymentMethod
from app.modules.base.schemas import BaseSchema, BaseResponseSchema


class TraderGroupBase(BaseSchema):
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None


class TraderGroupCreate(TraderGroupBase):
    pass


class TraderGroupUpdate(BaseSchema):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = None
    trader_ids: Optional[List[int]] = None
    merchant_ids: Optional[List[int]] = None


class TraderGroupBriefResponse(BaseResponseSchema, TraderGroupBase):
    id: int


class TraderBriefForGroup(BaseResponseSchema):
    id: int
    user_id: int


class MerchantBriefForGroup(BaseResponseSchema):
    id: int
    name: Optional[str] = None


class TraderGroupResponse(BaseResponseSchema, TraderGroupBase):
    id: int
    traders: List[TraderBriefForGroup] = Field(default_factory=list)
    merchants: List[MerchantBriefForGroup] = Field(default_factory=list)


class TraderMethodConfig(BaseSchema):
    fee: float = Field(..., ge=0)
    min_amount: float = Field(..., ge=0)
    max_amount: float = Field(..., ge=0)
    is_active: bool = True


class TraderBase(BaseSchema):
    is_payin_active: bool = False
    is_payout_active: bool = False


class TraderToggleRequest(BaseSchema):
    is_active: bool = Field(..., description="Set to true to enable, false to disable")


class TraderUpdateAdmin(BaseSchema):
    status: Optional[TraderStatus] = None
    is_payin_active: Optional[bool] = None
    is_payout_active: Optional[bool] = None
    telegram_group_id: Optional[int] = None
    methods_config: Optional[Dict[PaymentMethod, TraderMethodConfig]] = None
    merchant_ids: Optional[List[int]] = None
    group_ids: Optional[List[int]] = None
    accept_all_merchants: Optional[bool] = None
    priority_bonus_percent: Optional[Decimal] = None


class TraderMerchantBrief(BaseResponseSchema):
    id: int
    name: Optional[str] = None


class TraderResponse(BaseResponseSchema, TraderBase):
    id: int
    user_id: int
    status: TraderStatus
    telegram_group_id: Optional[int] = None
    accept_all_merchants: bool = False
    receipt_auto_check: bool = False
    priority_bonus_percent: Decimal = Decimal("0")
    methods_config: Dict[PaymentMethod, TraderMethodConfig]
    groups: List[TraderGroupBriefResponse]
    merchants: List[TraderMerchantBrief] = Field(default_factory=list)

    @model_validator(mode='before')
    @classmethod
    def transform_methods_config(cls, data: Any) -> Any:
        if hasattr(data, 'method_configs'):
            methods_config = {
                mc.payment_method: TraderMethodConfig(
                    fee=float(mc.fee),
                    min_amount=float(mc.min_amount),
                    max_amount=float(mc.max_amount),
                    is_active=bool(mc.is_active),
                ) for mc in data.method_configs
            }
            if isinstance(data, dict):
                data['methods_config'] = methods_config
                return data
            return {
                "id": data.id,
                "user_id": data.user_id,
                "status": data.status,
                "is_payin_active": data.is_payin_active,
                "is_payout_active": data.is_payout_active,
                "telegram_group_id": data.telegram_group_id,
                "accept_all_merchants": getattr(data, "accept_all_merchants", False),
                "receipt_auto_check": getattr(data, "receipt_auto_check", False),
                "priority_bonus_percent": data.priority_bonus_percent,
                "methods_config": methods_config,
                "groups": data.groups,
                "merchants": [
                    {"id": m.id, "name": getattr(m, "name", None)}
                    for m in (getattr(data, "merchants", None) or [])
                ],
            }
        return data


class TraderReceiptAutoCheckRequest(BaseSchema):
    enabled: bool = Field(..., description="Включить или выключить автоматическую проверку чеков для этого трейдера")


class TraderDefaultProviderRequest(BaseSchema):
    provider_id: Optional[int] = Field(
        None,
        description="ID активного провайдера проверки чеков по умолчанию; null — сбросить",
    )
