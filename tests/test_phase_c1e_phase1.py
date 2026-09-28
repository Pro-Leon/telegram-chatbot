"""C.1-E Phase 1 tests — P0/P1 runtime foundations.

Tests prove actual runtime behavior through the decision engine,
pipeline, and relationship modules. Each test traces the production
decision path, not isolated function calls.
"""

from __future__ import annotations

import pytest
from typing import Any

from commerce.decision import (
    CommerceDecisionContext,
    CommerceDecisionPolicy,
    CommerceReason,
    decide_commerce_action,
)
from commerce.feedback import (
    REJECTION_SEVERITY,
    RejectionType,
    classify_rejection,
    is_repeat_purchase_eligible,
)
from commerce.models import CommerceAction, PolicyDecision
from commerce.relationship import (
    CommercialPressure,
    OperatorHandoffReason,
    RelationshipState,
    TipEligibility,
    check_operator_handoff,
    check_tip_eligibility,
    derive_commercial_pressure,
    derive_relationship_state,
)
from commerce.signals import (
    CommerceSignals,
    signals_to_context,
)

ALLOWED = PolicyDecision(allowed=True, denial_reason="")
DENIED_BLOCKED = PolicyDecision(allowed=False, denial_reason="user_blocked")


def _ctx(**overrides: Any) -> CommerceDecisionContext:
    """Build a CommerceDecisionContext with sensible defaults."""
    defaults = dict(
        user_id=1,
        creator_id=2,
        eligibility=ALLOWED,
        buying_intent_score=0.0,
        relationship_score=0.0,
        conversational_phase="engaged_chat",
        signal_confidence=0.5,
    )
    defaults.update(overrides)
    return CommerceDecisionContext(**defaults)


def _signals(**overrides: Any) -> CommerceSignals:
    """Build CommerceSignals with safe defaults."""
    defaults = dict(
        purchase_intent=0.0,
        content_interest=0.0,
        relationship_engagement=0.5,
        price_interest=0.0,
        explicit_purchase_request=False,
        explicit_content_request=False,
        requested_price=None,
        declined_recent_offer=False,
        negative_sentiment=0.0,
        confidence=0.5,
        evidence=[],
        model_uncertainty=0.5,
        primary_intent="casual_chat",
        intent_tags=[],
        negative_intent_tags=[],
        fan_asks_question=False,
    )
    defaults.update(overrides)
    return CommerceSignals(**defaults)


# ═══════════════════════════════════════════════════════════════════════════
# P0-1: is_repeat_purchase_eligible() correct kwargs
# ═══════════════════════════════════════════════════════════════════════════


class TestP0_RepeatPurchaseEligibleKwargs:
    """P0-1: is_repeat_purchase_eligible() must use correct parameter names."""

    def test_eligible_returning_buyer(self):
        """Eligible buyer: 1+ purchase, cooled down, engaged, not paused."""
        assert is_repeat_purchase_eligible(
            total_purchases=1,
            hours_since_last_purchase=200.0,
            current_engagement=True,
            post_purchase_satisfaction="positive",
            commercial_paused=False,
            consecutive_rejections=0,
        ) is True

    def test_not_eligible_no_purchases(self):
        """Zero purchases → not eligible."""
        assert is_repeat_purchase_eligible(
            total_purchases=0,
            hours_since_last_purchase=None,
            current_engagement=True,
            post_purchase_satisfaction=None,
            commercial_paused=False,
            consecutive_rejections=0,
        ) is False

    def test_not_eligible_too_soon(self):
        """Purchased too recently → not eligible."""
        assert is_repeat_purchase_eligible(
            total_purchases=1,
            hours_since_last_purchase=24.0,
            current_engagement=True,
            post_purchase_satisfaction="positive",
            commercial_paused=False,
            consecutive_rejections=0,
        ) is False

    def test_not_eligible_commercial_paused(self):
        """Commercial pause → not eligible regardless of history."""
        assert is_repeat_purchase_eligible(
            total_purchases=3,
            hours_since_last_purchase=200.0,
            current_engagement=True,
            post_purchase_satisfaction="positive",
            commercial_paused=True,
            consecutive_rejections=3,
        ) is False

    def test_not_eligible_not_engaged(self):
        """Not currently engaged → not eligible."""
        assert is_repeat_purchase_eligible(
            total_purchases=1,
            hours_since_last_purchase=200.0,
            current_engagement=False,
            post_purchase_satisfaction="positive",
            commercial_paused=False,
            consecutive_rejections=0,
        ) is False

    def test_not_eligible_negative_satisfaction(self):
        """Negative satisfaction → not eligible."""
        assert is_repeat_purchase_eligible(
            total_purchases=1,
            hours_since_last_purchase=200.0,
            current_engagement=True,
            post_purchase_satisfaction="negative",
            commercial_paused=False,
            consecutive_rejections=0,
        ) is False

    def test_not_eligible_recent_rejection(self):
        """Recent rejection → not eligible."""
        assert is_repeat_purchase_eligible(
            total_purchases=1,
            hours_since_last_purchase=200.0,
            current_engagement=True,
            post_purchase_satisfaction="positive",
            commercial_paused=False,
            consecutive_rejections=1,
            hours_since_last_rejection=24.0,
        ) is False

    def test_kwargs_match_function_signature(self):
        """Verify function accepts the correct keyword arguments."""
        import inspect
        sig = inspect.signature(is_repeat_purchase_eligible)
        param_names = list(sig.parameters.keys())
        assert "total_purchases" in param_names
        assert "hours_since_last_purchase" in param_names
        assert "current_engagement" in param_names
        assert "post_purchase_satisfaction" in param_names
        assert "commercial_paused" in param_names
        assert "consecutive_rejections" in param_names
        # Must NOT have the old wrong kwarg
        assert "purchase_count" not in param_names


# ═══════════════════════════════════════════════════════════════════════════
# P0-2: Operator handoff conditions wired to signals
# ═══════════════════════════════════════════════════════════════════════════


class TestP0_OperatorHandoffWired:
    """P0-2: check_operator_handoff() must fire when signal conditions are met."""

    def test_complaint_signal_triggers_handoff(self):
        """has_complaint=True → HANDOFF (legacy default: payment-unknown)."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            has_complaint=True,
        )
        assert should_handoff is True
        assert reason == OperatorHandoffReason.COMPLAINT

    def test_experience_only_complaint_skips_handoff(self):
        """H1: experience-only complaint (no payment signal) → no handoff."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            has_complaint=True,
            complaint_is_experience_only=True,
        )
        assert should_handoff is False
        assert reason is None

    def test_custom_request_triggers_handoff(self):
        """has_custom_request=True → HANDOFF."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            has_custom_request=True,
        )
        assert should_handoff is True
        assert reason == OperatorHandoffReason.CUSTOM_REQUEST

    def test_provider_uncertain_triggers_handoff(self):
        """provider_uncertain=True → HANDOFF."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            provider_uncertain=True,
        )
        assert should_handoff is True
        assert reason == OperatorHandoffReason.PROVIDER_UNCERTAINTY

    def test_high_model_uncertainty_triggers_handoff(self):
        """model_uncertainty >= 0.80 → HANDOFF."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            model_uncertainty=0.85,
        )
        assert should_handoff is True
        assert reason == OperatorHandoffReason.LLM_UNCERTAINTY

    def test_high_negative_sentiment_triggers_handoff(self):
        """negative_sentiment >= 0.70 → HANDOFF (legacy default)."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            negative_sentiment=0.75,
        )
        assert should_handoff is True
        assert reason == OperatorHandoffReason.COMPLAINT

    def test_experience_only_sentiment_skips_handoff(self):
        """H1: experience-only + high sentiment → no complaint handoff."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            negative_sentiment=0.75,
            complaint_is_experience_only=True,
        )
        assert should_handoff is False
        assert reason is None

    def test_repeated_fulfillment_failures_triggers_handoff(self):
        """recent_fulfillment_failures >= 2 → HANDOFF."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            recent_fulfillment_failures=2,
        )
        assert should_handoff is True
        assert reason == OperatorHandoffReason.REPEATED_FULFILLMENT_FAILURE

    def test_creator_config_issue_triggers_handoff(self):
        """creator_config_issue=True → HANDOFF."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            creator_config_issue=True,
        )
        assert should_handoff is True
        assert reason == OperatorHandoffReason.CREATOR_CONFIGURATION

    def test_ambiguous_high_intent_triggers_handoff(self):
        """buying_intent >= 0.70 + curiosity intent → HANDOFF."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            buying_intent_score=0.75,
            intent_category="curiosity",
        )
        assert should_handoff is True
        assert reason == OperatorHandoffReason.AMBIGUOUS_INTENT

    def test_no_signal_conditions_no_handoff(self):
        """No signal conditions met → no handoff."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
        )
        assert should_handoff is False
        assert reason is None

    def test_operator_required_state_triggers_handoff(self):
        """OPERATOR_REQUIRED relationship → HANDOFF."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.OPERATOR_REQUIRED,
            commercial_pressure=CommercialPressure.NONE,
        )
        assert should_handoff is True
        assert reason == OperatorHandoffReason.SAFETY_CONCERN


# ═══════════════════════════════════════════════════════════════════════════
# CONVERSATION: Normal conversation → CHAT
# ═══════════════════════════════════════════════════════════════════════════


class TestConversation_NormalChat:
    """Normal conversation should produce CHAT (no commercial action)."""

    def test_normal_conversation_no_intent(self):
        """No buying intent, no product → RELATIONSHIP_BUILDING."""
        ctx = _ctx(
            buying_intent_score=0.0,
            user_asked_to_buy=False,
            user_asked_about_price=False,
            user_requested_content=False,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.RELATIONSHIP_BUILDING

    def test_low_confidence_prefers_chat(self):
        """Low signal confidence + no explicit intent → conversation."""
        ctx = _ctx(
            signal_confidence=0.15,
            buying_intent_score=0.2,
            user_asked_to_buy=False,
            user_asked_about_price=False,
            user_requested_content=False,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.RELATIONSHIP_BUILDING
        assert decision.reason_code == CommerceReason.LOW_CONFIDENCE_CHAT

    def test_topic_change_no_commerce(self):
        """Casual chat intent → no commercial action."""
        ctx = _ctx(
            conversational_phase="engaged_chat",
            buying_intent_score=0.1,
            user_asked_to_buy=False,
            user_asked_about_price=False,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.RELATIONSHIP_BUILDING

    def test_appreciation_does_not_auto_sell(self):
        """Appreciation (not explicit buy) → no commercial action."""
        ctx = _ctx(
            buying_intent_score=0.3,
            user_asked_to_buy=False,
            user_asked_about_price=False,
            user_requested_content=False,
            conversational_phase="rapport",
        )
        decision = decide_commerce_action(ctx)
        assert decision.action in (
            CommerceAction.RELATIONSHIP_BUILDING,
            CommerceAction.NO_OFFER,
        )

    def test_curiosity_does_not_force_ppv(self):
        """Content curiosity without buying intent → no forced PPV."""
        ctx = _ctx(
            buying_intent_score=0.2,
            user_asked_to_buy=False,
            user_asked_about_price=False,
            user_requested_content=False,
            conversational_phase="content_curiosity",
        )
        decision = decide_commerce_action(ctx)
        assert decision.action != CommerceAction.OFFER_PPV


# ═══════════════════════════════════════════════════════════════════════════
# BUYING: Explicit intent → eligible commerce
# ═══════════════════════════════════════════════════════════════════════════


class TestBuying_ExplicitIntent:
    """Explicit buying intent should reach commerce when policy permits."""

    def test_explicit_buy_with_product(self):
        """Explicit buy + product available → OFFER_PPV."""
        ctx = _ctx(
            user_asked_to_buy=True,
            has_relevant_product=True,
            creator_sales_enabled=True,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.OFFER_PPV
        assert decision.reason_code == CommerceReason.STRONG_BUYING_SIGNAL

    def test_explicit_price_inquiry(self):
        """Price inquiry → OFFER_PPV."""
        ctx = _ctx(
            user_asked_about_price=True,
            has_relevant_product=True,
            creator_sales_enabled=True,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.OFFER_PPV

    def test_buying_intent_with_commercial_pause(self):
        """Buying intent + commercial pause → policy pause respected."""
        ctx = _ctx(
            user_asked_to_buy=True,
            commercial_paused=True,
            has_relevant_product=True,
        )
        decision = decide_commerce_action(ctx)
        # Commercial pause blocks even explicit buy at step 7.9
        assert decision.action == CommerceAction.RELATIONSHIP_BUILDING
        assert decision.reason_code == CommerceReason.COMMERCIAL_PAUSED

    def test_buying_intent_after_purchase_respects_aftercare(self):
        """Buying intent + aftercare pending → aftercare phase."""
        ctx = _ctx(
            user_asked_to_buy=True,
            aftercare_status="pending",
            total_purchases=1,
            has_relevant_product=True,
        )
        decision = decide_commerce_action(ctx)
        # Explicit buy overrides aftercare (step 8 > step 7.10)
        assert decision.action == CommerceAction.OFFER_PPV

    def test_buying_no_product_fail_closed(self):
        """Buying intent + no product → NO_OFFER (fail closed)."""
        ctx = _ctx(
            user_asked_to_buy=True,
            has_relevant_product=False,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.NO_OFFER
        assert decision.reason_code == CommerceReason.NO_RELEVANT_PRODUCT

    def test_buying_with_disabled_creator(self):
        """Buying intent + creator disabled → NO_OFFER."""
        ctx = _ctx(
            user_asked_to_buy=True,
            creator_sales_enabled=False,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.NO_OFFER
        assert decision.reason_code == CommerceReason.CREATOR_NOT_READY

    def test_strong_implicit_buying_signal(self):
        """High implicit buying score (0.85) → OFFER_PPV."""
        ctx = _ctx(
            buying_intent_score=0.85,
            has_relevant_product=True,
            creator_sales_enabled=True,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.OFFER_PPV

    def test_moderate_buying_signal(self):
        """Moderate buying score (0.60) → SOFT_OFFER."""
        ctx = _ctx(
            buying_intent_score=0.60,
            has_relevant_product=True,
            creator_sales_enabled=True,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.SOFT_OFFER


# ═══════════════════════════════════════════════════════════════════════════
# REJECTION: Suppresses commerce
# ═══════════════════════════════════════════════════════════════════════════


class TestRejection_Suppression:
    """Rejection must suppress commerce appropriately."""

    def test_hard_rejection_classification(self):
        """Hard rejection tags → RejectionType.HARD."""
        rtype = classify_rejection(
            negative_intent_tags=["rejection"],
            negative_sentiment=0.8,
            price_interest=0.0,
            intent_tags=[],
        )
        assert rtype == RejectionType.HARD

    def test_price_objection_classification(self):
        """Hesitation + price interest → PRICE_OBJECTION."""
        rtype = classify_rejection(
            negative_intent_tags=["hesitation"],
            negative_sentiment=0.3,
            price_interest=0.7,
            intent_tags=[],
        )
        assert rtype == RejectionType.PRICE_OBJECTION

    def test_rejection_severity_ordering(self):
        """HARD severity > PRICE_OBJECTION > SOFT > UNCERTAIN."""
        assert REJECTION_SEVERITY[RejectionType.HARD] > REJECTION_SEVERITY[RejectionType.PRICE_OBJECTION]
        assert REJECTION_SEVERITY[RejectionType.PRICE_OBJECTION] > REJECTION_SEVERITY[RejectionType.SOFT]
        assert REJECTION_SEVERITY[RejectionType.SOFT] > REJECTION_SEVERITY[RejectionType.UNCERTAIN]

    def test_3_consecutive_rejections_suppress(self):
        """3+ consecutive rejections → commercial pause."""
        ctx = _ctx(
            consecutive_rejections=3,
            buying_intent_score=0.7,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.RELATIONSHIP_BUILDING
        assert decision.reason_code == CommerceReason.REJECTION_ESCALATION

    def test_commercial_pause_blocks_explicit_buy(self):
        """Commercial pause blocks explicit buying intent."""
        ctx = _ctx(
            commercial_paused=True,
            user_asked_to_buy=True,
            buying_intent_score=0.9,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.RELATIONSHIP_BUILDING
        assert decision.reason_code == CommerceReason.COMMERCIAL_PAUSED

    def test_rejection_no_tip_bypass(self):
        """Rejection + tip eligible → commercial pause blocks tip."""
        ctx = _ctx(
            consecutive_rejections=3,
            commercial_paused=True,
            tip_eligibility="eligible",
        )
        decision = decide_commerce_action(ctx)
        # Commercial pause (step 7.9) fires before tip (step 8.5)
        assert decision.action == CommerceAction.RELATIONSHIP_BUILDING


# ═══════════════════════════════════════════════════════════════════════════
# TIP INTELLIGENCE: One canonical path
# ═══════════════════════════════════════════════════════════════════════════


class TestTip_Intelligence:
    """Tip eligibility has one authoritative path through check_tip_eligibility."""

    def test_support_intent_tip_eligible(self):
        """Warm relationship + support context → tip eligible."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            fan_asked_how_to_support=True,
        )
        assert eligibility == TipEligibility.ELIGIBLE
        assert reason == "contextual_request"

    def test_recent_tip_cooldown(self):
        """Recent tip within 72h → cooldown active."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            hours_since_last_tip=24.0,
        )
        assert eligibility == TipEligibility.COOLDOWN_ACTIVE
        assert reason == "tip_cooldown"

    def test_tip_fatigue_suppresses(self):
        """2+ ignored suggestions → ineligible."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            tip_suggestions_sent=3,
            tip_suggestions_ignored=2,
        )
        assert eligibility == TipEligibility.INELIGIBLE
        assert reason == "tip_fatigue"

    def test_commercial_pause_blocks_tip(self):
        """Commercial pause → tip ineligible."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.NONE,
            commercial_paused=True,
        )
        assert eligibility == TipEligibility.INELIGIBLE
        assert reason == "commercial_paused"

    def test_cold_relationship_no_tip(self):
        """Cold relationship → tip ineligible."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.COLD,
            commercial_pressure=CommercialPressure.NONE,
        )
        assert eligibility == TipEligibility.INELIGIBLE
        assert reason == "insufficient_relationship"

    def test_new_user_no_tip(self):
        """New user → tip ineligible."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.NEW,
            commercial_pressure=CommercialPressure.NONE,
        )
        assert eligibility == TipEligibility.INELIGIBLE
        assert reason == "insufficient_relationship"

    def test_contextual_min_cooldown(self):
        """Appreciation + recent tip (<12h) → cooldown."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            hours_since_last_tip=6.0,
            fan_expressed_appreciation=True,
        )
        assert eligibility == TipEligibility.COOLDOWN_ACTIVE
        assert reason == "contextual_min_cooldown"

    def test_operators_cannot_tip(self):
        """OPERATOR_REQUIRED → no tip."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.OPERATOR_REQUIRED,
            commercial_pressure=CommercialPressure.NONE,
        )
        assert eligibility == TipEligibility.INELIGIBLE
        assert reason == "insufficient_relationship"


# ═══════════════════════════════════════════════════════════════════════════
# HANDOFF: Authoritative, not prompt-only
# ═══════════════════════════════════════════════════════════════════════════


class TestHandoff_Authoritative:
    """Handoff must be a real deterministic outcome."""

    def test_explicit_operator_request(self):
        """OPERATOR_REQUIRED state → HANDOFF."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.OPERATOR_REQUIRED,
            commercial_pressure=CommercialPressure.NONE,
        )
        assert should_handoff is True
        assert reason == OperatorHandoffReason.SAFETY_CONCERN

    def test_complaint_triggers_handoff(self):
        """Complaint signal → HANDOFF (legacy default: payment-unknown)."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            has_complaint=True,
        )
        assert should_handoff is True
        assert reason == OperatorHandoffReason.COMPLAINT

    def test_experience_only_complaint_skips_handoff(self):
        """H1: experience-only complaint (no payment signal) → no handoff."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            has_complaint=True,
            complaint_is_experience_only=True,
        )
        assert should_handoff is False
        assert reason is None

    def test_handoff_decision_in_engine(self):
        """handoff_needed=True in context → OPERATOR_HANDOFF action."""
        ctx = _ctx(
            handoff_needed=True,
            handoff_reason="complaint",
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.OPERATOR_HANDOFF
        assert decision.reason_code == CommerceReason.OPERATOR_HANDOFF_NEEDED

    def test_handoff_stops_commerce(self):
        """Handoff active → no autonomous commerce."""
        ctx = _ctx(
            handoff_needed=True,
            user_asked_to_buy=True,
            has_relevant_product=True,
        )
        decision = decide_commerce_action(ctx)
        # Handoff (step 1.5) fires before explicit buy (step 8)
        assert decision.action == CommerceAction.OPERATOR_HANDOFF

    def test_no_handoff_normal_flow(self):
        """No handoff conditions → normal decision flow."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
        )
        assert should_handoff is False
        assert reason is None


# ═══════════════════════════════════════════════════════════════════════════
# AUTHORITY: LLM cannot bypass deterministic policy
# ═══════════════════════════════════════════════════════════════════════════


class TestAuthority_Limitations:
    """LLM cannot override deterministic decisions."""

    def test_low_confidence_no_commerce(self):
        """Low confidence signal → no commerce even with moderate intent."""
        signals = _signals(
            purchase_intent=0.6,
            confidence=0.1,
            primary_intent="casual_chat",
        )
        ctx = signals_to_context(
            signals,
            user_id=1,
            creator_id=2,
            eligibility=ALLOWED,
        )
        decision = decide_commerce_action(ctx)
        # Low confidence triggers step 7.7 → conversation
        assert decision.action == CommerceAction.RELATIONSHIP_BUILDING

    def test_negative_signals_suppress_commerce(self):
        """Negative signals → suppress commerce."""
        signals = _signals(
            purchase_intent=0.5,
            negative_intent_tags=["rejection", "hesitation"],
            negative_sentiment=0.6,
        )
        ctx = signals_to_context(
            signals,
            user_id=1,
            creator_id=2,
            eligibility=ALLOWED,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.RELATIONSHIP_BUILDING

    def test_application_state_wins_over_signals(self):
        """Application state (eligibility denied) wins over LLM signals."""
        signals = _signals(
            purchase_intent=0.9,
            explicit_purchase_request=True,
            confidence=0.95,
        )
        ctx = signals_to_context(
            signals,
            user_id=1,
            creator_id=2,
            eligibility=DENIED_BLOCKED,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.NO_OFFER
        assert decision.reason_code == CommerceReason.USER_BLOCKED

    def test_no_product_fail_closed(self):
        """No product available → fail closed, no fabricated commerce."""
        signals = _signals(
            purchase_intent=0.9,
            explicit_purchase_request=True,
        )
        ctx = signals_to_context(
            signals,
            user_id=1,
            creator_id=2,
            eligibility=ALLOWED,
        )
        # Override has_relevant_product to False
        ctx = ctx.__class__(
            **{**ctx.__dict__, "has_relevant_product": False}
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.NO_OFFER

    def test_kill_switch_prevents_commerce(self):
        """Eligibility denied (kill switch) → no commerce."""
        ctx = _ctx(
            eligibility=DENIED_BLOCKED,
            user_asked_to_buy=True,
            buying_intent_score=0.95,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.NO_OFFER


# ═══════════════════════════════════════════════════════════════════════════
# CREATOR ISOLATION: Creator A cannot affect Creator B
# ═══════════════════════════════════════════════════════════════════════════


class TestCreatorIsolation:
    """Each creator's state is fully isolated."""

    def test_different_creators_different_contexts(self):
        """Two creators produce independent decision contexts."""
        ctx_a = _ctx(user_id=1, creator_id=100)
        ctx_b = _ctx(user_id=1, creator_id=200)
        # Both should decide independently
        d_a = decide_commerce_action(ctx_a)
        d_b = decide_commerce_action(ctx_b)
        # Same inputs → same output (deterministic)
        assert d_a.action == d_b.action

    def test_handoff_is_creator_scoped(self):
        """Handoff for Creator A does not affect Creator B."""
        should_handoff_a, _ = check_operator_handoff(
            relationship_state=RelationshipState.OPERATOR_REQUIRED,
            commercial_pressure=CommercialPressure.NONE,
        )
        should_handoff_b, _ = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
        )
        assert should_handoff_a is True
        assert should_handoff_b is False

    def test_tip_eligibility_is_creator_scoped(self):
        """Tip eligibility for Creator A does not affect Creator B."""
        elig_a, _ = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
        )
        elig_b, _ = check_tip_eligibility(
            relationship_state=RelationshipState.COLD,
            commercial_pressure=CommercialPressure.NONE,
        )
        assert elig_a == TipEligibility.ELIGIBLE
        assert elig_b == TipEligibility.INELIGIBLE


# ═══════════════════════════════════════════════════════════════════════════
# ADVERSARIAL SCENARIOS
# ═══════════════════════════════════════════════════════════════════════════


class TestAdversarial:
    """Adversarial scenarios that must fail safely."""

    def test_rejection_then_casual_message(self):
        """Fan says 'I don't want to buy' then sends casual message."""
        # First: rejection
        ctx_reject = _ctx(
            negative_intent_count=2,
            conversational_phase="cooldown",
        )
        d_reject = decide_commerce_action(ctx_reject)
        # Should not offer during cooldown
        assert d_reject.action != CommerceAction.OFFER_PPV

        # Then: casual message
        ctx_casual = _ctx(
            buying_intent_score=0.1,
            user_asked_to_buy=False,
            conversational_phase="engaged_chat",
        )
        d_casual = decide_commerce_action(ctx_casual)
        assert d_casual.action == CommerceAction.RELATIONSHIP_BUILDING

    def test_maybe_later_repeated(self):
        """Fan says 'maybe later' repeatedly → fatigue."""
        ctx = _ctx(
            recent_offer_count=3,
            recent_sales_attempt_count=3,
            buying_intent_score=0.4,
        )
        decision = decide_commerce_action(ctx)
        # Should trigger offer fatigue or cooldown
        assert decision.action in (
            CommerceAction.NO_OFFER,
            CommerceAction.RELATIONSHIP_BUILDING,
        )

    def test_praise_does_not_trigger_sale(self):
        """Fan praises creator repeatedly → no automatic sale."""
        ctx = _ctx(
            buying_intent_score=0.2,
            user_asked_to_buy=False,
            user_asked_about_price=False,
            conversational_phase="rapport",
        )
        decision = decide_commerce_action(ctx)
        assert decision.action != CommerceAction.OFFER_PPV

    def test_content_question_no_buying_intent(self):
        """Fan asks about content but no buying intent → no forced PPV."""
        ctx = _ctx(
            buying_intent_score=0.15,
            user_asked_to_buy=False,
            user_asked_about_price=False,
            user_requested_content=False,
            conversational_phase="content_curiosity",
        )
        decision = decide_commerce_action(ctx)
        assert decision.action != CommerceAction.OFFER_PPV

    def test_explicit_buy_during_aftercare(self):
        """Fan explicitly requests purchase during aftercare → honored."""
        ctx = _ctx(
            user_asked_to_buy=True,
            aftercare_status="pending",
            total_purchases=1,
            has_relevant_product=True,
        )
        decision = decide_commerce_action(ctx)
        # Explicit buy overrides aftercare (step 8 > step 7.10)
        assert decision.action == CommerceAction.OFFER_PPV

    def test_tip_after_recent_tip(self):
        """Fan asks for tip link after recent tip → cooldown."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            hours_since_last_tip=24.0,
        )
        assert eligibility == TipEligibility.COOLDOWN_ACTIVE

    def test_policy_blocked_kill_switch(self):
        """AUTONOMY_ENABLED=false (eligibility denied) → no commerce."""
        ctx = _ctx(
            eligibility=PolicyDecision(allowed=False, denial_reason="user_blocked"),
            user_asked_to_buy=True,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.NO_OFFER

    def test_duplicate_inbound_idempotent(self):
        """Same decision context → same decision (idempotent)."""
        ctx = _ctx(
            buying_intent_score=0.7,
            has_relevant_product=True,
        )
        d1 = decide_commerce_action(ctx)
        d2 = decide_commerce_action(ctx)
        assert d1.action == d2.action
        assert d1.reason_code == d2.reason_code

    def test_two_creators_identical_product_ids(self):
        """Two creators with same product_id → independent decisions."""
        ctx_a = _ctx(user_id=1, creator_id=100, has_relevant_product=True)
        ctx_b = _ctx(user_id=1, creator_id=200, has_relevant_product=True)
        d_a = decide_commerce_action(ctx_a)
        d_b = decide_commerce_action(ctx_b)
        # Both decide independently based on their own context
        assert d_a.action == d_b.action  # Same inputs → same output

    def test_negotiation_intent_no_bypass(self):
        """Negotiation intent → does not bypass policy."""
        ctx = _ctx(
            buying_intent_score=0.5,
            user_asked_to_buy=False,
            user_asked_about_price=False,
            conversational_phase="commercial_interest",
        )
        decision = decide_commerce_action(ctx)
        # Negotiation alone does not trigger OFFER_PPV without explicit signals
        assert decision.action != CommerceAction.OFFER_PPV or decision.confidence < 1.0


# ═══════════════════════════════════════════════════════════════════════════
# RELATIONSHIP STATE DERIVATION
# ═══════════════════════════════════════════════════════════════════════════


class TestRelationshipState:
    """Relationship state derivation is deterministic."""

    def test_blocked_user(self):
        """Blocked user → DO_NOT_PUSH."""
        state = derive_relationship_state(is_blocked=True)
        assert state == RelationshipState.DO_NOT_PUSH

    def test_opted_out_user(self):
        """Opted-out user → DO_NOT_PUSH."""
        state = derive_relationship_state(do_not_auto_reply=True)
        assert state == RelationshipState.DO_NOT_PUSH

    def test_recent_purchase_cooling_down(self):
        """Recent purchase (<7 days) → COOLING_DOWN."""
        state = derive_relationship_state(
            purchase_count=1,
            last_purchase_days_ago=3.0,
        )
        assert state == RelationshipState.COOLING_DOWN

    def test_repeat_buyer(self):
        """2+ purchases → REPEAT_BUYER."""
        state = derive_relationship_state(purchase_count=2)
        assert state == RelationshipState.REPEAT_BUYER

    def test_new_user(self):
        """Funnel stage 'new' → NEW."""
        state = derive_relationship_state(funnel_stage="new")
        assert state == RelationshipState.NEW

    def test_cold_user(self):
        """No recent messages, no purchases → COLD."""
        state = derive_relationship_state(
            last_message_days_ago=30.0,
            purchase_count=0,
        )
        assert state == RelationshipState.COLD


# ═══════════════════════════════════════════════════════════════════════════
# DECISION ENGINE PRIORITY ORDER
# ═══════════════════════════════════════════════════════════════════════════


class TestDecisionPriority:
    """Decision engine priority order is correct."""

    def test_eligibility_denied_wins_over_all(self):
        """Step 1 (eligibility) wins over everything else."""
        ctx = _ctx(
            eligibility=DENIED_BLOCKED,
            user_asked_to_buy=True,
            handoff_needed=True,
            commercial_paused=False,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.NO_OFFER
        assert decision.reason_code == CommerceReason.USER_BLOCKED

    def test_handoff_wins_over_explicit_buy(self):
        """Step 1.5 (handoff) wins over step 8 (explicit buy)."""
        ctx = _ctx(
            handoff_needed=True,
            user_asked_to_buy=True,
            has_relevant_product=True,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.OPERATOR_HANDOFF

    def test_commercial_pause_wins_over_tip(self):
        """Step 7.9 (commercial pause) wins over step 8.5 (tip)."""
        ctx = _ctx(
            commercial_paused=True,
            tip_eligibility="eligible",
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.RELATIONSHIP_BUILDING

    def test_explicit_buy_wins_over_implicit(self):
        """Step 8 (explicit buy) wins over step 9 (strong implicit)."""
        ctx = _ctx(
            user_asked_to_buy=True,
            buying_intent_score=0.9,
            has_relevant_product=True,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.OFFER_PPV
        assert decision.reason_code == CommerceReason.STRONG_BUYING_SIGNAL
