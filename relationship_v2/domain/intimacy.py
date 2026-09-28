"""Intimate history domain (Stage C3). Semantic abstractions, never verbatim.

History answers: comfort with teasing, preferred style, shared moments,
boundaries, turn-offs, progression. It is continuity context — never
permission. Data minimization by construction: the abstract signal column
is bounded and no raw-content column exists.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class IntimateKind(str, Enum):
    COMFORT = "comfort"
    PREFERENCE = "preference"
    BOUNDARY = "boundary"
    TURNOFF = "turnoff"
    MILESTONE = "milestone"


class IntimateStatus(str, Enum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


_VALID_INTIMATE_TRANSITIONS: dict[IntimateStatus, set[IntimateStatus]] = {
    IntimateStatus.CURRENT: {IntimateStatus.SUPERSEDED, IntimateStatus.ARCHIVED},
    IntimateStatus.SUPERSEDED: {IntimateStatus.ARCHIVED},
    IntimateStatus.ARCHIVED: set(),
}


def is_valid_intimate_transition(current: IntimateStatus, nxt: IntimateStatus) -> bool:
    if current == nxt:
        return True
    return nxt in _VALID_INTIMATE_TRANSITIONS.get(current, set())


class IntimateMemory(BaseModel):
    id: UUID
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    relationship_id: UUID
    kind: IntimateKind
    signal: str = Field(min_length=1, max_length=280)
    confidence: float = Field(ge=0.0, le=1.0)
    importance: str = Field(default="normal", pattern="^(critical|high|normal|low)$")
    status: IntimateStatus = IntimateStatus.CURRENT
    supersedes_id: UUID | None = None
    provenance: str = Field(min_length=1)
    source_event_id: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"frozen": True}
