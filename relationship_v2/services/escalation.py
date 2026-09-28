"""Escalation strategy engine (Phase 6).

Caller: FUTURE response engine (Phase 7) per turn, after context assembly.
Owner of: stage selection logic. Producer: strategy.changed events
(consumers FUTURE: observability, response planning).

Rules (ESCALATION_ENGINE.md + 04_STATE_MACHINE.md):
- Application logic decides; LLM never the decision-maker.
- PRESENT_OFFER requires commerce_eligible READ snapshot; blocked otherwise.
- AFTERCARE reads purchase confirmations; never invents them.
- Cooldowns block re-entry after rejection/stall until expiry.
- Reversible: BUILD_DESIRE->EXPLORE, RECOMMEND->RELATIONSHIP allowed.
- New/low-evidence fans stay in RELATIONSHIP; no forced funneling.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from relationship_v2.domain.escalation import EscalationStage
from relationship_v2.domain.relationship import RelationshipLifecycle
from relationship_v2.domain.strategy import (
    ConfidenceBreakdown,
    StrategyDecision,
    StrategyInputs,
)

logger = logging.getLogger("sunny.v2.escalation")

COOLDOWN_AFTER_REJECTION_SECONDS = 24 * 3600
COOLDOWN_AFTER_STALL_SECONDS = 6 * 3600

_FORWARD_ORDER: tuple[EscalationStage, ...] = (
    EscalationStage.RELATIONSHIP,
    EscalationStage.EXPLORE,
    EscalationStage.BUILD_DESIRE,
    EscalationStage.QUALIFY,
    EscalationStage.RECOMMEND,
    EscalationStage.PRESENT_OFFER,
    EscalationStage.AFTERCARE,
)


def is_valid_escalation_transition(
    current: EscalationStage,
    nxt: EscalationStage,
    commerce_eligible: bool = False,
) -> bool:
    """Guards: PRESENT_OFFER while ineligible always invalid."""
    if nxt == EscalationStage.PRESENT_OFFER and not commerce_eligible:
        return False
    if current == nxt:
        return True
    if current == EscalationStage.EXITED:
        return nxt == EscalationStage.RELATIONSHIP
    if current == EscalationStage.COOLING_DOWN:
        return nxt in (
            EscalationStage.RELATIONSHIP,
            EscalationStage.EXPLORE,
            EscalationStage.COOLING_DOWN,
        )
    if nxt == EscalationStage.EXITED or nxt == EscalationStage.COOLING_DOWN:
        return True
    if current in _FORWARD_ORDER and nxt in _FORWARD_ORDER:
        ci, ni = _FORWARD_ORDER.index(current), _FORWARD_ORDER.index(nxt)
        # Forward max one step; backward always allowed (reversible strategy).
        return ni == ci + 1 or ni < ci
    return False


def is_cooling_down(until: datetime | None, now: datetime | None = None) -> bool:
    if until is None:
        return False
    return (now or datetime.now(UTC)) < until


def _confidence(inputs: StrategyInputs) -> ConfidenceBreakdown:
    relationship = {"stranger": 0.2, "familiar": 0.5, "established": 0.75, "longstanding": 0.9}.get(
        inputs.familiarity, 0.3
    )
    engagement = min(1.0, inputs.engagement_evidence / 10.0) * 0.7 + inputs.responsiveness * 0.3
    commerce = 1.0 if inputs.commerce_eligible else 0.0
    total = round(
        0.35 * inputs.responsiveness + 0.25 * engagement + 0.25 * relationship + 0.15 * commerce, 3
    )
    return ConfidenceBreakdown(
        responsiveness=round(inputs.responsiveness, 3),
        engagement=round(min(1.0, engagement), 3),
        relationship=round(relationship, 3),
        commerce=commerce,
        total=total,
    )


def select_stage(
    relationship_id: UUID,
    creator_id: int,
    user_id: int,
    current: EscalationStage,
    inputs: StrategyInputs,
    now: datetime | None = None,
) -> StrategyDecision:
    """Deterministic stage selection. Pure function."""
    if creator_id <= 0 or user_id <= 0:
        raise ValueError("scope must be positive (fail-closed)")
    ts = now or datetime.now(UTC)
    # Safety first: boundaries/opt-outs exit commercial pursuit.
    if inputs.opted_out or inputs.has_boundaries and current == EscalationStage.PRESENT_OFFER:
        return _decide(
            relationship_id,
            creator_id,
            user_id,
            current,
            EscalationStage.EXITED,
            inputs,
            ts,
            "boundary_or_optout",
            commerce_intent="none",
        )
    # Cooldown blocks re-entry.
    if is_cooling_down(inputs.cooling_down_until, ts) and current not in (
        EscalationStage.COOLING_DOWN,
        EscalationStage.EXITED,
    ):
        return _decide(
            relationship_id,
            creator_id,
            user_id,
            current,
            EscalationStage.COOLING_DOWN,
            inputs,
            ts,
            "cooldown_active",
            commerce_intent="none",
        )
    # AFTERCARE only on purchase confirmation references.
    if inputs.purchase_ref_count >= 1 and current in (
        EscalationStage.AFTERCARE,
        EscalationStage.PRESENT_OFFER,
    ):
        return _decide(
            relationship_id,
            creator_id,
            user_id,
            current,
            EscalationStage.AFTERCARE,
            inputs,
            ts,
            "purchase_confirmed",
            commerce_intent="none",
        )
    # Stall steps back before any qualification gating (reversible strategy).
    if inputs.negative_evidence >= 3 and current in (
        EscalationStage.BUILD_DESIRE,
        EscalationStage.QUALIFY,
    ):
        back = (
            EscalationStage.EXPLORE
            if current == EscalationStage.BUILD_DESIRE
            else EscalationStage.BUILD_DESIRE
        )
        return _decide(
            relationship_id,
            creator_id,
            user_id,
            current,
            back,
            inputs,
            ts,
            "stall_step_back",
            commerce_intent="none",
        )
    if current == EscalationStage.RECOMMEND and inputs.negative_evidence >= 2:
        return _decide(
            relationship_id,
            creator_id,
            user_id,
            current,
            EscalationStage.RELATIONSHIP,
            inputs,
            ts,
            "recommend_stall_return",
            commerce_intent="none",
        )
    # PRESENT_OFFER gated on eligibility snapshot.
    if current == EscalationStage.RECOMMEND and inputs.responsiveness >= 0.7:
        if inputs.commerce_eligible:
            return _decide(
                relationship_id,
                creator_id,
                user_id,
                current,
                EscalationStage.PRESENT_OFFER,
                inputs,
                ts,
                "qualified_and_eligible",
                commerce_intent="request",
            )
        return _decide(
            relationship_id,
            creator_id,
            user_id,
            current,
            EscalationStage.RECOMMEND,
            inputs,
            ts,
            "qualified_but_ineligible_hold",
            commerce_intent="none",
        )
    # New/low-evidence fans hold RELATIONSHIP (no forced funneling).
    if (
        inputs.relationship_lifecycle == RelationshipLifecycle.NEW
        and current == EscalationStage.RELATIONSHIP
    ):
        if inputs.engagement_evidence >= 5 and inputs.responsiveness >= 0.6:
            return _decide(
                relationship_id,
                creator_id,
                user_id,
                current,
                EscalationStage.EXPLORE,
                inputs,
                ts,
                "new_fan_engaged",
                commerce_intent="none",
            )
        return _decide(
            relationship_id,
            creator_id,
            user_id,
            current,
            EscalationStage.RELATIONSHIP,
            inputs,
            ts,
            "new_fan_hold",
            commerce_intent="none",
        )
    if current == EscalationStage.RELATIONSHIP and (
        inputs.engagement_evidence >= 8 or inputs.responsiveness >= 0.7
    ):
        return _decide(
            relationship_id,
            creator_id,
            user_id,
            current,
            EscalationStage.EXPLORE,
            inputs,
            ts,
            "engagement_momentum",
            commerce_intent="none",
        )
    if (
        current == EscalationStage.EXPLORE
        and inputs.responsiveness >= 0.65
        and inputs.engagement_evidence >= 5
    ):
        return _decide(
            relationship_id,
            creator_id,
            user_id,
            current,
            EscalationStage.BUILD_DESIRE,
            inputs,
            ts,
            "explore_momentum",
            commerce_intent="none",
        )
    intent = (
        "evaluate" if current in (EscalationStage.QUALIFY, EscalationStage.RECOMMEND) else "none"
    )
    return _decide(
        relationship_id,
        creator_id,
        user_id,
        current,
        current,
        inputs,
        ts,
        "hold",
        commerce_intent=intent,
    )


def _decide(
    relationship_id: UUID,
    creator_id: int,
    user_id: int,
    current: EscalationStage,
    stage: EscalationStage,
    inputs: StrategyInputs,
    ts: datetime,
    reason: str,
    commerce_intent: str,
) -> StrategyDecision:
    objectives = {
        EscalationStage.RELATIONSHIP: "maintain_connection",
        EscalationStage.EXPLORE: "build_familiarity",
        EscalationStage.BUILD_DESIRE: "deepen_connection",
        EscalationStage.QUALIFY: "qualify_interest",
        EscalationStage.RECOMMEND: "recommend_fit",
        EscalationStage.PRESENT_OFFER: "present_confirmed_offer",
        EscalationStage.AFTERCARE: "aftercare",
        EscalationStage.EXITED: "respect_exit",
        EscalationStage.COOLING_DOWN: "respect_cooldown",
    }
    return StrategyDecision(
        relationship_id=relationship_id,
        creator_id=creator_id,
        user_id=user_id,
        stage=stage,
        previous_stage=current,
        confidence=_confidence(inputs),
        objective=objectives[stage],
        commerce_intent=commerce_intent,
        reason=reason,
        reversible=stage not in (EscalationStage.EXITED,),
        as_of=ts,
    )


async def record_strategy_change(
    decision: StrategyDecision,
    conversation_id: UUID | None = None,
    generation_id: str | None = None,
) -> dict:
    """Emit strategy.changed. Consumer FUTURE: response planning, observability."""
    from relationship_v2.persistence.repository import create_relationship_event

    if decision.stage == decision.previous_stage:
        raise ValueError("no stage change to record")
    event_id = f"strat-{decision.relationship_id}-{decision.previous_stage.value}-{decision.stage.value}-{decision.as_of.isoformat()}"
    return await create_relationship_event(
        event_id=event_id,
        event_type="strategy.changed",
        creator_id=decision.creator_id,
        user_id=decision.user_id,
        idempotency_key=event_id,
        producer="relationship_v2.services.escalation",
        payload={
            "previous": decision.previous_stage.value,
            "current": decision.stage.value,
            "objective": decision.objective,
            "reason": decision.reason,
            "confidence_total": decision.confidence.total,
        },
        relationship_id=decision.relationship_id,
        conversation_id=conversation_id,
        generation_id=generation_id,
    )
