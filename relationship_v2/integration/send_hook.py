"""Send-confirmation hook (Stage F4). Confirmed sends in, ResponseSent out.

Call sites (PRESERVED transport, untouched semantics): `chatbotv2/main.py`
after each `publish_event("message.sent", ...)` — Telegram accepted, stream
ACKed. Flag-gated by `is_v2_write_enabled()` (default off → single check,
zero I/O); fail-open (any V2 error logs and returns None — sending,
persistence, and realtime delivery never depend on V2).

Message identity: Telegram message id when present, else the send-stream
dedup id. Either is stable post-acceptance, so retries dedupe through the
processor instead of duplicating relationship history.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("sunny.v2.send_hook")


async def note_send_confirmed(
    creator_id: int,
    user_id: int,
    telegram_message_id: int | None,
    generation_id: str | None,
    was_edited: bool = False,
    queue_id: int | None = None,
    source: str = "chatbotv2.main",
    dedup_id: str | None = None,
    create_event: Callable[..., Awaitable[dict]] | None = None,
    mark_processed: Callable[..., Awaitable[dict]] | None = None,
    is_processed: Callable[[str, str], Awaitable[bool]] | None = None,
    get_or_create: Callable[..., Awaitable[dict]] | None = None,
) -> Any | None:
    """Feed a confirmed send to the V2 processor. Returns ProcessResult/None."""
    from core.architecture_router import is_v2_write_enabled

    if not is_v2_write_enabled():
        return None
    try:
        if not isinstance(creator_id, int) or creator_id <= 0:
            return None
        if not isinstance(user_id, int) or user_id <= 0:
            return None
        if telegram_message_id is not None:
            message_id = f"tg-{telegram_message_id}"
        elif dedup_id:
            message_id = f"dedup-{dedup_id}"
        else:
            return None
        from relationship_v2.integration.operator_events import build_response_sent
        from relationship_v2.services.event_processor import process_event

        event = build_response_sent(
            creator_id,
            user_id,
            message_id,
            generation_id or f"no-gen-{message_id}",
            source,
            was_edited=was_edited,
            queue_id=queue_id,
        )
        return await process_event(
            event,
            is_processed=is_processed,
            get_or_create=get_or_create,
            create_event=create_event,
            mark_processed=mark_processed,
        )
    except Exception:
        logger.debug("v2 send hook skipped (fail-open)", exc_info=True)
        return None
