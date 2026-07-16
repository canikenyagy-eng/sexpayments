from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    """Diagnostic helper — admin types ``/id`` in the group to discover
    the chat_id they should write into ``support_bot_chat_id`` in the
    admin platform-settings page.
    """
    chat = message.chat
    await message.reply(
        f"<b>Chat ID:</b> <code>{chat.id}</code>\n"
        f"<b>Type:</b> {chat.type}\n"
        f"<b>Title:</b> {chat.title or '—'}"
    )
