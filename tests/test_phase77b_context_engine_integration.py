"""Phase 77B regression tests — Context Engine + OneCall production integration.

Covers:
- build_one_call_context accepts retrieved_context and inserts as system message
- one_call_generation passes retrieved_context through to context builder
- ContextEngineObservation carries rendered_text for production use
- publish_events_batch backward compat (event vs event_type)
- commerce signals from OneCall reach deterministic commerce (signals param)
- OneCall hardening: invalid/exception goes to operator queue, not legacy fallback
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Context compaction — retrieved_context
# ---------------------------------------------------------------------------

class TestRetrievedContextCompaction:
    def test_retrieved_context_inserted_as_system_message(self):
        from core.context_compact import build_one_call_context
        msgs = build_one_call_context(
            user={"first_name": "Alex", "funnel_stage": "new"},
            profile={},
            persona="You are a helpful assistant.",
            recent_messages=[{"direction": "inbound", "content": "hi"}],
            retrieved_context="MEMORY: Alex likes hiking",
        )
        # Should contain system prompt + state + retrieved_context + conversation
        system_contents = [m["content"] for m in msgs if m["role"] == "system"]
        assert any("MEMORY" in c for c in system_contents)

    def test_empty_retrieved_context_not_inserted(self):
        from core.context_compact import build_one_call_context
        msgs_empty = build_one_call_context(
            user={"first_name": "Alex", "funnel_stage": "new"},
            profile={},
            persona="You are helpful.",
            recent_messages=[{"direction": "inbound", "content": "hi"}],
            retrieved_context="",
        )
        msgs_none = build_one_call_context(
            user={"first_name": "Alex", "funnel_stage": "new"},
            profile={},
            persona="You are helpful.",
            recent_messages=[{"direction": "inbound", "content": "hi"}],
            retrieved_context="   ",
        )
        # Both should have same number of messages (no extra system message)
        assert len(msgs_empty) == len(msgs_none)

    def test_retrieved_context_backward_compat(self):
        """Existing callers without retrieved_context still work."""
        from core.context_compact import build_one_call_context
        msgs = build_one_call_context(
            user={"first_name": "Alex", "funnel_stage": "new"},
            profile={},
            persona="You are helpful.",
            recent_messages=[{"direction": "inbound", "content": "hi"}],
        )
        assert len(msgs) >= 2


# ---------------------------------------------------------------------------
# OneCall pipeline — retrieved_context passthrough
# ---------------------------------------------------------------------------

class TestOneCallRetrievedContextPassthrough:
    @pytest.mark.asyncio
    async def test_one_call_generation_accepts_retrieved_context(self):
        """one_call_generation signature accepts retrieved_context without error."""
        from core.one_call_pipeline import one_call_generation
        import inspect
        sig = inspect.signature(one_call_generation)
        assert "retrieved_context" in sig.parameters

    @pytest.mark.asyncio
    async def test_one_call_pipeline_with_fallback_accepts_retrieved_context(self):
        from core.one_call_pipeline import one_call_pipeline_with_fallback
        # Should accept via **kwargs without TypeError
        mock_result = MagicMock(is_valid=True, reply="hi", quality_score=0.9, safety_flags=[], quality_flags=[], signals=None, validation_error=None)
        with patch("core.one_call_pipeline.one_call_generation", new_callable=AsyncMock, return_value=mock_result) as mock_gen:
            result = await one_call_pipeline_with_fallback(
                user_id=1, creator_id=1, user_message="hi",
                persona="p", profile={}, user={},
                retrieved_context="MEMORY: test",
            )
            # Either returns mock_result or fallback; should not raise
            assert result is not None
            # Verify retrieved_context was passed through
            if mock_gen.called:
                assert mock_gen.call_args.kwargs.get("retrieved_context") == "MEMORY: test"


# ---------------------------------------------------------------------------
# ContextEngineObservation — rendered_text
# ---------------------------------------------------------------------------

class TestContextEngineObservationRenderedText:
    def test_observation_has_rendered_text_field(self):
        from context_engine.worker_integration import ContextEngineObservation
        obs = ContextEngineObservation(enabled=True, rendered_text="MEMORY: test")
        assert obs.rendered_text == "MEMORY: test"

    def test_observation_default_rendered_text_empty(self):
        from context_engine.worker_integration import ContextEngineObservation
        obs = ContextEngineObservation(enabled=False)
        assert obs.rendered_text == ""

    @pytest.mark.asyncio
    async def test_observe_context_engine_populates_rendered_text(self):
        """When Context Engine succeeds, rendered_text should contain memory/commerce blocks."""
        from context_engine.worker_integration import observe_context_engine
        from context_engine.integration import ContextPipelineResult
        from context_engine.models import ContextSnapshot, ContextCategory
        # Build a minimal pipeline result with rendered blocks
        mock_rendered = MagicMock()
        mock_rendered.system_prompt = "sys"
        mock_rendered.state_block = "state"
        mock_rendered.commerce_block = "COMMERCE: offer available"
        mock_rendered.memory_block = "MEMORY: fan likes hiking"
        mock_rendered.temporal_block = "TEMPORAL: morning"
        mock_rendered.content_block = "CONTENT: vault item"

        mock_result = MagicMock(spec=ContextPipelineResult)
        mock_result.rendered = mock_rendered
        mock_result.gather_time_ms = 5.0
        mock_result.assembly_time_ms = 2.0
        mock_result.candidate_count = 10
        mock_result.selected_count = 4
        mock_result.total_tokens = 100

        mock_snapshot = MagicMock(spec=ContextSnapshot)

        with patch("context_engine.integration.ContextEngineIntegration") as MockIntegration:
            inst = MockIntegration.return_value
            inst.process = AsyncMock(return_value=mock_result)
            obs = await observe_context_engine(
                user_id=1, creator_id=1, user_message="hi", generation_id="gid", enabled=True
            )
            assert obs.enabled is True
            assert obs.rendered_text != ""
            assert "MEMORY" in obs.rendered_text or "COMMERCE" in obs.rendered_text
            assert obs.failed is False


# ---------------------------------------------------------------------------
# Event bus — backward compat
# ---------------------------------------------------------------------------

class TestEventBusBackwardCompat:
    @pytest.mark.asyncio
    async def test_publish_events_batch_accepts_event_key(self):
        """publish_events_batch should accept legacy 'event' key."""
        from core.event_bus import publish_events_batch
        from unittest.mock import AsyncMock, MagicMock, patch as mpatch
        # Force enable_websocket True and mock redis
        with mpatch("core.event_bus.get_settings") as mock_settings:
            mock_settings.return_value.enable_websocket = True
            mock_pipe = MagicMock()
            mock_pipe.publish = MagicMock()
            mock_pipe.execute = AsyncMock(return_value=[1,1])
            mock_redis = AsyncMock()
            mock_redis.pipeline = MagicMock(return_value=mock_pipe)
            with mpatch("db.redis.get_redis", new_callable=AsyncMock, return_value=mock_redis):
                ids = await publish_events_batch([
                    {"event": "ai.generation_completed", "data": {"x": 1}, "user_id": 1, "generation_id": "gid", "scope": "user"},
                    {"event_type": "suggestion.created", "data": {"y": 2}, "user_id": 1, "generation_id": "gid", "scope": "user"},
                ])
                assert len(ids) == 2
                # Should have published 2 events via pipeline
                assert mock_pipe.publish.call_count == 2

    @pytest.mark.asyncio
    async def test_publish_events_batch_disabled_returns_nones(self):
        from core.event_bus import publish_events_batch
        with patch("core.event_bus.get_settings") as mock_s:
            mock_s.return_value.enable_websocket = False
            ids = await publish_events_batch([{"event": "x", "data": {}}])
            assert ids == [None]


# ---------------------------------------------------------------------------
# OneCall hardening — no legacy cascade
# ---------------------------------------------------------------------------

class TestOneCallHardening:
    @pytest.mark.asyncio
    async def test_invalid_onecall_routes_to_operator_queue_not_legacy(self):
        """When OneCall returns invalid, should publish generation_completed and NOT call legacy generate_draft."""
        from unittest.mock import AsyncMock
        from core.one_call import OneCallResult
        from commerce.signals import CommerceSignals

        invalid_result = OneCallResult(
            reply="", signals=CommerceSignals.low_information(),
            confidence=0.0, needs_handoff=True, is_valid=False,
            validation_error="Schema validation failed", quality_score=0.0,
        )

        published = []

        async def mock_publish(event_type, data, **kw):
            published.append(event_type)
            return "eid"

        # Track if legacy generate_draft was called
        legacy_called = {"v": False}
        async def mock_generate_draft(*a, **kw):
            legacy_called["v"] = True
            return "legacy draft"

        with (
            patch("workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch("workers.llm_worker.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch("workers.llm_worker.generate_draft", side_effect=mock_generate_draft),
            patch("workers.llm_worker.is_auto_reply_enabled", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.add_to_operator_queue", new_callable=AsyncMock, return_value=123),
            patch("core.one_call_pipeline.one_call_pipeline_with_fallback", new_callable=AsyncMock, return_value=invalid_result),
            patch("core.event_bus.publish_event", side_effect=mock_publish),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
        ):
            from workers.llm_worker import process_message
            await process_message(user_id=99, user_message="hi", telegram_message_id=1, username="u", first_name="f", persona="p")
            # Should have published generation_completed via hardened path
            assert "ai.generation_completed" in published
            # Should NOT have called legacy generate_draft (hardened)
            assert legacy_called["v"] is False

    @pytest.mark.asyncio
    async def test_exception_onecall_routes_to_operator_queue(self):
        """When OneCall raises exception, should publish generation_completed."""
        from unittest.mock import AsyncMock

        async def raise_exc(*a, **kw):
            raise RuntimeError("LLM down")

        published = []

        async def mock_publish(event_type, data, **kw):
            published.append(event_type)
            return "eid"

        with (
            patch("workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch("workers.llm_worker.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch("workers.llm_worker.is_auto_reply_enabled", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.add_to_operator_queue", new_callable=AsyncMock, return_value=123),
            patch("core.one_call_pipeline.one_call_pipeline_with_fallback", side_effect=raise_exc),
            patch("core.event_bus.publish_event", side_effect=mock_publish),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
        ):
            from workers.llm_worker import process_message
            await process_message(user_id=99, user_message="hi", telegram_message_id=1, username="u", first_name="f", persona="p")
            assert "ai.generation_completed" in published


# ---------------------------------------------------------------------------
# Commerce signals — OneCall signals reach deterministic commerce
# ---------------------------------------------------------------------------

class TestOneCallCommerceSignalsReachDeterministic:
    @pytest.mark.asyncio
    async def test_onecall_signals_passed_to_try_commerce_draft(self):
        """OneCall's signals should be passed to _try_commerce_draft to avoid duplicate LLM call."""
        from unittest.mock import AsyncMock
        from core.one_call import OneCallResult
        from commerce.signals import CommerceSignals

        sig = CommerceSignals.low_information()
        # Set a purchase intent to trigger commerce
        sig.purchase_intent = 0.8

        mock_result = OneCallResult(
            reply="hello world this is a valid reply with enough words to pass quality heuristics",
            signals=sig, confidence=0.9, needs_handoff=False,
            is_valid=True, validation_error=None, quality_score=0.9,
        )

        commerce_called_with_signals = {}

        async def mock_try_commerce(user_id, context, persona, signals=None):
            commerce_called_with_signals["signals"] = signals
            return None  # No commerce draft

        with (
            patch("workers.llm_worker.acquire_user_lock", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.release_user_lock", new_callable=AsyncMock),
            patch("workers.llm_worker.upsert_user", new_callable=AsyncMock),
            patch("workers.llm_worker.is_user_auto_reply_excluded", new_callable=AsyncMock, return_value=False),
            patch("workers.llm_worker.build_qwen3_context", new_callable=AsyncMock, return_value=[]),
            patch("workers.llm_worker.is_auto_reply_enabled", new_callable=AsyncMock, return_value=True),
            patch("workers.llm_worker.enqueue_send", new_callable=AsyncMock),
            patch("workers.llm_worker.post_process", new_callable=AsyncMock),
            patch("core.event_bus.publish_event", new_callable=AsyncMock),
            patch("core.one_call_pipeline.one_call_pipeline_with_fallback", new_callable=AsyncMock, return_value=mock_result),
            patch("workers.llm_worker._try_commerce_draft", side_effect=mock_try_commerce),
        ):
            from workers.llm_worker import process_message
            await process_message(user_id=98, user_message="I want to buy", telegram_message_id=1, username="u", first_name="f", persona="p")
            # Should have called _try_commerce_draft with OneCall signals
            assert "signals" in commerce_called_with_signals
            assert commerce_called_with_signals["signals"] is sig
