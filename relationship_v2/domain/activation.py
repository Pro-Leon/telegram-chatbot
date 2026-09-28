"""Activation contracts (Phase 11 domain). Explicit, scoped, audited."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ActivationScope(BaseModel):
    """Bounded activation scope. Empty allowlist = no traffic (fail-closed)."""

    enabled: bool = False
    allowed_creator_ids: tuple[int, ...] = Field(default_factory=tuple)

    model_config = {"frozen": True}


class ActivationPreconditions(BaseModel):
    """Gates evaluated before any activation. All server-observed, never payload."""

    v1_disabled: bool = False
    shadow_passed: bool = False
    commerce_verified: bool = False
    dependencies_healthy: bool = False

    model_config = {"frozen": True}


class ActivationDecision(BaseModel):
    """Auditable activate/deactivate record. Data preserved on deactivate."""

    action: str = Field(min_length=1)
    creator_id: int | None = None
    actor: str = Field(min_length=1)
    reasons: list[str] = Field(default_factory=list)
    decided_at: datetime

    model_config = {"frozen": True}
