"""Conversation Outcomes — deterministic classification (Phase 19 → Phase 20 extended).

Phase 10 taxonomy roles (audit, no deletions):
- CanonicalOutcome (adaptive_optimization) is the PRIMARY strategy-learning
  taxonomy. New learning code should route through it.
- FeedbackEventType (commerce.feedback) represents behavioral/rejection
  events semantically different from strategy outcomes — retained.
- Opportunity evidence labels (POSITIVE / COMMERCIAL_NEGATIVE /
  PROCESS_NEGATIVE / CENSORED / UNAVAILABLE) describe opportunity
  maturity/evidence — retained.
- ConversationOutcome (this module, legacy) is kept for compatibility only.
  New Phase 10 code uses CanonicalOutcome + the separated
  RelationshipOutcome / CommerceOutcome namespaces in
  commerce.phase10_learning. Do not add another parallel classifier.
"""

from __future__ import annotations
import enum

# Phase 20 canonical taxonomy lives in adaptive_optimization; re-export here for backward compat
from commerce.adaptive_optimization import (
    CanonicalOutcome,
    OUTCOME_WEIGHTS,
    outcome_strength,
    classify_canonical_outcome,
)  # noqa: F401


class ConversationOutcome(str, enum.Enum):
    POSITIVE_ENGAGEMENT = "positive_engagement"
    NEUTRAL_ENGAGEMENT = "neutral_engagement"
    LOW_ENGAGEMENT = "low_engagement"
    QUESTION_ANSWERED = "question_answered"
    QUESTION_IGNORED = "question_ignored"
    TOPIC_CONTINUED = "topic_continued"
    TOPIC_CHANGED = "topic_changed"
    INTEREST_SIGNAL = "interest_signal"
    DESIRE_INCREASE = "desire_increase"
    DESIRE_DECREASE = "desire_decrease"
    OBJECTION = "objection"
    REJECTION = "rejection"
    OFFER_ACCEPTED = "offer_accepted"
    OFFER_DECLINED = "offer_declined"
    PURCHASE = "purchase"
    AFTERCARE_RESPONSE = "aftercare_response"
    REPEAT_INTEREST = "repeat_interest"
    RE_ENGAGEMENT_RESPONSE = "re_engagement_response"
    HUMAN_HANDOFF = "human_handoff"
    CONVERSATION_ENDED = "conversation_ended"
    # Phase 20 additional
    NO_SIGNAL = "no_signal"
    OFFER_REQUEST = "offer_request"
    REPEAT_PURCHASE = "repeat_purchase"
    COOLDOWN = "cooldown"
    PREFERENCE_LEARNED = "preference_learned"
    OPEN_LOOP_RESOLVED = "open_loop_resolved"
    OBJECTION_RESOLVED = "objection_resolved"
    AFTERCARE_ENGAGEMENT = "aftercare_engagement"


# Extended deterministic weights for legacy outcomes (learning evidence only)
LEGACY_OUTCOME_WEIGHTS: dict[str, float] = {
    ConversationOutcome.PURCHASE.value: 10.0,
    ConversationOutcome.REPEAT_PURCHASE.value: 12.0,
    ConversationOutcome.OBJECTION_RESOLVED.value: 6.0,
    ConversationOutcome.DESIRE_INCREASE.value: 4.0,
    ConversationOutcome.INTEREST_SIGNAL.value: 3.0,
    ConversationOutcome.AFTERCARE_RESPONSE.value: 3.0,
    ConversationOutcome.POSITIVE_ENGAGEMENT.value: 2.0,
    ConversationOutcome.TOPIC_CONTINUED.value: 1.5,
    ConversationOutcome.QUESTION_ANSWERED.value: 1.5,
    ConversationOutcome.NEUTRAL_ENGAGEMENT.value: 0.0,
    ConversationOutcome.NO_SIGNAL.value: 0.0,
    ConversationOutcome.CONVERSATION_ENDED.value: 0.0,
    ConversationOutcome.OBJECTION.value: -1.5,
    ConversationOutcome.DESIRE_DECREASE.value: -2.0,
    ConversationOutcome.COOLDOWN.value: -3.0,
    ConversationOutcome.HUMAN_HANDOFF.value: -1.0,
    ConversationOutcome.REJECTION.value: -4.0,
}


def get_outcome_weight(outcome: str | ConversationOutcome) -> float:
    key = outcome.value if isinstance(outcome, enum.Enum) else str(outcome).lower()
    if key in OUTCOME_WEIGHTS:
        return OUTCOME_WEIGHTS[key]
    return LEGACY_OUTCOME_WEIGHTS.get(key, 0.0)


def classify_outcome(
    previous_strategy: str, fan_message: str, desire_before: str, desire_after: str
) -> ConversationOutcome:
    """Legacy 6-branch classifier preserved for backward compat; Phase 20 uses classify_canonical_outcome."""
    low = fan_message.lower()
    if any(w in low for w in ["too expensive", "maybe later", "not now", "broke"]):
        return ConversationOutcome.OBJECTION
    if low.strip() in ["nah", "no", "not interested"]:
        return ConversationOutcome.REJECTION
    if "how much" in low or "where can i buy" in low:
        return ConversationOutcome.INTEREST_SIGNAL
    if len(fan_message.strip()) < 5:
        return ConversationOutcome.LOW_ENGAGEMENT
    if desire_after in ("desire", "qualification", "offer_ready") and desire_before in (
        "relationship",
        "curiosity",
    ):
        return ConversationOutcome.DESIRE_INCREASE
    return ConversationOutcome.NEUTRAL_ENGAGEMENT


def classify_outcome_canonical(
    *,
    fan_message: str | None = None,
    desire_before: str | None = None,
    desire_after: str | None = None,
    has_purchase: bool = False,
    is_repeat_purchase: bool = False,
    has_objection: bool = False,
    objection_resolved: bool = False,
    topic_continued: bool = False,
    question_answered: bool = False,
    open_loop_resolved: bool = False,
    preference_learned: bool = False,
    aftercare_engaged: bool = False,
    is_handoff: bool = False,
    is_cooldown: bool = False,
    is_conversation_end: bool = False,
    offer_requested: bool = False,
) -> ConversationOutcome:
    can = classify_canonical_outcome(
        fan_message=fan_message,
        desire_before=desire_before,
        desire_after=desire_after,
        has_purchase=has_purchase,
        is_repeat_purchase=is_repeat_purchase,
        has_objection=has_objection,
        objection_resolved=objection_resolved,
        topic_continued=topic_continued,
        question_answered=question_answered,
        open_loop_resolved=open_loop_resolved,
        preference_learned=preference_learned,
        aftercare_engaged=aftercare_engaged,
        is_handoff=is_handoff,
        is_cooldown=is_cooldown,
        is_conversation_end=is_conversation_end,
        offer_requested=offer_requested,
    )
    # map canonical to legacy enum if possible
    try:
        return ConversationOutcome(can.value)
    except Exception:
        return ConversationOutcome.NEUTRAL_ENGAGEMENT
