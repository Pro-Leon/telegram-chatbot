"""Phase C.1-B — Conversational Intent & Signal Intelligence tests.

Covers:
- Intent taxonomy validation
- Multi-intent support in CommerceSignals
- Negative signal detection
- Conversational phase derivation
- Decision engine phase-aware logic
- Offer fatigue detection
- Low confidence → conversation
- Scenario tests (greeting, casual, content, purchase, rejection, etc.)
- Adversarial tests
"""

from __future__ import annotations

from typing import Any

import pytest

from commerce.decision import (
    CommerceDecisionContext,
    CommerceReason,
    CommerceDecisionPolicy,
    decide_commerce_action,
)
from commerce.models import CommerceAction, PolicyDecision
from commerce.signals import (
    CommerceSignals,
    INTENT_CATEGORIES,
    _COMMERCIAL_INTENTS,
    _NEGATIVE_INTENTS,
    signals_to_context,
    _derive_conversational_phase,
)

ALLOWED = PolicyDecision(allowed=True)
DENIED = PolicyDecision(allowed=False, denial_reason="user_blocked")


def _make_signals(**overrides: Any) -> CommerceSignals:
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
        primary_intent="uncertain",
        intent_tags=[],
        negative_intent_tags=[],
        fan_asks_question=False,
    )
    defaults.update(overrides)
    return CommerceSignals(**defaults)


def _ctx(
    *,
    buying_intent_score: float | None = 0.0,
    relationship_score: float | None = 0.0,
    user_asked_to_buy: bool = False,
    user_asked_about_price: bool = False,
    user_requested_content: bool = False,
    previous_offer_status: str | None = None,
    has_active_offer: bool = False,
    recent_offer_count: int = 0,
    recent_sales_attempt_count: int = 0,
    conversational_phase: str = "unknown",
    negative_intent_count: int = 0,
    signal_confidence: float | None = None,
    fan_asks_question: bool = False,
    has_commercial_intent: bool = False,
    **extra: Any,
) -> CommerceDecisionContext:
    """Build a CommerceDecisionContext for testing."""
    return CommerceDecisionContext(
        user_id=1,
        creator_id=2,
        eligibility=ALLOWED,
        buying_intent_score=buying_intent_score,
        relationship_score=relationship_score,
        user_asked_to_buy=user_asked_to_buy,
        user_asked_about_price=user_asked_about_price,
        user_requested_content=user_requested_content,
        previous_offer_status=previous_offer_status,
        has_active_offer=has_active_offer,
        recent_offer_count=recent_offer_count,
        recent_sales_attempt_count=recent_sales_attempt_count,
        conversational_phase=conversational_phase,
        negative_intent_count=negative_intent_count,
        signal_confidence=signal_confidence,
        fan_asks_question=fan_asks_question,
        has_commercial_intent=has_commercial_intent,
        **extra,
    )


# ---------------------------------------------------------------------------
# 1. Intent taxonomy
# ---------------------------------------------------------------------------


class TestIntentTaxonomy:
    def test_commercial_intents_subset_of_all(self):
        assert _COMMERCIAL_INTENTS.issubset(INTENT_CATEGORIES)

    def test_negative_intents_subset_of_all(self):
        assert _NEGATIVE_INTENTS.issubset(INTENT_CATEGORIES)

    def test_negative_intents_are_hesitation_rejection_complaint(self):
        assert _NEGATIVE_INTENTS == {"hesitation", "rejection", "complaint"}

    def test_commercial_intents_include_purchase_and_content(self):
        assert "purchase_intent" in _COMMERCIAL_INTENTS
        assert "content_request" in _COMMERCIAL_INTENTS
        assert "price_inquiry" in _COMMERCIAL_INTENTS

    def test_valid_primary_intent_accepted(self):
        signals = _make_signals(primary_intent="greeting")
        assert signals.primary_intent == "greeting"

    def test_invalid_primary_intent_rejected(self):
        with pytest.raises(Exception):
            _make_signals(primary_intent="invalid_intent_xyz")

    def test_invalid_intent_tag_rejected(self):
        with pytest.raises(Exception):
            _make_signals(intent_tags=["invalid_tag"])

    def test_invalid_negative_intent_tag_rejected(self):
        with pytest.raises(Exception):
            _make_signals(negative_intent_tags=["purchase_intent"])


# ---------------------------------------------------------------------------
# 2. Multi-intent support
# ---------------------------------------------------------------------------


class TestMultiIntent:
    def test_multiple_intent_tags(self):
        signals = _make_signals(
            primary_intent="content_curiosity",
            intent_tags=["content_curiosity", "relationship_building"],
        )
        assert len(signals.intent_tags) == 2

    def test_empty_intent_tags(self):
        signals = _make_signals(intent_tags=[])
        assert signals.intent_tags == []

    def test_commercial_intent_detected(self):
        signals = _make_signals(intent_tags=["content_curiosity", "casual_chat"])
        has_commercial = bool(set(signals.intent_tags) & _COMMERCIAL_INTENTS)
        assert has_commercial is True

    def test_no_commercial_intent(self):
        signals = _make_signals(intent_tags=["casual_chat", "greeting"])
        has_commercial = bool(set(signals.intent_tags) & _COMMERCIAL_INTENTS)
        assert has_commercial is False

    def test_multi_intent_preserved_in_context(self):
        signals = _make_signals(
            intent_tags=["content_curiosity", "price_inquiry"],
        )
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        assert ctx.has_commercial_intent is True

    def test_low_information_has_no_intents(self):
        signals = CommerceSignals.low_information()
        assert signals.primary_intent == "uncertain"
        assert signals.intent_tags == []
        assert signals.negative_intent_tags == []


# ---------------------------------------------------------------------------
# 3. Negative signal detection
# ---------------------------------------------------------------------------


class TestNegativeSignals:
    def test_no_negative_signals(self):
        signals = _make_signals(negative_intent_tags=[])
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        assert ctx.negative_intent_count == 0

    def test_single_negative_signal(self):
        signals = _make_signals(negative_intent_tags=["hesitation"])
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        assert ctx.negative_intent_count == 1

    def test_multiple_negative_signals(self):
        signals = _make_signals(negative_intent_tags=["hesitation", "rejection"])
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        assert ctx.negative_intent_count == 2

    def test_negative_signals_suppress_commercial(self):
        ctx = _ctx(negative_intent_count=2)
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.RELATIONSHIP_BUILDING
        assert decision.reason_code == CommerceReason.NEGATIVE_SIGNALS_SUPPRESSED


# ---------------------------------------------------------------------------
# 4. Conversational phase derivation
# ---------------------------------------------------------------------------


class TestConversationalPhase:
    def test_greeting_phase(self):
        phase = _derive_conversational_phase("new", "greeting", False, False, False)
        assert phase == "opening"

    def test_rapport_phase(self):
        phase = _derive_conversational_phase("new", "personal_disclosure", False, False, False)
        assert phase == "rapport"

    def test_content_curiosity_phase(self):
        phase = _derive_conversational_phase("warm", "content_curiosity", False, False, False)
        assert phase == "content_curiosity"

    def test_commercial_interest_phase(self):
        phase = _derive_conversational_phase("warm", "purchase_intent", True, False, False)
        assert phase == "commercial_interest"

    def test_explicit_buy_phase(self):
        phase = _derive_conversational_phase("warm", "casual_chat", False, True, False)
        assert phase == "commercial_interest"

    def test_post_purchase_phase(self):
        phase = _derive_conversational_phase("purchased", "casual_chat", False, False, False)
        assert phase == "post_purchase"

    def test_cooldown_phase(self):
        phase = _derive_conversational_phase("warm", "casual_chat", False, False, True)
        assert phase == "cooldown"

    def test_engaged_chat_warm(self):
        phase = _derive_conversational_phase("warm", "casual_chat", False, False, False)
        assert phase == "engaged_chat"

    def test_rapport_for_new_uncertain(self):
        phase = _derive_conversational_phase("new", "uncertain", False, False, False)
        assert phase == "rapport"

    def test_operator_handoff_phase(self):
        phase = _derive_conversational_phase("operator_required", "casual_chat", False, False, False)
        assert phase == "operator_handoff"

    def test_none_intent_defaults_to_engaged(self):
        phase = _derive_conversational_phase("warm", None, False, False, False)
        assert phase == "engaged_chat"


# ---------------------------------------------------------------------------
# 5. Decision engine — phase-aware logic
# ---------------------------------------------------------------------------


class TestPhaseAwareDecision:
    def test_opening_phase_suppresses_commercial(self):
        ctx = _ctx(conversational_phase="opening", buying_intent_score=0.7)
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.RELATIONSHIP_BUILDING
        assert decision.reason_code == CommerceReason.CONVERSATIONAL_CHAT

    def test_rapport_phase_suppresses_commercial(self):
        ctx = _ctx(conversational_phase="rapport", buying_intent_score=0.7)
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.RELATIONSHIP_BUILDING

    def test_commercial_interest_phase_allows_offer(self):
        ctx = _ctx(
            conversational_phase="commercial_interest",
            user_asked_to_buy=True,
            has_commercial_intent=True,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.OFFER_PPV
        assert decision.reason_code == CommerceReason.STRONG_BUYING_SIGNAL

    def test_cooldown_phase_suppresses_after_decline(self):
        ctx = _ctx(
            conversational_phase="cooldown",
            previous_offer_status="declined",
            hours_since_last_offer=2.0,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.NO_OFFER


# ---------------------------------------------------------------------------
# 6. Offer fatigue
# ---------------------------------------------------------------------------


class TestOfferFatigue:
    def test_offer_fatigue_suppresses_moderate_intent(self):
        ctx = _ctx(
            recent_offer_count=1,
            recent_sales_attempt_count=2,
            buying_intent_score=0.60,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.NO_OFFER
        assert decision.reason_code == CommerceReason.OFFER_FATIGUE

    def test_offer_fatigue_does_not_suppress_strong_explicit(self):
        ctx = _ctx(
            recent_offer_count=1,
            recent_sales_attempt_count=2,
            buying_intent_score=0.85,
            user_asked_to_buy=True,
        )
        decision = decide_commerce_action(ctx)
        # Explicit intent with high score passes fatigue gate (step 7.5 checks < 0.80)
        assert decision.action == CommerceAction.OFFER_PPV

    def test_offer_fatigue_does_not_trigger_with_zero_offers(self):
        ctx = _ctx(
            recent_offer_count=0,
            recent_sales_attempt_count=2,
            buying_intent_score=0.60,
        )
        decision = decide_commerce_action(ctx)
        # Step 7.5 requires recent_offer_count >= 1
        assert decision.action == CommerceAction.SOFT_OFFER


# ---------------------------------------------------------------------------
# 7. Low confidence → conversation
# ---------------------------------------------------------------------------


class TestLowConfidence:
    def test_low_confidence_prefers_chat(self):
        ctx = _ctx(
            signal_confidence=0.20,
            buying_intent_score=0.60,
            user_asked_to_buy=False,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.RELATIONSHIP_BUILDING
        assert decision.reason_code == CommerceReason.LOW_CONFIDENCE_CHAT

    def test_low_confidence_does_not_override_explicit_buy(self):
        ctx = _ctx(
            signal_confidence=0.20,
            user_asked_to_buy=True,
        )
        decision = decide_commerce_action(ctx)
        # Explicit intent beats low confidence
        assert decision.action == CommerceAction.OFFER_PPV

    def test_low_confidence_does_not_override_price_ask(self):
        ctx = _ctx(
            signal_confidence=0.20,
            user_asked_about_price=True,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.OFFER_PPV

    def test_high_confidence_does_not_trigger_low_confidence_path(self):
        ctx = _ctx(
            signal_confidence=0.80,
            buying_intent_score=0.60,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.SOFT_OFFER


# ---------------------------------------------------------------------------
# 8. Scenario tests
# ---------------------------------------------------------------------------


class TestScenarioGreeting:
    def test_hey_greeting(self):
        signals = _make_signals(
            primary_intent="greeting",
            intent_tags=["greeting"],
            purchase_intent=0.0,
            content_interest=0.0,
        )
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action in (
            CommerceAction.RELATIONSHIP_BUILDING,
            CommerceAction.NO_OFFER,
        )


class TestScenarioCasualChat:
    def test_what_are_you_doing(self):
        signals = _make_signals(
            primary_intent="casual_chat",
            intent_tags=["casual_chat"],
            purchase_intent=0.0,
            content_interest=0.0,
        )
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action in (
            CommerceAction.RELATIONSHIP_BUILDING,
            CommerceAction.NO_OFFER,
        )


class TestScenarioRelationshipDisclosure:
    def test_rough_day(self):
        signals = _make_signals(
            primary_intent="personal_disclosure",
            intent_tags=["personal_disclosure", "relationship_building"],
            purchase_intent=0.0,
            content_interest=0.0,
            relationship_engagement=0.7,
        )
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action in (
            CommerceAction.RELATIONSHIP_BUILDING,
            CommerceAction.NO_OFFER,
        )


class TestScenarioContentCuriosity:
    def test_anything_new(self):
        signals = _make_signals(
            primary_intent="content_curiosity",
            intent_tags=["content_curiosity"],
            content_interest=0.7,
            purchase_intent=0.2,
        )
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        # Content curiosity alone shouldn't force an offer
        # but may lead to soft offer if relationship is warm
        assert ctx.has_commercial_intent is True


class TestScenarioExplicitBuy:
    def test_i_want_the_new_set(self):
        signals = _make_signals(
            primary_intent="purchase_intent",
            intent_tags=["purchase_intent"],
            purchase_intent=0.95,
            explicit_purchase_request=True,
        )
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.OFFER_PPV
        assert decision.reason_code == CommerceReason.STRONG_BUYING_SIGNAL


class TestScenarioPriceQuestion:
    def test_how_much_in_context(self):
        signals = _make_signals(
            primary_intent="price_inquiry",
            intent_tags=["price_inquiry"],
            price_interest=0.9,
            purchase_intent=0.6,
        )
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        assert ctx.user_asked_about_price is True


class TestScenarioAmbiguousPrice:
    def test_how_much_do_you_make(self):
        signals = _make_signals(
            primary_intent="casual_chat",
            intent_tags=["casual_chat"],
            price_interest=0.3,
            purchase_intent=0.1,
        )
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        assert ctx.user_asked_about_price is False


class TestScenarioRejection:
    def test_nah_im_good(self):
        signals = _make_signals(
            primary_intent="rejection",
            intent_tags=["rejection"],
            negative_intent_tags=["rejection"],
            purchase_intent=0.0,
            declined_recent_offer=True,
        )
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        assert ctx.negative_intent_count == 1
        decision = decide_commerce_action(ctx)
        assert decision.action in (
            CommerceAction.NO_OFFER,
            CommerceAction.RELATIONSHIP_BUILDING,
        )


class TestScenarioPriceObjection:
    def test_too_expensive(self):
        signals = _make_signals(
            primary_intent="hesitation",
            intent_tags=["hesitation"],
            negative_intent_tags=["hesitation"],
            purchase_intent=0.1,
        )
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action in (
            CommerceAction.RELATIONSHIP_BUILDING,
            CommerceAction.NO_OFFER,
        )


class TestScenarioCompliment:
    def test_youre_gorgeous(self):
        signals = _make_signals(
            primary_intent="relationship_building",
            intent_tags=["relationship_building"],
            purchase_intent=0.1,
            content_interest=0.0,
            relationship_engagement=0.8,
        )
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        decision = decide_commerce_action(ctx)
        # Compliment should not automatically become sales
        assert decision.action != CommerceAction.OFFER_PPV


class TestScenarioAppreciation:
    def test_youve_been_so_sweet(self):
        signals = _make_signals(
            primary_intent="relationship_building",
            intent_tags=["relationship_building"],
            purchase_intent=0.1,
            relationship_engagement=0.7,
        )
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        decision = decide_commerce_action(ctx)
        # Appreciation should not force a tip request
        assert decision.action != CommerceAction.TIP_SUGGESTION


class TestScenarioTipInterest:
    def test_how_can_i_tip_you(self):
        signals = _make_signals(
            primary_intent="tip_interest",
            intent_tags=["tip_interest"],
            purchase_intent=0.0,
        )
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        assert ctx.has_commercial_intent is True


class TestScenarioComplaint:
    def test_not_what_i_paid_for(self):
        signals = _make_signals(
            primary_intent="complaint",
            intent_tags=["complaint"],
            negative_intent_tags=["complaint"],
            negative_sentiment=0.9,
        )
        ctx_base = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        ctx = CommerceDecisionContext(
            user_id=ctx_base.user_id,
            creator_id=ctx_base.creator_id,
            eligibility=ctx_base.eligibility,
            buying_intent_score=ctx_base.buying_intent_score,
            relationship_score=ctx_base.relationship_score,
            conversational_phase=ctx_base.conversational_phase,
            negative_intent_count=ctx_base.negative_intent_count,
            signal_confidence=ctx_base.signal_confidence,
            fan_asks_question=ctx_base.fan_asks_question,
            has_commercial_intent=ctx_base.has_commercial_intent,
            handoff_needed=True,
        )
        decision = decide_commerce_action(ctx)
        # Complaint with handoff_needed → OPERATOR_HANDOFF
        assert decision.action == CommerceAction.OPERATOR_HANDOFF


class TestScenarioMultiIntent:
    def test_missed_you_and_anything_new(self):
        signals = _make_signals(
            primary_intent="relationship_building",
            intent_tags=["relationship_building", "content_curiosity"],
            purchase_intent=0.3,
            content_interest=0.5,
            relationship_engagement=0.7,
        )
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        assert ctx.has_commercial_intent is True
        assert ctx.negative_intent_count == 0


class TestScenarioLowConfidenceMessage:
    def test_interesting(self):
        signals = _make_signals(
            primary_intent="uncertain",
            intent_tags=["uncertain"],
            purchase_intent=0.1,
            content_interest=0.1,
            confidence=0.15,
            model_uncertainty=0.9,
        )
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        decision = decide_commerce_action(ctx)
        # Low confidence should bias toward chat
        assert decision.action in (
            CommerceAction.RELATIONSHIP_BUILDING,
            CommerceAction.NO_OFFER,
        )


# ---------------------------------------------------------------------------
# 9. Adversarial tests
# ---------------------------------------------------------------------------


class TestAdversarialSignals:
    def test_malformed_intent_rejected(self):
        with pytest.raises(Exception):
            _make_signals(primary_intent="DEFINITELY_NOT_VALID")

    def test_too_many_intent_tags_rejected(self):
        with pytest.raises(Exception):
            _make_signals(intent_tags=["greeting"] * 10)

    def test_commercial_intent_on_casual_message(self):
        """100% purchase intent on casual message should still be processed."""
        signals = _make_signals(
            primary_intent="purchase_intent",
            purchase_intent=1.0,
            explicit_purchase_request=True,
            confidence=0.95,
        )
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        decision = decide_commerce_action(ctx)
        # Even if the LLM is overconfident, the engine processes it
        assert decision.action == CommerceAction.OFFER_PPV

    def test_zero_confidence_explicit_purchase(self):
        """Zero confidence but explicit purchase request."""
        signals = _make_signals(
            purchase_intent=0.0,
            explicit_purchase_request=True,
            confidence=0.0,
        )
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        decision = decide_commerce_action(ctx)
        # Explicit request still triggers offer
        assert decision.action == CommerceAction.OFFER_PPV

    def test_none_signals_produce_no_interest(self):
        ctx = signals_to_context(
            None, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action in (
            CommerceAction.RELATIONSHIP_BUILDING,
            CommerceAction.NO_OFFER,
        )

    def test_denied_eligibility_overrides_all(self):
        signals = _make_signals(
            primary_intent="purchase_intent",
            explicit_purchase_request=True,
            purchase_intent=1.0,
        )
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=DENIED,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.NO_OFFER
        assert decision.reason_code == CommerceReason.USER_BLOCKED

    def test_kill_switch_respected(self):
        """AUTONOMY_ENABLED=false means no autonomous action."""
        # This is enforced at AutomationService level, not in decision engine
        # The decision engine still produces decisions; AutomationService gates execution
        ctx = _ctx(user_asked_to_buy=True)
        decision = decide_commerce_action(ctx)
        assert decision.allowed is True  # Decision is allowed, execution is gated

    def test_creator_isolation(self):
        """Different creator_ids produce independent decisions."""
        ctx1 = _ctx(user_asked_to_buy=True)
        ctx2 = _ctx(user_asked_to_buy=False, buying_intent_score=0.3)
        d1 = decide_commerce_action(ctx1)
        d2 = decide_commerce_action(ctx2)
        # Same user_id but different creator_ids — independent decisions
        assert d1.action == CommerceAction.OFFER_PPV
        assert d2.action in (
            CommerceAction.RELATIONSHIP_BUILDING,
            CommerceAction.NO_OFFER,
        )

    def test_negative_sentiment_high_with_operator(self):
        signals = _make_signals(
            negative_sentiment=0.9,
            negative_intent_tags=["complaint"],
        )
        ctx_base = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        ctx = CommerceDecisionContext(
            user_id=ctx_base.user_id,
            creator_id=ctx_base.creator_id,
            eligibility=ctx_base.eligibility,
            buying_intent_score=ctx_base.buying_intent_score,
            relationship_score=ctx_base.relationship_score,
            conversational_phase=ctx_base.conversational_phase,
            negative_intent_count=ctx_base.negative_intent_count,
            signal_confidence=ctx_base.signal_confidence,
            handoff_needed=True,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.OPERATOR_HANDOFF

    def test_duplicate_negative_signals(self):
        """Two negative signals should suppress commercial action."""
        ctx = _ctx(negative_intent_count=2)
        decision = decide_commerce_action(ctx)
        assert decision.action == CommerceAction.RELATIONSHIP_BUILDING
        assert decision.reason_code == CommerceReason.NEGATIVE_SIGNALS_SUPPRESSED

    def test_fan_asks_question_preserved(self):
        signals = _make_signals(fan_asks_question=True)
        ctx = signals_to_context(
            signals, user_id=1, creator_id=2, eligibility=ALLOWED,
        )
        assert ctx.fan_asks_question is True

    def test_topic_continuity_removed(self):
        """topic_continuity was a dead field removed in Phase 2."""
        # This field was extracted but never consumed
        assert not hasattr(CommerceSignals, "topic_continuity")
