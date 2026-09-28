"""Inbound event contract (Phase 4 domain).

Inbound system -> V2: fan-scoped event + idempotency key + correlation IDs.
Unknown scope fails closed; malformed payloads never create state.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class InboundDecision(str, Enum):
    ACCEPT = "accept"
    DUPLICATE = "duplicate"
    REJECT = "reject"


class RejectReason(str, Enum):
    UNKNOWN_SCOPE = "unknown_scope"
    MALFORMED = "malformed"
    STALE_VERSION = "stale_version"


class InboundEvent(BaseModel):
    """Normalized fan inbound. Identity from transport, never model output."""

    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1)
    inbound_event_id: str = Field(min_length=1)
    generation_id: str | None = None
    conversation_id: str | None = None
    text_hash: str = Field(min_length=1)
    text_length: int = Field(ge=0)

    model_config = {"frozen": True}


class InboundVerdict(BaseModel):
    decision: InboundDecision
    reason: str = Field(min_length=1)

    model_config = {"frozen": True}
