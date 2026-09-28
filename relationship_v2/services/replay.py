"""Replay normalizer (Stage F3). Legacy Telegram rows in, replay events out.

Caller: FUTURE backfill job, only after the live event model is stable
(Phased_Plan Phase 28: replay is never a prerequisite for first function).
Pure functions, no I/O: ordering, direction filtering, deterministic
idempotency (`replay:msg:{id}` so reruns dedupe through the processor),
and content hashing (identity without storing prose in keys).

Scope: inbound fan messages only. Historical OUTBOUND replay is excluded:
reconstructing Sunny's sends requires send-confirmation evidence the
messages table does not carry; inventing ResponseSent history would
violate the reality rule. Documented, not stubbed.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from relationship_v2.domain.event import V2Event, V2EventType
from relationship_v2.domain.inbound import InboundEvent

logger = logging.getLogger("sunny.v2.replay")

PROVENANCE = "relationship_v2.services.replay"


class ReplayMessage(BaseModel):
    """Minimal legacy row shape. Caller maps DB rows to this."""

    message_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    direction: str = Field(min_length=1)
    content: str = Field(min_length=1)
    telegram_message_id: int | None = None
    sent_at: datetime | None = None

    model_config = {"frozen": True}


def replay_key(message_id: int) -> str:
    """Deterministic idempotency key: reruns dedupe, never duplicate."""
    if message_id <= 0:
        raise ValueError("message_id must be positive")
    return f"replay:msg:{message_id}"


def text_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def normalize_message(msg: ReplayMessage) -> InboundEvent | None:
    """Map one legacy row to an inbound event. Non-inbound -> None."""
    try:
        if msg.direction.strip().lower() != "inbound":
            return None
        if not msg.content.strip():
            return None
        key = replay_key(msg.message_id)
        return InboundEvent(
            creator_id=1,
            user_id=msg.user_id,
            idempotency_key=key,
            inbound_event_id=key,
            generation_id=None,
            conversation_id=None,
            text_hash=text_hash(msg.content),
            text_length=len(msg.content),
        )
    except Exception:
        logger.exception("replay normalize failed (skip row)")
        return None


def _epoch(value: datetime | None) -> float:
    if value is None:
        return float("inf")
    ts = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return ts.timestamp()


def order_for_replay(messages: list[ReplayMessage]) -> list[ReplayMessage]:
    """Chronological replay order: sent time, then message id. Never raises."""
    try:
        return sorted(messages, key=lambda m: (_epoch(m.sent_at), m.message_id))
    except Exception:
        logger.exception("replay ordering failed (fail-open input order)")
        return list(messages)


def to_fan_received(
    event: InboundEvent, creator_id: int, source: str = PROVENANCE
) -> V2Event:
    """Inbound event -> FanMessageReceived domain event for the processor."""
    if creator_id <= 0:
        raise ValueError("creator_id must be positive (fail-closed)")
    if not source:
        raise ValueError("source required")
    return V2Event(
        event_id=event.inbound_event_id,
        event_type=V2EventType.FAN_MESSAGE_RECEIVED,
        creator_id=creator_id,
        user_id=event.user_id,
        idempotency_key=event.idempotency_key,
        source=source,
        payload={"text_hash": event.text_hash, "replay": True},
    )
