"""Relationship reasoning (Stage C4). Evidence in, cited answers out.

Caller: FUTURE context assembly (Stage D). Composes the Phase 2 snapshot
derivations with store counts into the Phase 12 questions: Who? How long?
What is it like? Comfort? Recent? Responds to? Unfinished? Intimate
thread? Away? Pure and deterministic — the LLM may phrase these answers,
it never computes them.

Continuity bands (v1 heuristics, documented): fragile when new/sparse or
long-absent; strong with milestones/episodes plus recent activity; stable
otherwise. Thresholds tune with shadow data, never per-turn LLM scores.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from relationship_v2.domain.context import (
    RelationshipContextSnapshot,
    RelationshipEvidence,
)
from relationship_v2.domain.relationship import RelationshipLifecycle

logger = logging.getLogger("sunny.v2.relationship_reasoning")


class StoreCounts(BaseModel):
    """Counts assembled by the caller from V2 tables. No content, just shape."""

    fact_count: int = Field(ge=0, default=0)
    episode_count: int = Field(ge=0, default=0)
    open_loop_count: int = Field(ge=0, default=0)
    due_loop_count: int = Field(ge=0, default=0)
    learned_pattern_count: int = Field(ge=0, default=0)
    intimate_signal_count: int = Field(ge=0, default=0)

    model_config = {"frozen": True}


class RelationshipReading(BaseModel):
    continuity: str = Field(pattern="^(fragile|stable|strong)$")
    tenure_answer: str = Field(min_length=1)
    comfort_answer: str = Field(min_length=1)
    engagement_answer: str = Field(min_length=1)
    unfinished_answer: str = Field(min_length=1)
    intimate_answer: str = Field(min_length=1)
    absence_answer: str = Field(min_length=1)
    questions_answered: list[str] = Field(default_factory=list)
    provenance: str = Field(min_length=1)

    model_config = {"frozen": True}


def derive_continuity(
    evidence: RelationshipEvidence, counts: StoreCounts
) -> str:
    """Continuity band from durability + recency evidence."""
    absence = evidence.days_since_last
    if evidence.interaction_count < 5 and evidence.milestone_count == 0:
        return "fragile"
    if absence is not None and absence > 30:
        return "fragile"
    recent = absence is None or absence <= 14
    durable = (
        evidence.milestone_count >= 3
        or counts.episode_count >= 8
        or evidence.interaction_count >= 50
    )
    if durable and recent:
        return "strong"
    return "stable"


def reason_about(
    snapshot: RelationshipContextSnapshot,
    evidence: RelationshipEvidence,
    counts: StoreCounts,
    provenance: str,
) -> RelationshipReading:
    """Answer the Phase 12 questions from snapshot + evidence + counts."""
    if not provenance:
        raise ValueError("provenance required")
    if (
        snapshot.creator_id <= 0
        or snapshot.user_id <= 0
        or evidence.interaction_count < 0
    ):
        raise ValueError("scope/evidence must be valid (fail-closed)")
    continuity = derive_continuity(evidence, counts)
    tenure = evidence.days_since_first
    tenure_answer = (
        f"known {tenure}d; {evidence.interaction_count} interactions, "
        f"{evidence.milestone_count} milestones; lifecycle {snapshot.lifecycle.value}"
        if tenure is not None
        else f"{evidence.interaction_count} interactions; lifecycle {snapshot.lifecycle.value}"
    )
    comfort_answer = (
        f"comfort {snapshot.comfort}; familiarity {snapshot.familiarity}; "
        f"continuity {continuity}"
    )
    engagement_answer = (
        f"{counts.learned_pattern_count} learned patterns; "
        f"{counts.episode_count} episodes; buyer {snapshot.buyer_class}"
    )
    unfinished_answer = (
        f"{counts.open_loop_count} open threads ({counts.due_loop_count} due); "
        f"{counts.fact_count} durable facts"
    )
    intimate_answer = (
        f"{counts.intimate_signal_count} intimate signals; "
        f"boundaries {'present' if snapshot.has_boundaries else 'none recorded'}"
    )
    absence = evidence.days_since_last
    if snapshot.reentry is not None:
        absence_answer = snapshot.reentry.resume_summary
    elif absence is None:
        absence_answer = "no absence data; treat as current"
    elif absence < 3:
        absence_answer = f"active ({absence}d since last); continue current thread"
    else:
        absence_answer = f"away {absence}d; resume, never cold-start"
    questions = [
        "who",
        "how_long",
        "relationship_like",
        "comfort",
        "recent",
        "responds_to",
        "unfinished",
        "intimate_thread",
        "away",
    ]
    void = snapshot.lifecycle == RelationshipLifecycle.NEW and evidence.interaction_count == 0
    if void:
        questions = ["who", "how_long"]
    return RelationshipReading(
        continuity=continuity,
        tenure_answer=tenure_answer,
        comfort_answer=comfort_answer,
        engagement_answer=engagement_answer,
        unfinished_answer=unfinished_answer,
        intimate_answer=intimate_answer,
        absence_answer=absence_answer,
        questions_answered=questions,
        provenance=provenance,
    )
