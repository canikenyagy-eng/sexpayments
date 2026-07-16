"""Trader-facing trader-profile schemas.

Backs ``GET /traders/me`` and the toggle endpoints under it. The shape
is what the trader's DashboardView renders: profile status, payin/payout
toggles and the per-method configuration card.
"""
from typing import Any, Dict, List, Optional

from pydantic import Field, model_validator

from app.common.enums.payments import PaymentMethod
from app.common.enums.traders import TraderStatus
from app.modules.base.schemas import BaseResponseSchema


class TraderMethodConfigPublic(BaseResponseSchema):
    """Per-method configuration block.

    Rendered as the "Конфигурация методов" card on the trader dashboard:
      * fee — the trader's commission rate for the method, in percent
      * min_amount / max_amount — order amount bounds for the method
      * is_active — whether the method is enabled for this trader
    """

    fee: float = Field(..., ge=0)
    min_amount: float = Field(..., ge=0)
    max_amount: float = Field(..., ge=0)
    is_active: bool = True


class TraderMeResponse(BaseResponseSchema):
    """Profile shape for ``GET /traders/me`` and its toggle endpoints.

    Carries the trader's own profile: identity, status, payin/payout
    flags and the per-method configuration. ``admin.TraderResponse`` is
    the wider shape used by admin views — it also carries the rotation
    groups and the merchant routing list.
    """

    id: int
    user_id: int
    status: TraderStatus
    is_payin_active: bool = False
    is_payout_active: bool = False
    telegram_group_id: Optional[int] = None
    accept_all_merchants: bool = False
    receipt_auto_check: bool = False
    default_receipt_check_provider_id: Optional[int] = None
    methods_config: Dict[PaymentMethod, TraderMethodConfigPublic]
    # Materialized achievements bonus (percentage points) added to every method fee.
    achievement_bonus_percent: float = 0

    @model_validator(mode="before")
    @classmethod
    def transform_methods_config(cls, data: Any) -> Any:
        """Flatten the ORM Trader into the response payload.

        Builds ``methods_config`` from the ORM ``method_configs`` rows
        and returns the trader-profile fields. Groups and merchants are
        not part of this shape.
        """
        if not hasattr(data, "method_configs"):
            return data
        methods_config = {
            mc.payment_method: TraderMethodConfigPublic(
                fee=float(mc.fee),
                min_amount=float(mc.min_amount),
                max_amount=float(mc.max_amount),
                is_active=bool(mc.is_active),
            )
            for mc in data.method_configs
        }
        if isinstance(data, dict):
            data["methods_config"] = methods_config
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
            "default_receipt_check_provider_id": getattr(
                data, "default_receipt_check_provider_id", None
            ),
            "methods_config": methods_config,
            "achievement_bonus_percent": float(getattr(data, "achievement_bonus_percent", 0) or 0),
        }
