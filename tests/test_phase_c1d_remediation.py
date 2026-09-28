"""Phase C.1-D Forensic Remediation — Tests for P0/P1/P2 Fixes.

Covers:
- P0-1: mark_offer_declined + rejection classification wiring
- P0-2: aftercare persistence (mark_aftercare_pending/completed, get_aftercare_status)
- P1-1: tip eligibility unification (fatigue detection in check_tip_eligibility)
- P1-3: tip cooldown ordering (HARD > PRICE_OBJECTION)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
)
from commerce.models import CommerceAction, PolicyDecision
from commerce.relationship import (
    CommercialPressure,
    RelationshipState,
    TipEligibility,
    check_tip_eligibility,
)


# ── Helpers ────────────────────────────────────────────────────────────────

_POLICY_DECISION_OK = PolicyDecision(allowed=True, denial_reason="")


def _make_context(**overrides) -> CommerceDecisionContext:
    defaults = dict(
        user_id=12345,
        creator_id=999,
        eligibility=_POLICY_DECISION_OK,
        creator_sales_enabled=True,
        has_relevant_product=True,
        has_active_offer=False,
        hours_since_last_offer=48.0,
        hours_since_last_purchase=168.0,
        recent_offer_count=1,
        recent_purchase_count=0,
        recent_sales_attempt_count=2,
        consecutive_rejections=0,
        total_purchases=0,
        aftercare_status="none",
        commercial_paused=False,
    )
    defaults.update(overrides)
    return CommerceDecisionContext(**defaults)


# ══════════════════════════════════════════════════════════════════════════════
# P0-1: Rejection Loop — mark_offer_declined + wiring
# ══════════════════════════════════════════════════════════════════════════════


class TestP01_RejectionLoop:
    """P0-1: Rejection classification must persist as offer decline."""

    def test_hard_rejection_classified(self):
        """HARD rejection is classified correctly."""
        result = classify_rejection(
            negative_intent_tags=["rejection"],
            negative_sentiment=0.8,
            price_interest=0.2,
            intent_tags=[],
        )
        assert result == RejectionType.HARD

    def test_price_objection_classified(self):
        """PRICE_OBJECTION is classified correctly."""
        result = classify_rejection(
            negative_intent_tags=["hesitation"],
            negative_sentiment=0.3,
            price_interest=0.7,
            intent_tags=[],
        )
        assert result == RejectionType.PRICE_OBJECTION

    def test_soft_rejection_not_hard(self):
        """SOFT rejection (hesitation without price signal) is NOT hard."""
        result = classify_rejection(
            negative_intent_tags=["hesitation"],
            negative_sentiment=0.2,
            price_interest=0.3,
            intent_tags=[],
        )
        assert result == RejectionType.SOFT

    def test_uncertain_not_hard(self):
        """UNCERTAIN is NOT classified as hard or price objection."""
        result = classify_rejection(
            negative_intent_tags=[],
            negative_sentiment=0.4,
            price_interest=0.1,
            intent_tags=[],
        )
        assert result == RejectionType.UNCERTAIN

    @pytest.mark.asyncio
    async def test_mark_offer_declined_persists(self):
        """mark_offer_declined transitions active offer to 'declined'."""
        from commerce.dao import mark_offer_declined

        mock_conn = AsyncMock()
        # P3.5.1: SELECT-then-conditional-UPDATE so sealed reason provenance
        # is preserved (merged) rather than overwritten.
        mock_conn.fetchrow = AsyncMock(side_effect=[
            {"id": 42, "reason": "prior note"},
            {
                "id": 42,
                "state": "declined",
                "reason": "prior note | DECLINED:rejection_hard",
                "creator_id": 999,
                "user_id": 12345,
            },
        ])
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock()))

        with patch("commerce.dao.get_pool", return_value=mock_pool):
            result = await mark_offer_declined(999, 12345, "rejection_hard")

        assert result is not None
        assert result["state"] == "declined"
        assert mock_conn.fetchrow.call_count == 2
        assert "SELECT id, reason FROM commerce_offers" in mock_conn.fetchrow.call_args_list[0][0][0]
        assert "prior note" in mock_conn.fetchrow.call_args_list[1][0][3]

    @pytest.mark.asyncio
    async def test_mark_offer_declined_no_active_offer(self):
        """mark_offer_declined returns None when no active offer exists."""
        from commerce.dao import mark_offer_declined

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock()))

        with patch("commerce.dao.get_pool", return_value=mock_pool):
            result = await mark_offer_declined(999, 12345, "rejection_hard")

        assert result is None

    def test_decline_only_for_hard_and_price_objection(self):
        """Only HARD and PRICE_OBJECTION should trigger decline."""
        # SOFT → should NOT decline
        soft = classify_rejection(
            negative_intent_tags=["hesitation"],
            negative_sentiment=0.2,
            price_interest=0.3,
            intent_tags=[],
        )
        assert soft not in (RejectionType.HARD, RejectionType.PRICE_OBJECTION)

        # UNCERTAIN → should NOT decline
        uncertain = classify_rejection(
            negative_intent_tags=[],
            negative_sentiment=0.4,
            price_interest=0.1,
            intent_tags=[],
        )
        assert uncertain not in (RejectionType.HARD, RejectionType.PRICE_OBJECTION)


# ══════════════════════════════════════════════════════════════════════════════
# P0-2: Aftercare Persistence
# ══════════════════════════════════════════════════════════════════════════════


class TestP02_AftercarePersistence:
    """P0-2: Aftercare status must be persisted and queryable."""

    @pytest.mark.asyncio
    async def test_mark_aftercare_pending(self):
        """mark_aftercare_pending sets aftercare_status to 'pending'."""
        from commerce.dao import mark_aftercare_pending

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock()))

        with patch("commerce.dao.get_pool", return_value=mock_pool):
            result = await mark_aftercare_pending(999, 12345)

        assert result is True
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_mark_aftercare_pending_no_offer(self):
        """mark_aftercare_pending returns False when no purchased offer exists."""
        from commerce.dao import mark_aftercare_pending

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 0")
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock()))

        with patch("commerce.dao.get_pool", return_value=mock_pool):
            result = await mark_aftercare_pending(999, 12345)

        assert result is False

    @pytest.mark.asyncio
    async def test_mark_aftercare_completed(self):
        """mark_aftercare_completed transitions pending→completed."""
        from commerce.dao import mark_aftercare_completed

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock()))

        with patch("commerce.dao.get_pool", return_value=mock_pool):
            result = await mark_aftercare_completed(999, 12345)

        assert result is True

    @pytest.mark.asyncio
    async def test_get_aftercare_status(self):
        """get_aftercare_status returns current status."""
        from commerce.dao import get_aftercare_status

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"aftercare_status": "pending"})
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock()))

        with patch("commerce.dao.get_pool", return_value=mock_pool):
            result = await get_aftercare_status(999, 12345)

        assert result == "pending"

    @pytest.mark.asyncio
    async def test_get_aftercare_status_none(self):
        """get_aftercare_status returns 'none' when no purchased offer exists."""
        from commerce.dao import get_aftercare_status

        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_pool = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock()))

        with patch("commerce.dao.get_pool", return_value=mock_pool):
            result = await get_aftercare_status(999, 12345)

        assert result == "none"

    def test_aftercare_suppresses_commerce(self):
        """Aftercare pending → AFTERCARE_PHASE decision."""
        ctx = _make_context(aftercare_status="pending", total_purchases=1)
        policy = CommerceDecisionPolicy()
        decision = decide_commerce_action(ctx, policy=policy)
        assert decision.reason_code == CommerceReason.AFTERCARE_PHASE
        assert decision.action != CommerceAction.OFFER_PPV

    def test_aftercare_completed_allows_commerce(self):
        """Aftercare completed → normal commerce allowed."""
        ctx = _make_context(aftercare_status="completed", total_purchases=1)
        policy = CommerceDecisionPolicy()
        decision = decide_commerce_action(ctx, policy=policy)
        # Should not be AFTERCARE_PHASE
        assert decision.reason_code != CommerceReason.AFTERCARE_PHASE

    def test_aftercare_none_allows_commerce(self):
        """Aftercare none → normal commerce."""
        ctx = _make_context(aftercare_status="none", total_purchases=0)
        policy = CommerceDecisionPolicy()
        decision = decide_commerce_action(ctx, policy=policy)
        assert decision.reason_code != CommerceReason.AFTERCARE_PHASE


# ══════════════════════════════════════════════════════════════════════════════
# P1-1: Tip Eligibility Unification
# ══════════════════════════════════════════════════════════════════════════════


class TestP11_TipEligibilityUnification:
    """P1-1: check_tip_eligibility must include fatigue detection."""

    def test_fatigue_suppresses_tip(self):
        """2+ ignored AND 2+ sent → INELIGIBLE (fatigue)."""
        elig, reason = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.NONE,
            tip_suggestions_sent=3,
            tip_suggestions_ignored=2,
        )
        assert elig == TipEligibility.INELIGIBLE
        assert reason == "tip_fatigue"

    def test_fatigue_not_triggered_with_few_sends(self):
        """1 sent, 1 ignored → NOT fatigued."""
        elig, reason = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.NONE,
            tip_suggestions_sent=1,
            tip_suggestions_ignored=1,
        )
        # Should be eligible (relationship-based)
        assert elig == TipEligibility.ELIGIBLE

    def test_contextual_trigger_overrides_cooldown(self):
        """Fan appreciation overrides cooldown (with 12h minimum)."""
        elig, reason = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.NONE,
            hours_since_last_tip=24.0,  # Beyond 12h minimum
            fan_expressed_appreciation=True,
        )
        assert elig == TipEligibility.ELIGIBLE
        assert reason == "contextual_request"

    def test_contextual_trigger_respects_min_cooldown(self):
        """Contextual trigger still respects 12h minimum."""
        elig, reason = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.NONE,
            hours_since_last_tip=8.0,  # Less than 12h
            fan_expressed_appreciation=True,
        )
        assert elig == TipEligibility.COOLDOWN_ACTIVE
        assert reason == "contextual_min_cooldown"

    def test_commercial_pause_suppresses_tip(self):
        """Commercial pause → INELIGIBLE."""
        elig, reason = check_tip_eligibility(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.NONE,
            commercial_paused=True,
        )
        assert elig == TipEligibility.INELIGIBLE
        assert reason == "commercial_paused"


# ══════════════════════════════════════════════════════════════════════════════
# P1-3: Tip Cooldown Ordering
# ══════════════════════════════════════════════════════════════════════════════


class TestP13_TipCooldownOrdering:
    """P1-3: HARD rejection must have longer cooldown than PRICE_OBJECTION."""

    def test_severity_mapping_ordering(self):
        """REJECTION_SEVERITY maps correctly."""
        assert REJECTION_SEVERITY[RejectionType.HARD] > REJECTION_SEVERITY[RejectionType.PRICE_OBJECTION]
        assert REJECTION_SEVERITY[RejectionType.PRICE_OBJECTION] > REJECTION_SEVERITY[RejectionType.SOFT]
        assert REJECTION_SEVERITY[RejectionType.SOFT] > REJECTION_SEVERITY[RejectionType.UNCERTAIN]


# ══════════════════════════════════════════════════════════════════════════════
# Adversarial Scenarios
# ══════════════════════════════════════════════════════════════════════════════


class TestAdversarialRemediation:
    """Realistic conversations testing behavioral boundaries."""

    def test_rejection_then_explicit_buy(self):
        """After rejection, explicit buy still works (authority preserved)."""
        ctx = _make_context(
            aftercare_status="none",
            consecutive_rejections=1,
            total_purchases=0,
            user_asked_to_buy=True,
        )
        policy = CommerceDecisionPolicy()
        decision = decide_commerce_action(ctx, policy=policy)
        # Explicit buy should still be honored
        assert decision.action == CommerceAction.OFFER_PPV

    def test_aftercare_then_explicit_buy(self):
        """Aftercare + explicit buy → explicit buy wins (authority gap preserved)."""
        ctx = _make_context(
            aftercare_status="pending",
            total_purchases=1,
            user_asked_to_buy=True,
        )
        policy = CommerceDecisionPolicy()
        decision = decide_commerce_action(ctx, policy=policy)
        # Explicit buy overrides aftercare (known authority gap)
        assert decision.action == CommerceAction.OFFER_PPV

    def test_commercial_paused_blocks_explicit_buy(self):
        """Commercial pause blocks even explicit buy (step 7.9 > step 8).

        This is the documented authority gap: aftercare doesn't block explicit
        buy, but commercial_paused does.
        """
        ctx = _make_context(
            commercial_paused=True,
            consecutive_rejections=1,  # Below rejection_escalation_threshold
            user_asked_to_buy=True,
        )
        policy = CommerceDecisionPolicy()
        decision = decide_commerce_action(ctx, policy=policy)
        # Step 7.9 (commercial_paused) fires before step 8 (explicit buy)
        assert decision.action == CommerceAction.RELATIONSHIP_BUILDING
        assert decision.reason_code == CommerceReason.COMMERCIAL_PAUSED

    def test_commercial_paused_blocks_without_explicit_buy(self):
        """Commercial pause without explicit buy → relationship building."""
        ctx = _make_context(
            commercial_paused=True,
            consecutive_rejections=1,
            user_asked_to_buy=False,
        )
        policy = CommerceDecisionPolicy()
        decision = decide_commerce_action(ctx, policy=policy)
        assert decision.action == CommerceAction.RELATIONSHIP_BUILDING

    def test_tip_fatigue_with_good_relationship(self):
        """Good relationship but fatigued → no tip suggestion."""
        elig, reason = check_tip_eligibility(
            relationship_state=RelationshipState.VIP,
            commercial_pressure=CommercialPressure.NONE,
            tip_suggestions_sent=5,
            tip_suggestions_ignored=3,
        )
        assert elig == TipEligibility.INELIGIBLE
        assert reason == "tip_fatigue"
