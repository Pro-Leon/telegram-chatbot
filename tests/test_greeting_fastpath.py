"""Phase 5.4: greeting fast-path (reply-only, no JSON schema)."""
import pytest

from core.one_call_pipeline import GREETING_SYSTEM_PROMPT, is_cold_greeting


def _u(**kw):
    d = {"first_name": "Luna", "funnel_stage": "new", "message_count": 0}
    d.update(kw)
    return d


def test_trigger_matrix():
    assert is_cold_greeting("hi", _u(), []) is True
    assert is_cold_greeting("  Hello! ", _u(), []) is True
    assert is_cold_greeting("how much for the bundle?", _u(), []) is False
    assert is_cold_greeting("hi", _u(funnel_stage="engaged"), []) is False
    assert is_cold_greeting("hi", _u(message_count=9), []) is False
    # production turn 1 carries the just-saved current inbound as one row
    assert is_cold_greeting("hi", _u(), [{"role": "user", "content": "hi"}]) is True
    assert is_cold_greeting("hi", _u(), [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]) is False
    assert is_cold_greeting(None, _u(), []) is False
    assert is_cold_greeting("", _u(), []) is False


def test_prompt_has_no_schema_example():
    assert "<<" not in GREETING_SYSTEM_PROMPT
    assert "commerce_signals" not in GREETING_SYSTEM_PROMPT
    assert "Sunny" in GREETING_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_fastpath_plain_text_queued_or_sent():
    from unittest.mock import AsyncMock, patch

    from core.one_call_pipeline import one_call_generation

    with patch(
        "core.one_call_pipeline.get_llm_provider",
    ) as gp:
        prov = AsyncMock()
        prov.generate = AsyncMock(return_value="heyy luna, how are you")
        prov.provider_name = "t"
        prov._model = "m"
        prov.last_prompt_tokens = 10
        prov.last_generation_tokens = 5
        gp.return_value = prov
        r = await one_call_generation(
            user_id=1, creator_id=1, user_message="hi", persona="p",
            profile={}, user=_u(), recent_messages=[], retrieved_context="",
        )
        assert getattr(r, "generation_kind", "") == "greeting_fastpath"
        assert r.reply == "heyy luna, how are you"
        assert r.is_valid is True
        # plain-text call: no JSON mime, no schema flag, small cap
        _, kwargs = prov.generate.await_args
        assert kwargs.get("response_mime_type") is None
        assert kwargs.get("onecall_json_schema") is False
        assert kwargs.get("max_output_tokens") == 100


@pytest.mark.asyncio
async def test_fastpath_empty_reply_invalid():
    from unittest.mock import AsyncMock, patch

    from core.one_call_pipeline import one_call_generation

    with patch("core.one_call_pipeline.get_llm_provider") as gp:
        prov = AsyncMock()
        prov.generate = AsyncMock(return_value="   ")
        prov.provider_name = "t"
        prov._model = "m"
        gp.return_value = prov
        r = await one_call_generation(
            user_id=1, creator_id=1, user_message="hi", persona="p",
            profile={}, user=_u(), recent_messages=[], retrieved_context="",
        )
        assert r.is_valid is False
        assert r.needs_handoff is True


@pytest.mark.asyncio
async def test_non_greeting_uses_json_path():
    from unittest.mock import AsyncMock, patch

    from core.one_call_pipeline import one_call_generation

    seen = {}

    async def fake_gen(**kwargs):
        seen.update(kwargs)
        return '{"reply": "here you go", "commerce_signals": {"purchase_intent": 0.0, "content_interest": 0.0, "relationship_engagement": 0.5, "price_interest": 0.0, "explicit_purchase_request": false, "explicit_content_request": false, "requested_price": null, "declined_recent_offer": false, "asks_for_free_content": false, "negative_sentiment": 0.0, "confidence": 0.9, "evidence": [], "model_uncertainty": 0.1, "primary_intent": "casual_chat", "intent_tags": [], "negative_intent_tags": [], "fan_asks_question": false}, "confidence": 0.9, "needs_handoff": false}'

    with patch("core.one_call_pipeline.get_llm_provider") as gp:
        prov = AsyncMock()
        prov.generate = AsyncMock(side_effect=fake_gen)
        prov.provider_name = "t"
        prov._model = "m"
        gp.return_value = prov
        r = await one_call_generation(
            user_id=1, creator_id=1, user_message="how much for the bundle?",
            persona="p", profile={}, user=_u(), recent_messages=[],
            retrieved_context="",
        )
        assert seen.get("onecall_json_schema") is True
        assert r.reply == "here you go"
