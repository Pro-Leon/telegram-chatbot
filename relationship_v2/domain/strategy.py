"""Strategy decision contract (Phase 6 domain). Strategy, not script."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from relationship_v2.domain.escalation import EscalationStage
from relationship_v2.domain.relationship import RelationshipLifecycle


class StrategyInputs(BaseModel):
    """Deterministic inputs. Commerce eligibility is a caller-supplied READ
    snapshot (Phase 8 wires the actual adapter); never fetched here."""

    relationship_lifecycle: RelationshipLifecycle = RelationshipLifecycle.NEW
    familiarity: str = "stranger"
    comfort: str = "low"
    momentum: str = "casual"
    responsiveness: float = Field(ge=0.0, le=1.0, default=0.5)
    engagement_evidence: int = Field(ge=0, default=0)
    negative_evidence: int = Field(ge=0, default=0)
    purchase_ref_count: int = Field(ge=0, default=0)
    commerce_eligible: bool = False
    has_boundaries: bool = False
    opted_out: bool = False
    cooling_down_until: datetime | None = None
    absence_days: int | None = Field(ge=0, default=None)

    model_config = {"frozen": True}


class ConfidenceBreakdown(BaseModel):
    """Structured score with named inputs, never a bare number."""

    responsiveness: float = Field(ge=0.0, le=1.0)
    engagement: float = Field(ge=0.0, le=1.0)
    relationship: float = Field(ge=0.0, le=1.0)
    commerce: float = Field(ge=0.0, le=1.0)
    total: float = Field(ge=0.0, le=1.0)

    model_config = {"frozen": True}


class StrategyDecision(BaseModel):
    """One-turn strategy. Immutable; computed by application logic, never LLM."""

    relationship_id: UUID
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    stage: EscalationStage
    previous_stage: EscalationStage
    confidence: ConfidenceBreakdown
    objective: str = Field(min_length=1)
    commerce_intent: str = Field(default="none")
    reason: str = Field(min_length=1)
    reversible: bool = True
    as_of: datetime

    model_config = {"frozen": True}
