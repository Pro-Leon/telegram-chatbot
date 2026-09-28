"""Turn guard (Stage E2). Scoped critical sections for V2 mutations.

Caller: FUTURE turn orchestrator / workers around generation->enqueue and
event-commit sections. Composes `queue.acquire/release_scope_lock`
(`v2:lock:{creator}:{user}`, bounded TTL, Redis PRESERVED infra).

Semantics (IDEMPOTENCY_AND_CONCURRENCY.md):
- Lock acquired -> run fn, always release in `finally`; fn errors propagate
  after release so idempotent retries stay safe.
- Lock contended -> return not-acquired; the caller DEFERS (no ACK-loss),
  never double-processes.
- V2 never acquires legacy `lock:user:` keys and legacy never acquires V2
  keys; cross-system serialization of the full inbound->approval->send->
  commit chain lands with the turn orchestrator (Stage F open item).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

from relationship_v2.services.queue import (
    LOCK_TTL_SECONDS,
    LockAcquirePort,
    LockReleasePort,
    acquire_scope_lock,
    release_scope_lock,
)

logger = logging.getLogger("sunny.v2.turn_guard")


class GuardResult(BaseModel):
    acquired: bool
    result: Any = None
    reason: str = Field(min_length=1)

    model_config = {"frozen": True, "arbitrary_types_allowed": True}


async def run_guarded(
    creator_id: int,
    user_id: int,
    fn: Callable[[], Awaitable[Any]],
    *,
    ttl_seconds: int = LOCK_TTL_SECONDS,
    acquire: LockAcquirePort | None = None,
    release: LockReleasePort | None = None,
) -> GuardResult:
    """Run `fn` under the V2 scope lock. Never runs `fn` on contention."""
    if creator_id <= 0 or user_id <= 0:
        raise ValueError("scope must be positive (fail-closed)")
    if not callable(fn):
        raise TypeError("fn must be callable")
    held = await acquire_scope_lock(
        creator_id, user_id, ttl_seconds=ttl_seconds, acquire=acquire
    )
    if not held:
        return GuardResult(acquired=False, reason="lock_contended_defer")
    try:
        return GuardResult(acquired=True, result=await fn(), reason="executed")
    finally:
        try:
            await release_scope_lock(creator_id, user_id, release=release)
        except Exception:
            logger.warning("scope lock release failed (TTL bounds it)", exc_info=True)
