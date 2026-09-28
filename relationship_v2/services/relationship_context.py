"""Relationship context service (Phase 2).

Caller: FUTURE conversation engine / context assembly (Phase 4/5).
Owner of: v2_relationship_snapshots persistence + relationship.updated events.
Producer: relationship.updated (consumer FUTURE: context assembly, observability).

Deterministic only. The LLM may describe the relationship; it never sets state.
Purchase history is referenced (counts/classes from commerce confirmations),
never duplicated as truth.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from relationship_v2.domain.context import (
    ReentryContext,
    RelationshipContextSnapshot,
    RelationshipEvidence,
)
from relationship_v2.domain.relationship import (
    RelationshipLifecycle,
    is_valid_relationship_transition,
)

logger = logging.getLogger("sunny.v2.relationship_context")

# Freshness TTL for snapshots presented as current (CONTEXT_ASSEMBLY.md).
SNAPSHOT_TTL_SECONDS = 300

BUYER_NONE = "none"
BUYER_FIRST = "first_time"
BUYER_REPEAT = "repeat"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def derive_lifecycle(
    current: RelationshipLifecycle, evidence: RelationshipEvidence
) -> RelationshipLifecycle:
    """Deterministic lifecycle derivation with evidence guards (no skipping)."""
    # Absence overrides: long inactivity parks the relationship as dormant.
    if evidence.days_since_last is not None and evidence.days_since_last > 30:
        candidate = RelationshipLifecycle.DORMANT
    elif current == RelationshipLifecycle.DORMANT:
        # Return path requires recent activity to reactivate.
        if evidence.days_since_last is not None and evidence.days_since_last <= 7:
            candidate = RelationshipLifecycle.REACTIVATED
        else:
            candidate = RelationshipLifecycle.DORMANT
    elif (
        evidence.interaction_count >= 50 and evidence.milestone_count >= 5
    ) or evidence.meaningful_interaction_count >= 30:
        candidate = RelationshipLifecycle.DEEP
    elif (
        evidence.interaction_count >= 20 and evidence.milestone_count >= 2
    ) or evidence.meaningful_interaction_count >= 10:
        candidate = RelationshipLifecycle.ESTABLISHED
    elif evidence.interaction_count >= 5 or evidence.milestone_count >= 1:
        candidate = RelationshipLifecycle.WARMING
    else:
        candidate = RelationshipLifecycle.NEW

    # Guard: never skip evidence (e.g. new -> deep). Step one level max,
    # except dormancy/reactivation which are absence-driven, not merit-driven.
    if candidate == current or is_valid_relationship_transition(current, candidate):
        return candidate
    order = [
        RelationshipLifecycle.NEW,
        RelationshipLifecycle.WARMING,
        RelationshipLifecycle.ESTABLISHED,
        RelationshipLifecycle.DEEP,
    ]
    if current in order and candidate in order:
        ci, ni = order.index(current), order.index(candidate)
        if ni > ci:
            return order[ci + 1]
    return current


def derive_familiarity(evidence: RelationshipEvidence) -> str:
    if evidence.milestone_count >= 5 or evidence.interaction_count >= 50:
        return "longstanding"
    if evidence.milestone_count >= 2 or evidence.interaction_count >= 20:
        return "established"
    if evidence.interaction_count >= 5 or evidence.milestone_count >= 1:
        return "familiar"
    return "stranger"


def derive_comfort(evidence: RelationshipEvidence) -> str:
    if evidence.meaningful_interaction_count >= 10 or evidence.milestone_count >= 3:
        return "high"
    if evidence.meaningful_interaction_count >= 3 or evidence.interaction_count >= 10:
        return "moderate"
    return "low"


def derive_buyer_class(purchase_ref_count: int) -> str:
    if purchase_ref_count >= 2:
        return BUYER_REPEAT
    if purchase_ref_count == 1:
        return BUYER_FIRST
    return BUYER_NONE


def build_reentry(
    lifecycle: RelationshipLifecycle,
    evidence: RelationshipEvidence,
    was_dormant: bool,
) -> ReentryContext | None:
    absence = evidence.days_since_last
    if absence is None or absence < 3:
        return None
    if was_dormant or lifecycle in (
        RelationshipLifecycle.DORMANT,
        RelationshipLifecycle.REACTIVATED,
    ):
        summary = f"return after {absence}d absence; resume prior threads, acknowledge return"
    elif absence >= 30:
        summary = f"long absence ({absence}d); retain identity and important memories"
    else:
        summary = f"brief absence ({absence}d); continue unresolved thread if appropriate"
    return ReentryContext(absence_days=absence, was_dormant=was_dormant, resume_summary=summary)


def evidence_hash(evidence: RelationshipEvidence) -> str:
    payload = json.dumps(evidence.model_dump(), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_relationship_context(
    relationship_id: UUID,
    creator_id: int,
    user_id: int,
    current_lifecycle: RelationshipLifecycle,
    version: int,
    evidence: RelationshipEvidence,
    provenance: str,
    now: datetime | None = None,
) -> RelationshipContextSnapshot:
    """Pure derivation: evidence -> immutable snapshot. No I/O, no LLM."""
    if creator_id <= 0 or user_id <= 0:
        raise ValueError("creator/user scope must be positive (fail-closed)")
    if not provenance:
        raise ValueError("provenance required")
    ts = now or _utcnow()
    lifecycle = derive_lifecycle(current_lifecycle, evidence)
    was_dormant = current_lifecycle == RelationshipLifecycle.DORMANT
    return RelationshipContextSnapshot(
        relationship_id=relationship_id,
        creator_id=creator_id,
        user_id=user_id,
        lifecycle=lifecycle,
        familiarity=derive_familiarity(evidence),
        comfort=derive_comfort(evidence),
        version=version,
        as_of=ts,
        absence_days=evidence.days_since_last,
        reentry=build_reentry(lifecycle, evidence, was_dormant),
        has_boundaries=evidence.boundary_count > 0,
        buyer_class=derive_buyer_class(evidence.purchase_ref_count),
        evidence_hash=evidence_hash(evidence),
        provenance=provenance,
    )


def is_snapshot_stale(
    snapshot: RelationshipContextSnapshot,
    now: datetime | None = None,
    ttl_seconds: int = SNAPSHOT_TTL_SECONDS,
) -> bool:
    """Stale snapshots must be refetched, never presented as current."""
    ts = now or _utcnow()
    return (ts - snapshot.as_of).total_seconds() > ttl_seconds


async def persist_snapshot(
    snapshot: RelationshipContextSnapshot,
) -> dict:
    """Owner of v2_relationship_snapshots. Append-only audit row."""
    from db.postgres import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO v2_relationship_snapshots
                (creator_id, user_id, relationship_id, lifecycle, version,
                 as_of, absence_days, evidence_hash, snapshot, provenance)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10)
            RETURNING *
            """,
            snapshot.creator_id,
            snapshot.user_id,
            snapshot.relationship_id,
            snapshot.lifecycle.value,
            snapshot.version,
            snapshot.as_of,
            snapshot.absence_days,
            snapshot.evidence_hash,
            snapshot.model_dump_json(),
            snapshot.provenance,
        )
        return dict(row)


async def record_lifecycle_change(
    snapshot: RelationshipContextSnapshot,
    previous: RelationshipLifecycle,
    relationship_id: UUID,
    conversation_id: UUID | None = None,
) -> dict:
    """Emit relationship.updated durable event. Consumer FUTURE: assembly/observability."""
    from relationship_v2.persistence.repository import create_relationship_event

    if snapshot.lifecycle == previous:
        raise ValueError("no lifecycle change to record")
    if not is_valid_relationship_transition(previous, snapshot.lifecycle):
        # Absence-driven dormancy/reactivation bypasses merit order; allow them.
        allowed = {
            (RelationshipLifecycle.DORMANT, RelationshipLifecycle.REACTIVATED),
        }
        same_chain = (
            previous
            in (
                RelationshipLifecycle.NEW,
                RelationshipLifecycle.WARMING,
                RelationshipLifecycle.ESTABLISHED,
                RelationshipLifecycle.DEEP,
            )
            and snapshot.lifecycle == RelationshipLifecycle.DORMANT
        )
        if (previous, snapshot.lifecycle) not in allowed and not same_chain:
            raise ValueError(f"invalid transition {previous} -> {snapshot.lifecycle}")
    event_id = f"rel-{snapshot.relationship_id}-{snapshot.version}-{snapshot.evidence_hash}"
    return await create_relationship_event(
        event_id=event_id,
        event_type="relationship.updated",
        creator_id=snapshot.creator_id,
        user_id=snapshot.user_id,
        idempotency_key=event_id,
        producer="relationship_v2.services.relationship_context",
        payload={
            "previous": previous.value,
            "current": snapshot.lifecycle.value,
            "evidence_hash": snapshot.evidence_hash,
        },
        relationship_id=relationship_id,
        conversation_id=conversation_id,
    )
