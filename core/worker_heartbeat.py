"""Lightweight Redis-backed worker heartbeat.

Workers write a heartbeat key periodically. The dashboard can check
worker liveness by inspecting these keys. Keys auto-expire via TTL.
"""

import asyncio
import json
import logging
import time

logger = logging.getLogger("heartbeat")

DEFAULT_INTERVAL_SECONDS = 10
DEFAULT_TTL_SECONDS = 30


async def write_heartbeat(
    worker_id: str,
    worker_type: str = "unknown",
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Periodically write a heartbeat key to Redis until stopped.

    Args:
        worker_id: unique identifier for this worker instance
        worker_type: category like 'llm', 'send', 'bot_main'
        interval_seconds: how often to refresh the heartbeat
        ttl_seconds: Redis key TTL — auto-expires if worker dies
        stop_event: optional asyncio.Event to signal shutdown
    """
    from db.redis import get_redis

    key = f"worker:heartbeat:{worker_id}"
    while True:
        if stop_event and stop_event.is_set():
            break
        try:
            r = await get_redis()
            payload = json.dumps(
                {
                    "worker_id": worker_id,
                    "worker_type": worker_type,
                    "timestamp": time.time(),
                }
            )
            await r.set(key, payload, ex=ttl_seconds)
        except Exception:
            logger.debug("Heartbeat write failed for %s", worker_id, exc_info=True)

        try:
            if stop_event:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                break
            else:
                await asyncio.sleep(interval_seconds)
        except TimeoutError:
            pass


async def read_worker_status(worker_id: str) -> dict | None:
    """Read a worker's heartbeat and return status info."""
    from db.redis import get_redis

    r = await get_redis()
    key = f"worker:heartbeat:{worker_id}"
    raw = await r.get(key)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    age = time.time() - data.get("timestamp", 0)
    ttl = await r.ttl(key)
    return {
        "worker_id": data.get("worker_id", worker_id),
        "worker_type": data.get("worker_type", "unknown"),
        "last_heartbeat": data.get("timestamp"),
        "age_seconds": round(age, 1),
        "ttl_seconds": ttl,
    }


async def list_active_workers(prefix: str = "worker:heartbeat:") -> list[dict]:
    """List all workers with active heartbeat keys."""
    from db.redis import get_redis

    r = await get_redis()
    workers = []
    async for key in r.scan_iter(f"{prefix}*"):
        worker_id = key.removeprefix(prefix)
        status = await read_worker_status(worker_id)
        if status:
            status["status"] = "alive" if status["ttl_seconds"] > 0 else "stale"
            workers.append(status)
    return workers


async def remove_heartbeat(worker_id: str) -> None:
    """Remove a worker's heartbeat key (called on clean shutdown)."""
    from db.redis import get_redis

    try:
        r = await get_redis()
        await r.delete(f"worker:heartbeat:{worker_id}")
    except Exception:
        logger.debug("Heartbeat cleanup failed for %s", worker_id, exc_info=True)
