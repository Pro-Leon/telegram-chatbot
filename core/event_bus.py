import logging
import time
import uuid
from typing import Sequence

try:
    import orjson as _json

    def _json_dumps(obj: dict) -> str:
        return _json.dumps(obj).decode()
except ImportError:
    import json as _json  # type: ignore[no-redef]

    def _json_dumps(obj: dict) -> str:
        return _json.dumps(obj)

from core.config import get_settings

logger = logging.getLogger("event_bus")

CHANNEL = "chatbot:events"


async def publish_event(
    event_type: str,
    data: dict,
    *,
    user_id: int | None = None,
    dialog_id: int | None = None,
    generation_id: str | None = None,
    scope: str = "global",
    creator_id: int | None = None,
) -> str | None:
    """Publish a real-time event to the Redis Pub/Sub channel.

    This is a best-effort operation. Failures are logged but never
    propagate to the caller — business logic must never depend on
    event publication succeeding.

    Returns the event_id on success, None on failure.
    """
    settings = get_settings()
    if not settings.enable_websocket:
        return None

    event_id = str(uuid.uuid4())
    event = {
        "event_id": event_id,
        "event_type": event_type,
        "timestamp_ms": int(time.time() * 1000),
        "user_id": user_id,
        "dialog_id": dialog_id,
        "generation_id": generation_id,
        "creator_id": creator_id,
        "scope": scope,
        "data": data,
    }

    try:
        from db.redis import get_redis

        r = await get_redis()
        await r.publish(CHANNEL, _json_dumps(event))
        return event_id
    except Exception:
        logger.warning("Failed to publish event %s (id=%s)", event_type, event_id, exc_info=True)
        return None


def _build_event(
    event_type: str,
    data: dict,
    *,
    user_id: int | None = None,
    dialog_id: int | None = None,
    generation_id: str | None = None,
    scope: str = "global",
    creator_id: int | None = None,
) -> dict:
    """Build an event dict (no I/O)."""
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "timestamp_ms": int(time.time() * 1000),
        "user_id": user_id,
        "dialog_id": dialog_id,
        "generation_id": generation_id,
        "creator_id": creator_id,
        "scope": scope,
        "data": data,
    }


async def publish_events_batch(
    events: Sequence[dict],
) -> list[str]:
    """Publish multiple events via a single Redis PUBLISH pipeline call.

    Each event dict must have keys: event_type, data, and optional
    user_id, dialog_id, generation_id, scope, creator_id.

    Returns a list of event_ids (None for any that failed to build).
    Best-effort: failures are logged, never raised.
    """
    settings = get_settings()
    if not settings.enable_websocket:
        return [None] * len(events)

    built = []
    event_ids = []
    for ev in events:
        eid = str(uuid.uuid4())
        event_ids.append(eid)
        # Backward compat: worker historically used "event", canonical is "event_type"
        _etype = ev.get("event_type") or ev.get("event")
        if not _etype:
            logger.warning("publish_events_batch: event missing event_type/event key: %s", ev)
            continue
        built.append({
            "event_id": eid,
            "event_type": _etype,
            "timestamp_ms": int(time.time() * 1000),
            "user_id": ev.get("user_id"),
            "dialog_id": ev.get("dialog_id"),
            "generation_id": ev.get("generation_id"),
            "creator_id": ev.get("creator_id"),
            "scope": ev.get("scope", "global"),
            "data": ev["data"],
        })

    try:
        from db.redis import get_redis

        r = await get_redis()
        pipe = r.pipeline(transaction=False)
        for ev in built:
            pipe.publish(CHANNEL, _json_dumps(ev))
        await pipe.execute()
        return event_ids
    except Exception:
        logger.warning("Failed to publish event batch (%d events)", len(built), exc_info=True)
        return [None] * len(events)
