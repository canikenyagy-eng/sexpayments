"""Callback handler for the "confirm order paid" inline button.

On tap the bot posts to the backend (which runs the SAME settlement as the
trader-cabinet "оплачено"), then edits the message to append a "✅ Подтверждено"
footer and REMOVE the button. The backend authorises by group: the chat the
button lives in must be the order's trader group. Backend errors collapse into
user-facing branches:

  * 400 (conflict)  → "Заявка уже закрыта или не в том статусе", remove button
  * 403 (forbidden) → "Эта группа не привязана к заявке"
  * 404 (not found) → "Заявка не найдена на бэкенде", remove button
  * anything else   → "Ошибка связи с бэкендом", button left for retry
"""
import json
import logging
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.text_decorations import html_decoration

from bot.keyboards.confirm import (
    BACK_PREFIX,
    CALLBACK_PREFIX,
    CHECK_LABEL,
    CHECK_PREFIX,
    NOOP_CALLBACK,
    PROVIDER_PREFIX,
    REQUEST_PREFIX,
    RESULT_CLEAN_LABEL,
    RESULT_ERROR_LABEL,
    RESULT_FAKE_LABEL,
    WAIT_LABEL,
    build_check_keyboard,
    build_provider_keyboard,
    parse_back_callback,
    parse_callback,
    parse_check_callback,
    parse_provider_callback,
    parse_request_callback,
)
from bot.services.backend_client import BackendClient, BackendError

logger = logging.getLogger(__name__)


def _err_message(err: BackendError, fallback: str) -> str:
    """Pull the trader-facing message out of the backend's JSON error body."""
    try:
        data = json.loads(err.body)
        msg = (data.get("error") or {}).get("message") or data.get("detail")
        if isinstance(msg, str) and msg.strip():
            return msg
    except Exception:
        pass
    return fallback


def _who(callback: CallbackQuery) -> str:
    user = callback.from_user
    if user is None:
        return "—"
    if user.username:
        return f"@{escape(user.username)}"
    if user.full_name:
        return escape(user.full_name)
    return "—"


async def _mark_confirmed(callback: CallbackQuery) -> None:
    """Append a 'confirmed by …' footer and REMOVE the button entirely."""
    msg = callback.message
    footer = f"\n\n✅ <b>Подтверждено:</b> {_who(callback)}"
    try:
        if msg.caption is not None:
            original = html_decoration.unparse(msg.caption, msg.caption_entities or [])
            await msg.edit_caption(caption=original + footer, reply_markup=None)
        elif msg.text is not None:
            original = html_decoration.unparse(msg.text, msg.entities or [])
            await msg.edit_text(text=original + footer, reply_markup=None)
        else:
            await msg.edit_reply_markup(reply_markup=None)
    except Exception as exc:
        logger.warning("Failed to edit message after confirm: %s", exc)


async def _drop_keyboard(callback: CallbackQuery) -> None:
    """Remove the button when it's no longer actionable (already closed / not found)."""
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


async def _mark_proof_requested(callback: CallbackQuery, label: str) -> None:
    """Append a 'proof requested' footer and REMOVE the buttons — the order moves
    into a dispute, so further action happens in the cabinet."""
    msg = callback.message
    footer = f"\n\n🔎 <b>Запрошено:</b> {escape(label)} · {_who(callback)}"
    try:
        if msg.caption is not None:
            original = html_decoration.unparse(msg.caption, msg.caption_entities or [])
            await msg.edit_caption(caption=original + footer, reply_markup=None)
        elif msg.text is not None:
            original = html_decoration.unparse(msg.text, msg.entities or [])
            await msg.edit_text(text=original + footer, reply_markup=None)
        else:
            await msg.edit_reply_markup(reply_markup=None)
    except Exception as exc:
        logger.warning("Failed to edit message after proof request: %s", exc)


def build_router(backend: BackendClient) -> Router:
    router = Router()

    @router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:"))
    async def confirm_handler(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return

        order_uuid = parse_callback(callback.data or "")
        if order_uuid is None:
            await callback.answer("Невалидные данные кнопки.", show_alert=True)
            return

        user = callback.from_user
        try:
            await backend.post_confirm(
                order_uuid=order_uuid,
                telegram_group_id=callback.message.chat.id,
                telegram_user_id=user.id if user else None,
                telegram_username=user.username if user else None,
            )
        except BackendError as err:
            if err.status == 400:
                await _drop_keyboard(callback)
                await callback.answer("Заявка уже закрыта или не в том статусе.", show_alert=True)
                return
            if err.status == 403:
                await callback.answer("Эта группа не привязана к заявке.", show_alert=True)
                return
            if err.status == 404:
                await _drop_keyboard(callback)
                await callback.answer("Заявка не найдена на бэкенде.", show_alert=True)
                return
            logger.warning("Backend rejected confirm: status=%s body=%s", err.status, err.body)
            await callback.answer("Бэкенд не принял подтверждение, попробуйте ещё раз.", show_alert=True)
            return
        except Exception as exc:
            logger.exception("Confirm backend call failed: %s", exc)
            await callback.answer("Ошибка связи с бэкендом.", show_alert=True)
            return

        await _mark_confirmed(callback)
        await callback.answer("Заявка подтверждена ✅")

    @router.callback_query(F.data.startswith(f"{REQUEST_PREFIX}:"))
    async def request_proof_handler(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return

        parsed = parse_request_callback(callback.data or "")
        if parsed is None:
            await callback.answer("Невалидные данные кнопки.", show_alert=True)
            return
        kind, order_uuid = parsed

        user = callback.from_user
        try:
            await backend.post_request_proof(
                order_uuid=order_uuid,
                kind=kind,
                telegram_group_id=callback.message.chat.id,
                telegram_user_id=user.id if user else None,
                telegram_username=user.username if user else None,
            )
        except BackendError as err:
            if err.status == 403:
                await callback.answer("Эта группа не привязана к заявке.", show_alert=True)
                return
            if err.status == 404:
                await _drop_keyboard(callback)
                await callback.answer("Заявка не найдена на бэкенде.", show_alert=True)
                return
            logger.warning("Backend rejected request-proof: status=%s body=%s", err.status, err.body)
            await callback.answer("Не удалось запросить пруф, попробуйте ещё раз.", show_alert=True)
            return
        except Exception as exc:
            logger.exception("Request-proof backend call failed: %s", exc)
            await callback.answer("Ошибка связи с бэкендом.", show_alert=True)
            return

        label = "видео" if kind == "video" else "PDF"
        await _mark_proof_requested(callback, label)
        await callback.answer(f"Запрошено: {label} ✅")

    # ── Receipt anti-fraud check: «Проверить чек» → provider picker → verdict ──

    @router.callback_query(F.data.startswith(f"{CHECK_PREFIX}:"))
    async def check_receipt_handler(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return

        parsed = parse_check_callback(callback.data or "")
        if parsed is None:
            await callback.answer("Невалидные данные кнопки.", show_alert=True)
            return
        ctx, order_uuid = parsed
        try:
            providers = await backend.get_receipt_check_providers(callback.message.chat.id)
        except Exception as exc:
            logger.warning("Failed to fetch receipt-check providers: %s", exc)
            await callback.answer("Не удалось получить список провайдеров.", show_alert=True)
            return
        if not providers:
            await callback.answer("Нет доступных провайдеров проверки.", show_alert=True)
            return
        try:
            await callback.message.edit_reply_markup(
                reply_markup=build_provider_keyboard(order_uuid, ctx, providers)
            )
        except Exception as exc:
            logger.warning("Failed to show provider picker: %s", exc)
        await callback.answer()

    @router.callback_query(F.data.startswith(f"{BACK_PREFIX}:"))
    async def check_back_handler(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return

        parsed = parse_back_callback(callback.data or "")
        if parsed is None:
            await callback.answer()
            return
        ctx, order_uuid = parsed
        try:
            await callback.message.edit_reply_markup(
                reply_markup=build_check_keyboard(order_uuid, ctx=ctx)
            )
        except Exception as exc:
            logger.warning("Failed to restore check keyboard: %s", exc)
        await callback.answer()

    @router.callback_query(F.data == NOOP_CALLBACK)
    async def check_noop_handler(callback: CallbackQuery) -> None:
        # Frozen verdict button — just dismiss the spinner.
        await callback.answer()

    @router.callback_query(F.data.startswith(f"{PROVIDER_PREFIX}:"))
    async def check_provider_handler(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return

        parsed = parse_provider_callback(callback.data or "")
        if parsed is None:
            await callback.answer("Невалидные данные кнопки.", show_alert=True)
            return
        provider_id, ctx, order_uuid = parsed

        async def _set_slot(label: str, cb: str) -> None:
            try:
                await callback.message.edit_reply_markup(
                    reply_markup=build_check_keyboard(
                        order_uuid, ctx=ctx, check_label=label, check_callback=cb
                    )
                )
            except Exception as exc:
                logger.warning("Failed to edit check slot: %s", exc)

        # Revert to the original buttons with the check slot showing «Ожидание».
        await _set_slot(WAIT_LABEL, NOOP_CALLBACK)

        retry_cb = f"{CHECK_PREFIX}:{ctx}:{order_uuid}"
        user = callback.from_user
        try:
            result = await backend.post_receipt_check(
                order_uuid=order_uuid,
                provider_id=provider_id,
                telegram_group_id=callback.message.chat.id,
                telegram_user_id=user.id if user else None,
                telegram_username=user.username if user else None,
            )
            status = result.get("status")
            is_clean = result.get("is_clean")
            if status in ("success", "cached") and is_clean is True:
                label, cb, alert, is_err = RESULT_CLEAN_LABEL, NOOP_CALLBACK, "Чек настоящий 🟢", False
            elif status in ("success", "cached") and is_clean is False:
                label, cb, alert, is_err = RESULT_FAKE_LABEL, NOOP_CALLBACK, "Чек — фейк 🔴", True
            else:  # failed — provider error / non-PDF; charge was refunded, allow retry
                label, cb, alert, is_err = RESULT_ERROR_LABEL, retry_cb, "Не удалось проверить чек.", True
        except BackendError as err:
            # Pre-check failure (balance / provider / group) — restore «Проверить чек».
            alert = (
                "Эта группа не привязана к заявке."
                if err.status == 403
                else _err_message(err, "Не удалось проверить чек.")
            )
            label, cb, is_err = CHECK_LABEL, retry_cb, True
        except Exception as exc:
            logger.exception("Receipt-check backend call failed: %s", exc)
            label, cb, alert, is_err = CHECK_LABEL, retry_cb, "Ошибка связи с бэкендом.", True

        await _set_slot(label, cb)
        try:
            await callback.answer(alert, show_alert=is_err)
        except Exception:
            pass  # callback may have expired after a long provider call — the button conveys it

    return router
