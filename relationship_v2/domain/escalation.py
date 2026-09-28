"""Escalation strategy state (strategy, not script)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class EscalationStage(str, Enum):
    RELATIONSHIP = "relationship"
    EXPLORE = "explore"
    BUILD_DESIRE = "build_desire"
    QUALIFY = "qualify"
    RECOMMEND = "recommend"
    PRESENT_OFFER = "present_offer"
    AFTERCARE = "aftercare"
    EXITED = "exited"
    COOLING_DOWN = "cooling_down"


class EscalationState(BaseModel):
    relationship_id: UUID
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    stage: EscalationStage = EscalationStage.RELATIONSHIP
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    cooldown_until: datetime | None = None
    commerce_eligible: bool = False
    version: int = Field(ge=1, default=1)
    as_of: datetime

    model_config = {"frozen": True}
