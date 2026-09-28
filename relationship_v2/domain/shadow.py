"""Shadow observation contracts (Phase 10 domain). Read-only by design."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ShadowInputs(BaseModel):
    """Everything the shadow run needs, caller-supplied. No I/O inside."""

    generation_id: str = Field(min_length=1)
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    inbound_text: str = Field(min_length=1)
    inbound_text_length: int = Field(ge=0)
    current_relationship_lifecycle: str = Field(min_length=1)
    relationship_version: int = Field(ge=1)
    interaction_count: int = Field(ge=0, default=0)
    meaningful_interaction_count: int = Field(ge=0, default=0)
    milestone_count: int = Field(ge=0, default=0)
    days_since_last: int | None = Field(ge=0, default=None)
    current_escalation_stage: str = Field(min_length=1)
    responsiveness: float = Field(ge=0.0, le=1.0, default=0.5)
    engagement_evidence: int = Field(ge=0, default=0)
    negative_evidence: int = Field(ge=0, default=0)
    purchase_ref_count: int = Field(ge=0, default=0)
    commerce_eligible: bool = False
    memory_lines: list[str] = Field(default_factory=list)
    conversation_topic: str | None = None
    legacy_stage: str | None = None
    legacy_memory_count: int | None = Field(ge=0, default=None)

    model_config = {"frozen": True}


class ShadowReport(BaseModel):
    """What V2 WOULD do. Observation only; authorizes nothing."""

    generation_id: str = Field(min_length=1)
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    inbound_intent: str = Field(min_length=1)
    derived_lifecycle: str = Field(min_length=1)
    selected_stage: str = Field(min_length=1)
    stage_reason: str = Field(min_length=1)
    plan_intent: str = Field(min_length=1)
    assembly_chars: int = Field(ge=0)
    assembly_sections: int = Field(ge=1)
    memory_lines_considered: int = Field(ge=0)
    observed_at: datetime

    model_config = {"frozen": True}


class ShadowDivergence(BaseModel):
    dimension: str = Field(min_length=1)
    v2_value: str = Field(min_length=1)
    legacy_value: str = Field(min_length=1)
    note: str = Field(min_length=1)

    model_config = {"frozen": True}
