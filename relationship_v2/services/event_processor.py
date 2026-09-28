"""Event processor (Stage A3). Idempotent durable-event intake.

Caller: FUTURE turn orchestrator / workers. Owner of: (event_id, processor)
idempotency gate + v2_events persistence for mutating relationship events.
Producer of: v2_events rows, v2_processed_events markers.

Flow: receive -> discard non-mutating candidates (zero I/O) -> check
processed -> get_or_create relationship -> persist domain event -> mark
processed. Crash between persist and mark is retry-safe: both inserts are
idempotent (ON CONFLICT DO NOTHING + fetch existing), so redelivery yields
one logical mutation.

Scope: FanMessageReceived + ResponseSent (+ other mutating types generically
as relational history). Memory/episode/pattern/open-loop updates are
Stage B/C. No commerce writes, no provider calls, no realtime bus imports
(Phase 1 realtime contract PRESERVED, untouched).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from relationship_v2.domain.event import V2Event, is_relationship_mutating

logger = logging.getLogger("sunny.v2.event_processor")

PROCESSOR_NAME = "event_processor"
PROVENANCE = "relationship_v2.services.event_processor"


class ProcessorOutcome(str, Enum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    DISCARDED = "discarded"
    REJECTED = "rejected"


class ProcessResult(BaseModel):
    outcome: ProcessorOutcome
    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    model_config = {"frozen": True}


IsProcessedPort = Callable[[str, str], Awaitable[bool]]
GetOrCreatePort = Callable[[int, int, str], Awaitable[dict]]
CreateEventPort = Callable[..., Awaitable[dict]]
MarkProcessedPort = Callable[..., Awaitable[dict]]


def route_event(event: V2Event) -> ProcessResult:
    """Pure routing: candidates discarded with zero I/O, mutating routed."""
    if not is_relationship_mutating(event.event_type):
        return ProcessResult(
            outcome=ProcessorOutcome.DISCARDED,
            event_id=event.event_id,
            event_type=event.event_type.value,
            reason="candidate_not_event",
        )
    return ProcessResult(
        outcome=ProcessorOutcome.APPLIED,
        event_id=event.event_id,
        event_type=event.event_type.value,
        reason="routed_persist",
    )


async def _default_is_processed(event_id: str, processor: str) -> bool:
    from relationship_v2.persistence.repository import is_event_processed

    return await is_event_processed(event_id, processor)


async def _default_get_or_create(creator_id: int, user_id: int, provenance: str) -> dict:
    from relationship_v2.persistence.repository import get_or_create_relationship

    return await get_or_create_relationship(creator_id, user_id, provenance)


async def _default_create_event(**kwargs) -> dict:  # type: ignore[no-untyped-def]
    from relationship_v2.persistence.repository import create_relationship_event

    return await create_relationship_event(**kwargs)


async def _default_mark_processed(**kwargs) -> dict:  # type: ignore[no-untyped-def]
    from relationship_v2.persistence.repository import mark_event_processed

    return await mark_event_processed(**kwargs)


async def process_event(
    event: V2Event,
    *,
    provenance: str = PROVENANCE,
    is_processed: IsProcessedPort | None = None,
    get_or_create: GetOrCreatePort | None = None,
    create_event: CreateEventPort | None = None,
    mark_processed: MarkProcessedPort | None = None,
) -> ProcessResult:
    """Idempotent intake for one domain event. Fail-closed, never silent."""
    if not provenance:
        return ProcessResult(
            outcome=ProcessorOutcome.REJECTED,
            event_id=event.event_id,
            event_type=event.event_type.value,
            reason="provenance_required",
        )
    routed = route_event(event)
    if routed.outcome == ProcessorOutcome.DISCARDED:
        return routed
    check = is_processed or _default_is_processed
    if await check(event.event_id, PROCESSOR_NAME):
        return ProcessResult(
            outcome=ProcessorOutcome.DUPLICATE,
            event_id=event.event_id,
            event_type=event.event_type.value,
            reason="already_processed",
        )
    get_rel = get_or_create or _default_get_or_create
    persist = create_event or _default_create_event
    mark = mark_processed or _default_mark_processed
    rel = await get_rel(event.creator_id, event.user_id, provenance)
    relationship_id: UUID | None = rel.get("id")
    await persist(
        event_id=event.event_id,
        event_type=event.event_type.value,
        creator_id=event.creator_id,
        user_id=event.user_id,
        idempotency_key=event.idempotency_key,
        producer=PROCESSOR_NAME,
        payload=event.payload,
        relationship_id=relationship_id,
        conversation_id=event.conversation_id,
        generation_id=event.generation_id,
    )
    marked = await mark(
        event_id=event.event_id,
        processor=PROCESSOR_NAME,
        creator_id=event.creator_id,
        user_id=event.user_id,
        relationship_id=relationship_id,
    )
    # Atomicity: the UNIQUE insert is the single linearization point. A
    # concurrent worker that lost the race gets inserted=False and reports
    # DUPLICATE, so redelivery can never double-apply downstream work.
    # Fakes without the flag default to True (single-writer assumption).
    if isinstance(marked, dict) and marked.get("inserted", True) is False:
        return ProcessResult(
            outcome=ProcessorOutcome.DUPLICATE,
            event_id=event.event_id,
            event_type=event.event_type.value,
            reason="lost_mark_race",
        )
    return ProcessResult(
        outcome=ProcessorOutcome.APPLIED,
        event_id=event.event_id,
        event_type=event.event_type.value,
        reason="persisted",
    )