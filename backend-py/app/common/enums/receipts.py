from enum import Enum
from typing import Tuple


class ReceiptSource(str, Enum):
    """Channel a receipt was uploaded through (stored on ``receipts.source``)."""

    MERCHANT_API = "merchant_api"    # HMAC server-to-server confirm-transfer
    MERCHANT_WEB = "merchant_web"    # merchant cabinet
    DISPUTE_BOT = "dispute_bot"      # merchant-dispute-bot (Telegram)
    SYSTEM = "system"
    TRADER = "trader"


class ReceiptUploader(str, Enum):
    """The entry channel ``ReceiptService.upload`` is called through — resolves
    to the ``(ReceiptSource, actor)`` pair stamped on the stored receipt row.

    Separate from ``ReceiptSource`` because one actor (``merchant``) reaches us
    through several channels (API / web / dispute-bot); ``actor`` is the coarse
    "who" persisted on ``receipts.uploaded_by``.
    """

    MERCHANT = "merchant"                    # merchant HMAC API / bot confirm
    MERCHANT_DISPUTE_BOT = "merchant_dispute_bot"  # merchant via dispute-bot
    MERCHANT_WEB = "merchant_web"            # merchant cabinet
    SYSTEM = "system"                        # platform / admin-initiated
    TRADER = "trader"

    @property
    def source(self) -> "ReceiptSource":
        return _UPLOADER_CHANNEL[self][0]

    @property
    def actor(self) -> str:
        return _UPLOADER_CHANNEL[self][1]


# uploader → (receipt source, coarse actor stored on receipts.uploaded_by)
_UPLOADER_CHANNEL: dict["ReceiptUploader", Tuple["ReceiptSource", str]] = {
    ReceiptUploader.MERCHANT: (ReceiptSource.MERCHANT_API, "merchant"),
    ReceiptUploader.MERCHANT_DISPUTE_BOT: (ReceiptSource.DISPUTE_BOT, "merchant"),
    ReceiptUploader.MERCHANT_WEB: (ReceiptSource.MERCHANT_WEB, "merchant"),
    ReceiptUploader.SYSTEM: (ReceiptSource.SYSTEM, "system"),
    ReceiptUploader.TRADER: (ReceiptSource.TRADER, "trader"),
}
