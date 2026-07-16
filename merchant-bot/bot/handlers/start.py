from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from bot.keyboards.inline import (
    BTN_CHANGE_TERMINAL,
    main_reply_keyboard,
    terminal_select_keyboard,
)
from bot.services.api_client import ApiClientError, MerchantBotApiClient

router = Router()


async def _show_terminal_picker(
    message: Message,
    api_client: MerchantBotApiClient,
    tg_user_id: int,
    active_terminal_id: int | None,
) -> None:
    try:
        merchants = await api_client.list_merchants(tg_user_id)
    except ApiClientError as exc:
        await message.answer(f"Не удалось загрузить список терминалов.\n\n{exc}")
        return

    if not merchants:
        await message.answer(
            "У вас нет доступных терминалов. Обратитесь к администратору.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if len(merchants) == 1 and active_terminal_id is None:
        only = merchants[0]
        try:
            await api_client.set_active_terminal(tg_user_id, only.id)
        except ApiClientError as exc:
            await message.answer(f"Не удалось активировать терминал.\n\n{exc}")
            return
        await message.answer(
            f"✅ Терминал <b>{only.name or f'#{only.id}'}</b> выбран автоматически.\n\n"
            "Чтобы создать заявку, просто отправьте сумму и метод, "
            "например: <code>1000 сбп</code>.",
            reply_markup=main_reply_keyboard(),
        )
        return

    await message.answer(
        "Выберите терминал:",
        reply_markup=terminal_select_keyboard(merchants, active_terminal_id),
    )


@router.message(CommandStart())
async def cmd_start(message: Message, api_client: MerchantBotApiClient) -> None:
    tg_user_id = message.from_user.id if message.from_user else 0
    try:
        state = await api_client.get_state(tg_user_id)
    except ApiClientError as exc:
        await message.answer(f"Ошибка получения состояния.\n\n{exc}")
        return

    if state.active_terminal_id is not None:
        name = state.active_terminal_name or f"#{state.active_terminal_id}"
        await message.answer(
            f"🏦 Добро пожаловать!\n\nАктивный терминал: <b>{name}</b>\n\n"
            "Чтобы создать заявку, отправьте сумму и метод, "
            "например: <code>1000 сбп</code>.",
            reply_markup=main_reply_keyboard(),
        )
        return

    await message.answer("🏦 Добро пожаловать!", reply_markup=main_reply_keyboard())
    await _show_terminal_picker(
        message, api_client, tg_user_id, state.active_terminal_id
    )


@router.message(F.text == BTN_CHANGE_TERMINAL)
async def change_terminal_text(
    message: Message, api_client: MerchantBotApiClient
) -> None:
    tg_user_id = message.from_user.id if message.from_user else 0
    try:
        state = await api_client.get_state(tg_user_id)
    except ApiClientError:
        state = None
    active_id = state.active_terminal_id if state else None
    await _show_terminal_picker(message, api_client, tg_user_id, active_id)


@router.callback_query(F.data.startswith("terminal:select:"))
async def select_terminal(
    callback: CallbackQuery, api_client: MerchantBotApiClient
) -> None:
    merchant_id = int(callback.data.split(":")[2])
    tg_user_id = callback.from_user.id if callback.from_user else 0

    try:
        state = await api_client.set_active_terminal(tg_user_id, merchant_id)
    except ApiClientError as exc:
        await callback.answer("Не удалось переключить терминал", show_alert=True)
        if callback.message:
            await callback.message.answer(str(exc))
        return

    await callback.answer("Терминал выбран")
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        name = state.active_terminal_name or f"#{state.active_terminal_id}"
        await callback.message.answer(
            f"✅ Терминал <b>{name}</b> активирован.",
            reply_markup=main_reply_keyboard(),
        )
