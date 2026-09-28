"""Phase 5.5: single plain-text retry on JSON-debris or weak-but-clean drafts."""
import pytest


def _u():
    return {"first_name": "Luna", "funnel_stage": "new", "message_count": 5}


@pytest.mark.asyncio
async def test_retry_on_invalid_json_uses_plain_retry():
    from unittest.mock import AsyncMock, patch

    from core.one_call_pipeline import one_call_generation

    calls = []

    async def fake_gen(**kwargs):
        calls.append(kwargs)
        if kwargs.get("onecall_json_schema"):
            return '{"reply": "oops truncated'
        return "im good! how are you doing today?"

    with patch("core.one_call_pipeline.get_llm_provider") as gp:
        prov = AsyncMock()
        prov.generate = AsyncMock(side_effect=fake_gen)
        prov.provider_name = "t"
        prov._model = "m"
        gp.return_value = prov
        r = await one_call_generation(
            user_id=1, creator_id=1, user_message="i'm okay, how are you?",
            persona="p", profile={}, user=_u(),
            recent_messages=[{"role": "assistant", "content": "hey Luna!"}],
            retrieved_context="",
        )
        assert len(calls) == 2
        assert calls[1].get("onecall_json_schema") is False
        assert getattr(r, "generation_kind", "") == "plain_retry"
        assert getattr(r, "call_index", 1) == 2
        assert "}," not in r.reply


@pytest.mark.asyncio
async def test_no_retry_on_safety_flags():
    from unittest.mock import AsyncMock, patch

    from core.one_call_pipeline import one_call_generation

    bad = '{"reply": "click make money now", "commerce_signals": {"purchase_intent": 0.0, "content_interest": 0.0, "relationship_engagement": 0.0, "price_interest": 0.0, "explicit_purchase_request": false, "explicit_content_request": false, "requested_price": null, "declined_recent_offer": false, "asks_for_free_content": false, "negative_sentiment": 0.0, "confidence": 0.9, "evidence": [], "model_uncertainty": 0.1, "primary_intent": "other", "intent_tags": [], "negative_intent_tags": [], "fan_asks_question": false}, "confidence": 0.9, "needs_handoff": false}'

    with patch("core.one_call_pipeline.get_llm_provider") as gp:
        prov = AsyncMock()
        prov.generate = AsyncMock(return_value=bad)
        prov.provider_name = "t"
        prov._model = "m"
        gp.return_value = prov
        with patch(
            "core.one_call_pipeline.validate_draft_quality",
            return_value=(True, 0.9, [], ["unsafe_content"]),
        ):
            r = await one_call_generation(
                user_id=1, creator_id=1, user_message="hello there friend",
                persona="p", profile={}, user=_u(), recent_messages=[],
                retrieved_context="",
            )
            assert prov.generate.await_count == 1
            assert getattr(r, "generation_kind", "") == "one_call"


@pytest.mark.asyncio
async def test_retry_keeps_better_score_only():
    from unittest.mock import AsyncMock, patch

    from core.one_call_pipeline import one_call_generation

    async def fake_gen(**kwargs):
        if kwargs.get("onecall_json_schema"):
            return "not json at all {{{"
        return "   "

    with patch("core.one_call_pipeline.get_llm_provider") as gp:
        prov = AsyncMock()
        prov.generate = AsyncMock(side_effect=fake_gen)
        prov.provider_name = "t"
        prov._model = "m"
        gp.return_value = prov
        r = await one_call_generation(
            user_id=1, creator_id=1, user_message="i'm okay, how are you?",
            persona="p", profile={}, user=_u(),
            recent_messages=[{"role": "assistant", "content": "hey!"}],
            retrieved_context="",
        )
        # retry produced nothing usable: original (invalid) stands, still queued
        assert r.needs_handoff is True
        assert prov.generate.await_count == 2
