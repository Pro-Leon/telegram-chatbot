from aiogram import Bot
from aiogram.types import Message

from core.config import get_settings

_settings = get_settings()

_bot: Bot | None = None


async def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=_settings.telegram_token)
    return _bot


async def send_message(chat_id: int, text: str) -> Message:
    bot = await get_bot()
    return await bot.send_message(chat_id=chat_id, text=text)


async def send_typing_action(chat_id: int) -> None:
    bot = await get_bot()
    await bot.send_chat_action(chat_id=chat_id, action="typing")


async def close_bot() -> None:
    global _bot
    if _bot is not None:
        await _bot.session.close()
        _bot = None
