"""Replay job (Stage F10). Legacy message lists in, processor events out.

Caller: FUTURE offline job, only after the live event model is stable
(Phase 28) with explicit approval — never automatic, never in the turn
path. Orders rows chronologically, normalizes inbound-only, feeds each
through `process_event` (reruns dedupe by `replay:msg:{id}`). Dry-run by
default (order + normalize + report, process nothing).

Scope notes (not stubbed, explicitly deferred):
- Memory extraction / episode reconstruction over replayed content runs
  through the live-stable pipeline later, not here: replaying stale
  content through deterministic extractors without operator review risks
  fossilizing outdated facts as current.
- Outbound history is skipped (no send-confirmation evidence; reality rule).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from pydantic import BaseModel, Field

from relationship_v2.services.replay import (
    ReplayMessage,
    normalize_message,
    order_for_replay,
    to_fan_received,
)

logger = logging.getLogger("sunny.v2.replay_job")

PROVENANCE = "relationship_v2.services.replay_job"


class ReplayReport(BaseModel):
    scanned: int = Field(ge=0)
    applied: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    skipped_non_inbound: int = Field(ge=0)
    skipped_malformed: int = Field(ge=0)
    dry_run: bool = True
    applied_ids: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}


async def run_replay(
    messages: list[ReplayMessage],
    creator_id: int,
    *,
    provenance: str = PROVENANCE,
    dry_run: bool = True,
    source: str = PROVENANCE,
    is_processed: Callable | None = None,
    get_or_create: Callable | None = None,
    create_event: Callable | None = None,
    mark_processed: Callable | None = None,
) -> ReplayReport:
    """Replay legacy messages in chronological order. Dry-run by default."""
    from relationship_v2.services.event_processor import ProcessorOutcome, process_event

    if creator_id <= 0:
        raise ValueError("creator_id must be positive (fail-closed)")
    if not provenance or not source:
        raise ValueError("provenance/source required")
    ordered = order_for_replay(messages or [])
    applied: list[str] = []
    duplicates = non_inbound = malformed = 0
    for msg in ordered:
        if not isinstance(msg, ReplayMessage):
            malformed += 1
            continue
        inbound = normalize_message(msg)
        if inbound is None:
            if msg.direction.strip().lower() != "inbound":
                non_inbound += 1
            else:
                malformed += 1
            continue
        if dry_run:
            applied.append(inbound.inbound_event_id)
            continue
        event = to_fan_received(inbound, creator_id, source)
        stored = await process_event(
            event,
            provenance=provenance,
            is_processed=is_processed,
            get_or_create=get_or_create,
            create_event=create_event,
            mark_processed=mark_processed,
        )
        if stored.outcome == ProcessorOutcome.APPLIED:
            applied.append(event.event_id)
        elif stored.outcome == ProcessorOutcome.DUPLICATE:
            duplicates += 1
        else:
            malformed += 1
    return ReplayReport(
        scanned=len(ordered),
        applied=len(applied),
        duplicates=duplicates,
        skipped_non_inbound=non_inbound,
        skipped_malformed=malformed,
        dry_run=dry_run,
        applied_ids=applied,
    )
