"""Phase 0.1 Echo preserve regression tests.

Canonical ref: docs/LUNA_RELIABILITY_PROGRAM.md Phase 0.1.
Scope: core/one_call_pipeline.py Step 6 only. No routing/worker/commerce changes.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from core.one_call_pipeline import one_call_generation
from core.routing import RoutingAction, decide_routing

ECHO_REPLY = "Your conversational response to the fan"


def _echo_payload(reply: str = ECHO_REPLY, confidence: float = 0.95):
    return json.dumps(
        {
            "reply": reply,
            "commerce_signals": {
                "purchase_intent": 0.0,
                "content_interest": 0.0,
                "relationship_engagement": 0.5,
                "price_interest": 0.0,
                "explicit_purchase_request": False,
                "explicit_content_request": False,
                "requested_price": None,
                "declined_recent_offer": False,
                "asks_for_free_content": False,
                "negative_sentiment": 0.0,
                "confidence": 0.9,
                "evidence": [],
                "model_uncertainty": 0.1,
                "primary_intent": "casual_chat",
                "intent_tags": [],
                "negative_intent_tags": [],
                "fan_asks_question": False,
            },
            "confidence": confidence,
            "needs_handoff": False,
        }
    )


def _mock_echo_provider(reply: str = ECHO_REPLY):
    provider = AsyncMock()
    provider.generate = AsyncMock(return_value=_echo_payload(reply))
    provider.provider_name = "test"
    provider._model = "test-model"
    provider.last_prompt_tokens = None
    provider.last_generation_tokens = None
    return provider


@pytest.mark.asyncio
async def test_pipeline_echo_preserved_capped_handoff():
    """Echo through full pipeline keeps flag, capped score, handoff."""
    provider = _mock_echo_provider()
    with patch("core.one_call_pipeline.get_llm_provider", return_value=provider):
        result = await one_call_generation(
            user_id=8151382101,
            creator_id=1,
            user_message="Hey Luna, how's it going?",
            persona="You are Luna.",
            profile={},
            user={"first_name": "Alex", "funnel_stage": "new"},
        )
    assert result.is_valid is True
    assert result.reply == ECHO_REPLY
    assert "prompt_echo" in result.quality_flags
    assert result.quality_score <= 0.29
    assert result.needs_handoff is True
    assert result.confidence <= 0.3


@pytest.mark.asyncio
async def test_pipeline_echo_overwrite_survival_punctuated():
    """Punctuated/cased echo variant also survives Step 6 overwrite."""
    provider = _mock_echo_provider("  YOUR CONVERSATIONAL RESPONSE TO THE FAN. ")
    with patch("core.one_call_pipeline.get_llm_provider", return_value=provider):
        result = await one_call_generation(
            user_id=8151382101,
            creator_id=1,
            user_message="Hey Luna, how's it going?",
            persona="You are Luna.",
            profile={},
            user={"first_name": "Alex", "funnel_stage": "new"},
        )
    assert "prompt_echo" in result.quality_flags
    assert result.quality_score <= 0.29
    assert result.needs_handoff is True


@pytest.mark.asyncio
async def test_pipeline_echo_routes_to_queue():
    """Pipeline echo result maps to QUEUE via blocking flags / handoff."""
    provider = _mock_echo_provider()
    with patch("core.one_call_pipeline.get_llm_provider", return_value=provider):
        result = await one_call_generation(
            user_id=8151382101,
            creator_id=1,
            user_message="Hey Luna, how's it going?",
            persona="You are Luna.",
            profile={},
            user={"first_name": "Alex", "funnel_stage": "new"},
        )
    decision = decide_routing(
        is_valid=bool(result.is_valid),
        score=result.quality_score,
        auto_approve_threshold=0.80,
        has_blocking_flags=bool(list(result.safety_flags) + list(result.quality_flags)),
        flags=list(result.safety_flags) + list(result.quality_flags),
        needs_handoff=bool(result.needs_handoff),
        advisory_handoff=bool(result.advisory_handoff),
    )
    assert decision.action is RoutingAction.QUEUE
