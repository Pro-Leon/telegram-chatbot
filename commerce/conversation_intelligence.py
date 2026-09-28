"""Conversation Intelligence Orchestrator — deterministic next-best-objective (Phase 16)."""

from __future__ import annotations
import enum
from dataclasses import dataclass
from typing import Any


class ConversationObjective(str, enum.Enum):
    RELATIONSHIP_BUILD = "relationship_build"
    CONTINUE_TOPIC = "continue_topic"
    FOLLOW_UP_OPEN_LOOP = "follow_up_open_loop"
    EXPLORE_INTEREST = "explore_interest"
    DEEPEN_DESIRE = "deepen_desire"
    QUALIFY = "qualify"
    HANDLE_OBJECTION = "handle_objection"
    PRESENT_OFFER = "present_offer"
    COMPLETE_PURCHASE = "complete_purchase"
    AFTERCARE = "aftercare"
    LEARN_PREFERENCE = "learn_preference"
    RE_ENGAGE = "re_engage"
    HUMAN_HANDOFF = "human_handoff"
    WAIT = "wait"


# Priority: lower number = higher priority
_PRIORITY = {
    ConversationObjective.HUMAN_HANDOFF: 1,
    ConversationObjective.COMPLETE_PURCHASE: 2,
    ConversationObjective.AFTERCARE: 3,
    ConversationObjective.HANDLE_OBJECTION: 4,
    ConversationObjective.FOLLOW_UP_OPEN_LOOP: 5,
    ConversationObjective.PRESENT_OFFER: 6,
    ConversationObjective.QUALIFY: 7,
    ConversationObjective.DEEPEN_DESIRE: 8,
    ConversationObjective.EXPLORE_INTEREST: 9,
    ConversationObjective.CONTINUE_TOPIC: 10,
    ConversationObjective.RELATIONSHIP_BUILD: 11,
    ConversationObjective.LEARN_PREFERENCE: 12,
    ConversationObjective.RE_ENGAGE: 13,
    ConversationObjective.WAIT: 99,
}


@dataclass(frozen=True)
class CandidateObjective:
    objective: ConversationObjective
    priority: int
    eligible: bool
    reason_code: str
    blocking_reason: str | None = None


def derive_conversation_objective(
    *,
    desire: str,
    temperature: str,
    sales_window: str,
    offer_readiness: str,
    has_active_offer: bool,
    aftercare_status: str,
    is_on_cooldown: bool,
    has_relevant_product: bool,
    explicit_purchase_request: bool = False,
    explicit_content_request: bool = False,
    has_open_loop: bool = False,
    open_loop_importance: float = 0.0,
    has_objection: bool = False,
    objection_type: str | None = None,
    is_blocked: bool = False,
    is_commercially_hot: bool = False,
    desire_evidence: tuple[str, ...] | list[str] | None = None,
) -> tuple[ConversationObjective, list[CandidateObjective]]:
    """Deterministic ranking of candidate objectives."""
    candidates: list[CandidateObjective] = []
    # Phase 3 provenance fence: warmth-derived INTEREST carries no
    # commercial evidence, so it cannot qualify for commercial-
    # conversational actions. Buying-signal / free-content / absent
    # behave exactly as today (backward-compatible: omitted param).
    _is_warmth_interest = False
    try:
        if desire_evidence is not None and desire == "interest":
            for _tok in desire_evidence:
                if isinstance(_tok, str) and _tok.startswith("interest:warmth"):
                    _is_warmth_interest = True
                    break
    except Exception:
        _is_warmth_interest = False

    # Safety / blocked
    candidates.append(
        CandidateObjective(
            objective=ConversationObjective.HUMAN_HANDOFF,
            priority=_PRIORITY[ConversationObjective.HUMAN_HANDOFF],
            eligible=is_blocked,
            reason_code="SAFETY_BLOCKED" if is_blocked else "NOT_BLOCKED",
            blocking_reason=None if is_blocked else "not_blocked",
        )
    )

    # Aftercare
    candidates.append(
        CandidateObjective(
            objective=ConversationObjective.AFTERCARE,
            priority=_PRIORITY[ConversationObjective.AFTERCARE],
            eligible=aftercare_status in ("pending", "sent"),
            reason_code="AFTERCARE_PENDING"
            if aftercare_status in ("pending", "sent")
            else "NO_AFTERCARE",
            blocking_reason=None if aftercare_status in ("pending", "sent") else "no_aftercare",
        )
    )

    # Complete purchase (has_active_offer with delivery issue) — simplified as not eligible unless has_active_offer and no purchase
    candidates.append(
        CandidateObjective(
            objective=ConversationObjective.COMPLETE_PURCHASE,
            priority=_PRIORITY[ConversationObjective.COMPLETE_PURCHASE],
            eligible=False,
            reason_code="NO_DELIVERY_ISSUE",
            blocking_reason="no_delivery_issue",
        )
    )

    # Handle objection
    candidates.append(
        CandidateObjective(
            objective=ConversationObjective.HANDLE_OBJECTION,
            priority=_PRIORITY[ConversationObjective.HANDLE_OBJECTION],
            eligible=has_objection or is_on_cooldown,
            reason_code="ACTIVE_OBJECTION"
            if has_objection
            else ("COOLDOWN_ACTIVE" if is_on_cooldown else "NO_OBJECTION"),
            blocking_reason=None if (has_objection or is_on_cooldown) else "no_objection",
        )
    )

    # Follow-up open loop
    candidates.append(
        CandidateObjective(
            objective=ConversationObjective.FOLLOW_UP_OPEN_LOOP,
            priority=_PRIORITY[ConversationObjective.FOLLOW_UP_OPEN_LOOP],
            eligible=has_open_loop
            and open_loop_importance >= 0.7
            and not is_on_cooldown
            and aftercare_status not in ("pending", "sent"),
            reason_code="OPEN_LOOP" if has_open_loop else "NO_OPEN_LOOP",
            blocking_reason=None if has_open_loop else "no_open_loop",
        )
    )

    # Present offer
    candidates.append(
        CandidateObjective(
            objective=ConversationObjective.PRESENT_OFFER,
            priority=_PRIORITY[ConversationObjective.PRESENT_OFFER],
            eligible=offer_readiness == "ready"
            and sales_window == "open"
            and has_relevant_product
            and not has_active_offer
            and not is_on_cooldown
            and aftercare_status not in ("pending", "sent"),
            reason_code="OFFER_READY" if offer_readiness == "ready" else "NOT_READY",
            blocking_reason=None if offer_readiness == "ready" else "not_ready",
        )
    )

    # Qualify
    candidates.append(
        CandidateObjective(
            objective=ConversationObjective.QUALIFY,
            priority=_PRIORITY[ConversationObjective.QUALIFY],
            eligible=desire in ("qualification", "desire")
            and sales_window in ("building", "open")
            and not is_on_cooldown,
            reason_code="QUALIFICATION" if desire == "qualification" else "NOT_QUALIFIED",
            blocking_reason=None if desire in ("qualification", "desire") else "low_desire",
        )
    )

    # Deepen desire
    candidates.append(
        CandidateObjective(
            objective=ConversationObjective.DEEPEN_DESIRE,
            priority=_PRIORITY[ConversationObjective.DEEPEN_DESIRE],
            eligible=(
                desire in ("interest", "desire")
                and temperature in ("warm", "hot")
                and not is_on_cooldown
                and not _is_warmth_interest
            ),
            reason_code=(
                "WARMTH"
                if _is_warmth_interest
                else ("DESIRE" if desire in ("interest", "desire") else "LOW_DESIRE")
            ),
            blocking_reason=(
                "warmth_only"
                if _is_warmth_interest
                else (None if desire in ("interest", "desire") else "low_desire")
            ),
        )
    )

    # Explore interest
    candidates.append(
        CandidateObjective(
            objective=ConversationObjective.EXPLORE_INTEREST,
            priority=_PRIORITY[ConversationObjective.EXPLORE_INTEREST],
            eligible=(
                desire in ("curiosity", "interest")
                and sales_window == "building"
                and not _is_warmth_interest
            ),
            reason_code=(
                "WARMTH"
                if _is_warmth_interest
                else ("CURIOSITY" if desire in ("curiosity", "interest") else "NO_CURIOSITY")
            ),
            blocking_reason=(
                "warmth_only"
                if _is_warmth_interest
                else (None if desire in ("curiosity", "interest") else "no_curiosity")
            ),
        )
    )

    # Direct fan request should win over relationship building but not over safety/aftercare/objection
    if explicit_purchase_request or explicit_content_request:
        candidates.append(
            CandidateObjective(
                objective=ConversationObjective.PRESENT_OFFER
                if explicit_purchase_request
                else ConversationObjective.QUALIFY,
                priority=5,  # between objection and follow_up
                eligible=True,
                reason_code="DIRECT_REQUEST",
                blocking_reason=None,
            )
        )

    # Continue topic
    candidates.append(
        CandidateObjective(
            objective=ConversationObjective.CONTINUE_TOPIC,
            priority=_PRIORITY[ConversationObjective.CONTINUE_TOPIC],
            eligible=True,
            reason_code="CONTINUE_TOPIC",
            blocking_reason=None,
        )
    )

    # Relationship build (fallback)
    candidates.append(
        CandidateObjective(
            objective=ConversationObjective.RELATIONSHIP_BUILD,
            priority=_PRIORITY[ConversationObjective.RELATIONSHIP_BUILD],
            eligible=True,
            reason_code="RELATIONSHIP_BUILD",
            blocking_reason=None,
        )
    )

    # Re-engage
    candidates.append(
        CandidateObjective(
            objective=ConversationObjective.RE_ENGAGE,
            priority=_PRIORITY[ConversationObjective.RE_ENGAGE],
            eligible=sales_window == "open" and not has_active_offer and not is_on_cooldown,
            reason_code="REENGAGE" if sales_window == "open" else "NO_WINDOW",
            blocking_reason=None if sales_window == "open" else "no_window",
        )
    )

    # Wait
    candidates.append(
        CandidateObjective(
            objective=ConversationObjective.WAIT,
            priority=_PRIORITY[ConversationObjective.WAIT],
            eligible=sales_window == "cooldown" or is_on_cooldown,
            reason_code="COOLDOWN_ACTIVE" if is_on_cooldown else "NO_WINDOW",
            blocking_reason=None if is_on_cooldown else "no_cooldown",
        )
    )

    # Rank by priority, eligible first
    eligible = [c for c in candidates if c.eligible]
    if eligible:
        eligible.sort(key=lambda c: c.priority)
        selected = eligible[0].objective
    else:
        # Fallback to relationship_build
        selected = ConversationObjective.RELATIONSHIP_BUILD

    return selected, candidates
