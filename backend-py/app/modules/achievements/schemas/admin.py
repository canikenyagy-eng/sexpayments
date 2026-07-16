"""Admin-facing achievements settings schemas.

Mirror the doliv / premoderation typed-settings pattern: the achievements config
lives in ``platform_settings`` (``achievements_enabled`` / ``achievement_bonus_max_percent``
/ ``achievement_rules``), surfaced/edited through one typed admin endpoint."""
from typing import Any, List, Optional

from app.modules.base.schemas import BaseResponseSchema, BaseSchema


class AdminAchievementSettings(BaseResponseSchema):
    enabled: bool
    bonus_max_percent: float
    rules: List[Any]


class AdminAchievementSettingsUpdate(BaseSchema):
    enabled: Optional[bool] = None
    bonus_max_percent: Optional[float] = None
    rules: Optional[List[Any]] = None
