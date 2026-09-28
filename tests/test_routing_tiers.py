"""Phase 1.4 routing tiers: HARD veto vs INFO penalty-only."""

import pytest

from core.routing import HARD_QUALITY_FLAGS, RoutingAction, decide_routing


def test_hard_prompt_echo_vetoes_high_score():
    d = decide_routing(is_valid=True, score=0.95, flags=["prompt_echo"])
    assert d.action is RoutingAction.QUEUE
    assert d.reason == "blocking_flags"


def test_hard_safety_names_veto():
    for flag in ["price_mention", "distress_signal", "photo_promise"]:
        assert flag in HARD_QUALITY_FLAGS
        d = decide_routing(is_valid=True, score=0.95, flags=[flag])
        assert d.action is RoutingAction.QUEUE
        assert d.reason == "blocking_flags"


def test_hard_commercial_cta_vetoes():
    assert "unauthorized_commercial_cta" in HARD_QUALITY_FLAGS
    d = decide_routing(is_valid=True, score=0.95, flags=["unauthorized_commercial_cta"])
    assert d.action is RoutingAction.QUEUE
    assert d.reason == "blocking_flags"


def test_unknown_flag_fail_closed():
    d = decide_routing(is_valid=True, score=0.95, flags=["some_future_flag"])
    assert d.action is RoutingAction.QUEUE


def test_info_high_score_auto_sends():
    for flag in ["speaker_prefix_leak", "markup_echo", "short_reply", "fan_word", "repeat"]:
        assert flag not in HARD_QUALITY_FLAGS
        d = decide_routing(is_valid=True, score=0.95, flags=[flag])
        assert d.action is RoutingAction.AUTO_SEND
        assert d.reason == "approved"


def test_info_low_score_below_threshold():
    d = decide_routing(is_valid=True, score=0.4, flags=["speaker_prefix_leak"])
    assert d.action is RoutingAction.QUEUE
    assert d.reason == "below_threshold"


def test_hard_plus_info_still_vetoes():
    d = decide_routing(is_valid=True, score=0.95, flags=["speaker_prefix_leak", "prompt_echo"])
    assert d.action is RoutingAction.QUEUE
    assert d.reason == "blocking_flags"


def test_legacy_bool_without_list_keeps_veto():
    d = decide_routing(is_valid=True, score=0.95, has_blocking_flags=True)
    assert d.action is RoutingAction.QUEUE
    assert d.reason == "blocking_flags"


def test_legacy_false_no_flags_sends():
    d = decide_routing(is_valid=True, score=0.95)
    assert d.action is RoutingAction.AUTO_SEND


def test_handoff_policy_untouched():
    d = decide_routing(is_valid=True, score=0.95, needs_handoff=True, advisory_handoff=False)
    assert d.action is RoutingAction.QUEUE
    assert d.reason == "corroborated_handoff"
    assert d.corroborated_handoff is True
    d2 = decide_routing(is_valid=True, score=0.95, needs_handoff=True, advisory_handoff=True)
    assert d2.action is RoutingAction.AUTO_SEND


@pytest.mark.asyncio
async def test_gate_info_high_score_auto_sends():
    """End-to-end gate proof: info-only high-score draft auto-sends."""
    import contextlib
    import hashlib
    from unittest.mock import AsyncMock, MagicMock, patch

    from commerce.signals import CommerceSignals
    from commerce.single_creator import SingleCreatorStatus
    from core.one_call import OneCallResult

    def _gid(uid, msg, tg):
        return hashlib.md5(f"{uid}:{msg}:{tg}".encode()).hexdigest()

    generation_id = _gid(21, "hello there", 21)
    mock_one = OneCallResult(
        reply="Hey there, how was your day?",
        signals=CommerceSignals.low_information(),
        confidence=0.9,
        needs_handoff=False,
        advisory_handoff=False,
        is_valid=True,
        validation_error=None,
        quality_score=0.9,
        quality_flags=["speaker_prefix_leak"],
        safety_flags=[],
        provider_name="ollama",
        model_name="qwen2.5:3b",
    )
    mock_obs = MagicMock(
        enabled=True,
        failed=False,
        candidate_count=0,
        selected_count=0,
        dropped_count=0,
        token_count=0,
        char_count=0,
        gather_ms=0,
        total_ms=0,
        score_ms=0,
        dedup_ms=0,
        budget_ms=0,
        render_ms=0,
        rendered_text="",
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
    mock_auth.recent_messages = ()
    mock_auth.persona = "p"
    mock_auth.persona_name = None
    mock_auth.user = {"first_name": "x", "funnel_stage": "new", "message_count": 1}
    mock_auth.profile = {}
    mock_auth.conversation_state = None
    mock_auth.commerce_context_text = ""
    mock_auth.metadata = {"acquisition_ms": 1}
    mock_auth.summary = None
    mock_creator = MagicMock()
    mock_creator.status = SingleCreatorStatus.READY
    mock_creator.creator_id = 3
    sent = {}
    queued = {}

    async def fake_enqueue(data, dedup_id=None, generation_id=None, creator_id=None):
        sent.update(data)
        return "sid"

    async def fake_queue(**kwargs):
        queued.update(kwargs)
        return 9

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
    stack.enter_context(
        patch("core.event_bus.publish_events_batch", new_callable=AsyncMock, return_value=[])
    )
    stack.enter_context(
        patch("workers.llm_worker.is_auto_reply_enabled", new_callable=AsyncMock, return_value=True)
    )
    stack.enter_context(patch("workers.llm_worker.enqueue_send", side_effect=fake_enqueue))
    stack.enter_context(patch("workers.llm_worker.add_to_operator_queue", side_effect=fake_queue))
    # Phase 3.3 turn-gate uses real Redis: claim the turn so a stale
    # turn_send key from an earlier run cannot suppress this test's turn
    # (same precedent as test_p33_14_4_single_outbound.py:357).
    stack.enter_context(
        patch("db.redis.try_claim_turn_send", new=AsyncMock(return_value=True))
    )
    stack.enter_context(patch("workers.llm_worker.post_process", new_callable=AsyncMock))
    stack.enter_context(
        patch("workers.llm_worker._try_commerce_draft", new_callable=AsyncMock, return_value=None)
    )
    stack.enter_context(
        patch(
            "core.one_call_pipeline.one_call_pipeline_with_fallback",
            new_callable=AsyncMock,
            return_value=mock_one,
        )
    )
    stack.enter_context(
        patch("db.postgres.insert_generation_telemetry", new_callable=AsyncMock, return_value=True)
    )
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
            user_id=21,
            user_message="hello there",
            telegram_message_id=21,
            username="u",
            first_name="f",
            persona="p",
            generation_id=generation_id,
        )
        assert sent.get("was_auto_approved") is True
        assert queued == {}
    finally:
        stack.close()
