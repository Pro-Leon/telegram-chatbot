"""Contradiction resolver (Stage B1). Explicit conflict classification.

Caller: memory pipeline after extraction, before validation/persistence.
Complements (never replaces) `memory_validation.validate_fact_candidate`:
this module CLASSIFIES the conflict and produces an explicit record;
the validator remains the promotion gate and the repository remains the
only writer. No I/O here — pure functions.

Rules (05_MEMORY_SPEC.md + MEMORY_ARCHITECTURE.md + sunny_upgrade_v2 §9):
- Newest verified source wins for "now"; history preserved ("used to be").
- Same value -> reinforcement, no new row.
- Weak inference contradicting current -> HOLD (keep current, no supersede).
- Fan correction -> supersede with correction reason; error and fix persist.
- Invalid: candidate -> superseded (guarded by validator lifecycle).
"""

from __future__ import annotations

import logging
from enum import Enum

from pydantic import BaseModel, Field

from relationship_v2.domain.memory import MemoryFact, MemoryFactStatus
from relationship_v2.services.memory_extraction import (
    PROMOTION_GATE,
    MemoryCandidate,
)

logger = logging.getLogger("sunny.v2.contradiction_resolver")


class ConflictKind(str, Enum):
    NO_CONFLICT = "no_conflict"
    REINFORCEMENT = "reinforcement"
    DIRECT_CONTRADICTION = "direct_contradiction"
    CORRECTION = "correction"


class ConflictResolution(str, Enum):
    ACCEPT = "accept"
    CONFIRM = "confirm"
    SUPERSEDE = "supersede"
    HOLD = "hold"


class ConflictRecord(BaseModel):
    """Explicit conflict record. Carried in v2_events payloads; never silent."""

    memory_key: str = Field(min_length=1, max_length=128)
    previous_value: str | None = None
    candidate_value: str = Field(min_length=1)
    kind: ConflictKind
    resolution: ConflictResolution
    supersedes_id: str | None = None
    is_correction: bool = False
    reason: str = Field(min_length=1)
    provenance: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)

    model_config = {"frozen": True}


def _normalize(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _current_for_key(facts: list[MemoryFact], key: str) -> MemoryFact | None:
    return next(
        (f for f in facts if f.memory_key == key and f.status == MemoryFactStatus.CURRENT),
        None,
    )


def resolve_conflict(
    candidate: MemoryCandidate,
    current_facts: list[MemoryFact],
    *,
    is_correction: bool = False,
) -> ConflictRecord:
    """Classify one candidate against current facts. Pure, never raises on prose."""
    base = {
        "memory_key": candidate.memory_key,
        "candidate_value": candidate.value,
        "provenance": candidate.provenance,
        "source_event_id": candidate.source_event_id,
    }
    current = _current_for_key(current_facts, candidate.memory_key)
    if current is None:
        return ConflictRecord(
            **base,
            previous_value=None,
            kind=ConflictKind.NO_CONFLICT,
            resolution=ConflictResolution.ACCEPT,
            reason="no current fact for key; validation gate decides promotion",
        )
    if _normalize(current.value) == _normalize(candidate.value):
        return ConflictRecord(
            **base,
            previous_value=current.value,
            kind=ConflictKind.REINFORCEMENT,
            resolution=ConflictResolution.CONFIRM,
            reason="repeated confirmation; reinforce without new row",
        )
    if is_correction:
        return ConflictRecord(
            **base,
            previous_value=current.value,
            kind=ConflictKind.CORRECTION,
            resolution=ConflictResolution.SUPERSEDE,
            supersedes_id=str(current.id),
            is_correction=True,
            reason=f"fan correction: {current.value!r} was erroneous, {candidate.value!r} stands",
        )
    if candidate.explicitly_stated or candidate.confidence >= PROMOTION_GATE:
        return ConflictRecord(
            **base,
            previous_value=current.value,
            kind=ConflictKind.DIRECT_CONTRADICTION,
            resolution=ConflictResolution.SUPERSEDE,
            supersedes_id=str(current.id),
            reason=f"newest verified wins for now: {current.value!r} -> {candidate.value!r}",
        )
    return ConflictRecord(
        **base,
        previous_value=current.value,
        kind=ConflictKind.DIRECT_CONTRADICTION,
        resolution=ConflictResolution.HOLD,
        supersedes_id=str(current.id),
        reason="weak inference cannot displace verified current; hold candidate",
    )
