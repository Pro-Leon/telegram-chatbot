"""Shadow validation (Phase 10). Observe-only; never sends/mutates.

Caller: FUTURE shadow harness (outside this package). This module composes
ONLY pure V2 services over caller-supplied inputs and returns a report plus
legacy divergences. It performs zero I/O by construction:

- no DB reads/writes (no repository imports)
- no Redis locks/streams
- no LLM calls (no GenerationPort invocation; plans built, never rendered)
- no commerce calls (eligibility is a caller-supplied bool snapshot)
- no event emission, no queue interaction, no sends

Any import added here beyond relationship_v2/pydantic/stdlib fails review.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

from relationship_v2.domain.escalation import EscalationStage
from relationship_v2.domain.relationship import RelationshipLifecycle
from relationship_v2.domain.shadow import ShadowDivergence, ShadowInputs, ShadowReport
from relationship_v2.domain.strategy import StrategyInputs
from relationship_v2.services.context_assembly import assemble
from relationship_v2.services.conversation_engine import derive_topic
from relationship_v2.services.escalation import select_stage
from relationship_v2.services.relationship_context import derive_lifecycle
from relationship_v2.services.response_engine import build_plan, interpret

logger = logging.getLogger("sunny.v2.shadow")


def observe_turn(inputs: ShadowInputs, now: datetime | None = None) -> ShadowReport:
    """Pure shadow pass. Deterministic given inputs + now."""
    if inputs.creator_id <= 0 or inputs.user_id <= 0:
        raise ValueError("scope must be positive (fail-closed)")
    ts = now or datetime.now(UTC)
    intent = interpret(inputs.inbound_text)
    try:
        current_lifecycle = RelationshipLifecycle(inputs.current_relationship_lifecycle)
    except ValueError:
        raise ValueError("unknown relationship lifecycle (fail-closed)")
    try:
        current_stage = EscalationStage(inputs.current_escalation_stage)
    except ValueError:
        raise ValueError("unknown escalation stage (fail-closed)")
    from relationship_v2.domain.context import RelationshipEvidence

    evidence = RelationshipEvidence(
        interaction_count=inputs.interaction_count,
        meaningful_interaction_count=inputs.meaningful_interaction_count,
        milestone_count=inputs.milestone_count,
        days_since_last=inputs.days_since_last,
    )
    lifecycle = derive_lifecycle(current_lifecycle, evidence)
    strategy_inputs = StrategyInputs(
        relationship_lifecycle=lifecycle,
        responsiveness=inputs.responsiveness,
        engagement_evidence=inputs.engagement_evidence,
        negative_evidence=inputs.negative_evidence,
        purchase_ref_count=inputs.purchase_ref_count,
        commerce_eligible=inputs.commerce_eligible,
    )
    decision = select_stage(
        uuid4(), inputs.creator_id, inputs.user_id, current_stage, strategy_inputs, ts
    )
    topic = derive_topic(inputs.conversation_topic, False, None)
    section_inputs = {
        "relationship_state": [f"{lifecycle.value}"],
        "long_term_memory": list(inputs.memory_lines),
        "recent_conversation": [f"inbound_len={inputs.inbound_text_length}"],
        "conversation_state": [f"topic={topic}"],
        "escalation_state": [decision.stage.value],
        "response_constraints": ["no invented prices"],
    }
    ctx = assemble(
        inputs.generation_id,
        inputs.creator_id,
        inputs.user_id,
        section_inputs,
        conversation_id=None,
        now=ts,
    )
    plan = build_plan(
        inputs.generation_id,
        inputs.creator_id,
        inputs.user_id,
        uuid4(),
        intent,
        [f"intent={intent.value}"],
        [],
        ["invented prices", "invented purchases"],
        "shadow-observe",
        ts,
    )
    return ShadowReport(
        generation_id=inputs.generation_id,
        creator_id=inputs.creator_id,
        user_id=inputs.user_id,
        inbound_intent=intent.value,
        derived_lifecycle=lifecycle.value,
        selected_stage=decision.stage.value,
        stage_reason=decision.reason,
        plan_intent=plan.intent.value,
        assembly_chars=ctx.total_chars,
        assembly_sections=len(ctx.sections),
        memory_lines_considered=len(inputs.memory_lines),
        observed_at=ts,
    )


def compare_with_legacy(report: ShadowReport, inputs: ShadowInputs) -> list[ShadowDivergence]:
    """Deterministic V2-vs-legacy diff. Notes only; selects no winner."""
    out: list[ShadowDivergence] = []
    if inputs.legacy_stage is not None and inputs.legacy_stage != report.selected_stage:
        out.append(
            ShadowDivergence(
                dimension="stage",
                v2_value=report.selected_stage,
                legacy_value=inputs.legacy_stage,
                note="strategy differs; inspect memory/engagement inputs",
            )
        )
    if (
        inputs.legacy_memory_count is not None
        and inputs.legacy_memory_count != report.memory_lines_considered
    ):
        out.append(
            ShadowDivergence(
                dimension="memory_coverage",
                v2_value=str(report.memory_lines_considered),
                legacy_value=str(inputs.legacy_memory_count),
                note="retrieval coverage differs; inspect ranking inputs",
            )
        )
    return out
