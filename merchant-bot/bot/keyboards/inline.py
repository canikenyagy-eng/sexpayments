from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot.services.api_client import BotMerchantItem


BTN_CHANGE_TERMINAL = "🔄 Сменить терминал"
BTN_BALANCE = "💰 Баланс"


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Native Telegram keyboard with the primary actions."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=BTN_CHANGE_TERMINAL),
                KeyboardButton(text=BTN_BALANCE),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Например: 1000 сбп",
    )


def terminal_select_keyboard(
    merchants: list[BotMerchantItem],
    active_terminal_id: int | None = None,
) -> InlineKeyboardMarkup:
    rows = []
    for m in merchants:
        marker = "✅ " if active_terminal_id == m.id else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}🏦 {m.name or f'Терминал #{m.id}'} [{m.status}]",
                    callback_data=f"terminal:select:{m.id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def created_order_keyboard(order_id: str, merchant_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📤 Загрузить чек",
                    callback_data=f"payment:upload_receipt:{merchant_id}:{order_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить заявку",
                    callback_data=f"payment:cancel:{merchant_id}:{order_id}",
                )
            ],
        ]
    )


def proof_request_keyboard(order_id: str, merchant_id: int) -> InlineKeyboardMarkup:
    """Single «attach proof» button under a backend-pushed PDF/video proof
    request. Reuses the upload-receipt callback so the file-upload FSM (and the
    confirm/attach backend call) is exactly the same as confirming an order."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📎 Приложить доказательство",
                    callback_data=f"payment:upload_receipt:{merchant_id}:{order_id}",
                )
            ],
        ]
    )


def active_order_keyboard(order_id: str, merchant_id: int) -> InlineKeyboardMarkup:
    """Keyboard shown alongside /status for non-final orders."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📤 Загрузить чек",
                    callback_data=f"payment:upload_receipt:{merchant_id}:{order_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить заявку",
                    callback_data=f"payment:cancel:{merchant_id}:{order_id}",
                )
            ],
        ]
    )
