"""Turn orchestrator, inbound half (Stage F9a). One fan message in, plan out.

Composes ONLY shipped, tested primitives: guarded lock -> idempotent
intake -> turn context -> strategy selection -> response plan. No new
state machines, no LLM calls (plan-only posture; generation wiring is a
later decision), no transport, no commerce writes.

Deferred with reasons (not stubbed): persisted escalation-stage tracking
(caller passes current stage; `v2_relationships.escalation_stage` readback
lands with stage persistence), negative-evidence counts (needs a polarity
read in the composer), response-latency responsiveness (defaults 0.5).
Commerce eligibility here is a v1 heuristic (available + unconstrained);
PRESENT_OFFER stays hard-gated on it by `select_stage`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from relationship_v2.domain.escalation import EscalationStage
from relationship_v2.domain.event import V2Event, V2EventType
from relationship_v2.domain.response import ResponseIntent, ResponsePlan
from relationship_v2.domain.strategy import StrategyDecision, StrategyInputs
from relationship_v2.services.turn_context import TurnContext

logger = logging.getLogger("sunny.v2.turn_orchestrator")

PROVENANCE = "relationship_v2.services.turn_orchestrator"


class InboundOutcome(str, Enum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    DEFERRED = "deferred"
    REJECTED = "rejected"


class InboundTurnResult(BaseModel):
    outcome: InboundOutcome
    generation_id: str = Field(min_length=1)
    plan: ResponsePlan | None = None
    strategy: StrategyDecision | None = None
    recall_refs: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)

    model_config = {"frozen": True}


TurnContextPort = Callable[..., Awaitable[TurnContext]]


async def _default_turn_context(*args: Any, **kwargs: Any) -> TurnContext:
    from relationship_v2.services.turn_context import assemble_turn_context

    return await assemble_turn_context(*args, **kwargs)


def _text_hash(text: str) -> str:
    from relationship_v2.services.replay import text_hash

    return text_hash(text)


async def run_inbound_turn(
    creator_id: int,
    user_id: int,
    text: str,
    message_id: str,
    generation_id: str,
    *,
    provenance: str = PROVENANCE,
    now: datetime | None = None,
    current_stage: EscalationStage = EscalationStage.RELATIONSHIP,
    turn_context: TurnContextPort | None = None,
    is_processed: Callable | None = None,
    get_or_create: Callable | None = None,
    create_event: Callable | None = None,
    mark_processed: Callable | None = None,
    acquire: Callable | None = None,
    release: Callable | None = None,
) -> InboundTurnResult:
    """Process one inbound fan message to a response plan. Fail-closed."""
    from relationship_v2.services.event_processor import process_event
    from relationship_v2.services.response_engine import build_plan, interpret
    from relationship_v2.services.turn_guard import run_guarded

    if creator_id <= 0 or user_id <= 0:
        raise ValueError("scope must be positive (fail-closed)")
    for name, val in (
        ("generation_id", generation_id),
        ("message_id", message_id),
        ("provenance", provenance),
    ):
        if not val:
            raise ValueError(f"{name} required")
    if not text or not text.strip():
        return InboundTurnResult(
            outcome=InboundOutcome.REJECTED,
            generation_id=generation_id,
            reason="empty_text",
        )
    ts = now or datetime.now(UTC)

    async def _section() -> InboundTurnResult:
        key = f"inbound:{message_id}"
        received = V2Event(
            event_id=key,
            event_type=V2EventType.FAN_MESSAGE_RECEIVED,
            creator_id=creator_id,
            user_id=user_id,
            idempotency_key=key,
            source=provenance,
            payload={"text_hash": _text_hash(text)},
        )
        stored = await process_event(
            received,
            provenance=provenance,
            is_processed=is_processed,
            get_or_create=get_or_create,
            create_event=create_event,
            mark_processed=mark_processed,
        )
        from relationship_v2.services.event_processor import ProcessorOutcome

        if stored.outcome == ProcessorOutcome.DUPLICATE:
            return InboundTurnResult(
                outcome=InboundOutcome.DUPLICATE,
                generation_id=generation_id,
                reason="redelivery",
            )
        if stored.outcome != ProcessorOutcome.APPLIED:
            return InboundTurnResult(
                outcome=InboundOutcome.REJECTED,
                generation_id=generation_id,
                reason=f"intake_{stored.outcome.value}",
            )
        ctx = await (turn_context or _default_turn_context)(
            creator_id, user_id, generation_id, provenance=provenance, now=ts
        )
        commerce = ctx.commerce
        commerce_eligible = (
            commerce is not None
            and not ctx.commerce_unavailable
            and not commerce.deterministic_constraints
        )
        from relationship_v2.services.escalation import select_stage

        decision = select_stage(
            ctx.relationship_id,
            creator_id,
            user_id,
            current_stage,
            StrategyInputs(
                relationship_lifecycle=ctx.snapshot.lifecycle,
                familiarity=ctx.snapshot.familiarity,
                comfort=ctx.snapshot.comfort,
                responsiveness=0.5,
                engagement_evidence=len(ctx.recall_refs),
                negative_evidence=0,
                purchase_ref_count=commerce.purchase_count if commerce else 0,
                commerce_eligible=commerce_eligible,
                has_boundaries=ctx.snapshot.has_boundaries,
                absence_days=ctx.snapshot.absence_days,
            ),
            ts,
        )
        intent: ResponseIntent = interpret(text)
        plan = build_plan(
            generation_id,
            creator_id,
            user_id,
            ctx.relationship_id,
            intent,
            [f"recall:{r}" for r in ctx.recall_refs[:3]],
            [],
            ["invented prices", "invented purchases"],
            provenance,
            ts,
        )
        return InboundTurnResult(
            outcome=InboundOutcome.APPLIED,
            generation_id=generation_id,
            plan=plan,
            strategy=decision,
            recall_refs=list(ctx.recall_refs),
            reason="planned",
        )

    guarded = await run_guarded(
        creator_id, user_id, _section, acquire=acquire, release=release
    )
    if not guarded.acquired:
        return InboundTurnResult(
            outcome=InboundOutcome.DEFERRED,
            generation_id=generation_id,
            reason="lock_contended_defer",
        )
    return guarded.result
