"""Open-loop domain (Stage C1). Durable conversational threads.

An open loop is something Sunny should remember and potentially return to:
soccer game, job interview, promise, unfinished story. Statuses follow the
spec lifecycle; DUE is time-derived (expected_at passed) while the stored
row remains OPEN until explicitly transitioned.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class OpenLoopStatus(str, Enum):
    OPEN = "open"
    DUE = "due"
    REFERENCED = "referenced"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    DISMISSED = "dismissed"


class OpenLoopPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


_VALID_LOOP_TRANSITIONS: dict[OpenLoopStatus, set[OpenLoopStatus]] = {
    OpenLoopStatus.OPEN: {
        OpenLoopStatus.DUE,
        OpenLoopStatus.REFERENCED,
        OpenLoopStatus.RESOLVED,
        OpenLoopStatus.EXPIRED,
        OpenLoopStatus.DISMISSED,
    },
    OpenLoopStatus.DUE: {
        OpenLoopStatus.REFERENCED,
        OpenLoopStatus.RESOLVED,
        OpenLoopStatus.EXPIRED,
        OpenLoopStatus.DISMISSED,
    },
    OpenLoopStatus.REFERENCED: {
        OpenLoopStatus.DUE,
        OpenLoopStatus.RESOLVED,
        OpenLoopStatus.EXPIRED,
        OpenLoopStatus.DISMISSED,
    },
    OpenLoopStatus.RESOLVED: set(),
    OpenLoopStatus.EXPIRED: set(),
    OpenLoopStatus.DISMISSED: set(),
}


def is_valid_open_loop_transition(current: OpenLoopStatus, nxt: OpenLoopStatus) -> bool:
    if current == nxt:
        return True
    return nxt in _VALID_LOOP_TRANSITIONS.get(current, set())


class OpenLoop(BaseModel):
    id: UUID
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    relationship_id: UUID
    source_memory_id: UUID | None = None
    source_event_id: str | None = None
    description: str = Field(min_length=1, max_length=512)
    expected_at: datetime | None = None
    priority: OpenLoopPriority = OpenLoopPriority.NORMAL
    status: OpenLoopStatus = OpenLoopStatus.OPEN
    last_referenced_at: datetime | None = None
    follow_up_attempts: int = Field(ge=0, default=0)
    resolved_at: datetime | None = None
    outcome: str | None = None
    provenance: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime

    model_config = {"frozen": True}
