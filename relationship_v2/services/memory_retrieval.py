"""Hybrid memory retrieval (Phase 3).

Caller: FUTURE context assembly (Phase 5). Pure ranking functions here;
async DB-backed retrieval lives in repository (same scoring). No exact-topic
dependence: importance + recency + relevance combine; callback tier ranks
high-importance unresolved memories even with zero topic overlap.

Budget: bounded output, deterministic truncation with dropped counts.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from relationship_v2.domain.memory import MemoryEpisode, MemoryFact, MemoryFactStatus

logger = logging.getLogger("sunny.v2.memory_retrieval")

IMPORTANCE_WEIGHT = {"critical": 4.0, "high": 2.0, "normal": 1.0, "low": 0.5}
RECENCY_HALF_LIFE_DAYS = 14.0


class RetrievalBudget(BaseModel):
    tier1_max: int = Field(ge=1, default=6)
    tier2_max: int = Field(ge=0, default=6)
    tier3_max: int = Field(ge=0, default=3)

    model_config = {"frozen": True}


class RetrievalResult(BaseModel):
    tier1_ids: list[str] = Field(default_factory=list)
    tier2_ids: list[str] = Field(default_factory=list)
    tier3_ids: list[str] = Field(default_factory=list)
    dropped: int = 0

    model_config = {"frozen": True}


def _tokens(text: str) -> set[str]:
    return {t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if t}


def _recency_boost(now: datetime, then: datetime) -> float:
    age_days = max(0.0, (now - then).total_seconds() / 86400.0)
    return math.exp(-age_days / RECENCY_HALF_LIFE_DAYS)


def score_fact(fact: MemoryFact, query: str, now: datetime | None = None) -> float:
    """Importance + recency + relevance. Deterministic."""
    ts = now or datetime.now(UTC)
    q = _tokens(query)
    f = _tokens(f"{fact.memory_key} {fact.value} {fact.category}")
    overlap = len(q & f) / max(1, len(q)) if q else 0.0
    relevance = 0.3 + 0.7 * overlap  # nonzero base: no exact-topic dependence
    importance = IMPORTANCE_WEIGHT.get(fact.importance.value, 1.0)
    recency = 0.5 + 0.5 * _recency_boost(ts, fact.effective_from)
    current_boost = 1.5 if fact.status == MemoryFactStatus.CURRENT else 1.0
    return importance * relevance * recency * current_boost * (0.5 + fact.confidence)


def score_episode(episode: MemoryEpisode, query: str, now: datetime | None = None) -> float:
    ts = now or datetime.now(UTC)
    q = _tokens(query)
    e = _tokens(f"{episode.episode_type.value} {episode.summary}")
    overlap = len(q & e) / max(1, len(q)) if q else 0.0
    relevance = 0.3 + 0.7 * overlap
    recency = 0.5 + 0.5 * _recency_boost(ts, episode.created_at)
    return relevance * recency * (0.5 + episode.salience)


def callback_score(importance: str, salience: float, age_days: float, unresolved: bool) -> float:
    """Proactive recall score: no topic overlap required."""
    base = IMPORTANCE_WEIGHT.get(importance, 1.0)
    novelty = 1.0 + min(1.0, age_days / 30.0)
    return base * (0.5 + salience) * novelty * (1.5 if unresolved else 1.0)


def retrieve_tiered(
    facts: list[MemoryFact],
    episodes: list[MemoryEpisode],
    query: str,
    budget: RetrievalBudget | None = None,
    now: datetime | None = None,
) -> RetrievalResult:
    """Tier 1 always-relevant, Tier 2 context-relevant, Tier 3 callbacks."""
    b = budget or RetrievalBudget()
    ts = now or datetime.now(UTC)
    ranked_facts = sorted(((score_fact(f, query, ts), f) for f in facts), key=lambda t: -t[0])
    ranked_episodes = sorted(
        ((score_episode(e, query, ts), e) for e in episodes), key=lambda t: -t[0]
    )
    # Tier 1: current facts + top recent episodes (always relevant).
    tier1: list[str] = []
    for _, f in ranked_facts:
        if f.status == MemoryFactStatus.CURRENT and len(tier1) < b.tier1_max:
            tier1.append(str(f.id))
    for _, e in ranked_episodes:
        if len(tier1) >= b.tier1_max:
            break
        eid = str(e.id)
        if eid not in tier1:
            tier1.append(eid)
    # Tier 2: next-best by relevance not already selected.
    selected = set(tier1)
    tier2: list[str] = []
    for _, f in ranked_facts:
        fid = str(f.id)
        if fid not in selected and len(tier2) < b.tier2_max:
            tier2.append(fid)
            selected.add(fid)
    # Tier 3: callback candidates — high importance, older, salient.
    callbacks: list[tuple[float, str]] = []
    for f in facts:
        if str(f.id) in selected:
            continue
        if f.importance.value not in ("critical", "high"):
            continue
        age = max(0.0, (ts - f.effective_from).total_seconds() / 86400.0)
        if age < 1.0:
            continue
        callbacks.append((callback_score(f.importance.value, f.confidence, age, True), str(f.id)))
    callbacks.sort(key=lambda t: -t[0])
    tier3 = [cid for _, cid in callbacks[: b.tier3_max]]
    total_ranked = len(ranked_facts) + len(ranked_episodes)
    dropped = max(0, total_ranked - len(tier1) - len(tier2) - len(tier3))
    return RetrievalResult(tier1_ids=tier1, tier2_ids=tier2, tier3_ids=tier3, dropped=dropped)
