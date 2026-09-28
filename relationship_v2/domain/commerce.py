"""Commerce read models (Phase 8 domain). Transient; truth stays in commerce.

Per 06_COMMERCE_BOUNDARY.md result contract + DOMAIN_MODEL.md invariant 1:
no commerce truth stored in V2 tables except cached confirmations
(CommerceContextRef with commerce_request_id/confirmed_at/expiry).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class StrategyAction(str, Enum):
    RETRY = "retry"
    REPLAN = "replan"
    HANDOFF = "handoff"
    SUPPRESS = "suppress"


class CommerceContext(BaseModel):
    """Normalized READ snapshot. Authored by commerce, consumed by V2."""

    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    purchase_status: str = "unknown"
    purchase_count: int = Field(ge=0, default=0)
    last_purchase_at: datetime | None = None
    active_offer: bool = False
    available_opportunities: list[str] = Field(default_factory=list)
    owned_content_refs: list[str] = Field(default_factory=list)
    aftercare_state: str = "none"
    cooldowns: dict[str, str] = Field(default_factory=dict)
    deterministic_constraints: list[str] = Field(default_factory=list)
    as_of: datetime

    model_config = {"frozen": True}


class PurchaseFeedback(BaseModel):
    """Validated purchase event -> relationship inputs. Confirmation-gated."""

    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    episode_type: str = "purchase"
    summary: str = Field(min_length=1)
    reconciled: bool = False
    provenance: str = Field(min_length=1)

    model_config = {"frozen": True}
