"""Phase 1.2 wiring tests: rails at pipeline Step 6 + commerce overwrite.

Pipeline fixtures use one_call_generation with a mocked provider (no I/O).
The commerce test drives process_message with the phase88 harness shape.
"""

import contextlib
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.one_call_pipeline import one_call_generation
from core.routing import RoutingAction, decide_routing


def _payload(reply):
    import json

    return json.dumps({"reply": reply, "confidence": 0.9})


def _provider(reply):
    p = AsyncMock()

    async def _gen(**kwargs):
        # Plain-text (non-schema) calls get raw prose; JSON-schema calls get
        # the OneCall envelope. Mirrors production provider behavior.
        if kwargs.get("onecall_json_schema"):
            return _payload(reply)
        return reply

    p.generate = AsyncMock(side_effect=_gen)
    p.provider_name = "test"
    p._model = "test-model"
    p.last_prompt_tokens = None
    p.last_generation_tokens = None
    return p


def _queued(result):
    return decide_routing(
        is_valid=bool(result.is_valid),
        score=result.quality_score,
        auto_approve_threshold=0.80,
        has_blocking_flags=bool(list(result.safety_flags) + list(result.quality_flags)),
        flags=list(result.safety_flags) + list(result.quality_flags),
        needs_handoff=bool(result.needs_handoff),
        advisory_handoff=bool(result.advisory_handoff),
    )


@pytest.mark.asyncio
async def test_pipeline_fan_leak_rails_queue():
    with patch(
        "core.one_call_pipeline.get_llm_provider",
        return_value=_provider("Hey fan, great to see you!"),
    ):
        res = await one_call_generation(
            user_id=1,
            creator_id=1,
            user_message="hello",
            persona="You are Luna.",
            profile={},
            user={"first_name": "Alex"},
        )
    assert "fan_word" in res.quality_flags
    assert res.needs_handoff is True
    assert _queued(res).action is RoutingAction.QUEUE


@pytest.mark.asyncio
async def test_pipeline_mid_speaker_rails_queue():
    with patch(
        "core.one_call_pipeline.get_llm_provider",
        return_value=_provider("Glad you asked. CHARACTER: here is the plan."),
    ):
        res = await one_call_generation(
            user_id=1,
            creator_id=1,
            user_message="hello",
            persona="You are Luna.",
            profile={},
            user={"first_name": "Alex"},
        )
    assert "speaker_prefix" in res.quality_flags
    assert res.needs_handoff is True
    assert _queued(res).action is RoutingAction.QUEUE


@pytest.mark.asyncio
async def test_pipeline_markup_rails_queue():
    with patch(
        "core.one_call_pipeline.get_llm_provider",
        return_value=_provider("[PLAYER MESSAGE] hello there friend"),
    ):
        res = await one_call_generation(
            user_id=1,
            creator_id=1,
            user_message="hello",
            persona="You are Luna.",
            profile={},
            user={"first_name": "Alex"},
        )
    assert "markup_echo" in res.quality_flags
    assert res.needs_handoff is True
    assert _queued(res).action is RoutingAction.QUEUE


@pytest.mark.asyncio
async def test_pipeline_identical_last3_rails_queue():
    history = [
        {"role": "assistant", "content": "Hey Luna, how is it going?"},
        {"role": "user", "content": "good, busy here"},
    ]
    with patch(
        "core.one_call_pipeline.get_llm_provider",
        return_value=_provider("Hey Luna, how is it going?"),
    ):
        res = await one_call_generation(
            user_id=1,
            creator_id=1,
            user_message="hey again",
            persona="You are Luna.",
            profile={},
            user={"first_name": "Alex"},
            recent_messages=history,
        )
    assert "repeat" in res.quality_flags
    assert res.needs_handoff is True
    assert _queued(res).action is RoutingAction.QUEUE


def _gid(uid, msg, tg):
    return hashlib.md5(f"{uid}:{msg}:{tg}".encode()).hexdigest()


@pytest.mark.asyncio
async def test_commerce_overwrite_echo_queued_never_auto():
    from commerce.execution import ExecutionStatus
    from commerce.selection import (
        CommerceSelectionReason,
        CommerceSelectionResult,
        CommerceSelectionStatus,
    )
    from commerce.signals import CommerceSignals
    from commerce.single_creator import SingleCreatorStatus
    from core.one_call import OneCallResult

    generation_id = _gid(7, "send it", 7)
    creator_id = 9
    mock_one = OneCallResult(
        reply="hey gorgeous, how are you?",
        signals=CommerceSignals.low_information(),
        confidence=0.9,
        needs_handoff=False,
        is_valid=True,
        validation_error=None,
        quality_score=0.9,
        quality_flags=[],
        safety_flags=[],
        provider_name="ollama",
        model_name="qwen2.5:3b",
        input_tokens=5,
        output_tokens=10,
    )
    mock_one.signals.purchase_intent = 0.9
    mock_selection = CommerceSelectionResult(
        status=CommerceSelectionStatus.USE_COMMERCE_RESPONSE,
        reason=CommerceSelectionReason.COMMERCE_COMPLETED,
        commerce_response_text="Your conversational response to the fan",
        execution_status=ExecutionStatus.EXECUTED,
        offer_active=True,
    )
    published = []

    async def fake_batch(evts):
        for ev in evts:
            published.append((ev.get("event") or ev.get("event_type"), ev.get("data")))
        return []

    telemetry = {}

    async def fake_insert(data):
        telemetry.update(data)
        return True

    mock_obs = MagicMock(
        enabled=True,
        failed=False,
        candidate_count=5,
        selected_count=3,
        dropped_count=2,
        token_count=100,
        char_count=400,
        gather_ms=10,
        total_ms=20,
        score_ms=5,
        dedup_ms=1,
        budget_ms=1,
        render_ms=1,
        rendered_text="ctx",
        pipeline_result=None,
        conflict_dropped=0,
        lexical_dedup_removed=0,
        truncation_count=0,
        budget_violations=0,
        degradation_level=0,
        category_tokens={},
        retrieval_metrics={},
    )
    mock_auth = MagicMock()
    mock_auth.recent_messages = ({"direction": "inbound", "content": "send it"},)
    mock_auth.persona = "p"
    mock_auth.persona_name = None
    mock_auth.user = {"first_name": "x", "funnel_stage": "new", "message_count": 10}
    mock_auth.profile = {}
    mock_auth.conversation_state = {"current_topic": None}
    mock_auth.commerce_context_text = ""
    mock_auth.metadata = {"acquisition_ms": 5}
    mock_auth.summary = None
    mock_creator = MagicMock()
    mock_creator.status = SingleCreatorStatus.READY
    mock_creator.creator_id = creator_id
    stack = contextlib.ExitStack()
    stack.enter_context(
        patch("workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True)
    )
    stack.enter_context(patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock))
    stack.enter_context(patch("workers.llm_worker.upsert_user", new_callable=AsyncMock))
    stack.enter_context(
        patch(
            "workers.llm_worker.is_user_auto_reply_excluded",
            new_callable=AsyncMock,
            return_value=False,
        )
    )
    stack.enter_context(
        patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[])
    )
    stack.enter_context(
        patch(
            "context_engine.authoritative_assembly.assemble_authoritative_context",
            new_callable=AsyncMock,
            return_value=mock_auth,
        )
    )
    stack.enter_context(
        patch(
            "context_engine.worker_integration.observe_context_engine",
            new_callable=AsyncMock,
            return_value=mock_obs,
        )
    )
    stack.enter_context(patch("core.event_bus.publish_events_batch", side_effect=fake_batch))
    stack.enter_context(
        patch("workers.llm_worker.is_auto_reply_enabled", new_callable=AsyncMock, return_value=True)
    )
    mock_enqueue = stack.enter_context(
        patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock, return_value="sid")
    )
    mock_queue = stack.enter_context(
        patch("workers.llm_worker.add_to_operator_queue", new_callable=AsyncMock, return_value=1)
    )
    # Phase 3.3 turn-gate uses real Redis: claim the turn so a stale
    # turn_send key from an earlier run cannot suppress this test's turn
    # (same precedent as test_p33_14_4_single_outbound.py:357).
    stack.enter_context(
        patch("db.redis.try_claim_turn_send", new=AsyncMock(return_value=True))
    )
    stack.enter_context(patch("workers.llm_worker.post_process", new_callable=AsyncMock))
    stack.enter_context(
        patch(
            "workers.llm_worker._try_commerce_draft",
            new_callable=AsyncMock,
            return_value=mock_selection,
        )
    )
    stack.enter_context(
        patch(
            "core.one_call_pipeline.one_call_pipeline_with_fallback",
            new_callable=AsyncMock,
            return_value=mock_one,
        )
    )
    stack.enter_context(patch("db.postgres.insert_generation_telemetry", side_effect=fake_insert))
    stack.enter_context(
        patch(
            "commerce.single_creator.resolve_single_application_creator",
            new_callable=AsyncMock,
            return_value=mock_creator,
        )
    )
    stack.enter_context(
        patch(
            "memory.creator_persona.get_structured_persona_async",
            new_callable=AsyncMock,
            return_value=None,
        )
    )
    stack.enter_context(
        patch(
            "workers.llm_worker._settings",
            MagicMock(
                llm_path="new",
                context_engine_enabled=True,
                context_engine_observational=True,
                context_engine_sample_rate=1.0,
                user_lock_ttl=60,
                auto_approve_threshold=0.80,
                llm_provider="ollama",
                ollama_model="qwen2.5:3b",
            ),
        )
    )
    try:
        from workers.llm_worker import process_message

        await process_message(
            user_id=7,
            user_message="send it",
            telegram_message_id=7,
            username="u",
            first_name="f",
            persona="p",
            generation_id=generation_id,
        )
        await __import__("asyncio").sleep(0.05)
        # Post-overwrite rails verdict survived to telemetry; turn queued, never auto-sent.
        assert "prompt_echo" in telemetry.get("scoring_flags", [])
        assert mock_queue.await_count >= 1
        for call in mock_enqueue.await_calls:
            assert call.args[0].get("was_auto_approved") is not True
        completed = [d for e, d in published if e == "ai.generation_completed"]
        assert completed and all(c.get("was_auto_approved") is not True for c in completed)
    finally:
        stack.close()
