"""Callback handlers for the moderation inline keyboard.

Two registered handlers:
  * ``mod:<uuid>:<decision>`` — actual decision click
  * ``mod_noop``               — taps after the cycle is closed (the
                                 keyboard rewriter swaps every callback
                                 to this sentinel)

The decision handler posts to the backend, then edits the inline keyboard
to show the resolved state. All backend errors collapse into one of three
user-facing branches:
  * 400 + body contains "conflict" → "Уже обработано", refresh keyboard
  * 404                            → "Ордер не найден", drop keyboard
  * anything else                  → "Ошибка связи с бэкендом",
                                     keyboard left as-is so the admin can retry
"""
import logging
from datetime import datetime, timezone
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.text_decorations import html_decoration

from bot.keyboards.moderation import (
    CALLBACK_PREFIX,
    LABELS,
    NOOP_CALLBACK,
    build_resolved_keyboard,
    parse_callback,
)
from bot.services.backend_client import BackendClient, BackendError

logger = logging.getLogger(__name__)


# Bot-side short token → backend ModerationDecision value.
DECISION_MAP: dict[str, str] = {
    "accept": "accept",
    "pdf": "request_pdf",
    "video": "request_video",
}


def _format_elapsed(seconds: int) -> str:
    """``mm:ss`` formatter with a hard cap at ``99:00``.

    Used to show how long the receipt sat in the support chat before an
    admin clicked a decision. ``99:00`` is the explicit ceiling — anything
    older renders identically so the badge stays a stable width.
    """
    if seconds <= 0:
        return "00:00"
    if seconds >= 99 * 60:
        return "99:00"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _elapsed_since_message(callback: CallbackQuery) -> str:
    """Time from when the bot posted the card in chat to the admin click.

    ``callback.message.date`` is the Telegram-side timestamp of the bot's
    card — exactly the moment the receipt became visible to admins. Falls
    back to ``00:00`` if the underlying message is missing (very old
    Telegram clients).
    """
    msg = callback.message
    if msg is None or msg.date is None:
        return "00:00"
    posted = msg.date
    if posted.tzinfo is None:  # defensive — aiogram returns UTC-aware
        posted = posted.replace(tzinfo=timezone.utc)
    elapsed = int((datetime.now(timezone.utc) - posted).total_seconds())
    return _format_elapsed(elapsed)


def _format_moderator(callback: CallbackQuery) -> str:
    """Render an HTML snippet identifying the admin who clicked."""
    user = callback.from_user
    if user is None:
        return "—"
    if user.username:
        who = f"@{escape(user.username)}"
    elif user.full_name:
        who = escape(user.full_name)
    else:
        who = "—"
    return f"{who} (<code>{user.id}</code>)"


def _decision_footer(short_decision: str, callback: CallbackQuery) -> str:
    """Block appended to the card after a decision is taken."""
    label = LABELS.get(short_decision, short_decision)
    elapsed = _elapsed_since_message(callback)
    return (
        f"\n\n✅ <b>Решение ({elapsed}):</b> {escape(label)}"
        f"\n<b>Модератор:</b> {_format_moderator(callback)}"
    )


async def _append_decision_to_message(
    callback: CallbackQuery, short_decision: str
) -> None:
    """Append the resolution footer to the card and refresh the keyboard.

    The card may be either a document (caption) or a plain text message —
    we pick the right edit method and rebuild the HTML from entities so
    existing bold/code formatting in the original card is preserved.
    """
    msg = callback.message
    footer = _decision_footer(short_decision, callback)
    new_keyboard = build_resolved_keyboard(short_decision)

    try:
        if msg.caption is not None:
            original_html = html_decoration.unparse(
                msg.caption, msg.caption_entities or []
            )
            await msg.edit_caption(
                caption=original_html + footer,
                reply_markup=new_keyboard,
            )
        elif msg.text is not None:
            original_html = html_decoration.unparse(
                msg.text, msg.entities or []
            )
            await msg.edit_text(
                text=original_html + footer,
                reply_markup=new_keyboard,
            )
        else:
            await msg.edit_reply_markup(reply_markup=new_keyboard)
    except Exception as exc:
        logger.warning("Failed to edit message after decision: %s", exc)


def build_router(allowed_chat_id: int, backend: BackendClient) -> Router:
    router = Router()

    @router.callback_query(F.data == NOOP_CALLBACK)
    async def noop_handler(callback: CallbackQuery) -> None:
        # Clears the spinner so the user sees an immediate response.
        await callback.answer()

    @router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:"))
    async def decision_handler(callback: CallbackQuery) -> None:
        if callback.message is None or callback.message.chat.id != allowed_chat_id:
            await callback.answer("Этот бот работает только в admin-чате.", show_alert=True)
            return

        parsed = parse_callback(callback.data or "")
        if parsed is None:
            await callback.answer("Невалидные данные кнопки.", show_alert=True)
            return
        order_uuid, short_decision = parsed

        backend_decision = DECISION_MAP.get(short_decision)
        if backend_decision is None:
            await callback.answer("Неизвестное решение.", show_alert=True)
            return

        from_user = callback.from_user
        try:
            await backend.post_moderation(
                order_uuid=order_uuid,
                decision=backend_decision,
                moderator_tg_id=from_user.id if from_user else None,
                moderator_username=from_user.username if from_user else None,
                message_id=callback.message.message_id,
            )
        except BackendError as err:
            if err.status == 400 and "conflict" in (err.body or "").lower():
                await _mark_already_handled(callback)
                return
            if err.status == 404:
                logger.warning("Order not found on backend: %s", order_uuid)
                try:
                    await callback.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
                await callback.answer("Ордер не найден на бэкенде.", show_alert=True)
                return
            logger.warning("Backend rejected moderation: status=%s body=%s", err.status, err.body)
            await callback.answer("Бэкенд не принял решение, попробуйте ещё раз.", show_alert=True)
            return
        except Exception as exc:
            logger.exception("Backend call failed: %s", exc)
            await callback.answer("Ошибка связи с бэкендом.", show_alert=True)
            return

        await _append_decision_to_message(callback, short_decision)
        await callback.answer("Принято.")

    return router


async def _mark_already_handled(callback: CallbackQuery) -> None:
    """Best-effort: remove the active keyboard and tell the admin the
    decision was already taken by someone else."""
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer("Это решение уже принято.", show_alert=True)
