"""LLM observation gate (Stage B2). Proposals in, dispositions out.

Caller: FUTURE turn pipeline after generation (Stage C persists PROMOTE_*).
LLM is advisory only: this module takes caller-supplied parsed proposals
(never calls providers — no provider imports) and routes each through the
same gates as deterministic candidates:

  validation (provenance/creator/commerce/confidence)
    -> contradiction classification (explicit record)
      -> persistence policy (PROMOTE_CURRENT / PROMOTE_VALIDATED / HOLD / REJECT)

Rules (sunny_upgrade_v2 §26-29 + §41 confidence bands):
- <0.60 never becomes durable important memory.
- Creator identity / commerce truth from prose always rejected.
- Repeated confirmation reinforces (HOLD, no new row); weak inference
  contradicting current is held, never supersedes.
- Malformed items are rejected individually; the batch never raises on
  proposal content. Missing scope/provenance raises (programmer error).
"""

from __future__ import annotations

import logging
from enum import Enum

from pydantic import BaseModel, Field

from relationship_v2.domain.memory import MemoryFact
from relationship_v2.services.contradiction_resolver import (
    ConflictRecord,
    ConflictResolution,
    resolve_conflict,
)
from relationship_v2.services.memory_extraction import MemoryCandidate
from relationship_v2.services.memory_validation import (
    ValidationCode,
    validate_fact_candidate,
)

logger = logging.getLogger("sunny.v2.llm_observation")

LLM_PROPOSAL_VERSION = "llm-proposal-1"


class ObservationDisposition(str, Enum):
    PROMOTE_CURRENT = "promote_current"
    PROMOTE_VALIDATED = "promote_validated"
    HOLD = "hold"
    REJECT = "reject"


class MemoryObservation(BaseModel):
    category: str = Field(min_length=1, max_length=64)
    memory_key: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=1024)
    confidence: float = Field(ge=0.0, le=1.0)
    importance: str = Field(default="normal")
    explicitly_stated: bool = False

    model_config = {"frozen": True}


class PatternObservation(BaseModel):
    pattern: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1024)
    confidence: float = Field(ge=0.0, le=1.0)

    model_config = {"frozen": True}


class ObservationBatch(BaseModel):
    generation_id: str = Field(min_length=1)
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    source_event_id: str = Field(min_length=1)
    provenance: str = Field(min_length=1)
    memories: list[MemoryObservation] = Field(default_factory=list)
    patterns: list[PatternObservation] = Field(default_factory=list)

    model_config = {"frozen": True}


class ObservationResult(BaseModel):
    kind: str = Field(min_length=1)
    key: str = Field(min_length=1)
    disposition: ObservationDisposition
    validation_code: str = Field(min_length=1)
    conflict: ConflictRecord | None = None
    reason: str = Field(min_length=1)

    model_config = {"frozen": True}


class ObservationReport(BaseModel):
    generation_id: str = Field(min_length=1)
    results: list[ObservationResult] = Field(default_factory=list)
    promoted: int = Field(ge=0, default=0)
    held: int = Field(ge=0, default=0)
    rejected: int = Field(ge=0, default=0)

    model_config = {"frozen": True}


_HARD_REJECT_CODES = frozenset(
    {
        ValidationCode.REJECT_MISSING_PROVENANCE,
        ValidationCode.REJECT_CREATOR_IDENTITY,
        ValidationCode.REJECT_COMMERCE_TRUTH,
    }
)


def _review_memory(
    obs: MemoryObservation,
    batch: ObservationBatch,
    current_facts: list[MemoryFact],
) -> ObservationResult:
    try:
        candidate = MemoryCandidate(
            kind="fact",
            category=obs.category,
            memory_key=obs.memory_key,
            value=obs.value,
            confidence=obs.confidence,
            importance=obs.importance,
            explicitly_stated=obs.explicitly_stated,
            extractor_version=LLM_PROPOSAL_VERSION,
            source_event_id=batch.source_event_id,
            provenance=batch.provenance,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open on proposal content; batch continues
        return ObservationResult(
            kind="memory",
            key=obs.memory_key or "unknown",
            disposition=ObservationDisposition.REJECT,
            validation_code="malformed_proposal",
            reason=f"malformed proposal rejected: {exc}",
        )
    verdict = validate_fact_candidate(candidate, current_facts)
    record = resolve_conflict(candidate, current_facts)
    if verdict.code in _HARD_REJECT_CODES:
        return ObservationResult(
            kind="memory",
            key=obs.memory_key,
            disposition=ObservationDisposition.REJECT,
            validation_code=verdict.code.value,
            conflict=record,
            reason=verdict.reason,
        )
    if verdict.code == ValidationCode.REJECT_LOW_CONFIDENCE:
        # Weak proposals are never promoted, but a contradicting weak
        # inference is held (with its conflict record) rather than dropped;
        # weak claims to important new facts are rejected outright.
        if record.resolution == ConflictResolution.HOLD:
            return ObservationResult(
                kind="memory",
                key=obs.memory_key,
                disposition=ObservationDisposition.HOLD,
                validation_code=verdict.code.value,
                conflict=record,
                reason=record.reason,
            )
        return ObservationResult(
            kind="memory",
            key=obs.memory_key,
            disposition=ObservationDisposition.REJECT,
            validation_code=verdict.code.value,
            conflict=record,
            reason=verdict.reason,
        )
    if record.resolution == ConflictResolution.CONFIRM:
        return ObservationResult(
            kind="memory",
            key=obs.memory_key,
            disposition=ObservationDisposition.HOLD,
            validation_code=verdict.code.value,
            conflict=record,
            reason="reinforcement held; no new row",
        )
    if record.resolution == ConflictResolution.HOLD:
        return ObservationResult(
            kind="memory",
            key=obs.memory_key,
            disposition=ObservationDisposition.HOLD,
            validation_code=verdict.code.value,
            conflict=record,
            reason=record.reason,
        )
    if verdict.code == ValidationCode.SUPERSEDE_CONTRADICTION and verdict.promote:
        return ObservationResult(
            kind="memory",
            key=obs.memory_key,
            disposition=ObservationDisposition.PROMOTE_CURRENT,
            validation_code=verdict.code.value,
            conflict=record,
            reason=verdict.reason,
        )
    if verdict.code == ValidationCode.ACCEPT_CURRENT:
        return ObservationResult(
            kind="memory",
            key=obs.memory_key,
            disposition=ObservationDisposition.PROMOTE_CURRENT,
            validation_code=verdict.code.value,
            conflict=record,
            reason=verdict.reason,
        )
    if verdict.code == ValidationCode.ACCEPT_VALIDATED:
        return ObservationResult(
            kind="memory",
            key=obs.memory_key,
            disposition=ObservationDisposition.PROMOTE_VALIDATED,
            validation_code=verdict.code.value,
            conflict=record,
            reason=verdict.reason,
        )
    return ObservationResult(
        kind="memory",
        key=obs.memory_key,
        disposition=ObservationDisposition.HOLD,
        validation_code=verdict.code.value,
        conflict=record,
        reason=verdict.reason,
    )


def _review_pattern(obs: PatternObservation) -> ObservationResult:
    if obs.confidence < 0.60:
        return ObservationResult(
            kind="pattern",
            key=obs.pattern,
            disposition=ObservationDisposition.REJECT,
            validation_code="reject_low_confidence",
            reason="weak pattern inference rejected below gate",
        )
    return ObservationResult(
        kind="pattern",
        key=obs.pattern,
        disposition=ObservationDisposition.HOLD,
        validation_code="accept_validated",
        reason="pattern held for evidence accumulation; never a one-event rule",
    )


def review_batch(
    batch: ObservationBatch,
    current_facts: list[MemoryFact],
) -> ObservationReport:
    """Route every proposal to a disposition. Pure; never calls providers."""
    results: list[ObservationResult] = []
    for obs in batch.memories:
        results.append(_review_memory(obs, batch, current_facts))
    for obs in batch.patterns:
        results.append(_review_pattern(obs))
    promoted = sum(
        1
        for r in results
        if r.disposition
        in (
            ObservationDisposition.PROMOTE_CURRENT,
            ObservationDisposition.PROMOTE_VALIDATED,
        )
    )
    held = sum(1 for r in results if r.disposition == ObservationDisposition.HOLD)
    rejected = sum(1 for r in results if r.disposition == ObservationDisposition.REJECT)
    return ObservationReport(
        generation_id=batch.generation_id,
        results=results,
        promoted=promoted,
        held=held,
        rejected=rejected,
    )
