"""Memory validation + contradiction resolution (Phase 3).

Caller: FUTURE conversation engine after extraction (Phase 4), before any
persistence. Owner of promotion/supersession decisions; persistence itself
stays in relationship_v2.persistence.repository.

Rules (MEMORY_ARCHITECTURE.md + 05_MEMORY_SPEC.md):
- No memory write without provenance (enforced: provenance required).
- Confidence <0.60 never promoted to durable important memory.
- Contradictions supersede (newest verified wins for now), history preserved.
- Invalid: candidate -> superseded, deletion of current without superseder.
- Creator identity / commerce claims rejected with stable codes.
"""

from __future__ import annotations

import logging
from enum import Enum

from pydantic import BaseModel, Field

from relationship_v2.domain.memory import MemoryFact, MemoryFactStatus
from relationship_v2.services.memory_extraction import (
    PROMOTION_GATE,
    MemoryCandidate,
    is_commerce_claim,
    is_creator_identity_claim,
)

logger = logging.getLogger("sunny.v2.memory_validation")


class ValidationCode(str, Enum):
    ACCEPT_CURRENT = "accept_current"
    ACCEPT_VALIDATED = "accept_validated"
    REJECT_LOW_CONFIDENCE = "reject_low_confidence"
    REJECT_CREATOR_IDENTITY = "reject_creator_identity"
    REJECT_COMMERCE_TRUTH = "reject_commerce_truth"
    REJECT_MISSING_PROVENANCE = "reject_missing_provenance"
    SUPERSEDE_CONTRADICTION = "supersede_contradiction"
    CONFIRM_EXISTING = "confirm_existing"


class ValidationVerdict(BaseModel):
    code: ValidationCode
    promote: bool = False
    supersedes_id: str | None = None
    reason: str = Field(min_length=1)

    model_config = {"frozen": True}


def _normalize(value: str) -> str:
    return " ".join(value.strip().lower().split())


def validate_fact_candidate(
    candidate: MemoryCandidate,
    current_facts: list[MemoryFact],
) -> ValidationVerdict:
    """Decide the fate of one fact candidate. Pure function."""
    if not candidate.provenance:
        return ValidationVerdict(
            code=ValidationCode.REJECT_MISSING_PROVENANCE,
            reason="provenance required",
        )
    if is_creator_identity_claim(candidate):
        return ValidationVerdict(
            code=ValidationCode.REJECT_CREATOR_IDENTITY,
            reason="creator identity never comes from conversation text",
        )
    if is_commerce_claim(candidate):
        return ValidationVerdict(
            code=ValidationCode.REJECT_COMMERCE_TRUTH,
            reason="commerce truth requires commerce confirmation",
        )
    same_key = [f for f in current_facts if f.memory_key == candidate.memory_key]
    current = next((f for f in same_key if f.status == MemoryFactStatus.CURRENT), None)
    if current is not None and _normalize(current.value) == _normalize(candidate.value):
        return ValidationVerdict(
            code=ValidationCode.CONFIRM_EXISTING,
            reason="repeated confirmation; reinforce without new row",
        )
    if current is not None and _normalize(current.value) != _normalize(candidate.value):
        # Contradiction: newest verified source wins for now; history preserved
        # via supersede (caller persists both rows transactionally).
        if candidate.confidence < PROMOTION_GATE and not candidate.explicitly_stated:
            return ValidationVerdict(
                code=ValidationCode.REJECT_LOW_CONFIDENCE,
                reason="contradiction needs explicit statement or >= gate",
            )
        return ValidationVerdict(
            code=ValidationCode.SUPERSEDE_CONTRADICTION,
            promote=True,
            supersedes_id=str(current.id),
            reason=f"supersede {current.value!r} with {candidate.value!r}",
        )
    if candidate.confidence < PROMOTION_GATE and candidate.importance in (
        "critical",
        "high",
    ):
        return ValidationVerdict(
            code=ValidationCode.REJECT_LOW_CONFIDENCE,
            reason="weak inference cannot become important fact",
        )
    if candidate.explicitly_stated:
        return ValidationVerdict(
            code=ValidationCode.ACCEPT_CURRENT,
            promote=True,
            reason="explicit statement",
        )
    return ValidationVerdict(
        code=ValidationCode.ACCEPT_VALIDATED,
        promote=True,
        reason="inference held at validated, not current",
    )


def is_valid_memory_status_transition(current: MemoryFactStatus, nxt: MemoryFactStatus) -> bool:
    """Memory lifecycle guard (STATE_MACHINES.md)."""
    allowed: dict[MemoryFactStatus, set[MemoryFactStatus]] = {
        MemoryFactStatus.CANDIDATE: {
            MemoryFactStatus.VALIDATED,
            MemoryFactStatus.CURRENT,
            MemoryFactStatus.ARCHIVED,
        },
        MemoryFactStatus.VALIDATED: {
            MemoryFactStatus.CURRENT,
            MemoryFactStatus.ARCHIVED,
        },
        MemoryFactStatus.CURRENT: {
            MemoryFactStatus.SUPERSEDED,
            MemoryFactStatus.ARCHIVED,
        },
        MemoryFactStatus.SUPERSEDED: {MemoryFactStatus.ARCHIVED},
        MemoryFactStatus.ARCHIVED: set(),
    }
    if current == nxt:
        return True
    return nxt in allowed.get(current, set())
