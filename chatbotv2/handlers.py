import asyncio
import logging

from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat, User

from chatbotv2.client import get_client
from chatbotv2.config import get_settings
from core.generation import ensure_generation_id, telegram_generation_id
from db.postgres import get_default_persona, get_user_persona, save_inbound_message, upsert_user
from db.redis import (
    DebounceConsumeError,
    cache_default_persona,
    cache_user_persona,
    check_rate_limit,
    debounce_consume,
    debounce_enqueue,
    enqueue_inbound,
    get_cached_default_persona,
    get_cached_user_persona,
    get_debounced_messages,
)

logger = logging.getLogger("chatbotv2.handlers")
_settings = get_settings()

#: Phase 2.1: inbound length cap (mirrors OneCallReply max_length=2000).
MAX_INBOUND_CHARS = 2000


def normalize_inbound_text(raw: object) -> tuple[str | None, bool]:
    """Normalize raw inbound message text (pure, never raises).

    Returns ``(text, truncated)``; ``text`` is ``None`` when the message
    must be dropped (non-string input such as stickers/media, or empty
    after stripping). Over-long text is truncated to MAX_INBOUND_CHARS
    with ``truncated=True`` (flag, not a block).
    """
    try:
        if not isinstance(raw, str):
            return None, False
        text = raw.strip()
        if not text:
            return None, False
        if len(text) > MAX_INBOUND_CHARS:
            return text[:MAX_INBOUND_CHARS], True
        return text, False
    except Exception:
        return None, False


async def handle_incoming_message(event: events.NewMessage.Event) -> None:
    if event.sender_id is None:
        return

    # Sunny V1 cutover (Phase 3): legacy conversational ingress is DISABLED.
    # Commerce provider paths (webhooks/polling/reconciliation/scheduler) do
    # not enter through here and are unaffected. Server-side flag only.
    try:
        from core.architecture_router import is_v1_conversational_enabled, log_v1_suppressed

        if not is_v1_conversational_enabled():
            log_v1_suppressed("handlers.handle_incoming_message", user_id=event.sender_id)
            return
    except Exception:
        # Fail closed: an unreadable gate must not resurrect V1.
        logger.warning("V1 gate unreadable at ingress — failing closed")
        return

    try:
        sender = await event.get_sender()
        if isinstance(sender, (Channel, Chat)):
            return

        if not isinstance(sender, User) or sender.bot:
            return

        user_id = event.sender_id
        username = sender.username or ""
        first_name = sender.first_name or ""

        # Phase 2.1 input rails: normalize once; drops never reach
        # save/event/debounce/enqueue. Truncation is a flag, not a block.
        _raw_text = event.message.message
        _inbound_text, _inbound_truncated = normalize_inbound_text(_raw_text)
        if _inbound_text is None:
            logger.debug("Dropping non-text/empty inbound user=%s", user_id)
            return
        if _inbound_truncated:
            logger.info(
                "Inbound truncated user=%s raw_len=%d",
                user_id,
                len(_raw_text) if isinstance(_raw_text, str) else -1,
            )
            try:
                from commerce.production_control import record_metric as _rc_trunc

                _rc_trunc(name="inbound_truncated", creator_id=None, user_id=user_id, value=1.0)
            except Exception:
                pass
        _message_text = _inbound_text

        allowed = await check_rate_limit(user_id, _settings.rate_limit_per_minute)

        allowed = await check_rate_limit(user_id, _settings.rate_limit_per_minute)
        if not allowed:
            try:
                await event.reply("⚠️ You're sending messages too fast. Please slow down.")
            except Exception:
                logger.exception("Failed to send rate limit reply")
            return

        # If this entity was previously blacklisted, inbound proves it's reachable again
        try:
            from core.entity_blacklist import unblacklist_entity

            await unblacklist_entity(user_id)
        except Exception:
            logger.debug("Blacklist uncheck failed for user=%s", user_id, exc_info=True)

        await upsert_user(user_id, username, first_name)

        from core.event_bus import publish_event

        # P0-3: deterministic generation_id at intake, preserved through debounce
        # Canonical helper (core/generation.py); algorithm unchanged: md5(user:content:tgId).
        generation_id = telegram_generation_id(user_id, _message_text, event.message.id)

        # Resolve creator for creator-scoped event, debounce, and history isolation (Phase 43F: must be before save)
        # P1.4: creator_id is REQUIRED for creator-owned inbound – authoritative
        _creator_id = None
        try:
            from commerce.single_creator import resolve_single_application_creator, SingleCreatorStatus
            _ctx = await resolve_single_application_creator()
            if _ctx.status == SingleCreatorStatus.READY and _ctx.creator_id is not None:
                _creator_id = _ctx.creator_id
        except Exception:
            _creator_id = None

        if _creator_id is None:
            logger.warning("creator_id unavailable for inbound user=%s – failing closed, not persisting inbound (P1.4) – routing to DLQ (P1.6 R-04)", user_id)
            # P1.6 R-04: creator resolution failure must be observable/recoverable, not silently dropped
            try:
                from db.redis import DLQ_STREAM, get_redis
                import json as _json
                import time as _time

                r = await get_redis()
                payload = {
                    "user_id": str(user_id),
                    "content": _message_text,
                    "telegram_message_id": str(event.message.id),
                    "username": username,
                    "first_name": first_name,
                    "generation_id": generation_id,
                }
                record = {
                    "message_id": f"ingress:{generation_id}",
                    "reason": "creator_context_unavailable",
                    "stream": "inbound",
                    "failure_timestamp": str(int(_time.time())),
                    "replay_count": "0",
                    "payload": _json.dumps(payload, default=str),
                    "worker_id": "ingress",
                }
                await r.xadd(DLQ_STREAM, record)
            except Exception:
                logger.exception("Failed to DLQ ingress message for user=%s reason=creator_context_unavailable", user_id)
            try:
                await publish_event(
                    "ai.generation_failed",
                    {"error": "creator_context_unavailable"},
                    user_id=user_id,
                    dialog_id=user_id,
                    generation_id=generation_id,
                    scope="user",
                )
            except Exception:
                pass
            return

        message_id = await save_inbound_message(
            user_id=user_id,
            content=_message_text,
            telegram_message_id=event.message.id,
            creator_id=_creator_id,
            generation_id=generation_id,
        )

        await publish_event(
            "message.created",
            {
                "message_id": message_id,
                "telegram_message_id": event.message.id,
                "content": _message_text,
                "direction": "inbound",
                "username": username,
                "first_name": first_name,
            },
            user_id=user_id,
            dialog_id=user_id,
            generation_id=generation_id,
            creator_id=_creator_id,
            scope="user",
        )

        # M2: debounce_enqueue returns the owner fence token (truthy) when this
        # message elected the window owner, else "" (falsy). Only the owner
        # spawns the delayed consumer, and the token fences it against a
        # successor window (stale timers abdicate without consuming).
        owner_fence = await debounce_enqueue(
            user_id=user_id,
            content=_message_text,
            message_data={
                "user_id": str(user_id),
                "content": _message_text,
                "telegram_message_id": str(event.message.id),
                "username": username,
                "first_name": first_name,
                "generation_id": generation_id,
                "creator_id": str(_creator_id),
            },
            window_seconds=_settings.debounce_window_seconds,
            creator_id=_creator_id,
        )

        if not owner_fence:
            client = await get_client()
            try:
                await client.send_typing(await event.get_input_chat(), User(user_id))
            except Exception:
                logger.exception("Failed to send typing indicator")
            return

        asyncio.create_task(_wait_and_process(user_id, username, first_name, creator_id=_creator_id, fence=owner_fence))

    except Exception:
        logger.exception("Error handling incoming message from %s", event.sender_id)


async def _wait_and_process(
    user_id: int,
    username: str,
    first_name: str,
    creator_id: int | None = None,
    fence: str | None = None,
) -> None:
    await asyncio.sleep(_settings.debounce_window_seconds)

    # P1.4: creator_id is authoritative – do not re-resolve if already supplied; if still None, fail closed
    if creator_id is None:
        logger.warning("creator_id missing in _wait_and_process for user=%s – failing closed (P1.4)", user_id)
        # P1.6 R-04: also DLQ any buffered messages when creator missing at processing time
        try:
            from db.redis import DLQ_STREAM, get_redis
            import json as _json
            import time as _time

            # Attempt to retrieve buffered messages without creator (legacy fallback) for DLQ preservation
            # If get_debounced_messages requires creator, we synthesize DLQ from raw redis scan
            r = await get_redis()
            # Scan for any debounce keys for this user without creator (legacy) – best-effort
            # Primary recoverable path is already handled at ingress, this is defense-in-depth
            record = {
                "message_id": f"ingress:debounce:{user_id}:{int(_time.time())}",
                "reason": "creator_context_unavailable",
                "stream": "inbound",
                "failure_timestamp": str(int(_time.time())),
                "replay_count": "0",
                "payload": _json.dumps({"user_id": str(user_id), "creator_id": "", "reason": "creator_missing_at_debounce"}, default=str),
                "worker_id": "debounce",
            }
            await r.xadd(DLQ_STREAM, record)
        except Exception:
            logger.debug("Failed to DLQ debounce creator-less payload for user=%s", user_id, exc_info=True)
        return

    # M2 fenced consume: the timer proves window ownership with the fence
    # token minted at election. A stale timer (successor window elected while
    # this task slept) abdicates without reading or removing anything.
    # Redis failure also abdicates, leaving the buffer for bounded recovery.
    if fence:
        try:
            consume_status, debounced = await debounce_consume(user_id, creator_id, fence)
        except DebounceConsumeError:
            logger.warning(
                "debounce consume failed for user=%s creator=%s – leaving buffer for recovery",
                user_id,
                creator_id,
            )
            return
        if consume_status != "ok" or not debounced:
            logger.debug(
                "debounce window not consumed (status=%s) for user=%s creator=%s",
                consume_status,
                user_id,
                creator_id,
            )
            return
    else:
        # Legacy unfenced path (kept for backward-compatible direct callers;
        # production ingress always supplies a fence).
        debounced = await get_debounced_messages(user_id, creator_id=creator_id)
        if not debounced:
            return

    latest = debounced[-1]
    first = debounced[0] if debounced else latest
    user_id = int(latest.get("user_id", user_id))

    # Phase 2.3: full-window merge. Earlier burst messages were saved per-msg
    # for audit at ingress; the LLM turn carries all of them in buffer order.
    # Single-message windows are byte-identical to latest-only. Window identity
    # (generation_id, tg reservation) stays the first message's (window owner).
    try:
        _merged_parts = [
            str(m.get("content", ""))
            for m in debounced
            if isinstance(m, dict) and str(m.get("content", "")).strip()
        ]
        merged_content = "\n".join(_merged_parts) if _merged_parts else str(latest.get("content", ""))
    except Exception:
        merged_content = str(latest.get("content", ""))

    # Creator-scoped persona resolution — P0 isolation
    persona = await get_cached_user_persona(user_id, creator_id=creator_id)
    if persona is None:
        persona = await get_user_persona(user_id, creator_id=creator_id)
        if persona:
            await cache_user_persona(user_id, persona, creator_id=creator_id)
    if not persona:
        persona = await get_cached_default_persona(creator_id=creator_id)
        if persona is None:
            persona = await get_default_persona(creator_id=creator_id) or ""
            if persona:
                await cache_default_persona(persona, creator_id=creator_id)
    # Phase 2.1: enqueue_inbound raises on XADD failure AFTER the debounce
    # buffer was consumed. This handler runs as a bare create_task, so an
    # escaping exception would silently lose the turn: log + metric, no raise.
    # (Other enqueue_inbound callers keep the raise.)
    # Phase 2.3: merged content + first-message window identity below.
    try:
        await enqueue_inbound(
            {
                "user_id": str(user_id),
                "content": merged_content,
                "telegram_message_id": str(first.get("telegram_message_id", 0)),
                "username": latest.get("username", username),
                "first_name": latest.get("first_name", first_name),
                "persona": persona,
                # First message's generation_id is authoritative (window owner);
                # recompute only as recovery when genuinely absent.
                "generation_id": ensure_generation_id(
                    first.get("generation_id"),
                    user_id=first.get("user_id", user_id),
                    content=first.get("content", ""),
                    telegram_message_id=first.get("telegram_message_id", 0),
                )
                or "",
                "creator_id": str(creator_id),
            }
        )
    except Exception:
        logger.exception(
            "Inbound enqueue failed user=%s creator=%s — turn lost after consume",
            user_id,
            creator_id,
        )
        try:
            from commerce.production_control import record_metric as _rc_enq_fail

            _rc_enq_fail(name="inbound_enqueue_failed", creator_id=creator_id, user_id=user_id, value=1.0)
        except Exception:
            pass
        return


def setup_handlers(client: TelegramClient) -> None:
    @client.on(events.NewMessage(incoming=True))
    async def _handler(event: events.NewMessage.Event) -> None:
        await handle_incoming_message(event)
