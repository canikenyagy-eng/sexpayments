"""Generic key/value table for platform-wide settings.

Values are stored as text; typed access (bool/int/str) is provided by
``SettingsService._coerce`` using the type declared in
``SETTING_DEFS`` in ``app/modules/settings/service.py``.

Keep this table small — it's read on hot paths (e.g. order confirmation
to decide whether to gate the receipt through support-bot premoderation).
Service-level callers are expected to cache reads per request.
"""
from sqlalchemy import Column, DateTime, Integer, String, Text

from app.common.types import utcnow
from app.infrastructure.db.base import Base


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    id = Column(Integer, primary_key=True)
    key = Column(String(128), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)
    description = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
