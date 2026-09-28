"""Relationship context domain (Phase 2). Deterministic, no I/O."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from relationship_v2.domain.relationship import RelationshipLifecycle


class RelationshipEvidence(BaseModel):
    """Deterministic inputs for derivation. Caller assembles from V2 tables.

    Caller (Phase 2): relationship_v2.services.relationship_context.
    Future callers: memory system (Phase 3), conversation engine (Phase 4).
    Purchase fields reference commerce confirmations only, never invented truth.
    """

    interaction_count: int = Field(ge=0, default=0)
    meaningful_interaction_count: int = Field(ge=0, default=0)
    milestone_count: int = Field(ge=0, default=0)
    episode_count: int = Field(ge=0, default=0)
    boundary_count: int = Field(ge=0, default=0)
    days_since_first: int | None = Field(ge=0, default=None)
    days_since_last: int | None = Field(ge=0, default=None)
    purchase_ref_count: int = Field(ge=0, default=0)
    inbound_count: int = Field(ge=0, default=0)
    sent_count: int = Field(ge=0, default=0)

    model_config = {"frozen": True}


class ReentryContext(BaseModel):
    """Explicit absence/return context. Never a cold start."""

    absence_days: int | None = Field(ge=0, default=None)
    was_dormant: bool = False
    resume_summary: str = Field(min_length=1)

    model_config = {"frozen": True}


class RelationshipContextSnapshot(BaseModel):
    """Authoritative conversational snapshot for one fan. Immutable once built."""

    relationship_id: UUID
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    lifecycle: RelationshipLifecycle
    familiarity: str = Field(min_length=1)
    comfort: str = Field(min_length=1)
    version: int = Field(ge=1)
    as_of: datetime
    absence_days: int | None = Field(ge=0, default=None)
    reentry: ReentryContext | None = None
    has_boundaries: bool = False
    buyer_class: str = Field(default="none")
    evidence_hash: str = Field(min_length=1)
    provenance: str = Field(min_length=1)

    model_config = {"frozen": True}
