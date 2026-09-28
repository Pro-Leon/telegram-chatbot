import asyncio
import json
import logging

from core.config import get_settings
from core.event_bus import CHANNEL

logger = logging.getLogger("event_subscriber")

MAX_BACKOFF = 30.0
INITIAL_BACKOFF = 1.0


async def start_event_subscriber() -> None:
    """Background task that subscribes to Redis Pub/Sub and broadcasts events.

    Handles Redis disconnects with bounded exponential backoff.
    Malformed events are logged and skipped.
    """
    from chatbotv2.dashboard.ws_manager import get_manager

    settings = get_settings()
    if not settings.enable_websocket:
        logger.info("WebSocket disabled, event subscriber not starting")
        return

    backoff = INITIAL_BACKOFF
    r = None
    pubsub = None

    while True:
        try:
            import redis.asyncio as redis

            r = redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            pubsub = r.pubsub()
            await pubsub.subscribe(CHANNEL)
            logger.info("Subscribed to Redis channel: %s", CHANNEL)
            backoff = INITIAL_BACKOFF

            async for raw_msg in pubsub.listen():
                if raw_msg["type"] != "message":
                    continue

                try:
                    event = json.loads(raw_msg["data"])
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Malformed event JSON, skipping")
                    continue

                event_type = event.get("event_type")
                if not event_type:
                    logger.warning("Event missing event_type, skipping")
                    continue

                dialog_id = event.get("dialog_id")
                scope = event.get("scope", "global")
                creator_id = event.get("creator_id")

                manager = get_manager()
                await manager.broadcast(event, dialog_id=dialog_id if scope == "user" else None, creator_id=creator_id)

        except asyncio.CancelledError:
            logger.info("Event subscriber shutting down")
            break
        except Exception:
            logger.warning("Event subscriber error, reconnecting in %.1fs", backoff, exc_info=True)
            try:
                if pubsub is not None:
                    await pubsub.unsubscribe()
                    await pubsub.close()
            except Exception:  # noqa: BLE001, S110
                pass
            try:
                if r is not None:
                    await r.close()
            except Exception:  # noqa: BLE001, S110
                pass
            r = None
            pubsub = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

    if pubsub is not None:
        try:
            await pubsub.unsubscribe(CHANNEL)
        except Exception:  # noqa: BLE001, S110
            pass
    if r is not None:
        try:
            await r.close()
        except Exception:  # noqa: BLE001, S110
            pass
