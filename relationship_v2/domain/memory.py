"""Memory domain: facts (temporal) + episodes (append-only)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class MemoryFactStatus(str, Enum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    CURRENT = "current"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class MemoryImportance(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class MemoryEpisodeType(str, Enum):
    INTRODUCTION = "introduction"
    PERSONAL_DISCOVERY = "personal_discovery"
    SHARED_INTEREST = "shared_interest"
    OPEN_LOOP = "open_loop"
    PROMISE = "promise"
    HUMOR = "humor"
    FLIRTATION = "flirtation"
    INTIMATE_THREAD = "intimate_thread"
    CONTENT_INTERACTION = "content_interaction"
    PURCHASE = "purchase"
    POST_PURCHASE = "post_purchase"
    OBJECTION = "objection"
    DECLINED = "declined"
    REENGAGEMENT = "reengagement"
    ABSENCE_RETURN = "absence_return"
    NEGATIVE_ENGAGEMENT = "negative_engagement"
    POSITIVE_ENGAGEMENT = "positive_engagement"
    BOUNDARY = "boundary"
    IMPORTANT_EVENT = "important_event"


class MemoryFact(BaseModel):
    """One temporal fact. Corrections supersede, never overwrite."""

    id: UUID
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    relationship_id: UUID
    category: str = Field(min_length=1, max_length=64)
    memory_key: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1)
    previous_value: str | None = None
    status: MemoryFactStatus = MemoryFactStatus.CANDIDATE
    importance: MemoryImportance = MemoryImportance.NORMAL
    confidence: float = Field(ge=0.0, le=1.0)
    effective_from: datetime
    effective_to: datetime | None = None
    supersedes_id: UUID | None = None
    provenance: str = Field(min_length=1)
    source_event_id: str | None = None
    source_message_id: str | None = None
    generation_id: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _check_temporal(self) -> MemoryFact:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        if self.status == MemoryFactStatus.SUPERSEDED and self.effective_to is None:
            raise ValueError("superseded facts must carry effective_to")
        return self


class MemoryEpisode(BaseModel):
    """Append-only per-turn/event episode."""

    id: UUID
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    relationship_id: UUID
    episode_type: MemoryEpisodeType
    summary: str = Field(min_length=1)
    salience: float = Field(ge=0.0, le=1.0, default=0.5)
    generation_id: str | None = None
    conversation_id: UUID | None = None
    provenance: str = Field(min_length=1)
    created_at: datetime

    model_config = {"frozen": True}
