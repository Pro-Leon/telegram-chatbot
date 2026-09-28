"""Relationship state, commercial pressure, and tip eligibility (Phase C).

Derives deterministic relationship and pressure state from observable data.
No LLM calls, no provider calls, no credentials, no randomness.

Relationship state maps from existing funnel_stage + purchase history +
recency + segment membership. Commercial pressure tracks recent activity
to prevent excessive selling. Tip eligibility uses DropFans canonical links.
"""

import enum
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


class RelationshipState(str, enum.Enum):
    """Deterministic relationship state derived from observable data.

    Maps from existing funnel_stage + purchase history + recency.
    The LLM never assigns this — it is derived from DB state.
    """

    COLD = "cold"
    NEW = "new"
    ENGAGED = "engaged"
    WARM = "warm"
    BUYING_SIGNAL = "buying_signal"
    PURCHASED = "purchased"
    REPEAT_BUYER = "repeat_buyer"
    VIP = "vip"
    COOLING_DOWN = "cooling_down"
    DO_NOT_PUSH = "do_not_push"
    OPERATOR_REQUIRED = "operator_required"


class CommercialPressure(str, enum.Enum):
    """Deterministic commercial pressure level.

    NONE: normal conversation, no commercial material unless fan raises it.
    SOFT: commercial content may be mentioned when contextually relevant.
    MODERATE: specific product may be surfaced after meaningful buying signals.
    DIRECT: fan has clearly requested content/price/purchase info.

    Pressure decreases after rejection, ignored offers, unanswered messages.
    Pressure increases after explicit requests, purchases, returning fans.
    """

    NONE = "none"
    SOFT = "soft"
    MODERATE = "moderate"
    DIRECT = "direct"


class TipEligibility(str, enum.Enum):
    """Whether a tip suggestion is appropriate right now."""

    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    COOLDOWN_ACTIVE = "cooldown_active"
    NO_CONTEXT = "no_context"
    DO_NOT_PUSH = "do_not_push"


class OperatorHandoffReason(str, enum.Enum):
    """Deterministic reasons for operator handoff."""

    AMBIGUOUS_INTENT = "ambiguous_intent"
    CUSTOM_REQUEST = "custom_request"
    PROVIDER_UNCERTAINTY = "provider_uncertainty"
    PAYMENT_DISPUTE = "payment_dispute"
    COMPLAINT = "complaint"
    UNUSUAL_REQUEST = "unusual_request"
    REPEATED_FULFILLMENT_FAILURE = "repeated_fulfillment_failure"
    SAFETY_CONCERN = "safety_concern"
    LLM_UNCERTAINTY = "llm_uncertainty"
    CREATOR_CONFIGURATION = "creator_configuration"


from commerce.models import CapabilityStatus, CreatorCapabilities


# ── Funnel stage → relationship state mapping ──────────────────────────────
#
# Phase 2.4 audit note: this ordering is intentional, not inverted. Funnel
# rank runs new < warming < engaged < converted, and relationship rank runs
# NEW < ENGAGED < WARM < PURCHASED (rule precedence 10 < 9 < 8 < 6 below), so
# the static map is monotonic: warming→ENGAGED, engaged→WARM. Rule 8
# (funnel engaged/converted + recent ⇒ WARM) and rule 9 (recent ⇒ ENGAGED)
# implement the same ranking dynamically; this map is the static fallback
# and is currently unused (derivations go through derive_relationship_state
# via the Phase 2.4 single owner in core/conversation_state.py).
_FUNNEL_STAGE_MAP = {
    "new": RelationshipState.NEW,
    "warming": RelationshipState.ENGAGED,
    "engaged": RelationshipState.WARM,
    "converted": RelationshipState.PURCHASED,
    "vip": RelationshipState.VIP,
}


def derive_relationship_state(
    *,
    funnel_stage: str | None = None,
    purchase_count: int = 0,
    last_purchase_days_ago: float | None = None,
    last_message_days_ago: float | None = None,
    message_count: int = 0,
    has_active_offer: bool = False,
    previous_offer_declined: bool = False,
    is_blocked: bool = False,
    do_not_auto_reply: bool = False,
    segment_names: list[str] | None = None,
    operator_intervention_recent: bool = False,
) -> RelationshipState:
    """Derive relationship state from observable data.

    Pure function — no I/O, no LLM, no randomness.
    Priority order (first match wins):
      1. blocked/opted-out → DO_NOT_PUSH
      2. operator intervention → OPERATOR_REQUIRED
      3. cooling down after purchase → COOLING_DOWN
      4. VIP segment/funnel → VIP
      5. repeat buyer → REPEAT_BUYER
      6. has bought → PURCHASED
      7. buying signal (active offer + recent engagement) → BUYING_SIGNAL
      8. warm (engaged funnel + good recency) → WARM
      9. engaged (recent messages) → ENGAGED
      10. new (recent first message) → NEW
      11. cold (everything else) → COLD
    """
    segments = set(segment_names or [])

    # 1. Hard blocks
    if is_blocked or do_not_auto_reply:
        return RelationshipState.DO_NOT_PUSH

    # 2. Operator intervention
    if operator_intervention_recent:
        return RelationshipState.OPERATOR_REQUIRED

    # 3. Cooling down after recent purchase
    if (
        last_purchase_days_ago is not None
        and last_purchase_days_ago < 7.0
        and purchase_count > 0
    ):
        return RelationshipState.COOLING_DOWN

    # 4. VIP
    if funnel_stage == "vip" or "vip" in segments:
        return RelationshipState.VIP

    # 5. Repeat buyer
    if purchase_count >= 2:
        return RelationshipState.REPEAT_BUYER

    # 6. Has purchased
    if purchase_count >= 1:
        return RelationshipState.PURCHASED

    # 7. Buying signal: active offer + recent engagement
    if has_active_offer and last_message_days_ago is not None and last_message_days_ago < 3:
        return RelationshipState.BUYING_SIGNAL

    # 8. Warm
    if funnel_stage in ("engaged", "converted"):
        if last_message_days_ago is not None and last_message_days_ago < 7:
            return RelationshipState.WARM

    # 9. Engaged
    if last_message_days_ago is not None and last_message_days_ago < 3:
        return RelationshipState.ENGAGED

    # 10. New
    if funnel_stage == "new" or (
        last_message_days_ago is not None and last_message_days_ago <= 7
    ):
        return RelationshipState.NEW

    # 11. Cold
    return RelationshipState.COLD


def derive_commercial_pressure(
    *,
    relationship_state: RelationshipState = RelationshipState.COLD,
    recent_offer_count_24h: int = 0,
    recent_purchase_count_24h: int = 0,
    hours_since_last_offer: float | None = None,
    hours_since_last_purchase: float | None = None,
    previous_offer_declined: bool = False,
    previous_offer_ignored: bool = False,
    unanswered_messages: int = 0,
    buying_intent_score: float | None = None,
    explicit_request: bool = False,
) -> CommercialPressure:
    """Derive commercial pressure from recent activity.

    Pure function — no I/O, no LLM, no randomness.
    Pressure increases with explicit requests and strong signals.
    Pressure decreases with rejection, ignoring, and excessive activity.
    """
    # Hard blocks → no pressure
    if relationship_state in (
        RelationshipState.DO_NOT_PUSH,
        RelationshipState.OPERATOR_REQUIRED,
        RelationshipState.COOLING_DOWN,
    ):
        return CommercialPressure.NONE

    # Explicit request → DIRECT
    if explicit_request:
        return CommercialPressure.DIRECT

    # Rejection or ignored → NONE (cooldown)
    if previous_offer_declined or previous_offer_ignored:
        return CommercialPressure.NONE

    # Excessive recent activity → NONE
    if recent_offer_count_24h >= 2:
        return CommercialPressure.NONE

    # Unanswered messages → reduce pressure
    if unanswered_messages >= 3:
        return CommercialPressure.NONE

    # Strong buying signal → MODERATE
    if buying_intent_score is not None and buying_intent_score >= 0.80:
        return CommercialPressure.MODERATE

    # Moderate buying signal + appropriate relationship → SOFT
    if buying_intent_score is not None and buying_intent_score >= 0.55:
        if relationship_state in (
            RelationshipState.WARM,
            RelationshipState.BUYING_SIGNAL,
            RelationshipState.PURCHASED,
            RelationshipState.REPEAT_BUYER,
            RelationshipState.VIP,
        ):
            return CommercialPressure.SOFT

    # Recent purchase → soft pressure for aftercare
    if (
        hours_since_last_purchase is not None
        and hours_since_last_purchase < 48
    ):
        return CommercialPressure.SOFT

    # Engaged relationship with no blockers → SOFT
    if relationship_state in (
        RelationshipState.ENGAGED,
        RelationshipState.WARM,
        RelationshipState.BUYING_SIGNAL,
        RelationshipState.PURCHASED,
        RelationshipState.REPEAT_BUYER,
        RelationshipState.VIP,
    ):
        return CommercialPressure.SOFT

    # Everything else → NONE
    return CommercialPressure.NONE


# ── Tip eligibility ────────────────────────────────────────────────────────

# Default tip cooldown thresholds (hours)
# Stronger negative signals result in stronger suppression
_TIP_COOLDOWN_BASE = 72.0  # Base cooldown for warm relationships
_TIP_COOLDOWN_ENGAGED = 48.0  # Shorter for engaged users
_TIP_COOLDOWN_VIP = 24.0  # Shorter for VIP users
_TIP_COOLDOWN_FATIGUE_MULTIPLIER = 1.5  # Each ignored suggestion adds 50%
_TIP_COOLDOWN_CONTEXTUAL_MIN = 12.0  # Minimum spacing for contextual triggers


def _calculate_tip_cooldown_hours(
    relationship_state: RelationshipState,
    tip_suggestions_ignored: int,
    tip_suggestions_sent: int,
) -> float:
    """Calculate graduated tip cooldown based on relationship and fatigue.

    Stronger negative signals (more ignored suggestions) result in
    stronger suppression (longer cooldowns). Warmer relationships
    get shorter cooldowns.
    """
    # Base cooldown by relationship state
    if relationship_state == RelationshipState.VIP:
        base = _TIP_COOLDOWN_VIP
    elif relationship_state == RelationshipState.ENGAGED:
        base = _TIP_COOLDOWN_ENGAGED
    else:
        base = _TIP_COOLDOWN_BASE

    # Fatigue multiplier: each ignored suggestion adds 50%
    fatigue_multiplier = 1.0 + (tip_suggestions_ignored * 0.5)

    return base * fatigue_multiplier


def check_tip_eligibility(
    *,
    relationship_state: RelationshipState,
    commercial_pressure: CommercialPressure,
    hours_since_last_tip: float | None = None,
    has_active_offer: bool = False,
    recent_purchase_count: int = 0,
    fan_expressed_appreciation: bool = False,
    fan_asked_how_to_support: bool = False,
    tip_suggestions_sent: int = 0,
    tip_suggestions_ignored: int = 0,
    commercial_paused: bool = False,
) -> tuple[TipEligibility, str]:
    """Check whether a tip suggestion is appropriate.

    Returns (eligibility, reason).
    Tip suggestions are contextual and infrequent.
    Fatigue-aware: ignores suggestions reduce eligibility.
    """
    # Hard blocks
    if relationship_state in (
        RelationshipState.DO_NOT_PUSH,
        RelationshipState.OPERATOR_REQUIRED,
        RelationshipState.COLD,
        RelationshipState.NEW,
    ):
        return TipEligibility.INELIGIBLE, "insufficient_relationship"

    # Commercial pause: never suggest during active pause
    if commercial_paused:
        return TipEligibility.INELIGIBLE, "commercial_paused"

    # Active offer in progress → don't distract
    if has_active_offer:
        return TipEligibility.INELIGIBLE, "active_offer"

    # Recent purchase → aftercare, not upsell
    if recent_purchase_count > 0:
        return TipEligibility.INELIGIBLE, "recent_purchase"

    # Fatigue: 2+ suggestions ignored AND 2+ sent → suppress
    # Fatigue overrides contextual triggers to prevent repeated ignored suggestions
    if tip_suggestions_ignored >= 2 and tip_suggestions_sent >= 2:
        return TipEligibility.INELIGIBLE, "tip_fatigue"

    # Contextual triggers (appreciation / how to support) override cooldown
    # but still respect minimum spacing to avoid spam
    if fan_expressed_appreciation or fan_asked_how_to_support:
        if hours_since_last_tip is not None and hours_since_last_tip < _TIP_COOLDOWN_CONTEXTUAL_MIN:
            return TipEligibility.COOLDOWN_ACTIVE, "contextual_min_cooldown"
        return TipEligibility.ELIGIBLE, "contextual_request"

    # Graduated cooldown based on relationship and fatigue
    cooldown_hours = _calculate_tip_cooldown_hours(
        relationship_state, tip_suggestions_ignored, tip_suggestions_sent
    )
    if (
        hours_since_last_tip is not None
        and hours_since_last_tip < cooldown_hours
    ):
        return TipEligibility.COOLDOWN_ACTIVE, "tip_cooldown"

    # Relationship-based eligibility
    if relationship_state in (
        RelationshipState.WARM,
        RelationshipState.BUYING_SIGNAL,
        RelationshipState.PURCHASED,
        RelationshipState.REPEAT_BUYER,
        RelationshipState.VIP,
    ):
        return TipEligibility.ELIGIBLE, "relationship_based"

    if relationship_state == RelationshipState.ENGAGED:
        if commercial_pressure in (CommercialPressure.NONE, CommercialPressure.SOFT):
            return TipEligibility.ELIGIBLE, "engaged_low_pressure"

    return TipEligibility.INELIGIBLE, "no_context"


# ── Operator handoff decision ──────────────────────────────────────────────

_OFFER_NEGATIVE_STATUSES = frozenset({"declined", "revoked"})


def check_operator_handoff(
    *,
    relationship_state: RelationshipState,
    commercial_pressure: CommercialPressure,
    intent_category: str = "",
    buying_intent_score: float | None = None,
    negative_sentiment: float = 0.0,
    model_uncertainty: float = 0.0,
    recent_fulfillment_failures: int = 0,
    has_complaint: bool = False,
    has_custom_request: bool = False,
    provider_uncertain: bool = False,
    creator_config_issue: bool = False,
    # H1 triage exemption (additive, default preserves legacy behavior):
    # a positively-identified experience-only complaint (explicit
    # experience wording, no technical payment signal, no payment
    # claim — see commerce/complaint_triage_evidence.py) does not
    # auto-handoff; the RECOVER/sincerity path handles realization.
    # Applies to both complaint-attributed rules below; every other
    # handoff reason is untouched.
    complaint_is_experience_only: bool = False,
) -> tuple[bool, OperatorHandoffReason | None]:
    """Deterministic operator handoff check.

    Returns (should_handoff, reason).
    """
    # Safety concerns
    if relationship_state == RelationshipState.OPERATOR_REQUIRED:
        return True, OperatorHandoffReason.SAFETY_CONCERN

    if creator_config_issue:
        return True, OperatorHandoffReason.CREATOR_CONFIGURATION

    # Payment disputes and complaints
    # H1: experience-only complaints (no payment signal) skip this rule.
    if has_complaint and not complaint_is_experience_only:
        return True, OperatorHandoffReason.COMPLAINT

    # Custom requests
    if has_custom_request:
        return True, OperatorHandoffReason.CUSTOM_REQUEST

    # Provider uncertainty
    if provider_uncertain:
        return True, OperatorHandoffReason.PROVIDER_UNCERTAINTY

    # Repeated fulfillment failures
    if recent_fulfillment_failures >= 2:
        return True, OperatorHandoffReason.REPEATED_FULFILLMENT_FAILURE

    # High model uncertainty
    if model_uncertainty >= 0.80:
        return True, OperatorHandoffReason.LLM_UNCERTAINTY

    # Strong negative sentiment
    # H1: experience-only complaints (no payment signal) skip this rule.
    if negative_sentiment >= 0.70 and not complaint_is_experience_only:
        return True, OperatorHandoffReason.COMPLAINT

    # Ambiguous high intent (buyer signals but no clear action)
    if (
        buying_intent_score is not None
        and buying_intent_score >= 0.70
        and intent_category in ("curiosity", "content_interest", "")
    ):
        return True, OperatorHandoffReason.AMBIGUOUS_INTENT

    return False, None


# ── Creator capabilities ───────────────────────────────────────────────────


def derive_creator_capabilities(
    *,
    has_dropfans_integration: bool = False,
    dropfans_authenticated: bool = False,
    has_valid_product: bool = False,
    has_valid_sales_url: bool = False,
    provider_healthy: bool = True,
    tip_capability_available: bool = True,
) -> CreatorCapabilities:
    """Derive creator capabilities from existing state.

    Pure function — no I/O, no LLM, no randomness.
    Uses existing state queries to determine what a creator can actually do.
    """
    content_sales = CapabilityStatus.UNKNOWN
    if has_dropfans_integration and dropfans_authenticated and has_valid_product and has_valid_sales_url:
        content_sales = CapabilityStatus.AVAILABLE
    elif has_dropfans_integration and not dropfans_authenticated:
        content_sales = CapabilityStatus.UNAVAILABLE

    tips = CapabilityStatus.UNKNOWN
    if has_dropfans_integration and dropfans_authenticated and tip_capability_available:
        tips = CapabilityStatus.AVAILABLE
    elif has_dropfans_integration and not dropfans_authenticated:
        tips = CapabilityStatus.UNAVAILABLE

    provider_health = CapabilityStatus.AVAILABLE if provider_healthy else CapabilityStatus.UNAVAILABLE

    return CreatorCapabilities(
        content_sales=content_sales,
        tips=tips,
        provider_health=provider_health,
        has_valid_product=has_valid_product,
        has_valid_sales_url=has_valid_sales_url,
        has_dropfans_integration=has_dropfans_integration,
        dropfans_authenticated=dropfans_authenticated,
    )


# ── Context dataclass ──────────────────────────────────────────────────────


@dataclass
class RelationshipContext:
    """Derived relationship context for the LLM and decision engine.

    Pure data — no provider calls, no credentials, no API keys.
    """

    state: RelationshipState = RelationshipState.COLD
    pressure: CommercialPressure = CommercialPressure.NONE
    tip_eligibility: TipEligibility = TipEligibility.INELIGIBLE
    tip_reason: str = ""
    handoff_needed: bool = False
    handoff_reason: OperatorHandoffReason | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for LLM context injection."""
        return {
            "relationship_state": self.state.value,
            "commercial_pressure": self.pressure.value,
            "tip_eligibility": self.tip_eligibility.value,
            "tip_reason": self.tip_reason,
            "handoff_needed": self.handoff_needed,
            "handoff_reason": self.handoff_reason.value if self.handoff_reason else None,
        }
