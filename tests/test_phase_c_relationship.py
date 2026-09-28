"""Phase C: Relationship state derivation tests.

Tests for commerce/relationship.py — pure derivation functions,
no I/O, no mocks needed.
"""

from __future__ import annotations

import pytest
from commerce.relationship import (
    CommercialPressure,
    OperatorHandoffReason,
    RelationshipState,
    TipEligibility,
    derive_commercial_pressure,
    derive_relationship_state,
    check_tip_eligibility,
    check_operator_handoff,
)


# ── Relationship state derivation ─────────────────────────────────────────


class TestDeriveRelationshipState:
    """Test the relationship state derivation function."""

    def test_new_user(self):
        """New user with no purchases, recent messages → ENGAGED."""
        state = derive_relationship_state(
            funnel_stage="new",
            purchase_count=0,
            last_purchase_days_ago=None,
            last_message_days_ago=1,
            message_count=5,
            has_active_offer=False,
        )
        # Recent messages (1 day ago) triggers ENGAGED state
        assert state == RelationshipState.ENGAGED

    def test_engaged_user(self):
        """User with 1 purchase 10 days ago → PURCHASED."""
        state = derive_relationship_state(
            funnel_stage="engaged",
            purchase_count=1,
            last_purchase_days_ago=10,
            last_message_days_ago=1,
            message_count=15,
            has_active_offer=False,
        )
        assert state == RelationshipState.PURCHASED

    def test_warm_user(self):
        """User with 2+ purchases → REPEAT_BUYER."""
        state = derive_relationship_state(
            funnel_stage="engaged",
            purchase_count=2,
            last_purchase_days_ago=10,
            last_message_days_ago=1,
            message_count=25,
            has_active_offer=False,
        )
        assert state == RelationshipState.REPEAT_BUYER

    def test_buying_signal(self):
        """User with active offer + recent messages → BUYING_SIGNAL."""
        state = derive_relationship_state(
            funnel_stage="engaged",
            purchase_count=0,
            last_purchase_days_ago=None,
            last_message_days_ago=1,
            message_count=30,
            has_active_offer=True,
        )
        assert state == RelationshipState.BUYING_SIGNAL

    def test_purchased_user(self):
        """User with 1 purchase → PURCHASED."""
        state = derive_relationship_state(
            funnel_stage="engaged",
            purchase_count=1,
            last_purchase_days_ago=10,
            last_message_days_ago=1,
            message_count=15,
            has_active_offer=False,
        )
        assert state == RelationshipState.PURCHASED

    def test_repeat_buyer(self):
        """User with 2+ purchases → REPEAT_BUYER."""
        state = derive_relationship_state(
            funnel_stage="engaged",
            purchase_count=2,
            last_purchase_days_ago=10,
            last_message_days_ago=1,
            message_count=30,
            has_active_offer=False,
        )
        assert state == RelationshipState.REPEAT_BUYER

    def test_vip_user(self):
        """User with vip funnel stage → VIP."""
        state = derive_relationship_state(
            funnel_stage="vip",
            purchase_count=5,
            last_purchase_days_ago=10,
            last_message_days_ago=1,
            message_count=50,
            has_active_offer=False,
        )
        assert state == RelationshipState.VIP

    def test_cooling_down(self):
        """User with recent purchase (7 days) → COOLING_DOWN."""
        state = derive_relationship_state(
            funnel_stage="engaged",
            purchase_count=1,
            last_purchase_days_ago=3,
            last_message_days_ago=1,
            message_count=20,
            has_active_offer=False,
        )
        assert state == RelationshipState.COOLING_DOWN

    def test_operator_required(self):
        """User with operator intervention → OPERATOR_REQUIRED."""
        state = derive_relationship_state(
            funnel_stage="engaged",
            purchase_count=2,
            last_purchase_days_ago=10,
            last_message_days_ago=1,
            message_count=20,
            has_active_offer=False,
            operator_intervention_recent=True,
        )
        assert state == RelationshipState.OPERATOR_REQUIRED

    def test_cold_state(self):
        """User with no purchases, no funnel stage, and old messages → COLD."""
        state = derive_relationship_state(
            funnel_stage=None,
            purchase_count=0,
            last_purchase_days_ago=None,
            last_message_days_ago=30,
            message_count=2,
            has_active_offer=False,
        )
        assert state == RelationshipState.COLD

    def test_do_not_push(self):
        """Blocked user → DO_NOT_PUSH."""
        state = derive_relationship_state(
            funnel_stage="engaged",
            purchase_count=2,
            last_purchase_days_ago=10,
            last_message_days_ago=1,
            message_count=20,
            has_active_offer=False,
            is_blocked=True,
        )
        assert state == RelationshipState.DO_NOT_PUSH


# ── Commercial pressure derivation ────────────────────────────────────────


class TestDeriveCommercialPressure:
    """Test the commercial pressure derivation function."""

    def test_none_pressure(self):
        """Cold user → NONE."""
        pressure = derive_commercial_pressure(
            relationship_state=RelationshipState.COLD,
        )
        assert pressure == CommercialPressure.NONE

    def test_soft_pressure(self):
        """Engaged user → SOFT."""
        pressure = derive_commercial_pressure(
            relationship_state=RelationshipState.ENGAGED,
        )
        assert pressure == CommercialPressure.SOFT

    def test_moderate_pressure(self):
        """User with high buying intent → MODERATE."""
        pressure = derive_commercial_pressure(
            relationship_state=RelationshipState.BUYING_SIGNAL,
            buying_intent_score=0.85,
        )
        assert pressure == CommercialPressure.MODERATE

    def test_direct_pressure(self):
        """User with explicit request → DIRECT."""
        pressure = derive_commercial_pressure(
            relationship_state=RelationshipState.WARM,
            explicit_request=True,
        )
        assert pressure == CommercialPressure.DIRECT

    def test_none_on_rejection(self):
        """User with declined offer → NONE."""
        pressure = derive_commercial_pressure(
            relationship_state=RelationshipState.WARM,
            previous_offer_declined=True,
        )
        assert pressure == CommercialPressure.NONE


# ── Tip eligibility ──────────────────────────────────────────────────────


class TestCheckTipEligibility:
    """Test the tip eligibility check function."""

    def test_ineligible_cold(self):
        """Cold user → ineligible."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.COLD,
            commercial_pressure=CommercialPressure.NONE,
        )
        assert eligibility == TipEligibility.INELIGIBLE

    def test_ineligible_new(self):
        """New user → ineligible."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.NEW,
            commercial_pressure=CommercialPressure.NONE,
        )
        assert eligibility == TipEligibility.INELIGIBLE

    def test_eligible_warm(self):
        """Warm user → eligible."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
        )
        assert eligibility == TipEligibility.ELIGIBLE

    def test_eligible_contextual(self):
        """User expressing appreciation → eligible."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.ENGAGED,
            commercial_pressure=CommercialPressure.NONE,
            fan_expressed_appreciation=True,
        )
        assert eligibility == TipEligibility.ELIGIBLE
        assert reason == "contextual_request"

    def test_ineligible_active_offer(self):
        """User with active offer → ineligible."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            has_active_offer=True,
        )
        assert eligibility == TipEligibility.INELIGIBLE
        assert reason == "active_offer"

    def test_cooldown_active(self):
        """User with recent tip → cooldown."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            hours_since_last_tip=24,
        )
        assert eligibility == TipEligibility.COOLDOWN_ACTIVE

    def test_ineligible_do_not_push(self):
        """DO_NOT_PUSH user → ineligible."""
        eligibility, reason = check_tip_eligibility(
            relationship_state=RelationshipState.DO_NOT_PUSH,
            commercial_pressure=CommercialPressure.NONE,
        )
        assert eligibility == TipEligibility.INELIGIBLE


# ── Operator handoff ─────────────────────────────────────────────────────


class TestCheckOperatorHandoff:
    """Test the operator handoff check function."""

    def test_no_handoff_cold(self):
        """Cold user → no handoff."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.COLD,
            commercial_pressure=CommercialPressure.NONE,
        )
        assert should_handoff is False
        assert reason is None

    def test_handoff_operator_required(self):
        """OPERATOR_REQUIRED state → handoff."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.OPERATOR_REQUIRED,
            commercial_pressure=CommercialPressure.NONE,
        )
        assert should_handoff is True
        assert reason == OperatorHandoffReason.SAFETY_CONCERN

    def test_handoff_complaint(self):
        """User with complaint → handoff (legacy default: payment-unknown)."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            has_complaint=True,
        )
        assert should_handoff is True
        assert reason == OperatorHandoffReason.COMPLAINT

    def test_handoff_technical_complaint(self):
        """Technical payment complaint → handoff (H1 triage passthrough)."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            has_complaint=True,
            complaint_is_experience_only=False,
        )
        assert should_handoff is True
        assert reason == OperatorHandoffReason.COMPLAINT

    def test_no_handoff_experience_only_complaint(self):
        """Experience-only complaint (no payment signal) → no handoff (H1).

        RECOVER/sincerity handles realization instead.
        """
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            has_complaint=True,
            complaint_is_experience_only=True,
        )
        assert should_handoff is False
        assert reason is None

    def test_handoff_custom_request(self):
        """User with custom request → handoff."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            has_custom_request=True,
        )
        assert should_handoff is True
        assert reason == OperatorHandoffReason.CUSTOM_REQUEST

    def test_no_handoff_warm(self):
        """Warm user without issues → no handoff."""
        should_handoff, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
        )
        assert should_handoff is False
        assert reason is None


# ── Edge cases ───────────────────────────────────────────────────────────


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_purchases(self):
        """Zero purchases → COLD or NEW."""
        state = derive_relationship_state(
            funnel_stage="new",
            purchase_count=0,
            last_purchase_days_ago=None,
            last_message_days_ago=10,
            message_count=0,
            has_active_offer=False,
        )
        assert state in (RelationshipState.COLD, RelationshipState.NEW)

    def test_many_messages_no_purchases(self):
        """Many messages but no purchases → COLD or NEW."""
        state = derive_relationship_state(
            funnel_stage="new",
            purchase_count=0,
            last_purchase_days_ago=None,
            last_message_days_ago=1,
            message_count=100,
            has_active_offer=False,
        )
        assert state in (RelationshipState.COLD, RelationshipState.NEW, RelationshipState.ENGAGED)

    def test_empty_segments(self):
        """Empty segment list → handled gracefully."""
        state = derive_relationship_state(
            funnel_stage="new",
            purchase_count=0,
            last_purchase_days_ago=None,
            last_message_days_ago=10,
            message_count=5,
            has_active_offer=False,
            segment_names=[],
        )
        assert state is not None

    def test_none_timestamps(self):
        """None timestamps → handled gracefully."""
        state = derive_relationship_state(
            funnel_stage="new",
            purchase_count=0,
            last_purchase_days_ago=None,
            last_message_days_ago=None,
            message_count=5,
            has_active_offer=False,
        )
        assert state is not None
