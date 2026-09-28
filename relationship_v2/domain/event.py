"""Sunny V2 event envelope (Phase 0/A1).

Owner: relationship_v2. Pure domain, no I/O.

Contract: Sunny_Relationship_V2_Phased_Plan.md Phase 2 (Event Model).
Envelope carries creator+fan scope (existing runtime uses creator_id/user_id,
not bare fan_id), generation_id for idempotency, and schema_version.

Reality rule: ResponseGenerated/Rejected/Approved/Edited are CANDIDATES and
must never mutate durable relationship state. Only ResponseSent (including an
operator-edited response that was actually sent) plus real-world happenings
(FanMessageReceived, purchases, returns, episodes) are relationship events.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class V2EventType(str, Enum):
    FAN_MESSAGE_RECEIVED = "FanMessageReceived"
    RESPONSE_GENERATED = "ResponseGenerated"
    RESPONSE_REJECTED = "ResponseRejected"
    RESPONSE_EDITED = "ResponseEdited"
    RESPONSE_APPROVED = "ResponseApproved"
    RESPONSE_SENT = "ResponseSent"
    RESPONSE_FAILED = "ResponseFailed"
    PURCHASE_COMPLETED = "PurchaseCompleted"
    PURCHASE_REFUNDED = "PurchaseRefunded"
    OFFER_PRESENTED = "OfferPresented"
    OFFER_ACCEPTED = "OfferAccepted"
    OFFER_DECLINED = "OfferDeclined"
    PRODUCT_VIEWED = "ProductViewed"
    CONTENT_DELIVERED = "ContentDelivered"
    FAN_RETURNED = "FanReturned"
    EPISODE_STARTED = "EpisodeStarted"
    EPISODE_UPDATED = "EpisodeUpdated"
    EPISODE_CLOSED = "EpisodeClosed"


# Drafts/candidates: never mutate durable relationship state on their own.
NON_MUTATING_TYPES = frozenset(
    {
        V2EventType.RESPONSE_GENERATED,
        V2EventType.RESPONSE_REJECTED,
        V2EventType.RESPONSE_EDITED,
        V2EventType.RESPONSE_APPROVED,
    }
)

SCHEMA_VERSION = "v2-event-1"


class V2Event(BaseModel):
    """Immutable relationship-domain event envelope."""

    event_id: str = Field(min_length=1, max_length=128)
    event_type: V2EventType
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    relationship_id: UUID | None = None
    conversation_id: UUID | None = None
    episode_id: UUID | None = None
    message_id: str | None = None
    generation_id: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=128)
    payload: dict = Field(default_factory=dict)
    schema_version: str = Field(default=SCHEMA_VERSION, min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = {"frozen": True}


def is_relationship_mutating(event_type: V2EventType) -> bool:
    """True only for events allowed to mutate durable relationship state."""
    return event_type not in NON_MUTATING_TYPES


class ProcessedEvent(BaseModel):
    """Idempotency marker: (event_id, processor) processed once.

    Contract: Phased_Plan Phase 25 + sunny_upgrade_v2 §59.
    One row per (event_id, processor); reprocessing returns existing row.
    """

    id: UUID
    event_id: str = Field(min_length=1, max_length=128)
    processor: str = Field(min_length=1, max_length=128)
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    relationship_id: UUID | None = None
    processed_at: datetime

    model_config = {"frozen": True}
