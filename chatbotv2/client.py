import logging
import os
import secrets

from telethon import TelegramClient

from chatbotv2.config import get_settings

logger = logging.getLogger("chatbotv2.client")
_settings = get_settings()

_client: TelegramClient | None = None


async def get_client() -> TelegramClient:
    global _client
    if _client is None:
        _client = TelegramClient(
            _settings.telethon_session,
            _settings.api_id,
            _settings.api_hash,
        )
        code = os.environ.get("TELETHON_CODE", "")
        if code:
            await _client.start(
                phone=lambda: _settings.phone_number,
                code=lambda: code,
            )
        else:
            await _client.start(phone=_settings.phone_number)
        me = await _client.get_me()
        logger.info("Logged in as %s (id=%s)", me.username, me.id)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.disconnect()
        _client = None


async def send_message(entity, text: str, random_id: bytes | None = None):
    """Send a text message to a Telegram entity.

    Args:
        entity: Telegram entity (chat ID, username, etc.)
        text: Message text.
        random_id: 128-bit random bytes for deduplication. Generated automatically if not provided.
    """
    client = await get_client()

    class _Msg:
        def __init__(self, telethon_msg):
            self.message_id = telethon_msg.id
            self.id = telethon_msg.id

    if random_id is None:
        random_id = secrets.token_bytes(16)
    tl_msg = await client.send_message(entity, text, random_id=random_id)
    return _Msg(tl_msg)


async def send_file(
    entity, file, caption: str = "", force_document: bool = False, random_id: bytes | None = None
):
    """Send a file (photo/document) to a Telegram entity.

    Args:
        entity: Telegram entity (chat ID, username, etc.)
        file: File path (str), bytes, or file-like object.
        caption: Optional caption text.
        force_document: If True, send as document instead of auto-detect.
        random_id: 128-bit random bytes for deduplication. Generated automatically if not provided.
    """
    client = await get_client()

    class _Msg:
        def __init__(self, telethon_msg):
            self.message_id = telethon_msg.id
            self.id = telethon_msg.id

    if random_id is None:
        random_id = secrets.token_bytes(16)
    tl_msg = await client.send_file(
        entity,
        file,
        caption=caption,
        force_document=force_document,
        random_id=random_id,
    )
    return _Msg(tl_msg)
