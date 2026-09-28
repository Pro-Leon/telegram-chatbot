"""Post-send learning path (Stage F9b). One sent event in, stores updated.

Composes ONLY shipped, tested primitives into the §28 pipeline slice for
Sunny's own sent messages: idempotent intake -> extraction -> observation
review -> episode persistence -> engagement accumulation -> intimate
persistence -> open-loop creation. All store access flows through
injectable ports (defaults bind the real repository); tests use fakes.

Scope notes (not stubbed, explicitly deferred):
- Fact promotion to CURRENT happens on confirmation/repetition through the
  normal validation lifecycle; this path persists PROMOTE_* dispositions as
  candidate rows (retrievable, never presented as verified current).
- Loop `expected_at` stays empty (no date parsing); due derivation uses
  recorded expectations once the pipeline supplies them.
- Rejected/held candidates are counted in the report, not persisted.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from relationship_v2.domain.event import V2Event, V2EventType

logger = logging.getLogger("sunny.v2.post_send")

PROVENANCE = "relationship_v2.services.post_send"


class PostSendOutcome(str, Enum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


class PostSendResult(BaseModel):
    outcome: PostSendOutcome
    event_id: str = Field(min_length=1)
    episodes_created: int = Field(ge=0, default=0)
    facts_recorded: int = Field(ge=0, default=0)
    signals_recorded: int = Field(ge=0, default=0)
    intimate_recorded: int = Field(ge=0, default=0)
    loops_opened: int = Field(ge=0, default=0)
    held: int = Field(ge=0, default=0)
    rejected: int = Field(ge=0, default=0)
    reason: str = Field(min_length=1)

    model_config = {"frozen": True}


def _priority_for(salience: float) -> str:
    if salience >= 0.75:
        return "high"
    if salience >= 0.5:
        return "normal"
    return "low"


async def apply_response_sent(
    event: V2Event,
    sent_text: str,
    *,
    provenance: str = PROVENANCE,
    is_processed: Callable | None = None,
    get_or_create: Callable | None = None,
    create_event: Callable | None = None,
    mark_processed: Callable | None = None,
    list_facts: Callable | None = None,
    create_fact: Callable | None = None,
    create_episode: Callable | None = None,
    record_signal: Callable | None = None,
    record_intimate: Callable | None = None,
    create_loop: Callable | None = None,
) -> PostSendResult:
    """Learn from a confirmed send. Only ResponseSent mutates (reality rule)."""
    from relationship_v2.services.event_processor import ProcessorOutcome, process_event

    if event.event_type != V2EventType.RESPONSE_SENT:
        return PostSendResult(
            outcome=PostSendOutcome.REJECTED,
            event_id=event.event_id,
            reason="only_response_sent_learns",
        )
    if not sent_text or not sent_text.strip():
        return PostSendResult(
            outcome=PostSendOutcome.REJECTED,
            event_id=event.event_id,
            reason="empty_text",
        )
    if not provenance:
        raise ValueError("provenance required")

    stored = await process_event(
        event,
        provenance=provenance,
        is_processed=is_processed,
        get_or_create=get_or_create,
        create_event=create_event,
        mark_processed=mark_processed,
    )
    if stored.outcome == ProcessorOutcome.DUPLICATE:
        return PostSendResult(
            outcome=PostSendOutcome.DUPLICATE,
            event_id=event.event_id,
            reason="redelivery",
        )
    if stored.outcome != ProcessorOutcome.APPLIED:
        return PostSendResult(
            outcome=PostSendOutcome.REJECTED,
            event_id=event.event_id,
            reason=f"intake_{stored.outcome.value}",
        )

    from relationship_v2.services.memory_extraction import CandidateKind, extract_candidates

    candidates = extract_candidates(sent_text, event.event_id, provenance)

    rel = await (get_or_create or _default_get_or_create)(
        event.creator_id, event.user_id, provenance
    )
    relationship_id: UUID = rel["id"]
    current_rows = await (list_facts or _default_list_facts)(
        event.creator_id, event.user_id
    )

    from relationship_v2.services.episode_manager import plan_candidates
    from relationship_v2.services.intimate_history import plan_intimate
    from relationship_v2.services.llm_observation import (
        MemoryObservation,
        ObservationBatch,
        ObservationDisposition,
        review_batch,
    )

    current_facts = []
    for row in current_rows:
        try:
            current_facts.append(_row_to_fact(row))
        except Exception:
            logger.debug("skipping malformed fact row", exc_info=True)
    observations = ObservationBatch(
        generation_id=event.generation_id or f"no-gen-{event.event_id}",
        creator_id=event.creator_id,
        user_id=event.user_id,
        source_event_id=event.event_id,
        provenance=provenance,
        memories=[
            MemoryObservation(
                category=c.category,
                memory_key=c.memory_key,
                value=c.value,
                confidence=c.confidence,
                importance=c.importance,
                explicitly_stated=c.explicitly_stated,
            )
            for c in candidates
            if c.kind == CandidateKind.FACT
        ],
    )
    report = review_batch(observations, current_facts)
    episodes_created = facts_recorded = signals = intimate_n = loops = 0
    held = rejected = 0
    for result in report.results:
        if result.disposition in (
            ObservationDisposition.PROMOTE_CURRENT,
            ObservationDisposition.PROMOTE_VALIDATED,
        ):
            obs = next(
                (
                    o
                    for o in observations.memories
                    if o.memory_key == result.key
                ),
                None,
            )
            if obs is not None:
                await (create_fact or _default_create_fact)(
                    event.creator_id,
                    event.user_id,
                    relationship_id,
                    obs.category,
                    obs.memory_key,
                    obs.value,
                    obs.confidence,
                    importance=obs.importance,
                    provenance=provenance,
                    source_event_id=event.event_id,
                    generation_id=event.generation_id,
                )
                facts_recorded += 1
        elif result.disposition == ObservationDisposition.HOLD:
            held += 1
        else:
            rejected += 1

    for plan in plan_candidates(candidates):
        await (create_episode or _default_create_episode)(
            event.creator_id,
            event.user_id,
            relationship_id,
            plan.episode_type.value,
            plan.summary,
            provenance,
            salience=plan.salience,
            generation_id=event.generation_id,
        )
        episodes_created += 1
        if plan.follow_up_candidate:
            await (create_loop or _default_create_loop)(
                event.creator_id,
                event.user_id,
                relationship_id,
                plan.summary,
                provenance,
                priority=_priority_for(plan.salience),
                source_event_id=event.event_id,
            )
            loops += 1

    for candidate in candidates:
        if candidate.kind != CandidateKind.SIGNAL:
            continue
        if candidate.category == "intimate_signal":
            continue
        await (record_signal or _default_record_signal)(
            event.creator_id,
            event.user_id,
            relationship_id,
            candidate.memory_key,
            candidate.category,
            "neutral",
            candidate.confidence,
            provenance,
        )
        signals += 1

    for candidate in candidates:
        plan = plan_intimate(candidate)
        if plan is None:
            continue
        await (record_intimate or _default_record_intimate)(
            event.creator_id,
            event.user_id,
            relationship_id,
            plan.kind.value,
            plan.signal,
            plan.confidence,
            provenance,
            importance=plan.importance,
            source_event_id=event.event_id,
        )
        intimate_n += 1

    return PostSendResult(
        outcome=PostSendOutcome.APPLIED,
        event_id=event.event_id,
        episodes_created=episodes_created,
        facts_recorded=facts_recorded,
        signals_recorded=signals,
        intimate_recorded=intimate_n,
        loops_opened=loops,
        held=held,
        rejected=rejected,
        reason="learned",
    )


def _row_to_fact(row: dict[str, Any]):  # type: ignore[no-untyped-def]
    from uuid import UUID as _UUID

    from relationship_v2.domain.memory import MemoryFact, MemoryFactStatus, MemoryImportance

    return MemoryFact(
        id=row["id"] if isinstance(row.get("id"), _UUID) else _UUID(str(row["id"])),
        creator_id=int(row["creator_id"]),
        user_id=int(row["user_id"]),
        relationship_id=(
            row["relationship_id"]
            if isinstance(row.get("relationship_id"), _UUID)
            else _UUID(str(row["relationship_id"]))
        ),
        category=str(row.get("category", "legacy")),
        memory_key=str(row["memory_key"]),
        value=str(row["value"]),
        status=MemoryFactStatus(str(row.get("status", "current"))),
        importance=MemoryImportance(str(row.get("importance", "normal"))),
        confidence=float(row.get("confidence", 0.5)),
        effective_from=row.get("effective_from") or datetime.now(UTC),
        provenance=str(row.get("provenance", "replay")),
        created_at=row.get("created_at") or datetime.now(UTC),
        updated_at=row.get("updated_at") or datetime.now(UTC),
    )


async def _default_get_or_create(creator_id: int, user_id: int, provenance: str) -> dict:
    from relationship_v2.persistence.repository import get_or_create_relationship

    return await get_or_create_relationship(creator_id, user_id, provenance)


async def _default_list_facts(creator_id: int, user_id: int) -> list[dict]:
    from relationship_v2.persistence.repository import list_current_facts

    return await list_current_facts(creator_id, user_id)


async def _default_create_fact(
    creator_id: int,
    user_id: int,
    relationship_id: UUID,
    category: str,
    memory_key: str,
    value: str,
    confidence: float,
    importance: str = "normal",
    provenance: str = "",
    source_event_id: str | None = None,
    generation_id: str | None = None,
) -> dict:
    from relationship_v2.persistence.repository import create_memory_fact

    return await create_memory_fact(
        creator_id, user_id, relationship_id, category, memory_key, value,
        confidence, importance=importance, provenance=provenance,
        source_event_id=source_event_id, generation_id=generation_id,
    )


async def _default_create_episode(
    creator_id: int,
    user_id: int,
    relationship_id: UUID,
    episode_type: str,
    summary: str,
    provenance: str,
    salience: float = 0.5,
    generation_id: str | None = None,
) -> dict:
    from relationship_v2.persistence.repository import create_memory_episode

    return await create_memory_episode(
        creator_id, user_id, relationship_id, episode_type, summary,
        provenance, salience=salience, generation_id=generation_id,
    )


async def _default_record_signal(
    creator_id: int,
    user_id: int,
    relationship_id: UUID,
    topic: str,
    behavior: str,
    polarity: str,
    confidence: float,
    provenance: str,
) -> dict:
    from relationship_v2.persistence.repository import record_engagement_signal

    return await record_engagement_signal(
        creator_id, user_id, relationship_id, topic, behavior,
        polarity, confidence, provenance,
    )


async def _default_record_intimate(
    creator_id: int,
    user_id: int,
    relationship_id: UUID,
    kind: str,
    signal: str,
    confidence: float,
    provenance: str,
    importance: str = "normal",
    source_event_id: str | None = None,
) -> dict:
    from relationship_v2.persistence.repository import record_intimate_signal

    return await record_intimate_signal(
        creator_id, user_id, relationship_id, kind, signal, confidence,
        provenance, importance=importance, source_event_id=source_event_id,
    )


async def _default_create_loop(
    creator_id: int,
    user_id: int,
    relationship_id: UUID,
    description: str,
    provenance: str,
    priority: str = "normal",
    source_event_id: str | None = None,
) -> dict:
    from relationship_v2.persistence.repository import create_open_loop

    return await create_open_loop(
        creator_id, user_id, relationship_id, description, provenance,
        priority=priority, source_event_id=source_event_id,
    )
