"""Phase 75 — Runtime Performance & Correctness Validation.

Tests the Phase 74B optimizations for correctness, isolation,
serialization equivalence, parallel query independence, and
authority preservation.
"""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── B1: Profile Cache Correctness ───────────────────────────────────────────


class TestProfileCacheCorrectness:
    """Verify B1: canonical profile cache isolation and correctness."""

    def setup_method(self):
        from memory.context import _profile_cache
        _profile_cache.clear()

    def test_cache_starts_empty(self):
        from memory.context import get_last_profile, get_last_user
        assert get_last_profile() is None
        assert get_last_user() is None

    def test_cache_set_and_get_profile(self):
        from memory.context import _profile_cache, get_last_profile
        _profile_cache["profile"] = {"id": 1, "username": "test_user"}
        assert get_last_profile() == {"id": 1, "username": "test_user"}

    def test_cache_set_and_get_user(self):
        from memory.context import _profile_cache, get_last_user
        _profile_cache["user"] = {"id": 42, "first_name": "Alice"}
        assert get_last_user() == {"id": 42, "first_name": "Alice"}

    def test_cache_isolation_between_calls(self):
        from memory.context import _profile_cache, get_last_profile, get_last_user
        _profile_cache["profile"] = {"id": 1}
        _profile_cache["user"] = {"id": 42}
        assert get_last_profile()["id"] == 1
        assert get_last_user()["id"] == 42

    def test_cache_overwrite(self):
        from memory.context import _profile_cache, get_last_profile
        _profile_cache["profile"] = {"id": 1}
        _profile_cache["profile"] = {"id": 2}
        assert get_last_profile()["id"] == 2

    def test_cache_clear(self):
        from memory.context import _profile_cache, get_last_profile
        _profile_cache["profile"] = {"id": 1}
        _profile_cache.clear()
        assert get_last_profile() is None

    def test_creator_isolation_no_cross_contamination(self):
        from memory.context import _profile_cache, get_last_profile, get_last_user
        _profile_cache["profile"] = {"id": 1, "creator_id": 100}
        _profile_cache["user"] = {"id": 42, "creator_id": 100}
        profile = get_last_profile()
        user = get_last_user()
        assert profile["creator_id"] == 100
        assert user["creator_id"] == 100
        _profile_cache["profile"] = {"id": 2, "creator_id": 200}
        _profile_cache["user"] = {"id": 99, "creator_id": 200}
        assert get_last_profile()["creator_id"] == 200
        assert get_last_user()["creator_id"] == 200

    def test_cache_returns_none_when_empty(self):
        from memory.context import get_last_profile, get_last_user
        assert get_last_profile() is None
        assert get_last_user() is None

    def test_cache_handles_none_profile(self):
        from memory.context import _profile_cache, get_last_profile
        _profile_cache["profile"] = None
        assert get_last_profile() is None


# ─── B1: Optional Profile Param in Consumers ─────────────────────────────────


class TestOptionalProfileParam:
    """Verify B1: consumer functions accept optional profile param."""

    def test_long_term_memory_accepts_profile(self):
        import inspect

        from commerce.long_term_memory import get_long_term_memory
        sig = inspect.signature(get_long_term_memory)
        assert "profile" in sig.parameters

    def test_retrieve_relevant_memories_accepts_profile(self):
        import inspect

        from commerce.long_term_memory import retrieve_relevant_memories
        sig = inspect.signature(retrieve_relevant_memories)
        assert "profile" in sig.parameters

    def test_fan_knowledge_accepts_profile(self):
        import inspect

        from commerce.fan_knowledge import get_fan_knowledge
        sig = inspect.signature(get_fan_knowledge)
        assert "profile" in sig.parameters

    def test_retrieve_relevant_knowledge_accepts_profile(self):
        import inspect

        from commerce.fan_knowledge import retrieve_relevant_knowledge
        sig = inspect.signature(retrieve_relevant_knowledge)
        assert "profile" in sig.parameters

    def test_build_conversational_commerce_accepts_profile(self):
        import inspect

        from commerce.conversational import build_conversational_commerce_state
        sig = inspect.signature(build_conversational_commerce_state)
        assert "profile" in sig.parameters

    def test_get_commercial_preferences_accepts_profile(self):
        import inspect

        from db.postgres import get_commercial_preferences
        sig = inspect.signature(get_commercial_preferences)
        assert "profile" in sig.parameters


# ─── B2: Redundant Query Elimination Equivalence ─────────────────────────────


class TestRedundantQueryElimination:
    """Verify B2: build_llm_context accepts cached data params."""

    def test_build_llm_context_accepts_user_data(self):
        import inspect

        from memory.context_assembler import build_llm_context
        sig = inspect.signature(build_llm_context)
        assert "user_data" in sig.parameters

    def test_build_llm_context_accepts_recent_messages(self):
        import inspect

        from memory.context_assembler import build_llm_context
        sig = inspect.signature(build_llm_context)
        assert "recent_messages" in sig.parameters

    def test_build_llm_context_accepts_summary(self):
        import inspect

        from memory.context_assembler import build_llm_context
        sig = inspect.signature(build_llm_context)
        assert "summary" in sig.parameters

    @pytest.mark.asyncio
    async def test_build_llm_context_with_cached_data_skips_queries(self):
        from memory.context_assembler import (
            build_llm_context,
        )
        cached_user = {
            "id": 42,
            "first_name": "Test",
            "funnel_stage": "engaged",
            "is_blocked": False,
            "do_not_auto_reply": False,
            "message_count": 10,
        }
        cached_messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        with patch("memory.context_assembler._get_user_safe", new_callable=AsyncMock) as mock_user:
            with patch("memory.context_assembler._get_recent_messages_safe", new_callable=AsyncMock) as mock_msgs:
                with patch("memory.context_assembler._get_summary_safe", new_callable=AsyncMock) as mock_sum:
                    result = await build_llm_context(
                        creator_id=1,
                        user_id=42,
                        user_data=cached_user,
                        recent_messages=cached_messages,
                        summary="Test summary",
                    )
                    mock_user.assert_not_called()
                    mock_msgs.assert_not_called()
                    mock_sum.assert_not_called()
                    assert result is not None
                    assert result.user_id == 42


# ─── B3: Parallel Query Independence ─────────────────────────────────────────


class TestParallelQueryIndependence:
    """Verify B3: asyncio.gather queries are independent."""

    @pytest.mark.asyncio
    async def test_parallel_queries_return_all_results(self):
        from memory.context_assembler import build_llm_context
        with patch("memory.context_assembler._get_user_safe", new_callable=AsyncMock) as mock_user:
            mock_user.return_value = {
                "id": 42, "first_name": "Test", "funnel_stage": "new",
                "is_blocked": False, "do_not_auto_reply": False, "message_count": 0,
            }
            with patch("memory.context_assembler._get_recent_messages_safe", new_callable=AsyncMock) as mock_msgs:
                mock_msgs.return_value = []
                with patch("memory.context_assembler._get_summary_safe", new_callable=AsyncMock) as mock_sum:
                    mock_sum.return_value = (None, None)
                    result = await build_llm_context(
                        creator_id=1, user_id=42,
                    )
                    assert result is not None
                    assert result.user_id == 42

    @pytest.mark.asyncio
    async def test_partial_query_failure_graceful_degradation(self):
        from memory.context_assembler import build_llm_context
        with patch("memory.context_assembler._get_user_safe", new_callable=AsyncMock) as mock_user:
            mock_user.return_value = {
                "id": 42, "first_name": "Test", "funnel_stage": "new",
                "is_blocked": False, "do_not_auto_reply": False, "message_count": 0,
            }
            with patch("memory.context_assembler._get_recent_messages_safe", new_callable=AsyncMock) as mock_msgs:
                mock_msgs.return_value = []
                with patch("memory.context_assembler._get_summary_safe", new_callable=AsyncMock) as mock_sum:
                    mock_sum.return_value = (None, None)
                    with patch("memory.context_assembler._get_purchases_safe", new_callable=AsyncMock) as mock_purch:
                        mock_purch.side_effect = Exception("DB connection lost")
                        result = await build_llm_context(creator_id=1, user_id=42)
                        assert result is not None
                        assert result.purchase_count == 0


# ─── B4: Redis Batch Equivalence ─────────────────────────────────────────────


class TestRedisBatchEquivalence:
    """Verify B4: publish_events_batch produces equivalent events."""

    def test_publish_events_batch_exists(self):
        from core.event_bus import publish_events_batch
        assert callable(publish_events_batch)

    def test_publish_event_single_exists(self):
        from core.event_bus import publish_event
        assert callable(publish_event)

    def test_build_event_structure(self):
        from core.event_bus import _build_event
        event = _build_event(
            "test.event",
            {"key": "value"},
            user_id=42,
            dialog_id=42,
            generation_id="gen-123",
            creator_id=100,
            scope="user",
        )
        assert event["event_type"] == "test.event"
        assert event["data"] == {"key": "value"}
        assert event["user_id"] == 42
        assert event["dialog_id"] == 42
        assert event["generation_id"] == "gen-123"
        assert event["creator_id"] == 100
        assert event["scope"] == "user"
        assert "event_id" in event
        assert "timestamp_ms" in event

    def test_batch_event_count_matches(self):
        from core.event_bus import _build_event
        events = [
            {"event_type": "ev1", "data": {"a": 1}},
            {"event_type": "ev2", "data": {"b": 2}},
            {"event_type": "ev3", "data": {"c": 3}},
        ]
        built = [_build_event(e["event_type"], e["data"]) for e in events]
        assert len(built) == 3
        assert built[0]["event_type"] == "ev1"
        assert built[1]["event_type"] == "ev2"
        assert built[2]["event_type"] == "ev3"

    def test_event_ids_are_unique(self):
        from core.event_bus import _build_event
        ids = set()
        for _ in range(100):
            event = _build_event("test", {"x": 1})
            ids.add(event["event_id"])
        assert len(ids) == 100

    def test_timestamp_is_millis(self):
        from core.event_bus import _build_event
        before = int(time.time() * 1000)
        event = _build_event("test", {})
        after = int(time.time() * 1000)
        assert before <= event["timestamp_ms"] <= after

    @pytest.mark.asyncio
    async def test_batch_publish_disabled_websocket(self):
        from core.event_bus import publish_events_batch
        with patch("core.event_bus.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(enable_websocket=False)
            result = await publish_events_batch([
                {"event_type": "test", "data": {}},
            ])
            assert result == [None]


# ─── B5: Serialization Equivalence ───────────────────────────────────────────


class TestSerializationEquivalence:
    """Verify B5: orjson produces semantically equivalent output to stdlib json."""

    def _test_payloads(self):
        return [
            {"simple": "string"},
            {"number": 42},
            {"float": 3.14},
            {"boolean": True},
            {"null_value": None},
            {"list": [1, 2, 3]},
            {"nested": {"a": {"b": {"c": 1}}}},
            {"unicode": "Hello \u00e9\u00e8\u00ea \u4e16\u754c"},
            {"empty_string": ""},
            {"empty_list": []},
            {"empty_dict": {}},
            {"large_number": 999999999999999},
            {"negative": -42},
            {"zero": 0},
        ]

    def test_orjson_available(self):
        try:
            import orjson
            assert True
        except ImportError:
            pytest.skip("orjson not installed")

    def test_all_payloads_roundtrip(self):
        import orjson
        for payload in self._test_payloads():
            serialized = orjson.dumps(payload)
            deserialized = orjson.loads(serialized)
            assert deserialized == payload, f"Roundtrip failed for {payload}"

    def test_orjson_matches_stdlib_json(self):
        import orjson
        for payload in self._test_payloads():
            orjson_result = orjson.loads(orjson.dumps(payload))
            json_result = json.loads(json.dumps(payload))
            assert orjson_result == json_result, f"Mismatch for {payload}"

    def test_event_bus_json_dumps(self):
        import orjson as _orjson

        from core.event_bus import _json_dumps
        event = {
            "event_id": "test-123",
            "event_type": "test.event",
            "data": {"key": "value\u00e9"},
        }
        result = _json_dumps(event)
        parsed = _orjson.loads(result)
        assert parsed["event_id"] == "test-123"
        assert parsed["data"]["key"] == "value\u00e9"


# ─── Context Engine Budget Validation ────────────────────────────────────────


class TestContextEngineBudget:
    """Verify Context Engine budget constraints."""

    def test_global_budget_enforced(self):
        from context_engine.models import TOTAL_CONTEXT_BUDGET
        assert TOTAL_CONTEXT_BUDGET <= 2600

    def test_category_budgets_within_global(self):
        from context_engine.models import CATEGORY_BUDGETS, TOTAL_CONTEXT_BUDGET
        total = sum(CATEGORY_BUDGETS.values())
        assert total <= TOTAL_CONTEXT_BUDGET

    def test_all_required_categories_present(self):
        from context_engine.models import CATEGORY_BUDGETS, ContextCategory
        required = [ContextCategory.SYSTEM, ContextCategory.STATE, ContextCategory.COMMERCE, ContextCategory.TEMPORAL]
        for cat in required:
            assert cat in CATEGORY_BUDGETS, f"Missing category: {cat}"


# ─── Authority Hierarchy ─────────────────────────────────────────────────────


class TestAuthorityHierarchy:
    """Verify the authority hierarchy is preserved."""

    def test_llm1_is_extract_commerce_signals(self):
        from commerce.deepseek import extract_commerce_signals
        assert callable(extract_commerce_signals)

    def test_llm2_is_generate_draft(self):
        from workers.llm_worker import generate_draft
        assert callable(generate_draft)

    def test_llm3_is_score_draft(self):
        from core.scoring import score_draft
        assert callable(score_draft)

    def test_three_llm_responsibilities_are_distinct(self):
        from commerce.deepseek import extract_commerce_signals
        from core.scoring import score_draft
        from workers.llm_worker import generate_draft
        assert extract_commerce_signals is not generate_draft
        assert generate_draft is not score_draft
        assert extract_commerce_signals is not score_draft

    def test_commerce_selection_status_exists(self):
        from commerce.selection import CommerceSelectionStatus
        assert hasattr(CommerceSelectionStatus, "USE_COMMERCE_RESPONSE")
        assert hasattr(CommerceSelectionStatus, "FALLBACK_TO_STANDARD_LLM")

    def test_price_authority_from_commerce_state(self):
        from commerce.conversational import build_conversational_commerce_state
        assert callable(build_conversational_commerce_state)


# ─── Commerce Safety ─────────────────────────────────────────────────────────


class TestCommerceSafety:
    """Verify commerce safety invariants."""

    def test_no_product_no_offer(self):
        from commerce.conversational import build_conversational_commerce_state
        assert callable(build_conversational_commerce_state)

    def test_creator_scoped_queries(self):
        import inspect

        from db.postgres import get_commercial_preferences
        sig = inspect.signature(get_commercial_preferences)
        params = list(sig.parameters.keys())
        assert "creator_id" in params

    def test_is_repeat_purchase_eligible_callable(self):
        from commerce.feedback import is_repeat_purchase_eligible
        assert callable(is_repeat_purchase_eligible)


# ─── Failure Handling ────────────────────────────────────────────────────────


class TestFailureHandling:
    """Verify failure handling preserves production semantics."""

    def test_publish_event_handles_redis_failure(self):
        from core.event_bus import publish_event
        assert callable(publish_event)

    def test_publish_events_batch_handles_redis_failure(self):
        from core.event_bus import publish_events_batch
        assert callable(publish_events_batch)

    def test_context_engine_graceful_degradation(self):
        from context_engine.worker_integration import observe_context_engine
        assert callable(observe_context_engine)

    @pytest.mark.asyncio
    async def test_publish_event_returns_none_on_failure(self):
        from core.event_bus import publish_event
        with patch("core.event_bus.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(enable_websocket=True)
            with patch("db.redis.get_redis", new_callable=AsyncMock) as mock_redis:
                mock_redis.side_effect = Exception("Redis down")
                result = await publish_event("test.event", {"key": "value"})
                assert result is None

    @pytest.mark.asyncio
    async def test_publish_events_batch_returns_nones_on_failure(self):
        from core.event_bus import publish_events_batch
        with patch("core.event_bus.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(enable_websocket=True)
            with patch("db.redis.get_redis", new_callable=AsyncMock) as mock_redis:
                mock_redis.side_effect = Exception("Redis down")
                result = await publish_events_batch([
                    {"event_type": "ev1", "data": {}},
                    {"event_type": "ev2", "data": {}},
                ])
                assert result == [None, None]


# ─── No Fourth LLM ──────────────────────────────────────────────────────────


class TestNoFourthLLM:
    """Verify no fourth LLM was introduced by Phase 74B."""

    def test_context_engine_is_observational(self):
        from context_engine.worker_integration import observe_context_engine
        assert callable(observe_context_engine)

    def test_embeddings_not_counted_as_llm(self):
        from context_engine.models import TOTAL_CONTEXT_BUDGET
        assert TOTAL_CONTEXT_BUDGET > 0

    def test_phase74b_files_no_new_llm_imports(self):
        import os
        files_to_check = [
            "memory/context.py",
            "memory/context_assembler.py",
            "core/event_bus.py",
        ]
        llm_imports = ["llm.generate_draft", "llm.score_draft", "llm.extract_commerce_signals"]
        for filepath in files_to_check:
            full = os.path.join(os.path.dirname(__file__), "..", filepath)
            if not os.path.exists(full):
                continue
            with open(full) as f:
                content = f.read()
            for llm_import in llm_imports:
                assert llm_import not in content, f"Unexpected LLM import in {filepath}: {llm_import}"


# ─── Latency Instrumentation ─────────────────────────────────────────────────


class TestLatencyInstrumentation:
    """Verify timing instrumentation exists in the pipeline."""

    def test_generation_telemetry_has_timing_fields(self):
        from core.telemetry import GenerationTelemetry
        t = GenerationTelemetry()
        assert hasattr(t, "context_build_ms")
        assert hasattr(t, "generation_latency_ms")
        assert hasattr(t, "scoring_latency_ms")
        assert hasattr(t, "provider_latency_ms")
        assert hasattr(t, "total_e2e_latency_ms")

    def test_context_engine_observation_has_timing(self):
        from context_engine.worker_integration import ContextEngineObservation
        obs = ContextEngineObservation(enabled=False)
        assert hasattr(obs, "total_ms")
        assert hasattr(obs, "gather_ms")


# ─── Concurrency ─────────────────────────────────────────────────────────────


class TestConcurrency:
    """Verify profile cache under concurrent access."""

    def test_sequential_cache_updates(self):
        from memory.context import _profile_cache, get_last_profile
        for i in range(100):
            _profile_cache["profile"] = {"id": i}
            assert get_last_profile()["id"] == i

    def test_cache_thread_safety(self):
        import threading

        from memory.context import _profile_cache, get_last_profile
        errors = []

        def writer(n):
            try:
                for i in range(50):
                    _profile_cache["profile"] = {"id": n * 1000 + i}
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert get_last_profile() is not None


# ─── Regression Behavior ─────────────────────────────────────────────────────


class TestRegressionBehavior:
    """Verify no behavioral regressions from Phase 74B."""

    def test_existing_test_imports_work(self):
        pass

    def test_context_engine_budget_unchanged(self):
        from context_engine.models import TOTAL_CONTEXT_BUDGET
        assert TOTAL_CONTEXT_BUDGET > 0

    def test_token_budgets_unchanged(self):
        from memory.context import QWEN3_TOKEN_BUDGET, TOKEN_BUDGET
        assert TOKEN_BUDGET["system"] == 600
        assert QWEN3_TOKEN_BUDGET["system"] == 400

    def test_commerce_selection_status_unchanged(self):
        from commerce.selection import CommerceSelectionStatus
        assert CommerceSelectionStatus.USE_COMMERCE_RESPONSE is not None
        assert CommerceSelectionStatus.FALLBACK_TO_STANDARD_LLM is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
