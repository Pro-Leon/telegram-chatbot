import json

import redis.asyncio as redis

from core.config import get_settings

_settings = get_settings()

_client: redis.Redis | None = None

INBOUND_STREAM = "inbound_messages"
DRAFT_STREAM = "draft_messages"
SEND_STREAM = "send_messages"
DLQ_STREAM = "dead_letter_queue"


async def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(
            _settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


CONSUMER_GROUP = "llm_workers"


async def ensure_consumer_group() -> None:
    r = await get_redis()
    try:
        await r.xgroup_create(INBOUND_STREAM, CONSUMER_GROUP, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def enqueue_inbound(message_data: dict) -> str:
    r = await get_redis()
    msg_id = await r.xadd(INBOUND_STREAM, message_data)
    return msg_id


async def read_inbound(
    consumer_name: str,
    count: int = 10,
    block_ms: int = 2000,
) -> list[tuple[str, list[tuple[str, dict]]]]:
    r = await get_redis()
    try:
        messages = await r.xreadgroup(
            CONSUMER_GROUP,
            consumer_name,
            {INBOUND_STREAM: ">"},
            count=count,
            block=block_ms,
        )
        return messages or []
    except redis.ResponseError:
        return []


async def ack_inbound(message_id: str) -> None:
    r = await get_redis()
    await r.xack(INBOUND_STREAM, CONSUMER_GROUP, message_id)


async def requeue_stalled_messages(consumer_name: str, idle_ms: int = 30000) -> int:
    r = await get_redis()
    try:
        result = await r.xautoclaim(
            INBOUND_STREAM,
            CONSUMER_GROUP,
            consumer_name,
            "0",
            minidle=idle_ms,
            count=10,
        )
        if result and result[1]:
            return len(result[1])
        return 0
    except (redis.ResponseError, IndexError):
        return 0


async def move_to_dlq(message_id: str, reason: str) -> None:
    r = await get_redis()
    await r.xadd(DLQ_STREAM, {"message_id": message_id, "reason": reason})
    await r.xack(INBOUND_STREAM, CONSUMER_GROUP, message_id)


async def acquire_user_lock(user_id: int, ttl: int = 30) -> bool:
    r = await get_redis()
    result = await r.set(
        f"lock:user:{user_id}",
        "1",
        nx=True,
        ex=ttl,
    )
    return result is True


async def release_user_lock(user_id: int) -> None:
    r = await get_redis()
    await r.delete(f"lock:user:{user_id}")


async def debounce_enqueue(
    user_id: int,
    content: str,
    message_data: dict,
    window_seconds: int = 3,
) -> bool:
    r = await get_redis()
    key = f"debounce:{user_id}"
    lock_key = f"{key}:lock"

    is_window_owner = await r.set(lock_key, "1", nx=True, ex=window_seconds)
    await r.rpush(f"{key}:messages", json.dumps(message_data))

    if is_window_owner:
        await r.expire(f"{key}:messages", window_seconds + 10)
        return True
    return False


async def get_debounced_messages(user_id: int) -> list[dict]:
    r = await get_redis()
    key = f"debounce:{user_id}:messages"
    raw = await r.lrange(key, 0, -1)
    await r.delete(key)
    return [json.loads(m) for m in raw]


async def check_rate_limit(user_id: int, max_per_minute: int = 20) -> bool:
    r = await get_redis()
    key = f"ratelimit:{user_id}"
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, 60)
    return count <= max_per_minute


async def cache_user_context(user_id: int, context: dict, ttl: int = 300) -> None:
    r = await get_redis()
    await r.setex(f"context:{user_id}", ttl, json.dumps(context))


async def get_cached_context(user_id: int) -> dict | None:
    r = await get_redis()
    data = await r.get(f"context:{user_id}")
    return json.loads(data) if data else None


async def invalidate_context_cache(user_id: int) -> None:
    r = await get_redis()
    await r.delete(f"context:{user_id}")
