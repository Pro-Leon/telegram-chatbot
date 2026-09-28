"""Phase 75F: One-call pipeline integration tests.

Tests the one-call pipeline that integrates all previous phases.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.one_call_pipeline import (
    one_call_generation,
    one_call_pipeline_with_fallback,
)
from core.one_call import OneCallResult
from commerce.signals import CommerceSignals


class TestOneCallGeneration:
    """Tests for one_call_generation."""

    @pytest.mark.asyncio
    async def test_basic_generation(self):
        """Basic generation should work with valid context."""
        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(return_value=json.dumps({
            "reply": "Hey there! How's your day going?",
            "commerce_signals": {
                "purchase_intent": 0.2,
                "content_interest": 0.5,
                "relationship_engagement": 0.7,
                "price_interest": 0.1,
                "explicit_purchase_request": False,
                "explicit_content_request": False,
                "requested_price": None,
                "declined_recent_offer": False,
                "asks_for_free_content": False,
                "negative_sentiment": 0.0,
                "confidence": 0.8,
                "evidence": [],
                "model_uncertainty": 0.2,
                "primary_intent": "casual_chat",
                "intent_tags": [],
                "negative_intent_tags": [],
                "fan_asks_question": False,
            },
            "confidence": 0.85,
            "needs_handoff": False,
        }))

        with patch("core.one_call_pipeline.get_llm_provider", return_value=mock_provider):
            result = await one_call_generation(
                user_id=123,
                creator_id=456,
                user_message="Hello!",
                persona="You are Sunny.",
                profile={"age": 25},
                user={"first_name": "Alex", "funnel_stage": "new"},
            )

        assert result.is_valid is True
        assert result.reply == "Hey there! How's your day going?"
        assert result.confidence == 0.85
        assert result.needs_handoff is False

    @pytest.mark.asyncio
    async def test_generation_failure(self):
        """Generation failure should return fallback."""
        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(side_effect=Exception("Provider error"))

        with patch("core.one_call_pipeline.get_llm_provider", return_value=mock_provider):
            result = await one_call_generation(
                user_id=123,
                creator_id=456,
                user_message="Hello!",
                persona="You are Sunny.",
                profile={},
                user={"first_name": "Alex", "funnel_stage": "new"},
            )

        assert result.is_valid is False
        assert result.needs_handoff is True
        assert "Generation failed" in result.validation_error

    @pytest.mark.asyncio
    async def test_invalid_json_response(self):
        """Invalid JSON response should return fallback."""
        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(return_value="not valid json {{{")

        with patch("core.one_call_pipeline.get_llm_provider", return_value=mock_provider):
            result = await one_call_generation(
                user_id=123,
                creator_id=456,
                user_message="Hello!",
                persona="You are Sunny.",
                profile={},
                user={"first_name": "Alex", "funnel_stage": "new"},
            )

        assert result.is_valid is False
        assert result.needs_handoff is True

    @pytest.mark.asyncio
    async def test_commerce_hints_included(self):
        """Commerce hints should be added to context."""
        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(return_value=json.dumps({
            "reply": "Hey there!",
            "confidence": 0.8,
        }))

        with patch("core.one_call_pipeline.get_llm_provider", return_value=mock_provider):
            result = await one_call_generation(
                user_id=123,
                creator_id=456,
                user_message="Hello!",
                persona="You are Sunny.",
                profile={},
                user={"first_name": "Alex", "funnel_stage": "new"},
                commerce_text="Purchase: active offer pending",
                relationship_state="warm",
                has_active_offer=True,
            )

        # Verify provider was called
        mock_provider.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_quality_scoring_applied(self):
        """Quality scoring should be applied to valid responses."""
        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(return_value=json.dumps({
            "reply": "Hey! I was just thinking about you. How's your day going?",
            "confidence": 0.85,
        }))

        with patch("core.one_call_pipeline.get_llm_provider", return_value=mock_provider):
            result = await one_call_generation(
                user_id=123,
                creator_id=456,
                user_message="Hello!",
                persona="You are Sunny.",
                profile={},
                user={"first_name": "Alex", "funnel_stage": "new"},
            )

        assert result.is_valid is True
        assert result.quality_score > 0


class TestOneCallPipelineWithFallback:
    """Tests for one_call_pipeline_with_fallback."""

    @pytest.mark.asyncio
    async def test_successful_pipeline(self):
        """Successful pipeline should return one-call result."""
        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(return_value=json.dumps({
            "reply": "Hey there!",
            "confidence": 0.8,
        }))

        with patch("core.one_call_pipeline.get_llm_provider", return_value=mock_provider):
            result = await one_call_pipeline_with_fallback(
                user_id=123,
                creator_id=456,
                user_message="Hello!",
                persona="You are Sunny.",
                profile={},
                user={"first_name": "Alex", "funnel_stage": "new"},
            )

        assert result.is_valid is True
        assert result.reply == "Hey there!"

    @pytest.mark.asyncio
    async def test_fallback_on_failure(self):
        """Failure should fall back to 3-LLM pipeline."""
        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(side_effect=Exception("Provider error"))

        with patch("core.one_call_pipeline.get_llm_provider", return_value=mock_provider):
            result = await one_call_pipeline_with_fallback(
                user_id=123,
                creator_id=456,
                user_message="Hello!",
                persona="You are Sunny.",
                profile={},
                user={"first_name": "Alex", "funnel_stage": "new"},
            )

        # Should fall back to 3-LLM pipeline
        assert result.is_valid is True
        assert result.needs_handoff is True


class TestOneCallResultIntegration:
    """Tests for OneCallResult integration."""

    def test_result_with_safety_flags(self):
        """Result with safety flags should trigger handoff."""
        result = OneCallResult(
            reply="I'm feeling depressed",
            signals=CommerceSignals.low_information(),
            confidence=0.3,
            needs_handoff=True,
            safety_flags=["distress_signal"],
        )
        assert result.needs_handoff is True
        assert "distress_signal" in result.safety_flags

    def test_result_with_quality_flags(self):
        """Result with quality flags should trigger handoff."""
        result = OneCallResult(
            reply="ok",
            signals=CommerceSignals.low_information(),
            confidence=0.3,
            needs_handoff=True,
            quality_score=0.3,
            quality_flags=["too_generic"],
        )
        assert result.needs_handoff is True
        assert "too_generic" in result.quality_flags
