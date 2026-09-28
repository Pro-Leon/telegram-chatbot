"""Relationship summary distiller (Stage F5). Stores in, cache out.

Caller: FUTURE assembly/pipeline (F9+) or offline job. Pure distillation of
facts + episodes + an optional reasoning reading into a bounded, provenance-
linked summary. The summary is a CACHE, never the source of truth: it is
regenerable from inputs (same inputs -> same output, tested) and carries
the fact/episode ids it was built from so staleness is detectable.
Persistence of the cache (e.g. inside `v2_relationship_snapshots.snapshot`)
belongs to the caller; no new table here by design.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from relationship_v2.domain.memory import MemoryEpisode, MemoryFact
from relationship_v2.services.memory_retrieval import IMPORTANCE_WEIGHT

logger = logging.getLogger("sunny.v2.summary")

SUMMARY_BUDGET_CHARS = 1200


class Summary(BaseModel):
    lines: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    episode_ids: list[str] = Field(default_factory=list)
    total_chars: int = Field(ge=0)
    truncated: bool = False
    provenance: str = Field(min_length=1)
    as_of: datetime

    model_config = {"frozen": True}


def _fact_rank(f: MemoryFact) -> tuple:
    return (
        -IMPORTANCE_WEIGHT.get(f.importance.value, 1.0),
        -f.confidence,
        str(f.id),
    )


def _episode_rank(e: MemoryEpisode) -> tuple:
    return (-e.salience, str(e.id))


def build_summary(
    facts: list[MemoryFact],
    episodes: list[MemoryEpisode],
    reading_lines: list[str] | None = None,
    max_chars: int = SUMMARY_BUDGET_CHARS,
    provenance: str = "",
    now: datetime | None = None,
) -> Summary:
    """Distill a bounded summary. Pure; deterministic given inputs."""
    if not provenance:
        raise ValueError("provenance required")
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")
    ts = now or datetime.now(UTC)
    candidate_lines: list[str] = []
    for line in reading_lines or []:
        text = " ".join(line.split())[:280]
        if text:
            candidate_lines.append(text)
    ordered_facts = sorted(facts, key=_fact_rank)
    fact_ids = [str(f.id) for f in ordered_facts]
    for f in ordered_facts:
        candidate_lines.append(" ".join(f"{f.memory_key}={f.value}".split())[:200])
    ordered_episodes = sorted(episodes, key=_episode_rank)
    episode_ids = [str(e.id) for e in ordered_episodes]
    for e in ordered_episodes:
        candidate_lines.append(" ".join(f"{e.episode_type.value}: {e.summary}".split())[:200])
    kept: list[str] = []
    used = 0
    truncated = False
    for line in candidate_lines:
        if used + len(line) + 1 <= max_chars:
            kept.append(line)
            used += len(line) + 1
        else:
            truncated = True
    return Summary(
        lines=kept,
        fact_ids=fact_ids,
        episode_ids=episode_ids,
        total_chars=used,
        truncated=truncated,
        provenance=provenance,
        as_of=ts,
    )
