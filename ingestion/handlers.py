import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from core.config import get_settings
from db.postgres import save_inbound_message, upsert_user
from db.redis import (
    check_rate_limit,
    debounce_enqueue,
    enqueue_inbound,
    get_debounced_messages,
    invalidate_context_cache,
)

logger = logging.getLogger(__name__)
_settings = get_settings()

router = Router()


@router.message(CommandStart())
async def handle_start(message: Message):
    await upsert_user(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.first_name,
    )
    await message.answer(f"Hey {message.from_user.first_name}! 👋")


@router.message(F.text)
async def handle_text(message: Message):
    user = message.from_user

    allowed = await check_rate_limit(user.id, _settings.rate_limit_per_minute)
    if not allowed:
        return

    await upsert_user(
        user.id,
        user.username or "",
        user.first_name,
    )

    await save_inbound_message(
        user_id=user.id,
        content=message.text,
        telegram_message_id=message.message_id,
    )

    is_window_owner = await debounce_enqueue(
        user_id=user.id,
        content=message.text,
        message_data={
            "user_id": str(user.id),
            "content": message.text,
            "telegram_message_id": str(message.message_id),
            "username": user.username or "",
            "first_name": user.first_name,
        },
        window_seconds=_settings.debounce_window_seconds,
    )

    if not is_window_owner:
        await invalidate_context_cache(user.id)
        return

    asyncio.create_task(_wait_and_process(user.id, user.username or "", user.first_name))


async def _wait_and_process(user_id: int, username: str, first_name: str) -> None:
    await asyncio.sleep(_settings.debounce_window_seconds)

    debounced = await get_debounced_messages(user_id)
    if not debounced:
        return

    await invalidate_context_cache(user_id)

    latest = debounced[-1]
    await enqueue_inbound(
        {
            "user_id": str(latest.get("user_id", user_id)),
            "content": latest.get("content", ""),
            "telegram_message_id": str(latest.get("telegram_message_id", 0)),
            "username": latest.get("username", username),
            "first_name": latest.get("first_name", first_name),
        }
    )
