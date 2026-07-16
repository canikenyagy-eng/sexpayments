"""Pydantic schemas for the platform-settings admin tabs.

Self-contained — combines the small sets of platform settings each page
reads/writes into one view each.
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class AdminPlatformPremoderationSettings(BaseModel):
    """Combined view of the settings the premoderation page cares about."""

    receipt_premoderation_enabled: bool
    support_bot_chat_id: str
    # Minutes without a reaction before the support-bot re-pings the check
    # (reply to the original card). 0 disables reminders.
    premoderation_reminder_minutes: int


class AdminPlatformPremoderationSettingsUpdate(BaseModel):
    """Partial update — any field may be omitted."""

    receipt_premoderation_enabled: Optional[bool] = None
    support_bot_chat_id: Optional[str] = None
    premoderation_reminder_minutes: Optional[int] = Field(None, ge=0, le=1440)


class AdminPlatformNotificationSettings(BaseModel):
    """Combined view of the notification settings."""

    # Telegram chat_id of the support-bot group that receives platform
    # notifications (separate from the receipt-moderation group).
    notifications_chat_id: str
    # Per-event toggles. For now only "new withdrawal request" exists; add
    # more booleans here as new notification kinds are introduced.
    notify_withdrawal_requests: bool


class AdminPlatformNotificationSettingsUpdate(BaseModel):
    """Partial update — any field may be omitted."""

    notifications_chat_id: Optional[str] = None
    notify_withdrawal_requests: Optional[bool] = None


# ── Prime-Time (temporary global trader-fee boost) ────────────────────────


class PrimeTimeActivateRequest(BaseModel):
    """Admin starts a Prime-Time window: +``points`` to every trader's fee
    for ``minutes`` minutes."""

    points: Decimal = Field(..., ge=Decimal("0.01"), le=50, description="Percentage points added to trader fee (0.01% step)")
    minutes: int = Field(..., ge=1, le=43200, description="Window duration in minutes (up to 43200 = 30 days)")


class PrimeTimeResponse(BaseModel):
    """Current Prime-Time state — drives the banner."""

    active: bool
    points: Optional[float] = None
    ends_at: Optional[datetime] = None
