"""Receipt-moderation policy — the configurable answer to *whether* an uploaded
receipt must be reviewed before reaching the trader, and *where* a human review
would be delivered (per-merchant override → platform default → support-chat
configured).
"""
from dataclasses import dataclass

from app.modules.base.service import BaseService


@dataclass
class ModerationResolution:
    """Effective premoderation decision for one upload.

    ``review_required`` is the EFFECTIVE flag: the configured premoderation flag
    AND a usable support chat. When the flag is on but no chat is configured we
    fall back to "no review" (and the caller logs it) so a misconfiguration
    never strands the receipt.
    """
    review_required: bool
    support_chat_id: int = 0
    misconfigured: bool = False   # flag on but chat missing → fell back to off


class ReceiptModerationPolicy(BaseService):
    async def is_enabled_for(self, merchant) -> bool:
        """Receipt-premoderation flag: per-merchant override wins over the
        platform-wide default (NULL override → global)."""
        override = getattr(merchant, "receipt_premoderation_enabled", None)
        if override is not None:
            return bool(override)
        from app.modules.settings.service import SettingsService

        return await SettingsService(self.session).get_bool("receipt_premoderation_enabled")

    async def resolve(self, order, merchant) -> ModerationResolution:
        if not await self.is_enabled_for(merchant):
            return ModerationResolution(review_required=False)

        from app.modules.settings.service import SettingsService

        raw_chat_id = await SettingsService(self.session).get_str("support_bot_chat_id")
        try:
            chat_id = int(raw_chat_id) if raw_chat_id else 0
        except ValueError:
            chat_id = 0

        if chat_id == 0:
            return ModerationResolution(review_required=False, misconfigured=True)
        return ModerationResolution(review_required=True, support_chat_id=chat_id)
