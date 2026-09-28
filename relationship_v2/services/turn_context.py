"""Turn-context composer (Stage D2b). V2 stores in, assembled context out.

Caller: FUTURE `get_context()` / workers (D3). Orchestrates one inbound
turn's authoritative snapshot: relationship row -> evidence -> snapshot ->
reading -> recall -> commerce -> assembly. All store access flows through
injectable ports (defaults bind the real repository + D1 commerce ports);
tests use fakes, no live DB required.

Evidence mapping (documented, reviewable):
- interactions = COUNT FanMessageReceived; meaningful = COUNT ResponseSent
- milestones = COUNT episodes (purchase/post_purchase/important_event)
- boundaries = current intimate rows of kind boundary
- tenure/absence = relationship row timestamps
- purchases = authoritative commerce count (never invented)

Degradation (FAILURE_MODES): commerce failure -> commerce section marked
stale, context still built from relationship memory; PRESENT_OFFER stays
blocked downstream on stale commerce snapshots. Persistence failures
propagate (fail-closed scope/state).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from relationship_v2.domain.assembled import AssembledContext
from relationship_v2.domain.commerce import CommerceContext
from relationship_v2.domain.context import RelationshipContextSnapshot, RelationshipEvidence
from relationship_v2.domain.relationship import RelationshipLifecycle

logger = logging.getLogger("sunny.v2.turn_context")

PROVENANCE = "relationship_v2.services.turn_context"
RECALL_BUDGET = 3
MILESTONE_EPISODE_TYPES = ["purchase", "post_purchase", "important_event"]
LEARNED_SIGNAL_THRESHOLD = 3


class TurnContext(BaseModel):
    generation_id: str = Field(min_length=1)
    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    relationship_id: UUID
    snapshot: RelationshipContextSnapshot
    commerce: CommerceContext | None = None
    commerce_unavailable: bool = False
    recall_refs: list[str] = Field(default_factory=list)
    assembled: AssembledContext
    provenance: str = Field(min_length=1)

    model_config = {"frozen": True}


GetOrCreatePort = Callable[[int, int, str], Awaitable[dict[str, Any]]]
CountPort = Callable[..., Awaitable[int]]
ListPort = Callable[..., Awaitable[list[dict[str, Any]]]]


async def _default_get_or_create(creator_id: int, user_id: int, provenance: str) -> dict:
    from relationship_v2.persistence.repository import get_or_create_relationship

    return await get_or_create_relationship(creator_id, user_id, provenance)


async def _default_count_events(creator_id: int, user_id: int, types: list[str]) -> int:
    from relationship_v2.persistence.repository import count_events

    return await count_events(creator_id, user_id, types)


async def _default_count_facts(creator_id: int, user_id: int) -> int:
    from relationship_v2.persistence.repository import count_current_facts

    return await count_current_facts(creator_id, user_id)


async def _default_count_episodes(
    creator_id: int, user_id: int, types: list[str] | None = None
) -> int:
    from relationship_v2.persistence.repository import count_episodes

    return await count_episodes(creator_id, user_id, types)


async def _default_list_facts(creator_id: int, user_id: int, **kw: Any) -> list[dict]:
    from relationship_v2.persistence.repository import list_current_facts

    return await list_current_facts(creator_id, user_id, **kw)


async def _default_list_episodes(creator_id: int, user_id: int, **kw: Any) -> list[dict]:
    from relationship_v2.persistence.repository import list_recent_episodes

    return await list_recent_episodes(creator_id, user_id, **kw)


async def _default_list_signals(creator_id: int, user_id: int, **kw: Any) -> list[dict]:
    from relationship_v2.persistence.repository import list_signals

    return await list_signals(creator_id, user_id, **kw)


async def _default_list_loops(creator_id: int, user_id: int, **kw: Any) -> list[dict]:
    from relationship_v2.persistence.repository import list_active_loops

    return await list_active_loops(creator_id, user_id, **kw)


async def _default_list_intimate(creator_id: int, user_id: int, **kw: Any) -> list[dict]:
    from relationship_v2.persistence.repository import list_intimate_history

    return await list_intimate_history(creator_id, user_id, **kw)


def _days_between(now: datetime, then: datetime | None) -> int | None:
    if then is None:
        return None
    return max(0, int((now - then).total_seconds() // 86400))


def _line(text: str, limit: int = 280) -> str:
    return " ".join(text.split())[:limit]


async def assemble_turn_context(
    creator_id: int,
    user_id: int,
    generation_id: str,
    *,
    provenance: str = PROVENANCE,
    now: datetime | None = None,
    get_or_create: GetOrCreatePort | None = None,
    count_events: CountPort | None = None,
    count_facts: CountPort | None = None,
    count_episodes: CountPort | None = None,
    list_facts: ListPort | None = None,
    list_episodes: ListPort | None = None,
    list_signals: ListPort | None = None,
    list_loops: ListPort | None = None,
    list_intimate: ListPort | None = None,
    eligibility_port: Callable | None = None,
    opportunity_port: Callable | None = None,
    purchase_port: Callable | None = None,
) -> TurnContext:
    """Build one turn's authoritative context. Fail-closed on scope/state."""
    from relationship_v2.services.context_assembly import assemble
    from relationship_v2.services.proactive_recall import (
        RecallCandidate,
        rank_candidates,
    )
    from relationship_v2.services.relationship_context import load_relationship_context
    from relationship_v2.services.relationship_derivations import (
        derive_engagement,
        derive_intimacy_trajectory,
        derive_reciprocity,
    )
    from relationship_v2.services.relationship_reasoning import StoreCounts, reason_about

    if creator_id <= 0 or user_id <= 0:
        raise ValueError("scope must be positive (fail-closed)")
    if not generation_id or not provenance:
        raise ValueError("generation_id/provenance required")
    ts = now or datetime.now(UTC)
    rel = await (get_or_create or _default_get_or_create)(creator_id, user_id, provenance)
    relationship_id: UUID = rel["id"]
    try:
        current_lifecycle = RelationshipLifecycle(str(rel.get("lifecycle", "new")))
    except ValueError:
        raise ValueError("unknown stored lifecycle (fail-closed)")

    ev_counts = await (count_events or _default_count_events)(
        creator_id, user_id, ["FanMessageReceived"]
    )
    meaningful = await (count_events or _default_count_events)(
        creator_id, user_id, ["ResponseSent"]
    )
    milestones = await (count_episodes or _default_count_episodes)(
        creator_id, user_id, MILESTONE_EPISODE_TYPES
    )
    fact_total = await (count_facts or _default_count_facts)(creator_id, user_id)
    episode_total = await (count_episodes or _default_count_episodes)(creator_id, user_id)

    facts = await (list_facts or _default_list_facts)(creator_id, user_id)
    episodes = await (list_episodes or _default_list_episodes)(creator_id, user_id)
    signals = await (list_signals or _default_list_signals)(creator_id, user_id)
    loops = await (list_loops or _default_list_loops)(creator_id, user_id)
    intimate = await (list_intimate or _default_list_intimate)(creator_id, user_id)

    boundaries = [r for r in intimate if str(r.get("kind")) == "boundary"]
    evidence = RelationshipEvidence(
        interaction_count=ev_counts,
        meaningful_interaction_count=meaningful,
        milestone_count=milestones,
        episode_count=episode_total,
        boundary_count=len(boundaries),
        days_since_first=_days_between(ts, rel.get("first_interaction_at")),
        days_since_last=_days_between(ts, rel.get("last_interaction_at")),
        purchase_ref_count=0,
        inbound_count=ev_counts,
        sent_count=meaningful,
    )
    from relationship_v2.domain.open_loop import OpenLoopStatus
    from relationship_v2.services.open_loops import is_due as _is_due

    due = 0
    for loop in loops:
        try:
            st = OpenLoopStatus(str(loop.get("status", "open")))
        except ValueError:
            continue
        if _is_due(st, loop.get("expected_at"), ts):
            due += 1
    learned = sum(1 for s in signals if int(s.get("evidence_count", 0)) >= LEARNED_SIGNAL_THRESHOLD)
    counts = StoreCounts(
        fact_count=fact_total,
        episode_count=episode_total,
        open_loop_count=len(loops),
        due_loop_count=due,
        learned_pattern_count=learned,
        intimate_signal_count=len(intimate),
    )

    commerce: CommerceContext | None = None
    commerce_unavailable = False
    purchase_refs = 0
    try:
        from relationship_v2.integration.commerce_ports import eligibility_port as _elig
        from relationship_v2.integration.commerce_ports import opportunity_port as _opp
        from relationship_v2.integration.commerce_ports import purchase_port as _purch
        from relationship_v2.services.commerce_adapter import read_context

        commerce = await read_context(
            creator_id,
            user_id,
            eligibility_port or _elig,
            opportunity_port or _opp,
            purchase_port or _purch,
            ts,
        )
        purchase_refs = commerce.purchase_count
    except Exception:
        logger.warning("commerce context unavailable (degraded turn)", exc_info=True)
        commerce_unavailable = True
    evidence = evidence.model_copy(update={"purchase_ref_count": purchase_refs})

    snapshot = load_relationship_context(
        relationship_id,
        creator_id,
        user_id,
        current_lifecycle,
        int(rel.get("version", 1)),
        evidence,
        provenance,
        ts,
    )
    reading = reason_about(snapshot, evidence, counts, provenance)
    engagement = derive_engagement(ev_counts, meaningful, evidence.days_since_last)
    reciprocity = derive_reciprocity(evidence.inbound_count, evidence.sent_count)
    kind_counts = {"comfort": 0, "preference": 0, "milestone": 0}
    for row in intimate:
        kind = str(row.get("kind", ""))
        if kind in kind_counts:
            kind_counts[kind] += 1
    intimacy = derive_intimacy_trajectory(
        kind_counts["comfort"],
        kind_counts["preference"],
        kind_counts["milestone"],
        len(boundaries),
    )

    candidates: list[RecallCandidate] = []
    for loop in loops:
        try:
            st = OpenLoopStatus(str(loop.get("status", "open")))
        except ValueError:
            continue
        candidates.append(
            RecallCandidate(
                ref_id=f"loop:{loop.get('id')}",
                kind="loop",
                importance=str(loop.get("priority", "normal")),
                salience=0.6,
                unresolved=True,
                due=_is_due(st, loop.get("expected_at"), ts),
            )
        )
    for fact in facts:
        try:
            candidates.append(
                RecallCandidate(
                    ref_id=f"fact:{fact.get('id')}",
                    kind="fact",
                    importance=str(fact.get("importance", "normal")),
                    salience=float(fact.get("confidence", 0.5)),
                )
            )
        except Exception:
            logger.debug("skipping malformed fact row", exc_info=True)
            continue
    recall = rank_candidates(candidates, 3)

    memory_lines = [
        _line(f"{f.get('memory_key')}={f.get('value')} (conf {f.get('confidence')})")
        for f in facts
    ]
    episode_lines = [
        _line(f"{e.get('episode_type')}: {e.get('summary')}") for e in episodes
    ]
    signal_lines = [
        _line(f"{s.get('topic')}/{s.get('behavior')} {s.get('polarity')} x{s.get('evidence_count')}")
        for s in signals
    ]
    loop_lines = [
        _line(f"open: {loop.get('description')}") for loop in loops
    ] + [_line(f"recall: {item.ref_id} ({item.reason})") for item in recall.items]
    intimate_lines = [_line(f"{r.get('kind')}: {r.get('signal')}") for r in intimate]
    boundary_lines = [_line(f"boundary: {r.get('signal')}") for r in boundaries]
    if commerce is not None:
        commerce_lines = [
            _line(
                f"buyer status={commerce.purchase_status} count={commerce.purchase_count} "
                f"active_offer={commerce.active_offer} aftercare={commerce.aftercare_state}"
            ),
            *[_line(f"constraint: {c}") for c in commerce.deterministic_constraints],
        ]
    else:
        commerce_lines = ["commerce unavailable this turn (stale)"]
    stale = {"commerce_context"} if commerce_unavailable else set()
    assembled = assemble(
        generation_id,
        creator_id,
        user_id,
        {
            "relationship_state": [
                snapshot.lifecycle.value,
                f"familiarity={snapshot.familiarity}",
                f"comfort={snapshot.comfort}",
                f"engagement={engagement}",
                f"reciprocity={reciprocity}",
                f"intimacy={intimacy}",
                f"continuity={reading.continuity}",
                reading.tenure_answer,
                reading.absence_answer,
            ],
            "long_term_memory": memory_lines,
            "episodic_memory": episode_lines,
            "engagement_signals": signal_lines,
            "commerce_context": commerce_lines,
            "response_constraints": [
                *boundary_lines,
                "no invented prices",
                "no invented purchases",
            ],
            "conversation_state": loop_lines,
            "escalation_state": intimate_lines,
        },
        stale=stale,
        memory_lines=memory_lines,
        commerce_lines=commerce_lines,
        boundary_lines=[str(r.get("signal")) for r in boundaries],
        now=ts,
    )
    return TurnContext(
        generation_id=generation_id,
        creator_id=creator_id,
        user_id=user_id,
        relationship_id=relationship_id,
        snapshot=snapshot,
        commerce=commerce,
        commerce_unavailable=commerce_unavailable,
        recall_refs=[i.ref_id for i in recall.items],
        assembled=assembled,
        provenance=provenance,
    )
