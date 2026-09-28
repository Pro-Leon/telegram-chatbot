"""Response plan/generation/validation contracts (Phase 7 domain)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class ResponseIntent(str, Enum):
    ACKNOWLEDGE = "acknowledge"
    TEASE = "tease"
    QUESTION = "question"
    CALLBACK = "callback"
    COMMERCE_TRANSITION = "commerce_transition"
    AFTERCARE = "aftercare"
    BOUNDARY_RESPECT = "boundary_respect"
    REACTIVATE = "reactivate"


class ValidationOutcome(str, Enum):
    VALID = "valid"
    NEEDS_REVIEW = "needs_review"
    SUPPRESSED = "suppressed"


class OutboundState(str, Enum):
    PLANNED = "planned"
    VALIDATED = "validated"
    ENQUEUED = "enqueued"
    SENT = "sent"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


class ResponsePlan(BaseModel):
    """Explicit plan persisted before any token is generated."""

    generation_id: str = Field(min_length=1)
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    relationship_id: UUID
    intent: ResponseIntent
    key_points: list[str] = Field(default_factory=list)
    commerce_refs: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    response_length_target: int = Field(ge=1, le=2000, default=200)
    provenance: str = Field(min_length=1)
    created_at: datetime

    model_config = {"frozen": True}


class GeneratedResponse(BaseModel):
    """Structured LLM output. Advisory prose; never authority."""

    text: str = Field(min_length=1)
    generation_id: str = Field(min_length=1)
    strategy: str = Field(min_length=1)
    memory_references: list[str] = Field(default_factory=list)
    commerce_intent: str = "none"
    thread_continuity: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    model_metadata: str = Field(default="")

    model_config = {"frozen": True}


class ValidationVerdict(BaseModel):
    outcome: ValidationOutcome
    flags: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)

    model_config = {"frozen": True}
