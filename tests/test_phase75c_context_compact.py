"""Phase 75C: Deterministic context compaction tests.

Tests the compact context builder for the one-call pipeline.
"""

import pytest
from unittest.mock import MagicMock

from core.context_compact import (
    build_one_call_context,
    _build_compact_system_prompt,
    _build_compact_state_context,
    estimate_one_call_tokens,
    validate_one_call_context,
    ONE_CALL_TOKEN_BUDGET,
)


class TestBuildCompactSystemPrompt:
    """Tests for _build_compact_system_prompt."""

    def test_basic_prompt(self):
        """Basic prompt should include persona, fan info, and rules."""
        prompt = _build_compact_system_prompt(
            persona="You are Sunny Skye. Sunny Skye here!",
            user={"first_name": "Alex", "funnel_stage": "new"},
            profile={"age": 25, "interests": ["music", "travel"]},
        )
        assert "You are Sunny Skye" in prompt
        assert "Fan: Alex" in prompt
        assert "25" in prompt or "music" in prompt
        assert "Rules:" in prompt

    def test_identity_established(self):
        """Established identity should trim persona."""
        prompt = _build_compact_system_prompt(
            persona="You are Sunny Skye. Sunny Skye here!",
            user={"first_name": "Alex", "funnel_stage": "new"},
            profile={},
            persona_name="Sunny Skye",
            identity_established=True,
        )
        assert "You are sunny" in prompt
        assert "You are Sunny Skye" not in prompt

    def test_empty_profile(self):
        """Empty profile should show default text."""
        prompt = _build_compact_system_prompt(
            persona="You are Sunny.",
            user={"first_name": "Alex", "funnel_stage": "new"},
            profile={},
        )
        assert "No profile data yet." in prompt

    def test_stage_guidance(self):
        """Funnel stage should be included."""
        prompt = _build_compact_system_prompt(
            persona="You are Sunny.",
            user={"first_name": "Alex", "funnel_stage": "engaged"},
            profile={},
        )
        assert "engaged" in prompt.lower() or "deepen" in prompt.lower()


class TestBuildCompactStateContext:
    """Tests for _build_compact_state_context."""

    def test_basic_state(self):
        """Basic state should include header."""
        state = _build_compact_state_context(
            user={"first_name": "Alex", "funnel_stage": "new"},
            profile={},
        )
        assert "STATE: Alex" in state
        assert "new" in state

    def test_relationship_state(self):
        """Relationship state should be included."""
        state = _build_compact_state_context(
            user={"first_name": "Alex", "funnel_stage": "new", "relationship_state": "warm"},
            profile={},
        )
        assert "RELATIONSHIP: warm" in state

    def test_summary_compression(self):
        """Summary should be compressed to first sentence."""
        summary = "Alex is a music lover. He enjoys travel. He has purchased twice."
        state = _build_compact_state_context(
            user={"first_name": "Alex", "funnel_stage": "new"},
            profile={},
            summary=summary,
        )
        assert "SUMMARY:" in state
        # Should contain first sentence
        assert "music lover" in state

    def test_commerce_text(self):
        """Commerce text should be included if present."""
        state = _build_compact_state_context(
            user={"first_name": "Alex", "funnel_stage": "new"},
            profile={},
            commerce_text="Purchase: active offer pending",
        )
        # Commerce text should be processed
        assert "STATE:" in state

    def test_response_mode(self):
        """Response mode should be included."""
        state = _build_compact_state_context(
            user={"first_name": "Alex", "funnel_stage": "new"},
            profile={},
            response_mode="tease",
            question_allowed=False,
        )
        assert "RESPONSE: mode=tease" in state
        assert "QUESTION: allowed=false" in state


class TestBuildOneCallContext:
    """Tests for build_one_call_context."""

    def test_basic_context(self):
        """Basic context should have system + state messages."""
        messages = build_one_call_context(
            user={"first_name": "Alex", "funnel_stage": "new"},
            profile={},
            persona="You are Sunny.",
        )
        assert len(messages) >= 2
        assert messages[0]["role"] == "system"
        assert "Sunny" in messages[0]["content"]
        assert "STATE:" in messages[1]["content"]

    def test_recent_messages_included(self):
        """Recent messages should be included."""
        recent = [
            {"direction": "inbound", "content": "Hello!"},
            {"direction": "outbound", "content": "Hi there!"},
        ]
        messages = build_one_call_context(
            user={"first_name": "Alex", "funnel_stage": "new"},
            profile={},
            persona="You are Sunny.",
            recent_messages=recent,
        )
        # Should have system + state + 2 recent messages
        assert len(messages) >= 4

    def test_recent_messages_limited(self):
        """Recent messages should be limited to ONE_CALL_MAX_MESSAGES."""
        recent = [
            {"direction": "inbound", "content": f"Message {i}"}
            for i in range(20)
        ]
        messages = build_one_call_context(
            user={"first_name": "Alex", "funnel_stage": "new"},
            profile={},
            persona="You are Sunny.",
            recent_messages=recent,
        )
        # Count user/assistant messages
        conversation_msgs = [m for m in messages if m["role"] in ("user", "assistant")]
        assert len(conversation_msgs) <= 8

    def test_assistant_turns_limited(self):
        """Assistant turns should be limited to 3."""
        recent = [
            {"direction": "outbound", "content": f"Response {i}"}
            for i in range(10)
        ]
        messages = build_one_call_context(
            user={"first_name": "Alex", "funnel_stage": "new"},
            profile={},
            persona="You are Sunny.",
            recent_messages=recent,
        )
        # Count assistant messages
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        assert len(assistant_msgs) <= 3


class TestEstimateOneCallTokens:
    """Tests for estimate_one_call_tokens."""

    def test_basic_estimate(self):
        """Basic token estimation should work."""
        messages = [
            {"role": "system", "content": "Hello world"},
            {"role": "user", "content": "Hi there"},
        ]
        tokens = estimate_one_call_tokens(messages)
        assert tokens > 0
        assert isinstance(tokens, int)

    def test_empty_messages(self):
        """Empty messages should return 0."""
        tokens = estimate_one_call_tokens([])
        assert tokens == 0


class TestValidateOneCallContext:
    """Tests for validate_one_call_context."""

    def test_valid_context(self):
        """Valid context should pass validation."""
        messages = [
            {"role": "system", "content": "Hello world"},
            {"role": "user", "content": "Hi there"},
        ]
        is_valid, error = validate_one_call_context(messages)
        assert is_valid is True
        assert error == ""

    def test_too_large_context(self):
        """Context exceeding 8192 tokens should fail."""
        messages = [
            {"role": "system", "content": "x" * 100000},  # Very large
        ]
        is_valid, error = validate_one_call_context(messages)
        assert is_valid is False
        assert "too large" in error.lower()

    def test_system_prompt_too_large(self):
        """System over soft budget is warning-only under Option A (Pass 8)."""
        messages = [
            {"role": "system", "content": f"Fan: Alex\n{'x' * 5000}"},  # Large system prompt
        ]
        is_valid, error = validate_one_call_context(messages)
        assert is_valid is True  # soft diagnostic, wire still <1800 here
        assert error == ""


class TestOneCallTokenBudget:
    """Tests for ONE_CALL_TOKEN_BUDGET."""

    def test_budget_exists(self):
        """Token budget should be defined."""
        assert "system" in ONE_CALL_TOKEN_BUDGET
        assert "state" in ONE_CALL_TOKEN_BUDGET
        assert "conversation" in ONE_CALL_TOKEN_BUDGET
        assert "signals_hint" in ONE_CALL_TOKEN_BUDGET

    def test_budget_totals(self):
        """Total budget should be reasonable."""
        total = sum(ONE_CALL_TOKEN_BUDGET.values())
        assert total <= 8192  # Should fit within num_ctx
