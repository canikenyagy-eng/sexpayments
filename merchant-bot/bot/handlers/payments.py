import asyncio
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape
from io import BytesIO

_MSK = timezone(timedelta(hours=3))


def _fmt_msk(dt: datetime, pattern: str = "%d.%m %H:%M") -> str:
    """Render any datetime in Moscow time. Naive inputs are assumed UTC
    (FastAPI emits aware UTC ISO strings, but be defensive)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_MSK).strftime(pattern)

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Document, Message, PhotoSize, Video

from bot.keyboards.inline import (
    BTN_CHANGE_TERMINAL,
    active_order_keyboard,
    created_order_keyboard,
    main_reply_keyboard,
)
from bot.services.api_client import ApiClientError, MerchantBotApiClient, OrderInfo
from bot.utils.tracker import ActivePaymentTracker

router = Router()

UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# Order creation parser: "1000 сбп", "1500,50 карта", "2000 sbp", "1000 c2c".
# The method token may contain digits (c2c/с2с) and a comma — the wrong-layout
# typo "c,g" (Russian "сбп" mis-typed on the English QWERTY) is one of the
# accepted SBP aliases, see METHOD_ALIASES.
PAYMENT_RE = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s+([A-Za-zА-Яа-яЁё0-9,]+)\s*$"
)

# Maps a user-typed method token (lowercased) to our canonical method.
# Each canonical method has multiple aliases:
#   * Russian / English standard spellings
#   * Common typos & morphologies (-у / -ой, missing letter, swapped letters)
#   * Wrong-keyboard-layout transliterations — when a user typed the right word
#     on the wrong layout (e.g. wanted "сбп" but the keyboard was English →
#     came out as "c,g"; or wanted "card" on Russian layout → "сфкв").
METHOD_ALIASES: dict[str, str] = {
    # SBP
    "sbp": "sbp",
    "сбп": "sbp",
    "спб": "sbp",        # common typo: swapped letters
    "телефон": "sbp",
    "c,g": "sbp",        # Russian "сбп" typed on English QWERTY
    "ыиз": "sbp",        # English "sbp" typed on Russian ЙЦУКЕН
    # CARD / C2C
    "card": "card",
    "карта": "card",
    "карту": "card",
    "картой": "card",
    "кард": "card",
    "с2с": "card",       # both letters are Cyrillic 'с'
    "c2c": "card",       # both letters are Latin 'c'
    "сфкв": "card",      # English "card" typed on Russian ЙЦУКЕН
    "rfhnf": "card",     # Russian "карта" typed on English QWERTY
    "rfhl": "card",      # Russian "кард" typed on English QWERTY
    # SIM
    "sim": "sim",
    "сим": "sim",
}

METHOD_LABELS = {
    "card": ("💳", "Карта"),
    "sbp": ("📲", "СБП"),
    "sim": ("📱", "SIM"),
}
FINAL_STATUS_LABELS = {
    "success": "✅ Успешно завершена",
    "canceled": "❌ Отменена",
    "failed": "❌ Истекло время",
    "refunded": "↩️ Возврат",
}
STATUS_LABELS = {
    "created": "Создана, ожидание реквизитов",
    "pending": "Ожидание оплаты",
    "receipt_uploaded": "Чек загружен, на проверке",
    "disputed": "В споре",
    **FINAL_STATUS_LABELS,
}

RECEIPT_UPLOADABLE_STATUSES = {"created", "pending", "receipt_uploaded"}


# Per-TG asyncio locks: guarantees that orders from a single Telegram user
# are created strictly sequentially (many "1000 сбп" in a row still produce
# independent orders, but in the order they arrived, one at a time).
_tg_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def _tg_lock(tg_user_id: int) -> asyncio.Lock:
    return _tg_locks[tg_user_id]


def _is_no_requisite_error(exc: ApiClientError) -> bool:
    """Detect the backend's 404 `{"error":{"code":"not_found",
    "message":"No available requisite found at the moment."}}` response."""
    if exc.status != 404:
        return False
    if exc.code == "not_found":
        msg = (exc.message or str(exc)).lower()
        if "no available requisite" in msg:
            return True
    return False


class ReceiptState(StatesGroup):
    waiting_file = State()


# ── Helpers ────────────────────────────────────────────────────────────────


def _extract_uuid(text: str | None) -> str | None:
    if not text:
        return None
    match = UUID_RE.search(text)
    return match.group(0) if match else None


def _fmt_amount(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + " ₽"


def _method_icon(method: str) -> str:
    return METHOD_LABELS.get(method.lower(), ("💸", method))[0]


def _method_label(method: str) -> str:
    return METHOD_LABELS.get(method.lower(), ("💸", method))[1]


def _status_label(status: str) -> str:
    return STATUS_LABELS.get(status.lower(), status)


def _fmt_order(order: OrderInfo) -> str:
    method = order.requisite.payment_method if order.requisite else "—"
    bank = order.requisite.bank_name if order.requisite else "—"
    account = order.requisite.account_number if order.requisite else "—"
    holder = order.requisite.account_holder if order.requisite else "—"
    requisite_label = "Телефон" if method.lower() == "sbp" else "Карта"

    expires = f"{_fmt_msk(order.expires_at)} МСК" if order.expires_at else "—"
    # Requisite details go in a <pre> block so the merchant can copy the whole
    # thing with one tap. Free-text fields are HTML-escaped — a bank/holder with
    # & or < would otherwise break Telegram HTML parsing (message would 400).
    details = (
        f"Тип: {_method_label(method)}\n"
        f"Сумма: {_fmt_amount(order.amount)}\n"
        f"Банк: {escape(bank, quote=False)}\n"
        f"{requisite_label}: {escape(account, quote=False)}\n"
        f"Получатель: {escape(holder, quote=False)}\n"
        f"Истекает: {expires}"
    )
    lines = [
        "✅ Заявка создана!\n",
        f"ID: <code>{order.id}</code>\n",
        f"<pre>{details}</pre>",
        f"\nДля проверки: /status {order.id}",
    ]
    return "\n".join(lines)


def _fmt_status(order: OrderInfo) -> str:
    method = order.requisite.payment_method if order.requisite else "—"
    account = order.requisite.account_number if order.requisite else "—"
    bank = order.requisite.bank_name if order.requisite else "—"
    return (
        f"📄 Статус заявки <code>{order.id}</code>\n\n"
        f"{_status_label(order.status)}\n"
        f"Сумма: {_fmt_amount(order.amount)}\n"
        f"Метод: {_method_label(method)}\n"
        f"Банк: {bank}\n"
        f"Реквизит: {account}"
    )


def _fmt_final_notification(order: OrderInfo) -> str:
    label = FINAL_STATUS_LABELS.get(order.status.lower(), f"Статус: {order.status}")
    return f"{label}\n\nЗаявка <code>{order.id}</code>\nСумма: {_fmt_amount(order.amount)}"


async def _require_active_terminal(
    message: Message, api_client: MerchantBotApiClient
) -> tuple[int, int] | None:
    """Returns (tg_user_id, merchant_id) or notifies user and returns None."""
    tg_user_id = message.from_user.id if message.from_user else 0
    try:
        state = await api_client.get_state(tg_user_id)
    except ApiClientError as exc:
        await message.answer(f"Ошибка получения состояния.\n\n{exc}")
        return None
    if not state.active_terminal_id:
        await message.answer(
            f"Терминал не выбран. Нажмите «{BTN_CHANGE_TERMINAL}» или /start.",
            reply_markup=main_reply_keyboard(),
        )
        return None
    return tg_user_id, state.active_terminal_id


# ── Create order from text "1000 сбп" ──────────────────────────────────────


@router.message(
    F.text.regexp(PAYMENT_RE.pattern),
    ~StateFilter(ReceiptState.waiting_file),
)
async def create_order_from_text(
    message: Message,
    api_client: MerchantBotApiClient,
    tracker: ActivePaymentTracker,
) -> None:
    text = str(message.text or "").strip()
    match = PAYMENT_RE.match(text)
    if not match:
        return
    raw_amount, raw_method = match.group(1), match.group(2)
    try:
        amount = float(raw_amount.replace(",", "."))
    except ValueError:
        await message.answer("Не удалось распознать сумму.")
        return
    if amount <= 0:
        await message.answer("Сумма должна быть положительной.")
        return

    method = METHOD_ALIASES.get(raw_method.lower())
    if not method:
        await message.answer(
            f"Неизвестный метод: <b>{raw_method}</b>.\n"
            "Доступные: сбп / карта / sim."
        )
        return

    ctx = await _require_active_terminal(message, api_client)
    if not ctx:
        return
    tg_user_id, merchant_id = ctx

    async with _tg_lock(tg_user_id):
        try:
            order = await api_client.create_order(
                tg_user_id,
                merchant_id,
                amount=amount,
                currency="RUB",
                payment_method=method,
                chat_id=message.chat.id,
            )
        except ApiClientError as exc:
            if _is_no_requisite_error(exc):
                await message.answer(
                    "Активных реквизитов на данную сумму в данный момент нет.",
                    reply_markup=main_reply_keyboard(),
                )
                return
            await message.answer(f"Не удалось создать заявку.\n\n{exc}")
            return

        tracker.track(
            order_id=order.id,
            chat_id=message.chat.id,
            merchant_id=merchant_id,
            tg_user_id=tg_user_id,
            status=order.status,
        )
        await message.answer(
            _fmt_order(order),
            reply_markup=created_order_keyboard(order.id, merchant_id),
        )


# ── Status command ─────────────────────────────────────────────────────────


@router.message(Command("status"))
async def status_command(
    message: Message, api_client: MerchantBotApiClient
) -> None:
    ctx = await _require_active_terminal(message, api_client)
    if not ctx:
        return
    tg_user_id, merchant_id = ctx

    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    order_id = _extract_uuid(parts[1] if len(parts) > 1 else "")
    if not order_id:
        await message.answer("Используйте: /status <uuid>")
        return

    try:
        order = await api_client.get_order(tg_user_id, merchant_id, order_id)
    except ApiClientError as exc:
        await message.answer(f"Не удалось получить статус.\n\n{exc}")
        return

    kb = (
        active_order_keyboard(order.id, merchant_id)
        if order.status in RECEIPT_UPLOADABLE_STATUSES
        else None
    )
    await message.answer(_fmt_status(order), reply_markup=kb)


# ── Cancel order ───────────────────────────────────────────────────────────


def _parse_payment_cb(data: str, prefix: str) -> tuple[int, str] | None:
    """Parse 'prefix:merchant_id:order_id' callback_data."""
    parts = data.split(":", maxsplit=3)
    if len(parts) < 4 or f"{parts[0]}:{parts[1]}" != prefix:
        return None
    try:
        merchant_id = int(parts[2])
    except ValueError:
        return None
    return merchant_id, parts[3]


@router.callback_query(F.data.startswith("payment:cancel:"))
async def cancel_payment_callback(
    callback: CallbackQuery,
    api_client: MerchantBotApiClient,
    tracker: ActivePaymentTracker,
) -> None:
    tg_user_id = callback.from_user.id if callback.from_user else 0
    parsed = _parse_payment_cb(callback.data, "payment:cancel")
    if not parsed:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    merchant_id, order_id = parsed

    try:
        order = await api_client.cancel_order(tg_user_id, merchant_id, order_id)
    except ApiClientError as exc:
        await callback.answer("Не удалось отменить заявку", show_alert=True)
        if callback.message:
            await callback.message.answer(f"Ошибка отмены.\n\n{exc}")
        return

    tracker.untrack(order_id)
    await callback.answer("Заявка отменена")
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.message.answer(_fmt_final_notification(order))


# ── Receipt upload ─────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("payment:upload_receipt:"))
async def start_receipt_upload(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    parsed = _parse_payment_cb(callback.data, "payment:upload_receipt")
    if not parsed:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    merchant_id, order_id = parsed

    await state.set_state(ReceiptState.waiting_file)
    await state.update_data(receipt_order_id=order_id, receipt_merchant_id=merchant_id)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"📎 Отправьте файл чека (фото, документ или видео) для заявки:\n"
            f"<code>{order_id}</code>\n\n"
            "Или введите /cancel для отмены.",
        )


@router.message(Command("cancel"))
async def cancel_any_state(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current:
        await state.set_state(None)
        await message.answer(
            "Действие отменено.", reply_markup=main_reply_keyboard()
        )


async def _extract_file(message: Message) -> tuple[bytes, str] | None:
    buffer = BytesIO()
    if message.document:
        doc: Document = message.document
        await message.bot.download(doc, destination=buffer)
        return buffer.getvalue(), doc.file_name or "receipt.bin"
    if message.photo:
        photo: PhotoSize = message.photo[-1]
        await message.bot.download(photo, destination=buffer)
        return buffer.getvalue(), "receipt.jpg"
    if message.video:
        video: Video = message.video
        await message.bot.download(video, destination=buffer)
        return buffer.getvalue(), video.file_name or "receipt.mp4"
    if message.animation:
        await message.bot.download(message.animation, destination=buffer)
        return buffer.getvalue(), message.animation.file_name or "receipt.mp4"
    return None


@router.message(
    ReceiptState.waiting_file,
    F.document | F.photo | F.video | F.animation,
)
async def upload_receipt_fsm(
    message: Message,
    api_client: MerchantBotApiClient,
    tracker: ActivePaymentTracker,
    state: FSMContext,
) -> None:
    tg_user_id = message.from_user.id if message.from_user else 0
    data = await state.get_data()
    order_id = data.get("receipt_order_id")
    merchant_id = data.get("receipt_merchant_id")
    if not order_id or not merchant_id:
        await message.answer(
            "Ошибка: заявка не определена. Начните заново через /status."
        )
        await state.set_state(None)
        return

    file_data = await _extract_file(message)
    if not file_data:
        return
    content, file_name = file_data

    # Clear state *before* the network call so that any failure (400/conflict,
    # timeouts, etc.) never leaves the user stuck in ReceiptState.waiting_file —
    # otherwise subsequent text like "1000 сбп" would be swallowed by the filter.
    await state.set_state(None)

    try:
        order = await api_client.confirm_order(
            tg_user_id,
            merchant_id,
            order_id,
            file_name=file_name,
            content=content,
        )
    except ApiClientError as exc:
        await message.answer(
            f"Не удалось загрузить чек.\n\n{exc}",
            reply_markup=main_reply_keyboard(),
        )
        return

    tracker.track(
        order_id=order.id,
        chat_id=message.chat.id,
        merchant_id=merchant_id,
        tg_user_id=tg_user_id,
        status=order.status,
    )
    await message.answer(
        f"✅ Чек загружен.\n\n"
        f"Заявка: <code>{order.id}</code>\n"
        f"Статус: {_status_label(order.status)}",
        reply_markup=main_reply_keyboard(),
    )


@router.message(ReceiptState.waiting_file)
async def waiting_file_non_file(
    message: Message,
    api_client: MerchantBotApiClient,
    tracker: ActivePaymentTracker,
    state: FSMContext,
) -> None:
    """Any NON-file message while we're waiting for a receipt file.

    Registered after ``upload_receipt_fsm`` (which catches the files), so the
    bot is NEVER silent in this state. Without it a user who tapped «прикрепить
    чек» and never sent a file / pressed /cancel gets stuck: ``create_order_from_text``
    is filtered out here and the file handler ignores text, so "1000 сбп" would
    match no handler. If the text looks like an order command we drop the
    abandoned receipt flow and create the order; otherwise we nudge the user.
    """
    text = str(message.text or "").strip()
    if PAYMENT_RE.match(text):
        await state.set_state(None)
        await create_order_from_text(message, api_client, tracker)
        return
    await message.answer(
        "📎 Жду файл чека (фото, документ или видео). "
        "Или /cancel — чтобы отменить.",
        reply_markup=main_reply_keyboard(),
    )


# ── Legacy: receipt via UUID in caption ────────────────────────────────────


@router.message(
    F.document | F.photo | F.video | F.animation,
    ~StateFilter(ReceiptState.waiting_file),
)
async def upload_receipt_message(
    message: Message,
    api_client: MerchantBotApiClient,
    tracker: ActivePaymentTracker,
) -> None:
    ctx = await _require_active_terminal(message, api_client)
    if not ctx:
        return
    tg_user_id, merchant_id = ctx

    order_id = _extract_uuid(message.caption or message.text)
    if not order_id:
        await message.answer(
            "Отправьте чек с подписью, в которой указан UUID заявки.\n"
            "Пример подписи: <code>3fa85f64-5717-4562-b3fc-2c963f66afa6</code>"
        )
        return

    file_data = await _extract_file(message)
    if not file_data:
        return
    content, file_name = file_data

    try:
        order = await api_client.confirm_order(
            tg_user_id,
            merchant_id,
            order_id,
            file_name=file_name,
            content=content,
        )
    except ApiClientError as exc:
        await message.answer(f"Не удалось загрузить чек.\n\n{exc}")
        return

    tracker.track(
        order_id=order.id,
        chat_id=message.chat.id,
        merchant_id=merchant_id,
        tg_user_id=tg_user_id,
        status=order.status,
    )
    await message.answer(
        f"✅ Чек загружен.\n\n"
        f"Заявка: <code>{order.id}</code>\n"
        f"Статус: {_status_label(order.status)}"
    )
