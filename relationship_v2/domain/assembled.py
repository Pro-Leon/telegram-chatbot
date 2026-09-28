"""Assembled generation context (Phase 5 domain). Immutable once built."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ContextSection(BaseModel):
    """One budgeted section. Lines are pre-rendered, provenance-tagged strings."""

    name: str = Field(min_length=1)
    lines: list[str] = Field(default_factory=list)
    budget_chars: int = Field(gt=0)
    truncated: bool = False
    dropped_chars: int = Field(ge=0, default=0)

    model_config = {"frozen": True}


class ConflictResolution(BaseModel):
    winner: str = Field(min_length=1)
    loser: str = Field(min_length=1)
    rule: str = Field(min_length=1)

    model_config = {"frozen": True}


class AssembledContext(BaseModel):
    """Bounded LLM input. Deterministic; same inputs -> same output."""

    generation_id: str = Field(min_length=1)
    conversation_id: str | None = None
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    sections: list[ContextSection] = Field(min_length=1)
    resolutions: list[ConflictResolution] = Field(default_factory=list)
    stale_sections: list[str] = Field(default_factory=list)
    total_chars: int = Field(ge=0)
    as_of: datetime

    model_config = {"frozen": True}
