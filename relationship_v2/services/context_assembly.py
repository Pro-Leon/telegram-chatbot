"""Context assembly (Phase 5).

Caller: FUTURE response engine (Phase 7) per generation. Pure derivation over
caller-supplied section inputs (Phase 2 snapshots, Phase 3 retrieval, Phase 4
state, commerce confirmations). No DB I/O, no LLM, no V2→commerce calls here.

Rules (CONTEXT_ASSEMBLY.md):
- Composition order fixed; budgets per section; total bounded.
- Prioritization: constraints > commerce > relationship > recent conversation
  > long-term memory > background. Overflow truncates lowest priority first,
  never silently (dropped logged per section).
- Freshness: stale snapshots excluded + named in stale_sections.
- Conflict resolution precedence + log: commerce > memory; current > history;
  fan corrections > extractions; boundaries > strategy.
- Grounding: creator identity passed from system config (never conversation
  text); fan identity from transport scope; commerce only from confirmations.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from relationship_v2.domain.assembled import (
    AssembledContext,
    ConflictResolution,
    ContextSection,
)

logger = logging.getLogger("sunny.v2.context_assembly")

# Deterministic char budgets (proxy for tokens; documented, reviewable).
SECTION_BUDGETS: dict[str, int] = {
    "system": 400,
    "creator": 400,
    "fan_identity": 200,
    "relationship_state": 800,
    "long_term_memory": 1200,
    "episodic_memory": 1000,
    "recent_conversation": 1200,
    "conversation_state": 300,
    "engagement_signals": 400,
    "escalation_state": 300,
    "commerce_context": 800,
    "response_constraints": 600,
}
COMPOSITION_ORDER: tuple[str, ...] = (
    "system",
    "creator",
    "fan_identity",
    "relationship_state",
    "long_term_memory",
    "episodic_memory",
    "recent_conversation",
    "conversation_state",
    "engagement_signals",
    "escalation_state",
    "commerce_context",
    "response_constraints",
)
# Truncation priority: last-to-drop wins. Constraints + commerce survive longest.
DROP_ORDER: tuple[str, ...] = (
    "system",
    "creator",
    "fan_identity",
    "engagement_signals",
    "escalation_state",
    "long_term_memory",
    "episodic_memory",
    "conversation_state",
    "recent_conversation",
    "relationship_state",
    "commerce_context",
    "response_constraints",
)
TOTAL_BUDGET_CHARS = 6000


def _truncate_lines(lines: list[str], budget: int) -> tuple[list[str], int, bool]:
    kept: list[str] = []
    used = 0
    dropped_chars = 0
    for line in lines:
        if used + len(line) + 1 <= budget:
            kept.append(line)
            used += len(line) + 1
        else:
            dropped_chars += len(line) + 1
    return kept, dropped_chars, dropped_chars > 0


def resolve_conflicts(
    memory_lines: list[str],
    commerce_lines: list[str],
    boundary_lines: list[str],
    current_fact_keys: set[str],
    historical_fact_keys: set[str],
) -> tuple[list[str], list[ConflictResolution]]:
    """Precedence: commerce > memory; current > historical; boundaries > strategy."""
    resolutions: list[ConflictResolution] = []
    memory_by_key: dict[str, str] = {}
    for line in memory_lines:
        key = line.split("=", 1)[0].strip().lower() if "=" in line else line.strip().lower()
        memory_by_key.setdefault(key, line)
    # Historical facts lose to current facts on the same key.
    for key in historical_fact_keys & current_fact_keys:
        if key in memory_by_key:
            resolutions.append(
                ConflictResolution(
                    winner=f"current:{key}", loser=f"historical:{key}", rule="current_beats_history"
                )
            )
    resolved = list(dict.fromkeys(memory_lines))  # dedupe, keep order
    # Commerce confirmations beat any memory line claiming commerce truth.
    commerce_text = " ".join(commerce_lines).lower()
    if commerce_lines:
        for line in list(resolved):
            low = line.lower()
            if (
                any(w in low for w in ("price", "purchase", "ownership", "offer"))
                and low not in commerce_text
            ):
                resolved.remove(line)
                resolutions.append(
                    ConflictResolution(
                        winner="commerce_confirmation",
                        loser=line[:60],
                        rule="commerce_beats_memory",
                    )
                )
    # Boundaries beat strategy preferences: keep all boundary lines, drop
    # conflicting strategy lines mentioning the bounded topic.
    bounded_topics = {b.lower() for b in boundary_lines}
    final = list(resolved)
    for line in list(final):
        if line.lower() in bounded_topics and line not in boundary_lines:
            final.remove(line)
            resolutions.append(
                ConflictResolution(
                    winner="boundary", loser=line[:60], rule="boundary_beats_strategy"
                )
            )
    return final, resolutions


def assemble(
    generation_id: str,
    creator_id: int,
    user_id: int,
    section_inputs: dict[str, list[str]],
    stale: set[str] | None = None,
    memory_lines: list[str] | None = None,
    commerce_lines: list[str] | None = None,
    boundary_lines: list[str] | None = None,
    current_fact_keys: set[str] | None = None,
    historical_fact_keys: set[str] | None = None,
    conversation_id: str | None = None,
    now: datetime | None = None,
) -> AssembledContext:
    """Build the bounded context. Stale sections excluded, never current."""
    if creator_id <= 0 or user_id <= 0:
        raise ValueError("scope must be positive (fail-closed)")
    if not generation_id:
        raise ValueError("generation_id required")
    ts = now or datetime.now(UTC)
    stale_set = stale or set()
    mem = memory_lines or []
    com = commerce_lines or []
    bnd = boundary_lines or []
    resolved_memory, resolutions = resolve_conflicts(
        mem, com, bnd, current_fact_keys or set(), historical_fact_keys or set()
    )
    # Swap resolved memory lines into the long-term section input.
    effective_inputs = dict(section_inputs)
    if "long_term_memory" in effective_inputs:
        effective_inputs["long_term_memory"] = (
            resolved_memory or effective_inputs["long_term_memory"]
        )
    sections: list[ContextSection] = []
    stale_sections: list[str] = []
    for name in COMPOSITION_ORDER:
        if name in stale_set:
            stale_sections.append(name)
            sections.append(
                ContextSection(
                    name=name, lines=[], budget_chars=SECTION_BUDGETS[name], truncated=False
                )
            )
            continue
        raw = list(effective_inputs.get(name, []))
        kept, dropped, truncated = _truncate_lines(raw, SECTION_BUDGETS[name])
        sections.append(
            ContextSection(
                name=name,
                lines=kept,
                budget_chars=SECTION_BUDGETS[name],
                truncated=truncated,
                dropped_chars=dropped,
            )
        )
    total = sum(sum(len(line) + 1 for line in s.lines) for s in sections)
    # Global bound: truncate lowest-priority sections first, log drops.
    if total > TOTAL_BUDGET_CHARS:
        by_name = {s.name: s for s in sections}
        for name in DROP_ORDER:
            if total <= TOTAL_BUDGET_CHARS:
                break
            sec = by_name[name]
            if not sec.lines:
                continue
            # Drop whole lowest-priority section tail-first: keep first line only
            # for traceability, drop the rest.
            keep = sec.lines[:1]
            dropped_extra = sum(len(line) + 1 for line in sec.lines[1:])
            freed = sum(len(line) + 1 for line in sec.lines) - sum(len(line) + 1 for line in keep)
            by_name[name] = ContextSection(
                name=name,
                lines=keep,
                budget_chars=sec.budget_chars,
                truncated=True,
                dropped_chars=sec.dropped_chars + dropped_extra,
            )
            total -= freed
        sections = [by_name[n] for n in COMPOSITION_ORDER]
    return AssembledContext(
        generation_id=generation_id,
        conversation_id=conversation_id,
        creator_id=creator_id,
        user_id=user_id,
        sections=sections,
        resolutions=resolutions,
        stale_sections=sorted(stale_sections),
        total_chars=total,
        as_of=ts,
    )
