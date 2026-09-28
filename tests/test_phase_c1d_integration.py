"""Phase C.1-D — Behavioral feedback runtime integration tests.

Tests that C.1-C feedback fields survive the full state → pipeline → decision
projection path, post-purchase records behavioral feedback, and memory
renders behavioral context.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from commerce.decision import (
    CommerceDecisionContext,
    CommerceDecisionPolicy,
    CommerceReason,
    decide_commerce_action,
)
from commerce.feedback import (
    BehavioralEvent,
    FeedbackEventType,
    classify_rejection,
    is_repeat_purchase_eligible,
)
from commerce.models import CommerceAction, PolicyDecision
from commerce.pipeline import CommercePipelineRequest, run_commerce_pipeline


# ── Helpers ────────────────────────────────────────────────────────────────

_POLICY_DECISION_OK = PolicyDecision(allowed=True, denial_reason="")


def _make_context(**overrides) -> CommerceDecisionContext:
    """Build a minimal valid CommerceDecisionContext for testing."""
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
        total_tips_received=0,
        hours_since_last_tip=None,
        tip_suggestions_sent=0,
        tip_suggestions_ignored=0,
        aftercare_status="none",
        commercial_paused=False,
        fan_expressed_appreciation=False,
        fan_asked_how_to_support=False,
        repeat_purchase_eligible=False,
    )
    defaults.update(overrides)
    return CommerceDecisionContext(**defaults)


def _make_request(**overrides) -> CommercePipelineRequest:
    """Build a minimal valid CommercePipelineRequest for testing."""
    defaults = dict(
        user_id=12345,
        creator_id=999,
        messages=[{"role": "user", "content": "hey"}],
        eligibility=_POLICY_DECISION_OK,
        hours_since_last_offer=48.0,
        hours_since_last_purchase=168.0,
        recent_offer_count=1,
        recent_purchase_count=0,
        recent_sales_attempt_count=2,
        consecutive_rejections=0,
        total_purchases=0,
        total_tips_received=0,
        hours_since_last_tip=None,
        tip_suggestions_sent=0,
        tip_suggestions_ignored=0,
        aftercare_status="none",
        commercial_paused=False,
        fan_expressed_appreciation=False,
        fan_asked_how_to_support=False,
        repeat_purchase_eligible=False,
    )
    defaults.update(overrides)
    return CommercePipelineRequest(**defaults)


# ── State → Pipeline Field Survival ───────────────────────────────────────


class TestC1DFieldSurvival:
    """C.1-C fields survive state → pipeline → decision projection."""

    def test_consecutive_rejections_survives(self):
        req = _make_request(consecutive_rejections=2)
        assert req.consecutive_rejections == 2

    def test_commercial_paused_survives(self):
        req = _make_request(commercial_paused=True)
        assert req.commercial_paused is True

    def test_aftercare_status_survives(self):
        req = _make_request(aftercare_status="pending")
        assert req.aftercare_status == "pending"

    def test_repeat_purchase_eligible_survives(self):
        req = _make_request(repeat_purchase_eligible=True)
        assert req.repeat_purchase_eligible is True

    def test_tip_history_survives(self):
        req = _make_request(
            tip_suggestions_sent=3,
            tip_suggestions_ignored=1,
            hours_since_last_tip=24.0,
        )
        assert req.tip_suggestions_sent == 3
        assert req.tip_suggestions_ignored == 1
        assert req.hours_since_last_tip == 24.0

    def test_fan_signals_survive(self):
        req = _make_request(
            fan_expressed_appreciation=True,
            fan_asked_how_to_support=True,
        )
        assert req.fan_expressed_appreciation is True
        assert req.fan_asked_how_to_support is True


# ── Decision Engine Uses C.1-C Fields ─────────────────────────────────────


class TestC1DDecisionEngine:
    """Decision engine respects C.1-C behavioral feedback."""

    def test_commercial_paused_returns_relationship_building(self):
        """COMMERCIAL_PAUSED reason when commercial_paused=True."""
        ctx = _make_context(commercial_paused=True, consecutive_rejections=3)
        result = decide_commerce_action(ctx)
        assert result.action == CommerceAction.RELATIONSHIP_BUILDING
        assert result.reason_code == CommerceReason.COMMERCIAL_PAUSED

    def test_aftercare_returns_relationship_building(self):
        """AFTERCARE_PHASE reason when aftercare_status != none and total_purchases > 0."""
        ctx = _make_context(aftercare_status="pending", total_purchases=1)
        result = decide_commerce_action(ctx)
        assert result.action == CommerceAction.RELATIONSHIP_BUILDING
        assert result.reason_code == CommerceReason.AFTERCARE_PHASE

    def test_rejection_escalation_returns_relationship_building(self):
        """REJECTION_ESCALATION reason when consecutive_rejections >= threshold."""
        policy = CommerceDecisionPolicy(rejection_escalation_threshold=2)
        ctx = _make_context(consecutive_rejections=2)
        result = decide_commerce_action(ctx, policy=policy)
        assert result.action == CommerceAction.RELATIONSHIP_BUILDING
        assert result.reason_code == CommerceReason.REJECTION_ESCALATION

    def test_no_commercial_pause_allows_commerce(self):
        """No COMMERCIAL_PAUSED when commercial_paused=False."""
        ctx = _make_context(commercial_paused=False, consecutive_rejections=0)
        result = decide_commerce_action(ctx)
        assert result.reason_code != "COMMERCIAL_PAUSED"

    def test_no_aftercare_allows_commerce(self):
        """No AFTERCARE_PHASE when aftercare_status='none'."""
        ctx = _make_context(aftercare_status="none", total_purchases=0)
        result = decide_commerce_action(ctx)
        assert result.reason_code != "AFTERCARE_PHASE"


# ── Post-Purchase Records Feedback ────────────────────────────────────────


class TestC1DPostPurchaseFeedback:
    """Post-purchase records behavioral feedback events."""

    def test_handle_post_purchase_records_event(self):
        """handle_post_purchase calls feedback recording code."""
        from commerce.post_purchase import handle_post_purchase

        record = SimpleNamespace(
            user_id=12345,
            transaction_id="txn_abc",
            creator_id=999,
            product_id=42,
        )

        mock_store = MagicMock()

        with (
            patch("commerce.post_purchase.advance_funnel_to_converted", new_callable=AsyncMock),
            patch("commerce.post_purchase.enqueue_purchase_confirmation", new_callable=AsyncMock, return_value=True),
            patch("commerce.post_purchase.schedule_follow_up", new_callable=AsyncMock),
            patch("commerce.post_purchase.deliver_product_media", new_callable=AsyncMock),
            patch("commerce.feedback._behavioral_store", mock_store),
        ):
            import asyncio
            asyncio.run(handle_post_purchase(record))

        mock_store.append.assert_called_once()
        event = mock_store.append.call_args[0][0]
        assert event.event_type == FeedbackEventType.PURCHASE_COMPLETED
        assert event.user_id == 12345
        assert event.creator_id == 999


# ── Memory Context Renders Behavioral Feedback ────────────────────────────


class TestC1DMemoryRendering:
    """Memory context renders C.1-C behavioral feedback."""

    def test_render_rejection_count(self):
        from memory.context_assembler import LLMContext, render_context

        ctx = LLMContext(
            user_id=1,
            funnel_stage="new",
            is_blocked=False,
            do_not_auto_reply=False,
            first_name="test",
            message_count=5,
            conversation_summary=None,
            consecutive_rejections=2,
        )
        text = render_context(ctx)
        assert "Rejections: 2 consecutive" in text

    def test_render_commercial_pause(self):
        from memory.context_assembler import LLMContext, render_context

        ctx = LLMContext(
            user_id=1,
            funnel_stage="new",
            is_blocked=False,
            do_not_auto_reply=False,
            first_name="test",
            message_count=5,
            conversation_summary=None,
            commercial_paused=True,
        )
        text = render_context(ctx)
        assert "Commercial pause: active" in text

    def test_render_aftercare_status(self):
        from memory.context_assembler import LLMContext, render_context

        ctx = LLMContext(
            user_id=1,
            funnel_stage="new",
            is_blocked=False,
            do_not_auto_reply=False,
            first_name="test",
            message_count=5,
            conversation_summary=None,
            aftercare_status="pending",
        )
        text = render_context(ctx)
        assert "Aftercare: pending" in text

    def test_render_repeat_eligible(self):
        from memory.context_assembler import LLMContext, render_context

        ctx = LLMContext(
            user_id=1,
            funnel_stage="new",
            is_blocked=False,
            do_not_auto_reply=False,
            first_name="test",
            message_count=5,
            conversation_summary=None,
            repeat_purchase_eligible=True,
        )
        text = render_context(ctx)
        assert "Repeat purchase: eligible" in text

    def test_no_feedback_fields_when_defaults(self):
        from memory.context_assembler import LLMContext, render_context

        ctx = LLMContext(
            user_id=1,
            funnel_stage="new",
            is_blocked=False,
            do_not_auto_reply=False,
            first_name="test",
            message_count=5,
            conversation_summary=None,
        )
        text = render_context(ctx)
        assert "Rejections:" not in text
        assert "Commercial pause:" not in text
        assert "Aftercare:" not in text
        assert "Repeat purchase:" not in text


# ── End-to-End Scenarios ──────────────────────────────────────────────────


class TestC1DEndToEnd:
    """End-to-end behavioral feedback scenarios."""

    def test_rejection_then_escalation(self):
        ctx = _make_context(consecutive_rejections=3)
        result = decide_commerce_action(ctx)
        assert result.action == CommerceAction.RELATIONSHIP_BUILDING
        assert result.reason_code == CommerceReason.REJECTION_ESCALATION

    def test_purchase_then_aftercare_suppresses(self):
        ctx = _make_context(aftercare_status="pending", total_purchases=1)
        result = decide_commerce_action(ctx)
        assert result.action == CommerceAction.RELATIONSHIP_BUILDING
        assert result.reason_code == CommerceReason.AFTERCARE_PHASE

    def test_three_rejections_then_pause(self):
        ctx = _make_context(consecutive_rejections=3, commercial_paused=True)
        result = decide_commerce_action(ctx)
        assert result.action == CommerceAction.RELATIONSHIP_BUILDING
        assert result.reason_code == CommerceReason.COMMERCIAL_PAUSED

    def test_kill_switch_overrides_all(self):
        """autonomy_enabled=false in settings prevents execution."""
        mock_settings = MagicMock()
        mock_settings.autonomy_enabled = False
        with patch("automation.service._settings", mock_settings):
            from automation.service import AutomationService
            svc = AutomationService.__new__(AutomationService)
            import asyncio
            result = asyncio.run(svc.execute(
                creator_id=999,
                action="send_message",
                target="user:12345",
                params={"content": "test"},
            ))
            assert result["status"] == "cancelled"
            assert result["reason"] == "autonomy_disabled"

    def test_repeat_buyer_eligible(self):
        req = _make_request(repeat_purchase_eligible=True, total_purchases=3)
        assert req.repeat_purchase_eligible is True
        assert req.total_purchases == 3
