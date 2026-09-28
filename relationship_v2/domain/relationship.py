"""Relationship aggregate (Phase 1 domain)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class RelationshipLifecycle(str, Enum):
    NEW = "new"
    WARMING = "warming"
    ESTABLISHED = "established"
    DEEP = "deep"
    DORMANT = "dormant"
    REACTIVATED = "reactivated"


_VALID_TRANSITIONS: dict[RelationshipLifecycle, set[RelationshipLifecycle]] = {
    RelationshipLifecycle.NEW: {
        RelationshipLifecycle.WARMING,
        RelationshipLifecycle.DORMANT,
    },
    RelationshipLifecycle.WARMING: {
        RelationshipLifecycle.ESTABLISHED,
        RelationshipLifecycle.DORMANT,
    },
    RelationshipLifecycle.ESTABLISHED: {
        RelationshipLifecycle.DEEP,
        RelationshipLifecycle.DORMANT,
    },
    RelationshipLifecycle.DEEP: {RelationshipLifecycle.DORMANT},
    RelationshipLifecycle.DORMANT: {RelationshipLifecycle.REACTIVATED},
    RelationshipLifecycle.REACTIVATED: {
        RelationshipLifecycle.WARMING,
        RelationshipLifecycle.ESTABLISHED,
        RelationshipLifecycle.DORMANT,
    },
}


def is_valid_relationship_transition(
    current: RelationshipLifecycle, nxt: RelationshipLifecycle
) -> bool:
    if current == nxt:
        return True
    return nxt in _VALID_TRANSITIONS.get(current, set())


class Relationship(BaseModel):
    """Persistent structured relationship state. Not a single score."""

    id: UUID
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    lifecycle: RelationshipLifecycle = RelationshipLifecycle.NEW
    familiarity: str = "stranger"
    comfort: str = "low"
    version: int = Field(default=1, ge=1)
    first_interaction_at: datetime | None = None
    last_interaction_at: datetime | None = None
    provenance: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime

    model_config = {"frozen": True}


class RelationshipState(BaseModel):
    """Derived snapshot with as_of versioning (FUTURE reasoning output)."""

    relationship_id: UUID
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    lifecycle: RelationshipLifecycle
    version: int = Field(ge=1)
    as_of: datetime

    model_config = {"frozen": True}
