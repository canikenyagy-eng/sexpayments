from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    """Diagnostic helper — the merchant adds the bot to their group and
    types ``/id`` to discover the chat_id they need to give the admin
    so it can be stored in ``merchants.notify_telegram_group_id``.
    """
    chat = message.chat
    await message.reply(
        f"<b>Chat ID:</b> <code>{chat.id}</code>\n"
        f"<b>Type:</b> {chat.type}\n"
        f"<b>Title:</b> {chat.title or '—'}"
    )


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Friendly hello so a merchant who DMs the bot doesn't get silence."""
    await message.answer(
        "Привет! Я — merchant-notify-bot.\n\n"
        "Добавьте меня в вашу Telegram-группу и пришлите команду /id, чтобы "
        "получить chat_id. Передайте этот chat_id админу площадки — он внесёт "
        "его в карточку вашего мерчанта.\n\n"
        "Я не отвечаю на сообщения вне группы — все уведомления приходят "
        "по запросу со стороны платформы (например, когда саппорт просит "
        "приложить PDF или видео к чеку)."
    )
