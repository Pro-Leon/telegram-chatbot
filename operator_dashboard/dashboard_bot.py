import logging

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from core.config import get_settings
from db.postgres import (
    get_pending_queue_items,
    get_recent_messages,
    resolve_queue_item,
    save_outbound_message,
)

_settings = get_settings()
logger = logging.getLogger("dashboard")

_dashboard_bot: Bot | None = None
_router = Router()


def build_draft_keyboard(queue_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Send", callback_data=f"approve:{queue_id}"),
                InlineKeyboardButton(text="Edit", callback_data=f"edit:{queue_id}"),
            ],
            [
                InlineKeyboardButton(text="Reject", callback_data=f"reject:{queue_id}"),
                InlineKeyboardButton(text="History", callback_data=f"history:{queue_id}"),
            ],
        ]
    )


def is_authorized(telegram_id: int) -> bool:
    return telegram_id in _settings.operator_telegram_ids


@_router.message(Command(commands=["start"]))
async def handle_start(message: Message):
    if not is_authorized(message.from_user.id):
        await message.answer("You are not authorized as an operator.")
        return
    await message.answer(
        "Operator dashboard ready.\nCommands: /queue (view pending), /stats (daily stats)"
    )


@_router.message(Command(commands=["queue"]))
async def handle_queue(message: Message):
    if not is_authorized(message.from_user.id):
        await message.answer("Not authorized.")
        return

    items = await get_pending_queue_items(limit=10)
    if not items:
        await message.answer("No pending messages in queue.")
        return

    for item in items:
        score = item.get("confidence_score", 0)
        score_emoji = "🟢" if score > 0.7 else "🟡" if score > 0.5 else "🔴"
        flags = ", ".join(item.get("flags", [])) if item.get("flags") else "none"

        text = (
            f"📨 Queue #{item['id']}\n"
            f"Fan: {item.get('username', 'N/A')} ({item['user_id']})\n"
            f"Score: {score_emoji} {score:.0%}\n"
            f"Flags: {flags}\n\n"
            f"Draft:\n_{item['draft_content']}_"
        )

        await message.answer(
            text,
            parse_mode="Markdown",
            reply_markup=build_draft_keyboard(item["id"]),
        )


@_router.callback_query()
async def handle_callback(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        await callback.answer("Not authorized", show_alert=True)
        return

    action, _, queue_id_str = callback.data.partition(":")
    queue_id = int(queue_id_str)

    if action == "approve":
        await _handle_approve(callback, queue_id)
    elif action == "edit":
        await _handle_edit_request(callback, queue_id)
    elif action == "reject":
        await _handle_reject(callback, queue_id)
    elif action == "history":
        await _handle_history(callback, queue_id)
    else:
        await callback.answer("Unknown action")


async def _handle_approve(callback: CallbackQuery, queue_id: int):
    item = await _get_and_resolve(callback, queue_id)
    if item is None:
        return

    from ingestion.bot import send_message

    sent = await send_message(item["user_id"], item["draft_content"])

    await save_outbound_message(
        user_id=item["user_id"],
        content=item["draft_content"],
        draft_content=item["draft_content"],
        was_edited=False,
        was_auto_approved=False,
        confidence_score=item.get("confidence_score", 0),
        operator_id=callback.from_user.id,
        telegram_message_id=sent.message_id,
    )

    await callback.message.edit_text(f"✅ Sent to {item['user_id']}")


async def _handle_edit_request(callback: CallbackQuery, queue_id: int):
    prompt = f"Send your edited version (reply to this message):\nQueue ID: `{queue_id}`"
    await callback.message.reply(prompt, parse_mode="MarkdownV2")
    await callback.answer()


@_router.message(lambda m: m.reply_to_message is not None)
async def handle_edit_reply(message: Message):
    if not is_authorized(message.from_user.id):
        return

    original_text = message.reply_to_message.text or ""
    if "Queue ID:" not in original_text:
        return

    queue_id = int(original_text.split("Queue ID: `")[1].split("`")[0])

    item = await _get_and_resolve(message, queue_id)
    if item is None:
        await message.reply("Item not found or already handled")
        return

    edited_content = message.text

    from ingestion.bot import send_message

    sent = await send_message(item["user_id"], edited_content)

    await save_outbound_message(
        user_id=item["user_id"],
        content=edited_content,
        draft_content=item["draft_content"],
        was_edited=True,
        was_auto_approved=False,
        confidence_score=item.get("confidence_score", 0),
        operator_id=message.from_user.id,
        telegram_message_id=sent.message_id,
    )

    await message.reply("✅ Edited version sent")


async def _handle_reject(callback: CallbackQuery, queue_id: int):
    await resolve_queue_item(queue_id, "rejected", operator_id=callback.from_user.id)
    await callback.message.edit_text(f"🗑 Queue #{queue_id} rejected")


async def _handle_history(callback: CallbackQuery, queue_id: int):
    item = await _get_queue_item_safe(queue_id)
    if item is None:
        await callback.answer("Item not found")
        return

    history = await get_recent_messages(item["user_id"], limit=10)
    text = "\n\n".join(
        f"{'Fan' if m['direction'] == 'inbound' else 'You'}: {m['content']}" for m in history
    )
    await callback.message.reply(f"History:\n\n{text}")
    await callback.answer()


async def _get_queue_item_safe(queue_id: int):
    from db.postgres import get_queue_item

    return await get_queue_item(queue_id)


async def _get_and_resolve(source, queue_id: int):
    item = await _get_queue_item_safe(queue_id)
    if item is None or item.get("status") != "pending":
        if hasattr(source, "message"):
            await source.message.edit_text(f"Queue #{queue_id} already handled")
        return None

    await resolve_queue_item(
        queue_id,
        "approved",
        operator_id=getattr(source, "from_user", None).id if hasattr(source, "from_user") else None,
    )
    return item


async def notify_operator_of_draft(
    queue_id: int,
    user_id: int,
    draft: str,
    score: float,
    flags: list[str],
) -> None:
    bot_instance = await get_dashboard_bot()

    flag_text = ", ".join(flags) if flags else "none"
    score_emoji = "🟢" if score > 0.7 else "🟡" if score > 0.5 else "🔴"

    notification = (
        f"📨 New message to review\n\n"
        f"Fan ID: `{user_id}`\n"
        f"Queue ID: {queue_id}\n"
        f"Score: {score_emoji} {score:.0%}\n"
        f"Flags: {flag_text}\n\n"
        f"Draft:\n{draft}"
    )

    for operator_id in _settings.operator_telegram_ids:
        try:
            await bot_instance.send_message(
                chat_id=operator_id,
                text=notification,
                parse_mode="Markdown",
                reply_markup=build_draft_keyboard(queue_id),
            )
        except Exception:
            logger.exception("Failed to notify operator %s", operator_id)


async def get_dashboard_bot() -> Bot:
    global _dashboard_bot
    if _dashboard_bot is None:
        _dashboard_bot = Bot(token=_settings.operator_bot_token)
    return _dashboard_bot


async def init_dashboard_bot() -> Bot:
    bot_instance = await get_dashboard_bot()
    await bot_instance.set_my_commands([])
    return bot_instance


async def close_dashboard_bot() -> None:
    global _dashboard_bot
    if _dashboard_bot is not None:
        await _dashboard_bot.session.close()
        _dashboard_bot = None


def get_router() -> Router:
    return _router
