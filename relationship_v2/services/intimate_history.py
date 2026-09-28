"""Intimate continuity classifier (Stage C3). Candidates in, plans out.

Caller: FUTURE turn pipeline (Stage C persists via
`repository.record_intimate_signal`). No I/O here — pure mapping.

Rules (Phased_Plan Phase 11 + sunny_upgrade_v2 §16 + 09_SECURITY §data
minimization): abstract relational signals only. Explicit intimate text
arrives pre-abstracted from extraction; this module never echoes raw
content. Stated boundaries are critical; comfort/preference signals stay
below the promotion gate until repeated (pipeline holds them validated).
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from relationship_v2.domain.intimacy import IntimateKind
from relationship_v2.services.memory_extraction import CandidateKind, MemoryCandidate

logger = logging.getLogger("sunny.v2.intimate_history")


class IntimatePlan(BaseModel):
    kind: IntimateKind
    signal: str = Field(min_length=1, max_length=280)
    confidence: float = Field(ge=0.0, le=1.0)
    importance: str = Field(default="normal", pattern="^(critical|high|normal|low)$")
    reason: str = Field(min_length=1)
    provenance: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)

    model_config = {"frozen": True}


def plan_intimate(candidate: MemoryCandidate) -> IntimatePlan | None:
    """Map one candidate to an intimate plan, or None (not intimate).

    Never raises on candidate content; malformed scope returns None.
    """
    try:
        if not candidate.provenance or not candidate.source_event_id:
            return None
        base = {
            "provenance": candidate.provenance,
            "source_event_id": candidate.source_event_id,
        }
        if (
            candidate.kind == CandidateKind.SIGNAL
            and candidate.category == "intimate_signal"
        ):
            return IntimatePlan(
                kind=IntimateKind.COMFORT,
                signal="responsive to playful tone (abstract; no verbatim stored)",
                confidence=candidate.confidence,
                importance="normal",
                reason="abstract comfort signal from intimate turn",
                **base,
            )
        if candidate.memory_key == "boundary":
            stated = candidate.value.strip()[:200]
            return IntimatePlan(
                kind=IntimateKind.BOUNDARY,
                signal=f"stated boundary re: {stated}"[:280],
                confidence=0.9,
                importance="critical",
                reason="explicit fan boundary; always honored",
                **base,
            )
        return None
    except Exception:
        logger.exception("intimate planning failed (fail-open None)")
        return None
