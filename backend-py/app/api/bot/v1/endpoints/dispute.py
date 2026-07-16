"""Bot-channel endpoint for ``merchant-dispute-bot``.

The bot sits in a Telegram group/channel that an admin has bound to a merchant
(``merchants.dispute_telegram_group_id``). The merchant or their staff drops a
free-form message (containing an order uuid / external_id in any position — their
own internal id is often first) + a receipt file; the bot forwards the full text
here. We resolve the merchant by chat_id, apply the merchant's ``dispute_id_mask``
to pull OUR order id out of the text (and resolve candidates against the DB), then
feed the file into the normal receipt premoderation flow via
``OrderService.confirm_order`` (which attaches the receipt, flips the order to
RECEIPT_UPLOADED, and — when premoderation is enabled — opens a moderation row +
delivers it to support-bot).

Auth: shared ``X-Bot-Secret`` == ``MERCHANT_DISPUTE_BOT_SECRET``. The chat→merchant
binding is the trust boundary (any member of the bound chat may post).

Scope (v1): handles orders in PENDING (the normal receipt-upload case). Orders in
other states are rejected by ``confirm_order`` with 409 and the bot reports that
back in chat — re-upload of evidence on already-uploaded / disputed orders is a
follow-up that needs the confirm gate relaxed + idempotent moderation.
"""
import uuid as _uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from app.common.enums.receipts import ReceiptUploader
from app.api.bot.v1.dependencies import verify_merchant_dispute_bot_secret
from app.api.dependencies import get_service
from app.core.exceptions import NotFoundException
from app.core.logging import get_logger
from app.modules.disputes.repository import DisputeRepository
from app.modules.merchants.dispute_mask import candidate_identifiers
from app.modules.merchants.models import Merchant
from app.modules.orders.service import OrderService

logger = get_logger(__name__)

router = APIRouter()


class DisputeReceiptResponse(BaseModel):
    """Compact result the bot uses to reply in chat (✅ / status)."""

    order_uuid: str
    status: str
    moderation_status: str | None = None


def _is_uuid(value: str) -> bool:
    try:
        _uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


@router.post(
    "/receipt",
    response_model=DisputeReceiptResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_merchant_dispute_bot_secret)],
    summary="Attach a receipt posted in a merchant's dispute chat → premoderation",
)
async def dispute_receipt(
    chat_id: int = Form(..., description="Telegram chat_id the message came from"),
    attachment: UploadFile = File(..., description="Receipt file / photo (max 10MB)"),
    text: str | None = Form(
        None,
        description=(
            "Full message text the merchant posted. The merchant's "
            "dispute_id_mask is applied to pull OUR order id out of it."
        ),
    ),
    identifier: str | None = Form(
        None, description="Legacy: a single pre-extracted order UUID / external_id"
    ),
    order_service: OrderService = Depends(get_service(OrderService)),
) -> DisputeReceiptResponse:
    session = order_service.session

    # 1. Resolve the merchant(s) bound to this chat. The binding IS the auth
    #    model. A chat MAY be shared by several merchants; the unique order id in
    #    the message disambiguates which merchant it belongs to (an order maps to
    #    exactly one merchant), so we try each bound merchant's mask + scope and
    #    let the order resolution pick the owner. ``.all()`` (not
    #    ``scalar_one_or_none``) so two merchants on one chat never 500.
    merchants = (
        await session.execute(
            select(Merchant).where(Merchant.dispute_telegram_group_id == chat_id)
        )
    ).scalars().all()
    if not merchants:
        raise NotFoundException(f"No merchant bound to chat {chat_id}")

    # 2. Find the (merchant, order) the message refers to: for each bound merchant,
    #    pull ordered id candidates from the full message via THAT merchant's
    #    token-position mask (mask hit first, then the legacy single identifier,
    #    then every token) and resolve them merchant-scoped. A merchant's
    #    external_id can itself be UUID-shaped and we can't tell which key a token
    #    is, so try uuid first (when it parses) then external_id. The first token
    #    that resolves to one of a bound merchant's orders wins — a merchant's own
    #    id isn't in our system, so it's skipped automatically.
    merchant = None
    order_row = None
    for m in merchants:
        candidates = candidate_identifiers(
            text=text, identifier=identifier, mask=m.dispute_id_mask
        )
        for cand in candidates:
            if _is_uuid(cand):
                order_row = await order_service.repository.get_by_uuid_and_merchant(cand, m.id)
                if order_row is not None:
                    break
            order_row = await order_service.repository.get_by_external_id_and_merchant(cand, m.id)
            if order_row is not None:
                break
        if order_row is not None:
            merchant = m
            break
    if order_row is None:
        raise NotFoundException("No matching order found for any merchant bound to this chat")

    # 3. Attach the receipt + run premoderation. confirm_order re-resolves by the
    #    canonical uuid and raises 409 on wrong status — that propagates so the
    #    bot can report it back in chat.
    #    When the order has a dispute, link the receipt to it (appeal evidence
    #    in the unified receipts store).
    dispute = await DisputeRepository(session).get_by_order_id(order_row.id)
    order = await order_service.confirm_order(
        merchant=merchant,
        attachment=attachment,
        order_id=str(order_row.uuid),
        uploaded_by=ReceiptUploader.MERCHANT_DISPUTE_BOT,
        dispute_id=dispute.id if dispute else None,
    )

    moderation = getattr(order, "moderation_status", None)
    return DisputeReceiptResponse(
        order_uuid=str(order.uuid),
        status=order.status.value if hasattr(order.status, "value") else str(order.status),
        moderation_status=(
            moderation.value if hasattr(moderation, "value") else (moderation or None)
        ),
    )
