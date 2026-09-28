"""Queue / concurrency / idempotency (Phase 9).

Caller: FUTURE turn orchestrator + workers (Phase 10 shadow, Phase 11 live).
Owner of: V2 scoped-lock namespace + validated-turn enqueue flow.
Producer: conversation.updated / conversation.failed DLQ records.

Strategies (IDEMPOTENCY_AND_CONCURRENCY.md):
- Optimistic concurrency for memory/state (version check + retry/replan).
- Pessimistic scoped locks ONLY around generation->enqueue critical section.
- All locks bounded TTL, V2-namespaced keys (never legacy lock keys).
- Retries bounded with backoff; DLQ with replay limits.
- Redis is PRESERVED shared infra (db.redis); V2 keys are namespaced.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from relationship_v2.domain.response import ValidationOutcome, ValidationVerdict

logger = logging.getLogger("sunny.v2.queue")

LOCK_TTL_SECONDS = 30
MAX_REPLAY_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 2
BACKOFF_MAX_SECONDS = 60


def lock_key(creator_id: int, user_id: int) -> str:
    """V2 lock namespace. Never the legacy lock:user: key."""
    return f"v2:lock:{creator_id}:{user_id}"


LockAcquirePort = Callable[[str, int], Awaitable[bool]]
LockReleasePort = Callable[[str], Awaitable[None]]


async def acquire_scope_lock(
    creator_id: int,
    user_id: int,
    ttl_seconds: int = LOCK_TTL_SECONDS,
    acquire: LockAcquirePort | None = None,
) -> bool:
    """Pessimistic lock for the generation->enqueue critical section only."""
    if creator_id <= 0 or user_id <= 0:
        raise ValueError("scope must be positive (fail-closed)")
    if ttl_seconds <= 0 or ttl_seconds > 300:
        raise ValueError("lock TTL must be bounded (1..300s)")
    if acquire is None:
        acquire = _redis_acquire
    return await acquire(lock_key(creator_id, user_id), ttl_seconds)


async def release_scope_lock(
    creator_id: int,
    user_id: int,
    release: LockReleasePort | None = None,
) -> None:
    if release is None:
        release = _redis_release
    await release(lock_key(creator_id, user_id))


async def _redis_acquire(key: str, ttl: int) -> bool:
    from db.redis import get_redis

    r = await get_redis()
    return await r.set(key, "1", nx=True, ex=ttl) is True


async def _redis_release(key: str) -> None:
    from db.redis import get_redis

    r = await get_redis()
    await r.delete(key)


def backoff_seconds(attempt: int) -> int:
    """Bounded exponential backoff. Attempt counts from 1."""
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    return min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))


def should_replay(attempt: int, max_attempts: int = MAX_REPLAY_ATTEMPTS) -> bool:
    """DLQ replay gate. Beyond the limit -> suppress with audit, never loop."""
    return 1 <= attempt <= max_attempts


def is_stale_memory_write(existing_applied_at: object, incoming_source_at: object) -> bool:
    """Source-order gating: older sources cannot overwrite newer applied state."""
    try:
        return incoming_source_at < existing_applied_at  # type: ignore[operator]
    except TypeError:
        raise ValueError("unorderable timestamps (fail-closed)")


async def enqueue_validated_turn(
    creator_id: int,
    user_id: int,
    relationship_id: UUID,
    conversation_id: UUID,
    generation_id: str,
    inbound_event_id: str,
    plan_hash_value: str,
    verdict: ValidationVerdict,
    routing_decision: str,
) -> dict:
    """Enqueue a VALIDATED turn. Deduped on (conversation, generation).

    Requires a VALID verdict; anything else raises (never enqueues review
    items silently). Idempotent: redelivery returns the existing row.
    """
    from relationship_v2.persistence.repository import (
        create_conversation_turn,
        create_relationship_event,
    )

    if creator_id <= 0 or user_id <= 0:
        raise ValueError("scope must be positive (fail-closed)")
    if verdict.outcome != ValidationOutcome.VALID:
        raise ValueError(f"only VALID turns enqueue (got {verdict.outcome.value})")
    for name, val in (
        ("generation_id", generation_id),
        ("inbound_event_id", inbound_event_id),
        ("plan_hash", plan_hash_value),
        ("routing_decision", routing_decision),
    ):
        if not val:
            raise ValueError(f"{name} required")
    turn = await create_conversation_turn(
        creator_id=creator_id,
        user_id=user_id,
        relationship_id=relationship_id,
        conversation_id=conversation_id,
        generation_id=generation_id,
        inbound_event_id=inbound_event_id,
        plan_hash=plan_hash_value,
        validation_verdict=verdict.outcome.value,
        routing_decision=routing_decision,
    )
    event_id = f"turn-enq-{conversation_id}-{generation_id}"
    await create_relationship_event(
        event_id=event_id,
        event_type="conversation.updated",
        creator_id=creator_id,
        user_id=user_id,
        idempotency_key=event_id,
        producer="relationship_v2.services.queue",
        payload={"turn_id": str(turn["id"]), "verdict": verdict.outcome.value},
        relationship_id=relationship_id,
        conversation_id=conversation_id,
        generation_id=generation_id,
    )
    return turn


async def record_turn_failure(
    creator_id: int,
    user_id: int,
    relationship_id: UUID | None,
    conversation_id: UUID | None,
    generation_id: str,
    reason: str,
    attempt: int,
) -> dict:
    """DLQ record: conversation.failed with replay accounting. Never loses turns."""
    from relationship_v2.persistence.repository import create_relationship_event

    if not reason or not generation_id:
        raise ValueError("reason/generation_id required")
    if creator_id <= 0 or user_id <= 0:
        raise ValueError("scope must be positive (fail-closed)")
    replayable = should_replay(attempt)
    event_id = f"turn-fail-{generation_id}-{attempt}"
    return await create_relationship_event(
        event_id=event_id,
        event_type="conversation.failed",
        creator_id=creator_id,
        user_id=user_id,
        idempotency_key=event_id,
        producer="relationship_v2.services.queue",
        payload={"reason": reason, "attempt": attempt, "replayable": replayable},
        relationship_id=relationship_id,
        conversation_id=conversation_id,
        generation_id=generation_id,
    )
