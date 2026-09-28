"""Proactive recall ranker (Stage C5). Candidates in, small coherent set out.

Caller: FUTURE context assembly (Stage D). The caller builds candidates
from V2 stores (facts, episodes, open loops, patterns, intimate signals);
this module scores and selects purely. No I/O, no LLM.

Ranking (Phased_Plan Phase 14 + sunny_upgrade_v2 §22 + 05_MEMORY_SPEC):
importance x unresolved x time-sensitivity x novelty x follow-up value x
current relevance, with a repetition penalty for recently-recalled items
(negative callback history) so recall stays natural, never nagging. A
nonzero relevance floor lets important unresolved items surface with zero
topic overlap (e.g. "hey" -> job interview); the planner still decides.
"""

from __future__ import annotations

import logging
import math

from pydantic import BaseModel, Field

logger = logging.getLogger("sunny.v2.proactive_recall")

IMPORTANCE_WEIGHT = {"critical": 4.0, "high": 2.0, "normal": 1.0, "low": 0.5}
RECALL_PENALTY_DAYS = 3.0
DEFAULT_BUDGET = 3


class RecallCandidate(BaseModel):
    """One recall option. Built by the caller; scored here."""

    ref_id: str = Field(min_length=1)
    kind: str = Field(min_length=1, max_length=32)
    importance: str = Field(default="normal", pattern="^(critical|high|normal|low)$")
    salience: float = Field(ge=0.0, le=1.0, default=0.5)
    age_days: float = Field(ge=0.0, default=0.0)
    unresolved: bool = False
    due: bool = False
    relevance: float = Field(ge=0.0, le=1.0, default=0.0)
    recalled_days_ago: float | None = Field(ge=0.0, default=None)

    model_config = {"frozen": True}


class RecalledItem(BaseModel):
    ref_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    score: float = Field(ge=0.0)
    reason: str = Field(min_length=1)

    model_config = {"frozen": True}


class RecallSelection(BaseModel):
    items: list[RecalledItem] = Field(default_factory=list)
    dropped: int = Field(ge=0, default=0)

    model_config = {"frozen": True}


def score_candidate(c: RecallCandidate) -> float:
    """Deterministic recall score. Higher = more recall-worthy now."""
    importance = IMPORTANCE_WEIGHT.get(c.importance, 1.0)
    novelty = 1.0 + min(1.0, c.age_days / 30.0)
    relevance = 0.3 + 0.7 * c.relevance
    score = importance * (0.5 + c.salience) * novelty * relevance
    if c.unresolved:
        score *= 1.5
    if c.due:
        score *= 2.0
    if c.recalled_days_ago is not None and c.recalled_days_ago < RECALL_PENALTY_DAYS:
        score *= c.recalled_days_ago / RECALL_PENALTY_DAYS
    return round(score, 6)


def _reason(c: RecallCandidate) -> str:
    bits = [c.importance]
    if c.due:
        bits.append("due")
    elif c.unresolved:
        bits.append("unresolved")
    if c.relevance >= 0.5:
        bits.append("relevant")
    elif c.relevance == 0.0:
        bits.append("callback")
    return "+".join(bits)


def rank_candidates(
    candidates: list[RecallCandidate],
    budget: int = DEFAULT_BUDGET,
) -> RecallSelection:
    """Select up to `budget` items, deterministically ordered. Never raises."""
    if budget < 1:
        raise ValueError("budget must be >= 1")
    try:
        scored = [(score_candidate(c), c) for c in candidates]
    except Exception:
        logger.exception("recall scoring failed (fail-open empty)")
        return RecallSelection(items=[], dropped=len(candidates))
    scored.sort(key=lambda t: (-t[0], t[1].ref_id))
    picked = scored[:budget]
    return RecallSelection(
        items=[
            RecalledItem(ref_id=c.ref_id, kind=c.kind, score=s, reason=_reason(c))
            for s, c in picked
        ],
        dropped=max(0, len(scored) - len(picked)),
    )


def recency_decay(age_days: float, half_life_days: float = 14.0) -> float:
    """Recency multiplier for time-sensitive ranking. Pure helper."""
    if age_days < 0 or half_life_days <= 0:
        raise ValueError("age_days >= 0 and half_life_days > 0 required")
    return math.exp(-age_days / half_life_days)
