"""Conversation engine (Phase 4).

Caller: FUTURE turn orchestrator (Phase 5+ assembly, Phase 7 response).
Owner of: conversation lifecycle transitions on v2_conversations.
Producer: conversation.received / conversation.started / conversation.updated /
  conversation.failed / message.received (consumers FUTURE: assembly,
  observability). Phase 1 realtime events untouched (PRESERVED contract).

Covers CONVERSATION_ENGINE.md steps 1-2 + 4 + resumption/failure. Steps 3
(assembly), 5 (strategy), 6 (plan/generate/validate), 7 (enqueue) are FUTURE.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from relationship_v2.domain.conversation import (
    ConversationLifecycle,
    ConversationState,
    is_valid_conversation_transition,
)
from relationship_v2.domain.inbound import (
    InboundDecision,
    InboundEvent,
    InboundVerdict,
    RejectReason,
)

logger = logging.getLogger("sunny.v2.conversation_engine")


class TurnOutcome(str, Enum):
    ADVANCED = "advanced"
    REJECTED = "rejected"
    STALE = "stale"
    FAILED = "failed"


class TurnResult(BaseModel):
    outcome: TurnOutcome
    lifecycle: ConversationLifecycle
    reason: str = Field(min_length=1)

    model_config = {"frozen": True}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def classify_inbound(event: InboundEvent, seen_idempotency_keys: set[str]) -> InboundVerdict:
    """Step 1: idempotency + scope guards. Malformed never creates state."""
    if not event.idempotency_key or not event.inbound_event_id:
        return InboundVerdict(decision=InboundDecision.REJECT, reason=RejectReason.MALFORMED.value)
    if event.creator_id <= 0 or event.user_id <= 0:
        return InboundVerdict(
            decision=InboundDecision.REJECT, reason=RejectReason.UNKNOWN_SCOPE.value
        )
    if event.idempotency_key in seen_idempotency_keys:
        return InboundVerdict(decision=InboundDecision.DUPLICATE, reason="redelivery")
    return InboundVerdict(decision=InboundDecision.ACCEPT, reason="fresh")


def load_conversation_state(
    conversation_id: UUID,
    lifecycle: ConversationLifecycle,
    topic: str | None,
    version: int,
    as_of: datetime | None = None,
) -> ConversationState:
    """Step 2 (read path): creator-scoped snapshot load. Pure."""
    if version < 1:
        raise ValueError("version must be >= 1 (stale write guard)")
    return ConversationState(
        conversation_id=conversation_id,
        lifecycle=lifecycle,
        topic=topic,
        version=version,
        as_of=as_of or _utcnow(),
    )


def apply_turn(
    current: ConversationLifecycle,
    signal: str,
    expected_version: int,
    actual_version: int,
) -> TurnResult:
    """Step 4: guarded transition with optimistic version check.

    Signals: advance (normal turn), pause, resume, close, fail, recover.
    Invalid transitions rejected with codes; version mismatch -> stale.
    """
    if expected_version != actual_version:
        return TurnResult(
            outcome=TurnOutcome.STALE,
            lifecycle=current,
            reason=RejectReason.STALE_VERSION.value,
        )
    targets: dict[str, ConversationLifecycle] = {
        "advance": ConversationLifecycle.ACTIVE,
        "pause": ConversationLifecycle.PAUSED,
        "resume": ConversationLifecycle.RESUMED,
        "close": ConversationLifecycle.CLOSED,
        "fail": ConversationLifecycle.FAILED,
        "recover": ConversationLifecycle.ACTIVE,
    }
    if signal not in targets:
        return TurnResult(outcome=TurnOutcome.REJECTED, lifecycle=current, reason="unknown_signal")
    nxt = targets[signal]
    if is_valid_conversation_transition(current, nxt):
        outcome = (
            TurnOutcome.FAILED if nxt == ConversationLifecycle.FAILED else TurnOutcome.ADVANCED
        )
        return TurnResult(outcome=outcome, lifecycle=nxt, reason="guard_pass")
    return TurnResult(outcome=TurnOutcome.REJECTED, lifecycle=current, reason="invalid_transition")


def derive_topic(
    current_topic: str | None, explicit_change: bool, new_topic: str | None
) -> str | None:
    """Topic continuity: pivots explicit only. Short/empty never closes threads."""
    if explicit_change and new_topic and new_topic.strip():
        return new_topic.strip()[:128]
    return current_topic


def resume_context(
    topic: str | None,
    lifecycle: ConversationLifecycle,
    absence_days: int | None,
    last_activity_at: datetime | None,
) -> str:
    """Re-entry summary: restore full state, never a cold open."""
    base = f"resume {lifecycle.value}"
    if topic:
        base += f" on '{topic}'"
    if absence_days is not None and absence_days >= 3:
        base += f" after {absence_days}d absence"
    if last_activity_at is not None:
        base += f" (last {last_activity_at.isoformat()})"
    return base


async def transition_conversation(
    conversation_id: UUID,
    creator_id: int,
    user_id: int,
    expected_version: int,
    next_lifecycle: ConversationLifecycle,
    topic: str | None = None,
) -> dict | None:
    """Owner-side write: version-checked lifecycle update. None = stale conflict."""
    from db.postgres import get_pool

    if creator_id <= 0 or user_id <= 0:
        raise ValueError("scope must be positive (fail-closed)")
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT lifecycle, version FROM v2_conversations WHERE id = $1",
            conversation_id,
        )
        if row is None:
            raise ValueError("conversation not found")
        current = ConversationLifecycle(str(row["lifecycle"]))
        if not is_valid_conversation_transition(current, next_lifecycle):
            raise ValueError(f"invalid transition {current} -> {next_lifecycle}")
        updated = await conn.fetchrow(
            """
            UPDATE v2_conversations
            SET lifecycle = $1, topic = COALESCE($2, topic),
                version = version + 1, last_activity_at = NOW(), updated_at = NOW()
            WHERE id = $3 AND version = $4
            RETURNING *
            """,
            next_lifecycle.value,
            topic,
            conversation_id,
            expected_version,
        )
        return dict(updated) if updated is not None else None
