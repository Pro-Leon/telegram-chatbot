"""Retirement contracts (Phase 12 domain). Removal needs separate approval."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RetirementReadiness(BaseModel):
    """Verified-stability inputs. All must hold before removal is even planned."""

    v2_stable: bool = False
    v2_primary: bool = False
    legacy_writes_disabled: bool = False
    legacy_reads_disabled: bool = False
    commerce_independent: bool = False
    rollback_tested: bool = False
    approval_granted: bool = False

    model_config = {"frozen": True}


class RetirementDecision(BaseModel):
    action: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    blockers: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    decided_at: datetime

    model_config = {"frozen": True}
