"""Episode manager (Stage B3). Candidate events in, episode plans out.

Caller: FUTURE turn pipeline after extraction/validation (Stage C persists
via `repository.create_memory_episode`). No I/O here — pure classification.

Rules (Phased_Plan Phase 7/13 + sunny_upgrade_v2 §11-12 + 05_MEMORY_SPEC):
- Episodes preserve why information matters, not just the fact.
- Life events / important dates / promises flag follow-up candidates with a
  hint (open-loop creation itself is Stage C).
- Engagement signals are evidence, not events — no episode.
- Summaries bounded (observability: no raw dumps); salience from importance.
- Purchase/commerce happenings map to PURCHASE/POST_PURCHASE history only;
  commerce truth never invented here.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from relationship_v2.domain.memory import MemoryEpisodeType
from relationship_v2.services.memory_extraction import CandidateKind, MemoryCandidate

logger = logging.getLogger("sunny.v2.episode_manager")

MAX_SUMMARY_CHARS = 280

_SALIENCE = {"critical": 0.9, "high": 0.75, "normal": 0.5, "low": 0.3}

# Candidate category -> (episode type, follow-up candidate).
_CATEGORY_MAP: dict[str, tuple[MemoryEpisodeType, bool]] = {
    "life_event": (MemoryEpisodeType.IMPORTANT_EVENT, True),
    "important_dates": (MemoryEpisodeType.IMPORTANT_EVENT, True),
    "commitment": (MemoryEpisodeType.PROMISE, True),
    "family": (MemoryEpisodeType.PERSONAL_DISCOVERY, False),
    "identity": (MemoryEpisodeType.PERSONAL_DISCOVERY, False),
    "occupation": (MemoryEpisodeType.PERSONAL_DISCOVERY, False),
    "preferences": (MemoryEpisodeType.SHARED_INTEREST, False),
    "turn": (MemoryEpisodeType.PERSONAL_DISCOVERY, False),
}


class EpisodePlan(BaseModel):
    episode_type: MemoryEpisodeType
    summary: str = Field(min_length=1, max_length=1024)
    salience: float = Field(ge=0.0, le=1.0)
    follow_up_candidate: bool = False
    follow_up_hint: str | None = None
    reason: str = Field(min_length=1)
    provenance: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)

    model_config = {"frozen": True}


def _follow_hint(category: str, memory_key: str) -> str | None:
    if category == "life_event":
        return "ask how it went at a natural moment"
    if category == "important_dates":
        return "acknowledge when the date arrives"
    if category == "commitment":
        return "follow through on the commitment"
    if memory_key == "upcoming_event":
        return "ask how it went at a natural moment"
    return None


def plan_episode(candidate: MemoryCandidate) -> EpisodePlan | None:
    """Classify one candidate into an episode plan, or None (no event).

    Never raises on candidate content; malformed scope returns None.
    """
    try:
        if candidate.kind == CandidateKind.SIGNAL:
            return None
        if not candidate.value or not candidate.value.strip():
            return None
        if not candidate.provenance or not candidate.source_event_id:
            return None
        ep_type, follow_up = _CATEGORY_MAP.get(
            candidate.category,
            (MemoryEpisodeType.PERSONAL_DISCOVERY, False),
        )
        salience = _SALIENCE.get(candidate.importance, 0.5)
        summary = candidate.value.strip()[:MAX_SUMMARY_CHARS]
        hint = _follow_hint(candidate.category, candidate.memory_key) if follow_up else None
        return EpisodePlan(
            episode_type=ep_type,
            summary=summary,
            salience=salience,
            follow_up_candidate=follow_up,
            follow_up_hint=hint,
            reason=f"{candidate.kind.value}:{candidate.category}/{candidate.memory_key}",
            provenance=candidate.provenance,
            source_event_id=candidate.source_event_id,
        )
    except Exception:
        logger.exception("episode planning failed (fail-open None)")
        return None


def plan_candidates(candidates: list[MemoryCandidate]) -> list[EpisodePlan]:
    """Plan episodes for a batch, skipping non-events. Deterministic order."""
    out: list[EpisodePlan] = []
    for c in candidates:
        plan = plan_episode(c)
        if plan is not None:
            out.append(plan)
    return out
