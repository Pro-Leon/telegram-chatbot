"""Extended relationship derivations (Stage F6). Evidence in, bands out.

Companions to `relationship_context.derive_familiarity/derive_comfort`:
engagement (active participation), reciprocity (mutual vs assistant-led),
and intimacy trajectory (none -> playful -> romantic -> intimate ->
deeply_intimate from accumulated intimate signals). Pure, deterministic,
documented v1 heuristics — the LLM may phrase them, never computes them.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("sunny.v2.relationship_derivations")


def derive_engagement(
    interaction_count: int,
    meaningful_count: int,
    days_since_last: int | None,
) -> str:
    """Participation band. Stale relationships cap at moderate."""
    if interaction_count < 0 or meaningful_count < 0:
        raise ValueError("counts must be non-negative")
    if interaction_count == 0:
        return "low"
    recent = days_since_last is None or days_since_last <= 14
    if meaningful_count >= 10 and recent:
        return "high"
    if interaction_count >= 5 and (recent or meaningful_count >= 3):
        return "moderate"
    return "low"


def derive_reciprocity(inbound_count: int, sent_count: int) -> str:
    """Mutuality band from turn balance. No turns yet -> low."""
    if inbound_count < 0 or sent_count < 0:
        raise ValueError("counts must be non-negative")
    total = inbound_count + sent_count
    if total == 0:
        return "low"
    if total < 4:
        return "moderate" if total >= 2 else "low"
    ratio = min(inbound_count, sent_count) / max(inbound_count, sent_count)
    if ratio >= 0.5:
        return "high"
    return "moderate"


def derive_intimacy_trajectory(
    comfort_count: int,
    preference_count: int,
    milestone_count: int,
    boundary_count: int = 0,
) -> str:
    """Continuity band from intimate-signal accumulation. History, not permission."""
    for name, val in (
        ("comfort", comfort_count),
        ("preference", preference_count),
        ("milestone", milestone_count),
        ("boundary", boundary_count),
    ):
        if val < 0:
            raise ValueError(f"{name}_count must be non-negative")
    total = comfort_count + preference_count + milestone_count
    if total == 0:
        return "none"
    if total >= 6 or milestone_count >= 3:
        return "deeply_intimate"
    if total >= 3 or milestone_count >= 1:
        return "intimate"
    if preference_count >= 1:
        return "romantic"
    return "playful"


def find_due_loops(
    loops: list[dict],
    now,
    is_due_fn=None,
) -> list[dict]:
    """Filter actionable loops whose expected time passed. Pure scan.

    Execution (marking DUE rows) belongs to a later sweep job; this helper
    only identifies candidates so the sweep stays reviewable.
    """
    from relationship_v2.domain.open_loop import OpenLoopStatus
    from relationship_v2.services.open_loops import is_due

    check = is_due_fn or is_due
    out = []
    for loop in loops:
        try:
            status = OpenLoopStatus(str(loop.get("status", "open")))
        except ValueError:
            continue
        try:
            if status in (OpenLoopStatus.OPEN, OpenLoopStatus.REFERENCED) and check(
                status, loop.get("expected_at"), now
            ):
                out.append(loop)
        except Exception:
            logger.debug("skipping malformed loop row in due scan", exc_info=True)
            continue
    return out
