"""Operator outcome adapter (Stage E1). Dashboard/send outcomes in, V2Events out.

Boundary rules (Phased_Plan Phases 21-23 + sunny_upgrade_v2 §34-36):
- Draft generated -> temporary candidate. Rejected -> DISCARD (no event).
- Approved (unedited) -> ResponseApproved: still NOT a relationship event.
- Approved with edit -> ResponseEdited: records the actual text path, still
  NOT a relationship event until sent.
- Failed send -> ResponseFailed: NOT a relationship event.
- Only the confirmed send -> ResponseSent (built by `build_response_sent`
  at the send path): the single mutating conversational event.
- Payloads carry references (message/generation/queue IDs, edited flag),
  never raw message content (data minimization).

Pure constructors: the caller feeds results to `event_processor.process_event`,
which persists idempotently. This module never touches the dashboard, queue
tables, or send transport (all PRESERVED, untouched).
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from relationship_v2.domain.event import V2Event, V2EventType

logger = logging.getLogger("sunny.v2.operator_events")


class OperatorAction(str):
    APPROVED = "approved"
    APPROVED_EDITED = "approved_edited"
    REJECTED = "rejected"
    FAILED = "failed"


class OperatorDecision(BaseModel):
    event_type: V2EventType | None = None
    mutating: bool = False
    reason: str = Field(min_length=1)

    model_config = {"frozen": True}


def classify_action(status: str, was_edited: bool = False) -> OperatorDecision:
    """Map a dashboard/send outcome to its V2 meaning. Pure."""
    normalized = (status or "").strip().lower()
    if normalized == "rejected":
        return OperatorDecision(
            event_type=None, mutating=False, reason="draft_discarded_no_event"
        )
    if normalized == "failed":
        return OperatorDecision(
            event_type=V2EventType.RESPONSE_FAILED,
            mutating=False,
            reason="send_failed_no_mutation",
        )
    if normalized == "approved" and was_edited:
        return OperatorDecision(
            event_type=V2EventType.RESPONSE_EDITED,
            mutating=False,
            reason="edited_text_recorded_send_pending",
        )
    if normalized == "approved":
        return OperatorDecision(
            event_type=V2EventType.RESPONSE_APPROVED,
            mutating=False,
            reason="approval_is_not_send",
        )
    return OperatorDecision(
        event_type=None, mutating=False, reason="unknown_outcome_no_event"
    )


def build_response_sent(
    creator_id: int,
    user_id: int,
    message_id: str,
    generation_id: str,
    source: str,
    was_edited: bool = False,
    queue_id: int | None = None,
    event_id: str | None = None,
) -> V2Event:
    """Build the single mutating conversational event at send confirmation."""
    if creator_id <= 0 or user_id <= 0:
        raise ValueError("scope must be positive (fail-closed)")
    for name, val in (
        ("message_id", message_id),
        ("generation_id", generation_id),
        ("source", source),
    ):
        if not val:
            raise ValueError(f"{name} required")
    key = event_id or f"sent-{message_id}"
    payload: dict[str, object] = {
        "message_id": message_id,
        "was_edited": was_edited,
    }
    if queue_id is not None:
        payload["queue_id"] = queue_id
    return V2Event(
        event_id=key,
        event_type=V2EventType.RESPONSE_SENT,
        creator_id=creator_id,
        user_id=user_id,
        message_id=message_id,
        generation_id=generation_id,
        idempotency_key=key,
        source=source,
        payload=payload,
    )
