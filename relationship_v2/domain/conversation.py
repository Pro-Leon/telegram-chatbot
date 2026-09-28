"""Conversation domain: lifecycle, turns, derived state."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class ConversationLifecycle(str, Enum):
    STARTED = "started"
    ACTIVE = "active"
    PAUSED = "paused"
    RESUMED = "resumed"
    CLOSED = "closed"
    FAILED = "failed"


_VALID_CONVERSATION_TRANSITIONS: dict[ConversationLifecycle, set[ConversationLifecycle]] = {
    ConversationLifecycle.STARTED: {
        ConversationLifecycle.ACTIVE,
        ConversationLifecycle.FAILED,
    },
    ConversationLifecycle.ACTIVE: {
        ConversationLifecycle.PAUSED,
        ConversationLifecycle.CLOSED,
        ConversationLifecycle.FAILED,
    },
    ConversationLifecycle.PAUSED: {
        ConversationLifecycle.RESUMED,
        ConversationLifecycle.CLOSED,
    },
    ConversationLifecycle.RESUMED: {
        ConversationLifecycle.ACTIVE,
        ConversationLifecycle.PAUSED,
        ConversationLifecycle.CLOSED,
        ConversationLifecycle.FAILED,
    },
    ConversationLifecycle.FAILED: {
        ConversationLifecycle.ACTIVE,
        ConversationLifecycle.CLOSED,
    },
    ConversationLifecycle.CLOSED: set(),
}


def is_valid_conversation_transition(
    current: ConversationLifecycle, nxt: ConversationLifecycle
) -> bool:
    if current == nxt:
        return True
    return nxt in _VALID_CONVERSATION_TRANSITIONS.get(current, set())


class Conversation(BaseModel):
    id: UUID
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    relationship_id: UUID
    lifecycle: ConversationLifecycle = ConversationLifecycle.STARTED
    topic: str | None = None
    version: int = Field(default=1, ge=1)
    started_at: datetime
    last_activity_at: datetime
    ended_at: datetime | None = None
    provenance: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime

    model_config = {"frozen": True}


class ConversationTurn(BaseModel):
    """One inbound/outbound pair. No raw fan text: references only."""

    id: UUID
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    relationship_id: UUID
    conversation_id: UUID
    generation_id: str = Field(min_length=1)
    inbound_event_id: str = Field(min_length=1)
    plan_hash: str = Field(min_length=1)
    validation_verdict: str = Field(min_length=1)
    routing_decision: str = Field(min_length=1)
    created_at: datetime

    model_config = {"frozen": True}


class ConversationState(BaseModel):
    conversation_id: UUID
    lifecycle: ConversationLifecycle
    topic: str | None = None
    version: int = Field(ge=1)
    as_of: datetime

    model_config = {"frozen": True}
