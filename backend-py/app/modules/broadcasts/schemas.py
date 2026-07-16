from datetime import datetime
from typing import Optional

from pydantic import Field

from app.common.enums.broadcasts import BroadcastAudience, BroadcastStatus
from app.modules.base.schemas import BaseResponseSchema, BaseSchema


class BroadcastCreateRequest(BaseSchema):
    # 4096 = Telegram's single-message text limit.
    text: str = Field(..., min_length=1, max_length=4096)
    audience: BroadcastAudience = BroadcastAudience.ALL


class BroadcastResponse(BaseResponseSchema):
    id: int
    text: str
    audience: BroadcastAudience
    status: BroadcastStatus
    total_recipients: int
    delivered: int
    failed: int
    created_at: datetime
    finished_at: Optional[datetime] = None


class RecipientCountResponse(BaseResponseSchema):
    """Live recipient counts per audience — feeds the compose-form hint/confirm."""
    all: int
    except_blocked: int
