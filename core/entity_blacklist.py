"""Entity blacklist — permanently unresolvable Telegram peers.

When entity resolution fails permanently (ValueError on get_input_entity,
not a transient FloodWait), we blacklist that entity so:
  1. send_worker stops re-enqueueing operator_queue items for that user
  2. _process_send_stream short-circuits without calling Telegram at all
  3. The blacklist auto-heals: entries expire after 24h (the entity might
     become reachable if the user messages the bot, rejoining a group, etc.)

Storage:
  - Redis set  : blacklist:entities           (fast membership check)
  - Redis hash : blacklist:meta:<entity_id>  (reason, timestamp, fail_count)
  - Fallback   : in-memory set (if Redis unavailable)

The blacklist is NOT a ban — it only suppresses SEND attempts. Inbound
messages from a blacklisted entity are still processed normally (which
also removes the entity from the blacklist via unblacklist_entity).
"""

import logging
import time

logger = logging.getLogger("entity_blacklist")

# Redis keys
_BLACKLIST_SET = "blacklist:entities"
_BLACKLIST_META_PREFIX = "blacklist:meta"
# How long an entry lives before auto-expiry
_BLACKLIST_TTL_SECONDS = 24 * 3600  # 24 hours

# In-memory fallback (if Redis unavailable)
_in_memory_blacklist: set[str] = set()


async def is_blacklisted(entity_id: str | int) -> bool:
    """Check if an entity is blacklisted (permanently unresolvable)."""
    key = str(entity_id)
    try:
        from db.redis import get_redis

        r = await get_redis()
        return await r.sismember(_BLACKLIST_SET, key) == 1
    except Exception:
        # Fallback to in-memory
        return key in _in_memory_blacklist


async def blacklist_entity(
    entity_id: str | int,
    reason: str = "entity_not_found",
    fail_count: int = 1,
) -> None:
    """Add an entity to the blacklist.

    Best-effort: never raises.
    """
    key = str(entity_id)
    try:
        from db.redis import get_redis

        r = await get_redis()
        pipe = r.pipeline()
        pipe.sadd(_BLACKLIST_SET, key)
        pipe.expire(_BLACKLIST_SET, _BLACKLIST_TTL_SECONDS)
        meta_key = f"{_BLACKLIST_META_PREFIX}:{key}"
        pipe.hset(
            meta_key,
            mapping={
                "reason": reason,
                "blacklisted_at": str(int(time.time())),
                "fail_count": str(fail_count),
            },
        )
        pipe.expire(meta_key, _BLACKLIST_TTL_SECONDS)
        await pipe.execute()
        logger.warning(
            "entity_blacklist: blacklisted entity=%s reason=%s (ttl=%ds)",
            key,
            reason,
            _BLACKLIST_TTL_SECONDS,
        )
    except Exception:
        logger.debug("entity_blacklist: Redis blacklist failed, using in-memory", exc_info=True)
        _in_memory_blacklist.add(key)
        logger.warning("entity_blacklist: blacklisted entity=%s (in-memory) reason=%s", key, reason)


async def unblacklist_entity(entity_id: str | int) -> bool:
    """Remove an entity from the blacklist (e.g. when they message the bot again).

    Returns True if the entity was blacklisted.
    """
    key = str(entity_id)
    try:
        from db.redis import get_redis

        r = await get_redis()
        removed = await r.srem(_BLACKLIST_SET, key)
        meta_key = f"{_BLACKLIST_META_PREFIX}:{key}"
        await r.delete(meta_key)
        if removed:
            logger.info("entity_blacklist: unblacklisted entity=%s", key)
        _in_memory_blacklist.discard(key)
        return bool(removed)
    except Exception:
        was_present = key in _in_memory_blacklist
        _in_memory_blacklist.discard(key)
        if was_present:
            logger.info("entity_blacklist: unblacklisted entity=%s (in-memory)", key)
        return was_present


async def get_blacklist_size() -> int:
    """Return the number of blacklisted entities."""
    try:
        from db.redis import get_redis

        r = await get_redis()
        return await r.scard(_BLACKLIST_SET)
    except Exception:
        return len(_in_memory_blacklist)


async def list_blacklisted() -> list[str]:
    """Return all blacklisted entity IDs."""
    try:
        from db.redis import get_redis

        r = await get_redis()
        members = await r.smembers(_BLACKLIST_SET)
        return list(members) if members else []
    except Exception:
        return list(_in_memory_blacklist)


def reset_for_testing() -> None:
    """Clear in-memory blacklist (for tests)."""
    _in_memory_blacklist.clear()
