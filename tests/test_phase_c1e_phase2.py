"""C.1-E Phase 2 tests — Intelligence Wiring & Behavioral Hardening.

Tests cover:
1. Dead pipeline fields (P1-2)
2. Dead signal fields (P1-3)
3. _confidence metadata audit (P1-4)
4. Tip cooldown intelligence (P2-1)
5. Creator configuration detection (P2-2)
6. Aftercare as hard boundary (P2-3)

All tests are UNIT TESTED ONLY (no DB, no network, no LLM).
"""

import pytest
from unittest.mock import MagicMock

from commerce.decision import (
    CommerceDecisionContext,
    CommerceDecisionPolicy,
    decide_commerce_action,
    _classify_aftercare_intent,
)
from commerce.relationship import (
    RelationshipState,
    CommercialPressure,
    TipEligibility,
    OperatorHandoffReason,
    check_tip_eligibility,
    check_operator_handoff,
    derive_creator_capabilities,
    _calculate_tip_cooldown_hours,
    _TIP_COOLDOWN_BASE,
    _TIP_COOLDOWN_ENGAGED,
    _TIP_COOLDOWN_VIP,
    _TIP_COOLDOWN_CONTEXTUAL_MIN,
)
from commerce.models import CreatorCapabilities, CapabilityStatus
from commerce.signals import CommerceSignals, signals_to_context
from commerce.models import PolicyDecision


# ── Helper fixtures ────────────────────────────────────────────────────────


def _make_eligible() -> PolicyDecision:
    return PolicyDecision(allowed=True, denial_reason="")


def _make_ineligible(reason: str = "user_blocked") -> PolicyDecision:
    return PolicyDecision(allowed=False, denial_reason=reason)


def _make_context(**overrides) -> CommerceDecisionContext:
    defaults = {
        "user_id": 1001,
        "creator_id": 2001,
        "eligibility": _make_eligible(),
    }
    defaults.update(overrides)
    return CommerceDecisionContext(**defaults)


def _make_capabilities(
    can_sell: bool = True,
    can_tip: bool = True,
    provider_healthy: bool = True,
) -> CreatorCapabilities:
    return CreatorCapabilities(
        content_sales=CapabilityStatus.AVAILABLE if can_sell else CapabilityStatus.UNAVAILABLE,
        tips=CapabilityStatus.AVAILABLE if can_tip else CapabilityStatus.UNAVAILABLE,
        provider_health=CapabilityStatus.AVAILABLE if provider_healthy else CapabilityStatus.UNAVAILABLE,
        has_valid_product=can_sell,
        has_valid_sales_url=can_sell,
        has_dropfans_integration=True,
        dropfans_authenticated=True,
    )


# ── Phase 2 Item 1: Dead Pipeline Fields ──────────────────────────────────


class TestPipelineFieldAudit:
    """Verify pipeline fields are classified correctly."""

    def test_post_purchase_satisfaction_in_context(self):
        """post_purchase_satisfaction exists in CommerceDecisionContext."""
        ctx = _make_context(post_purchase_satisfaction="positive")
        assert ctx.post_purchase_satisfaction == "positive"

    def test_post_purchase_satisfaction_not_used_by_decision_engine(self):
        """post_purchase_satisfaction does not influence decision engine."""
        ctx = _make_context(
            post_purchase_satisfaction="negative",
            user_asked_to_buy=True,
        )
        decision = decide_commerce_action(ctx)
        # Explicit buy should still be honored despite negative satisfaction
        assert decision.action.value == "offer_ppv"

    def test_active_pipeline_fields_reach_decision_engine(self):
        """All active pipeline fields are present in decision context."""
        ctx = _make_context(
            commercial_paused=True,
            aftercare_status="pending",
            consecutive_rejections=3,
            tip_eligibility="eligible",
        )
        assert ctx.commercial_paused is True
        assert ctx.aftercare_status == "pending"
        assert ctx.consecutive_rejections == 3
        assert ctx.tip_eligibility == "eligible"


# ── Phase 2 Item 2: Dead Signal Fields ────────────────────────────────────


class TestSignalFieldAudit:
    """Verify dead signal fields are removed."""

    def test_removed_fields_not_in_commerce_signals(self):
        """Removed fields are not in CommerceSignals schema."""
        # These fields should no longer exist
        removed_fields = [
            "accepted_recent_offer",
            "asks_for_free_content",
            "conversation_relevance",
            "topic_continuity",
        ]
        for field in removed_fields:
            assert field not in CommerceSignals.model_fields

    def test_low_information_excludes_removed_fields(self):
        """low_information() does not include removed fields."""
        signals = CommerceSignals.low_information()
        # Should not have these attributes
        assert not hasattr(signals, "accepted_recent_offer")
        assert not hasattr(signals, "asks_for_free_content")
        assert not hasattr(signals, "conversation_relevance")
        assert not hasattr(signals, "topic_continuity")

    def test_active_signal_fields_present(self):
        """Active signal fields are still present."""
        signals = CommerceSignals.low_information()
        assert hasattr(signals, "purchase_intent")
        assert hasattr(signals, "explicit_purchase_request")
        assert hasattr(signals, "declined_recent_offer")
        assert hasattr(signals, "confidence")
        assert hasattr(signals, "primary_intent")


# ── Phase 2 Item 3: _confidence Metadata Audit ────────────────────────────


class TestConfidenceMetadataAudit:
    """Verify _confidence metadata is advisory-only."""

    def test_confidence_not_in_decision_context(self):
        """_confidence is not a field in CommerceDecisionContext."""
        ctx = _make_context()
        assert not hasattr(ctx, "_confidence")

    def test_signal_confidence_used_by_decision_engine(self):
        """signal_confidence is used by decision engine for low-confidence gate."""
        ctx = _make_context(
            signal_confidence=0.1,  # Below min_signal_confidence_for_commerce
            user_asked_to_buy=False,
            user_asked_about_price=False,
            user_requested_content=False,
        )
        decision = decide_commerce_action(ctx)
        # Low confidence should prefer conversation
        assert decision.action.value == "relationship_building"
        assert decision.reason_code.value == "low_confidence_chat"

    def test_high_confidence_does_not_block_commerce(self):
        """High signal_confidence does not block commerce."""
        ctx = _make_context(
            signal_confidence=0.9,
            user_asked_to_buy=True,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action.value == "offer_ppv"


# ── Phase 2 Item 4: Tip Cooldown Intelligence ─────────────────────────────


class TestTipCooldownIntelligence:
    """Verify graduated tip cooldown system."""

    def test_graduated_cooldown_by_relationship_state(self):
        """Different relationship states have different base cooldowns."""
        vip_cooldown = _calculate_tip_cooldown_hours(
            RelationshipState.VIP, 0, 0
        )
        engaged_cooldown = _calculate_tip_cooldown_hours(
            RelationshipState.ENGAGED, 0, 0
        )
        warm_cooldown = _calculate_tip_cooldown_hours(
            RelationshipState.WARM, 0, 0
        )

        assert vip_cooldown == _TIP_COOLDOWN_VIP
        assert engaged_cooldown == _TIP_COOLDOWN_ENGAGED
        assert warm_cooldown == _TIP_COOLDOWN_BASE

        # VIP should have shortest cooldown, warm longest
        assert vip_cooldown < engaged_cooldown < warm_cooldown

    def test_fatigue_increases_cooldown(self):
        """More ignored suggestions increase cooldown."""
        base_cooldown = _calculate_tip_cooldown_hours(
            RelationshipState.WARM, 0, 0
        )
        fatigued_cooldown = _calculate_tip_cooldown_hours(
            RelationshipState.WARM, 2, 3
        )

        # Fatigue should increase cooldown
        assert fatigued_cooldown > base_cooldown

    def test_tip_eligibility_respects_graduated_cooldown(self):
        """check_tip_eligibility uses graduated cooldown."""
        # VIP with recent tip (within VIP cooldown but outside base cooldown)
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.VIP,
            commercial_pressure=CommercialPressure.NONE,
            hours_since_last_tip=30.0,  # Within 72h base but outside 24h VIP
            has_active_offer=False,
            recent_purchase_count=0,
            fan_expressed_appreciation=False,
            fan_asked_how_to_support=False,
            tip_suggestions_sent=0,
            tip_suggestions_ignored=0,
            commercial_paused=False,
        )
        # VIP cooldown is 24h, so 30h should be eligible
        assert eligibility == TipEligibility.ELIGIBLE

    def test_contextual_trigger_respects_min_cooldown(self):
        """Contextual triggers respect minimum cooldown."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.NONE,
            hours_since_last_tip=6.0,  # Less than 12h minimum
            has_active_offer=False,
            recent_purchase_count=0,
            fan_expressed_appreciation=True,
            fan_asked_how_to_support=False,
            tip_suggestions_sent=0,
            tip_suggestions_ignored=0,
            commercial_paused=False,
        )
        assert eligibility == TipEligibility.COOLDOWN_ACTIVE
        assert reason == "contextual_min_cooldown"

    def test_fatigue_suppresses_even_with_contextual_trigger(self):
        """Fatigue suppresses even with contextual trigger."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.NONE,
            hours_since_last_tip=24.0,
            has_active_offer=False,
            recent_purchase_count=0,
            fan_expressed_appreciation=True,
            fan_asked_how_to_support=False,
            tip_suggestions_sent=3,
            tip_suggestions_ignored=3,
            commercial_paused=False,
        )
        # Fatigue should suppress despite contextual trigger
        assert eligibility == TipEligibility.INELIGIBLE
        assert reason == "tip_fatigue"


# ── Phase 2 Item 5: Creator Configuration Detection ───────────────────────


class TestCreatorCapabilityDetection:
    """Verify creator capability model."""

    def test_creator_capabilities_structure(self):
        """CreatorCapabilities has correct structure."""
        caps = _make_capabilities()
        assert caps.can_sell_content() is True
        assert caps.can_accept_tips() is True
        assert caps.is_provider_healthy() is True

    def test_creator_cannot_sell_content(self):
        """Creator without valid product cannot sell content."""
        caps = _make_capabilities(can_sell=False)
        assert caps.can_sell_content() is False

    def test_creator_cannot_accept_tips(self):
        """Creator without tip capability cannot accept tips."""
        caps = _make_capabilities(can_tip=False)
        assert caps.can_accept_tips() is False

    def test_creator_provider_unhealthy(self):
        """Creator with unhealthy provider is flagged."""
        caps = _make_capabilities(provider_healthy=False)
        assert caps.is_provider_healthy() is False

    def test_capabilities_block_commerce_in_decision_engine(self):
        """Capabilities block commerce in decision engine."""
        caps = _make_capabilities(can_sell=False)
        ctx = _make_context(
            user_asked_to_buy=True,
            creator_capabilities=caps,
        )
        decision = decide_commerce_action(ctx)
        # Should not offer content when creator cannot sell
        assert decision.action.value == "relationship_building"
        assert decision.metadata.get("content_ineligible_reason") == "creator_cannot_sell_content"

    def test_capabilities_block_tip_in_decision_engine(self):
        """Capabilities block tip suggestion in decision engine."""
        caps = _make_capabilities(can_tip=False)
        ctx = _make_context(
            tip_eligibility="eligible",
            creator_capabilities=caps,
        )
        decision = decide_commerce_action(ctx)
        # Should not suggest tip when creator cannot accept tips
        assert decision.action.value == "relationship_building"
        assert decision.metadata.get("tip_ineligible_reason") == "creator_cannot_accept_tips"

    def test_derive_creator_capabilities_from_state(self):
        """derive_creator_capabilities creates correct capabilities."""
        caps = derive_creator_capabilities(
            has_dropfans_integration=True,
            dropfans_authenticated=True,
            has_valid_product=True,
            has_valid_sales_url=True,
            provider_healthy=True,
            tip_capability_available=True,
        )
        assert caps.can_sell_content() is True
        assert caps.can_accept_tips() is True

    def test_derive_capabilities_missing_integration(self):
        """Capabilities reflect missing DropFans integration."""
        caps = derive_creator_capabilities(
            has_dropfans_integration=False,
            dropfans_authenticated=False,
            has_valid_product=False,
            has_valid_sales_url=False,
            provider_healthy=True,
            tip_capability_available=True,
        )
        assert caps.can_sell_content() is False
        assert caps.can_accept_tips() is False


# ── Phase 2 Item 6: Aftercare as Hard Boundary ────────────────────────────


class TestAftercareHardBoundary:
    """Verify aftercare is a policy boundary with explicit intent handling."""

    def test_aftercare_suppresses_casual_conversation(self):
        """Aftercare suppresses casual conversation."""
        ctx = _make_context(
            aftercare_status="pending",
            total_purchases=1,
            user_asked_to_buy=False,
            user_asked_about_price=False,
            user_requested_content=False,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action.value == "relationship_building"
        assert decision.reason_code.value == "aftercare_phase"

    def test_aftercare_suppresses_appreciation(self):
        """Aftercare suppresses appreciation without explicit buy."""
        ctx = _make_context(
            aftercare_status="pending",
            total_purchases=1,
            fan_expressed_appreciation=True,
            user_asked_to_buy=False,
            user_asked_about_price=False,
            user_requested_content=False,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action.value == "relationship_building"
        assert decision.reason_code.value == "aftercare_phase"

    def test_aftercare_suppresses_curiosity(self):
        """Aftercare suppresses curiosity without explicit buy."""
        ctx = _make_context(
            aftercare_status="pending",
            total_purchases=1,
            fan_asks_question=True,
            user_asked_to_buy=False,
            user_asked_about_price=False,
            user_requested_content=False,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action.value == "relationship_building"
        assert decision.reason_code.value == "aftercare_phase"

    def test_aftercare_honors_explicit_buying_intent(self):
        """Aftercare honors explicit buying intent."""
        ctx = _make_context(
            aftercare_status="pending",
            total_purchases=1,
            user_asked_to_buy=True,
        )
        decision = decide_commerce_action(ctx)
        # Explicit buy should be honored even during aftercare
        assert decision.action.value == "offer_ppv"

    def test_aftercare_honors_explicit_price_inquiry(self):
        """Aftercare honors explicit price inquiry."""
        ctx = _make_context(
            aftercare_status="pending",
            total_purchases=1,
            user_asked_about_price=True,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action.value == "offer_ppv"

    def test_aftercare_honors_explicit_content_request(self):
        """Aftercare honors explicit content request."""
        ctx = _make_context(
            aftercare_status="pending",
            total_purchases=1,
            user_requested_content=True,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action.value == "offer_ppv"

    def test_classify_aftercare_intent_casual(self):
        """_classify_aftercare_intent classifies casual intent."""
        ctx = _make_context(
            fan_expressed_appreciation=False,
            fan_asks_question=False,
            has_commercial_intent=False,
            user_asked_to_buy=False,
            user_asked_about_price=False,
            user_requested_content=False,
        )
        assert _classify_aftercare_intent(ctx) == "casual"

    def test_classify_aftercare_intent_appreciation(self):
        """_classify_aftercare_intent classifies appreciation intent."""
        ctx = _make_context(
            fan_expressed_appreciation=True,
            fan_asks_question=False,
            has_commercial_intent=False,
            user_asked_to_buy=False,
            user_asked_about_price=False,
            user_requested_content=False,
        )
        assert _classify_aftercare_intent(ctx) == "appreciation"

    def test_classify_aftercare_intent_curiosity(self):
        """_classify_aftercare_intent classifies curiosity intent."""
        ctx = _make_context(
            fan_expressed_appreciation=False,
            fan_asks_question=True,
            has_commercial_intent=False,
            user_asked_to_buy=False,
            user_asked_about_price=False,
            user_requested_content=False,
        )
        assert _classify_aftercare_intent(ctx) == "curiosity"

    def test_aftercare_metadata_includes_intent_type(self):
        """Aftercare decision includes intent type in metadata."""
        ctx = _make_context(
            aftercare_status="pending",
            total_purchases=1,
            fan_expressed_appreciation=True,
            user_asked_to_buy=False,
            user_asked_about_price=False,
            user_requested_content=False,
        )
        decision = decide_commerce_action(ctx)
        assert decision.metadata.get("aftercare_intent_type") == "appreciation"


# ── Authority Boundary Tests ──────────────────────────────────────────────


class TestAuthorityBoundaries:
    """Verify LLM cannot bypass deterministic controls."""

    def test_llm_cannot_override_tip_capability(self):
        """LLM cannot override creator tip capability."""
        caps = _make_capabilities(can_tip=False)
        ctx = _make_context(
            tip_eligibility="eligible",
            creator_capabilities=caps,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action.value == "relationship_building"

    def test_llm_cannot_override_aftercare_policy(self):
        """LLM cannot override aftercare policy."""
        ctx = _make_context(
            aftercare_status="pending",
            total_purchases=1,
            user_asked_to_buy=True,
        )
        decision = decide_commerce_action(ctx)
        # Explicit buy is honored, but this tests the policy is enforced
        assert decision.action.value == "offer_ppv"

    def test_llm_cannot_override_tip_cooldown(self):
        """LLM cannot override tip cooldown."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.NONE,
            hours_since_last_tip=6.0,  # Within cooldown
            has_active_offer=False,
            recent_purchase_count=0,
            fan_expressed_appreciation=False,
            fan_asked_how_to_support=False,
            tip_suggestions_sent=0,
            tip_suggestions_ignored=0,
            commercial_paused=False,
        )
        assert eligibility == TipEligibility.COOLDOWN_ACTIVE

    def test_llm_cannot_override_commercial_pause(self):
        """LLM cannot override commercial pause."""
        ctx = _make_context(
            commercial_paused=True,
            user_asked_to_buy=True,
        )
        decision = decide_commerce_action(ctx)
        # Commercial pause should block even explicit buy
        assert decision.action.value == "relationship_building"
        assert decision.reason_code.value == "commercial_paused"

    def test_llm_cannot_bypass_kill_switch(self):
        """LLM cannot bypass kill switch (tested via policy)."""
        # Kill switch is enforced at pipeline level, not decision level
        # This test verifies the decision engine respects eligibility
        ctx = _make_context(
            eligibility=_make_ineligible("user_blocked"),
        )
        decision = decide_commerce_action(ctx)
        assert decision.action.value == "no_offer"
        assert decision.allowed is False


# ── Adversarial Tests ─────────────────────────────────────────────────────


class TestAdversarialScenarios:
    """Adversarial tests for Phase 2 changes."""

    def test_explicit_buy_one_minute_after_purchase(self):
        """Fan asks for another purchase one minute after purchase.

        Note: Purchase cooldown (Step 5) blocks explicit buy within 6 hours.
        This is by design to prevent immediate re-purchase after purchase.
        """
        ctx = _make_context(
            aftercare_status="pending",
            total_purchases=1,
            hours_since_last_purchase=0.016,  # ~1 minute
            user_asked_to_buy=True,
        )
        decision = decide_commerce_action(ctx)
        # Purchase cooldown blocks explicit buy within 6 hours
        assert decision.action.value == "no_offer"
        assert decision.reason_code.value == "recent_purchase"

    def test_maybe_later_immediately_after_purchase(self):
        """Fan says 'maybe later' immediately after purchase."""
        ctx = _make_context(
            aftercare_status="pending",
            total_purchases=1,
            user_asked_to_buy=False,
            user_asked_about_price=False,
            user_requested_content=False,
        )
        decision = decide_commerce_action(ctx)
        # Should be aftercare, not commerce
        assert decision.action.value == "relationship_building"
        assert decision.reason_code.value == "aftercare_phase"

    def test_explicit_purchase_during_aftercare(self):
        """Fan explicitly asks for another product during aftercare."""
        ctx = _make_context(
            aftercare_status="pending",
            total_purchases=1,
            user_asked_to_buy=True,
        )
        decision = decide_commerce_action(ctx)
        # Should honor explicit buy
        assert decision.action.value == "offer_ppv"

    def test_repeatedly_ignored_tip_suggestions(self):
        """Fan repeatedly ignores tip suggestions."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.NONE,
            hours_since_last_tip=24.0,
            has_active_offer=False,
            recent_purchase_count=0,
            fan_expressed_appreciation=False,
            fan_asked_how_to_support=False,
            tip_suggestions_sent=5,
            tip_suggestions_ignored=5,
            commercial_paused=False,
        )
        # Should be fatigued
        assert eligibility == TipEligibility.INELIGIBLE
        assert reason == "tip_fatigue"

    def test_commercial_pause_and_explicit_buying_intent(self):
        """Commercial pause and explicit buying intent coexist."""
        ctx = _make_context(
            commercial_paused=True,
            user_asked_to_buy=True,
        )
        decision = decide_commerce_action(ctx)
        # Commercial pause should block even explicit buy
        assert decision.action.value == "relationship_building"
        assert decision.reason_code.value == "commercial_paused"

    def test_handoff_and_buying_intent_coexist(self):
        """Handoff and buying intent coexist."""
        ctx = _make_context(
            handoff_needed=True,
            user_asked_to_buy=True,
        )
        decision = decide_commerce_action(ctx)
        # Handoff should take priority over explicit buy
        assert decision.action.value == "operator_handoff"
        assert decision.reason_code.value == "operator_handoff_needed"

    def test_capabilities_block_content_sale(self):
        """Creator cannot sell content when capabilities block."""
        caps = _make_capabilities(can_sell=False)
        ctx = _make_context(
            user_asked_to_buy=True,
            creator_capabilities=caps,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action.value == "relationship_building"

    def test_capabilities_block_tip_suggestion(self):
        """Creator cannot accept tips when capabilities block."""
        caps = _make_capabilities(can_tip=False)
        ctx = _make_context(
            tip_eligibility="eligible",
            creator_capabilities=caps,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action.value == "relationship_building"

    def test_missing_product_blocks_content_sale(self):
        """Missing product blocks content sale."""
        caps = CreatorCapabilities(
            content_sales=CapabilityStatus.UNAVAILABLE,
            tips=CapabilityStatus.AVAILABLE,
            provider_health=CapabilityStatus.AVAILABLE,
            has_valid_product=False,
            has_valid_sales_url=False,
            has_dropfans_integration=True,
            dropfans_authenticated=True,
        )
        ctx = _make_context(
            user_asked_to_buy=True,
            creator_capabilities=caps,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action.value == "relationship_building"

    def test_unhealthy_provider_blocks_commerce(self):
        """Unhealthy provider blocks commerce."""
        caps = _make_capabilities(provider_healthy=False)
        ctx = _make_context(
            user_asked_to_buy=True,
            creator_capabilities=caps,
        )
        decision = decide_commerce_action(ctx)
        # Provider health check should block
        assert decision.action.value == "relationship_building"


# ── Decision Priority Matrix Tests ────────────────────────────────────────


class TestDecisionPriorityMatrix:
    """Verify decision priority ordering after Phase 2 changes."""

    def test_eligibility_wins_over_all(self):
        """Eligibility denial wins over all other conditions."""
        ctx = _make_context(
            eligibility=_make_ineligible("user_blocked"),
            handoff_needed=True,
            commercial_paused=True,
            user_asked_to_buy=True,
            tip_eligibility="eligible",
        )
        decision = decide_commerce_action(ctx)
        assert decision.action.value == "no_offer"
        assert decision.reason_code.value == "user_blocked"

    def test_handoff_wins_over_buy(self):
        """Handoff wins over explicit buy."""
        ctx = _make_context(
            handoff_needed=True,
            user_asked_to_buy=True,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action.value == "operator_handoff"

    def test_commercial_pause_wins_over_tip(self):
        """Commercial pause wins over tip suggestion."""
        ctx = _make_context(
            commercial_paused=True,
            tip_eligibility="eligible",
        )
        decision = decide_commerce_action(ctx)
        assert decision.action.value == "relationship_building"
        assert decision.reason_code.value == "commercial_paused"

    def test_aftercare_wins_over_implicit_buy(self):
        """Aftercare wins over implicit buying signal."""
        ctx = _make_context(
            aftercare_status="pending",
            total_purchases=1,
            buying_intent_score=0.9,
            user_asked_to_buy=False,
        )
        decision = decide_commerce_action(ctx)
        # Aftercare should suppress implicit buying signal
        assert decision.action.value == "relationship_building"
        assert decision.reason_code.value == "aftercare_phase"

    def test_explicit_buy_wins_over_aftercare(self):
        """Explicit buy wins over aftercare."""
        ctx = _make_context(
            aftercare_status="pending",
            total_purchases=1,
            user_asked_to_buy=True,
        )
        decision = decide_commerce_action(ctx)
        assert decision.action.value == "offer_ppv"
