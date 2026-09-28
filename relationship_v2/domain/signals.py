"""Interaction/engagement signals (Phase 3 domain).

Deterministic observations, never LLM verdicts. Probabilistic evidence with
counts; one event never creates a permanent preference.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class SignalPolarity(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class InteractionSignal(BaseModel):
    """One deterministic observation (topic affinity, responsiveness, style)."""

    id: UUID
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    relationship_id: UUID
    topic: str = Field(min_length=1, max_length=128)
    behavior: str = Field(min_length=1, max_length=64)
    polarity: SignalPolarity = SignalPolarity.NEUTRAL
    evidence_count: int = Field(ge=1, default=1)
    confidence: float = Field(ge=0.0, le=1.0)
    first_observed_at: datetime
    last_observed_at: datetime
    provenance: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime

    model_config = {"frozen": True}
