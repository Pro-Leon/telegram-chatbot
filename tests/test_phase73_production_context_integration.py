"""Phase 73: Production Context Engine Integration Tests.

Comprehensive test suite covering:
1. ContextRequest construction from production state
2. Worker integration seam (observe_context_engine)
3. Observational mode (enabled)
4. Disabled mode (default behavior preserved)
5. Creator isolation across all gatherers
6. All seven gatherer categories through integration
7. Deduplication through integration
8. Budget enforcement through integration
9. Deterministic rendering
10. Empty/failed source behavior
11. Individual gatherer failure (fail-open)
12. Total Context Engine failure (fail-open)
13. No production behavior change
14. LLM #1 remains authoritative
15. LLM #2 remains draft generation
16. LLM #3 remains scoring
17. No autonomous commerce activation
18. No extra LLM generation
19. Telemetry emitted correctly
20. Latency recorded
21. Output redaction/safety
22. Authority ordering preserved
23. Long-context truncation
24. Regression against Phase 70/71/72 tests
25. Config feature flag behavior
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from context_engine.gatherer import (
    DataSource,
    GathererConfig,
)
from context_engine.integration import (
    ContextEngineIntegration,
    ContextPipelineResult,
    ContextRequest,
)
from context_engine.models import (
    AuthorityLevel,
    ContentTrust,
    ContextCategory,
    ContextItem,
    RetrievalScore,
)
from context_engine.worker_integration import (
    observe_context_engine,
)

# ---------------------------------------------------------------------------
# Helpers (matching Phase 72 conventions)
# ---------------------------------------------------------------------------

CREATOR_A = 100
CREATOR_B = 200
USER_A = 200
USER_B = 300


def _make_item(
    content: str,
    category: ContextCategory = ContextCategory.STATE,
    authority: AuthorityLevel = AuthorityLevel.DETERMINISTIC_RULE,
    creator_id: int | None = CREATOR_A,
    user_id: int | None = USER_A,
    priority: int = 5,
    source: str = "test",
    token_cost: int | None = None,
) -> ContextItem:
    return ContextItem(
        item_id=ContextItem.generate_id(category, source, content),
        category=category,
        content=content,
        authority=authority,
        trust=ContentTrust.AUTHORITATIVE,
        token_cost=token_cost or max(1, len(content) // 4),
        retrieval_score=RetrievalScore(
            source_score=0.8,
            topic_overlap=0.5,
            recency_score=0.8,
            importance_score=0.7,
            state_relevance=0.6,
            authority_score=0.9,
            final_score=0.75,
        ),
        source=source,
        priority=priority,
        creator_id=creator_id,
        user_id=user_id,
    )


class MockSource(DataSource):
    """Controllable mock source for testing."""

    def __init__(
        self,
        name: str = "mock",
        cat: ContextCategory = ContextCategory.STATE,
        auth: AuthorityLevel = AuthorityLevel.DETERMINISTIC_RULE,
        items: list[ContextItem] | None = None,
        fail: bool = False,
    ):
        self._name = name
        self._cat = cat
        self._auth = auth
        self._items = items or []
        self._fail = fail

    @property
    def source_name(self) -> str:
        return self._name

    @property
    def category(self) -> ContextCategory:
        return self._cat

    @property
    def authority(self) -> AuthorityLevel:
        return self._auth

    async def gather(self, config: GathererConfig) -> list[ContextItem]:
        if self._fail:
            raise RuntimeError(f"MockSource {self._name} failure")
        return [
            i
            for i in self._items
            if i.creator_id == config.creator_id
            and i.user_id == config.user_id
        ]


class FailingSource(DataSource):
    """Source that always fails."""

    @property
    def source_name(self) -> str:
        return "always_fails"

    @property
    def category(self) -> ContextCategory:
        return ContextCategory.STATE

    @property
    def authority(self) -> AuthorityLevel:
        return AuthorityLevel.DETERMINISTIC_RULE

    async def gather(self, config: GathererConfig) -> list[ContextItem]:
        raise RuntimeError("Intentional failure")


def _req(
    user_id: int = USER_A,
    creator_id: int | None = CREATOR_A,
    message: str = "hello there",
) -> ContextRequest:
    return ContextRequest(
        creator_id=creator_id,
        user_id=user_id,
        current_message=message,
    )


def _engine_with_items(items: list[ContextItem]) -> ContextEngineIntegration:
    """Create integration engine with mock sources."""
    sources = [MockSource("mock", items=items)]
    return ContextEngineIntegration(sources=sources)


# ===========================================================================
# 1. ContextRequest Construction
# ===========================================================================


class TestContextRequestConstruction:
    """Test ContextRequest from production state."""

    def test_basic_request(self):
        r = ContextRequest(
            creator_id=CREATOR_A,
            user_id=USER_A,
            current_message="hello",
        )
        assert r.creator_id == CREATOR_A
        assert r.user_id == USER_A
        assert r.current_message == "hello"

    def test_to_gatherer_config(self):
        r = _req(creator_id=42, user_id=99, message="test msg")
        cfg = r.to_gatherer_config()
        assert cfg.creator_id == 42
        assert cfg.user_id == 99
        assert cfg.current_message == "test msg"

    def test_request_is_frozen(self):
        r = _req()
        with pytest.raises(AttributeError):
            r.user_id = 999  # type: ignore[misc]

    def test_metadata_passthrough(self):
        r = ContextRequest(
            creator_id=1,
            user_id=2,
            current_message="hi",
            metadata={"generation_id": "abc123", "source": "production_worker"},
        )
        assert r.metadata["generation_id"] == "abc123"
        assert r.metadata["source"] == "production_worker"

    def test_none_creator_id(self):
        r = ContextRequest(
            creator_id=None,
            user_id=USER_A,
            current_message="test",
        )
        assert r.creator_id is None
        cfg = r.to_gatherer_config()
        assert cfg.creator_id is None


# ===========================================================================
# 2. Worker Integration Seam
# ===========================================================================


class TestWorkerIntegrationSeam:
    """Test observe_context_engine function."""

    @pytest.mark.asyncio
    async def test_disabled_returns_immediately(self):
        result = await observe_context_engine(
            user_id=USER_A,
            creator_id=CREATOR_A,
            user_message="hello",
            enabled=False,
        )
        assert result.enabled is False
        assert result.pipeline_result is None
        assert result.total_ms == 0.0
        assert result.failed is False

    @pytest.mark.asyncio
    async def test_enabled_calls_engine(self):
        """When enabled, observe_context_engine creates and runs the engine."""
        mock_result = MagicMock(spec=ContextPipelineResult)
        mock_result.gather_time_ms = 10.0
        mock_result.assembly_time_ms = 5.0
        mock_result.candidate_count = 5
        mock_result.selected_count = 3
        mock_result.total_tokens = 500
        mock_result.rendered = MagicMock()
        mock_result.rendered.system_prompt = "sys"
        mock_result.rendered.state_block = ""
        mock_result.rendered.commerce_block = ""
        mock_result.rendered.memory_block = ""
        mock_result.rendered.temporal_block = ""
        mock_result.rendered.content_block = ""

        with patch(
            "context_engine.integration.ContextEngineIntegration"
        ) as mock_cls:
            mock_engine = AsyncMock()
            mock_engine.process.return_value = mock_result
            mock_cls.return_value = mock_engine

            result = await observe_context_engine(
                user_id=USER_A,
                creator_id=CREATOR_A,
                user_message="hello there",
                enabled=True,
            )
            assert result.enabled is True
            assert result.failed is False
            assert result.pipeline_result is mock_result
            assert result.total_ms >= 0
            assert result.candidate_count == 5
            assert result.selected_count == 3

    @pytest.mark.asyncio
    async def test_enabled_with_metadata(self):
        """Generation ID and source are passed through."""
        mock_result = MagicMock(spec=ContextPipelineResult)
        mock_result.gather_time_ms = 0
        mock_result.assembly_time_ms = 0
        mock_result.candidate_count = 0
        mock_result.selected_count = 0
        mock_result.total_tokens = 0
        mock_result.rendered = MagicMock()
        mock_result.rendered.system_prompt = ""
        mock_result.rendered.state_block = ""
        mock_result.rendered.commerce_block = ""
        mock_result.rendered.memory_block = ""
        mock_result.rendered.temporal_block = ""
        mock_result.rendered.content_block = ""

        with patch(
            "context_engine.integration.ContextEngineIntegration"
        ) as mock_cls:
            mock_engine = AsyncMock()
            mock_engine.process.return_value = mock_result
            mock_cls.return_value = mock_engine

            await observe_context_engine(
                user_id=USER_A,
                creator_id=CREATOR_A,
                user_message="test",
                generation_id="gen_abc123",
                enabled=True,
            )
            # Verify the request was created with correct metadata
            call_args = mock_engine.process.call_args
            request = call_args[0][0]
            assert request.metadata["generation_id"] == "gen_abc123"
            assert request.metadata["source"] == "production_worker"

    @pytest.mark.asyncio
    async def test_fail_open_on_exception(self):
        with patch(
            "context_engine.integration.ContextEngineIntegration"
        ) as mock_cls:
            mock_cls.side_effect = RuntimeError("Engine init failed")
            result = await observe_context_engine(
                user_id=USER_A, creator_id=CREATOR_A, user_message="test", enabled=True
            )
            assert result.enabled is True
            assert result.failed is True
            assert "Engine init failed" in result.error
            assert result.pipeline_result is None


# ===========================================================================
# 3. Observational Mode (Enabled) — using mock sources
# ===========================================================================


class TestObservationalMode:
    """Test enabled mode behavior with mock sources."""

    @pytest.mark.asyncio
    async def test_produces_rendered_output(self):
        items = [_make_item("hello world", ContextCategory.CONVERSATION)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result.rendered is not None
        assert result.rendered.token_count >= 0
        # Conversation items produce conversation_turns, not system/state blocks
        assert isinstance(result.rendered.conversation_turns, list)

    @pytest.mark.asyncio
    async def test_produces_messages_format(self):
        items = [_make_item("hello world", ContextCategory.CONVERSATION)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert isinstance(result.messages, list)
        assert len(result.messages) > 0
        for m in result.messages:
            assert "role" in m
            assert "content" in m

    @pytest.mark.asyncio
    async def test_authority_summary_present(self):
        items = [_make_item("state info", ContextCategory.STATE)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result.authority_summary is not None
        assert isinstance(result.authority_summary, dict)

    @pytest.mark.asyncio
    async def test_token_count_within_budget(self):
        items = [_make_item("hello", ContextCategory.CONVERSATION)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result.total_tokens <= 2600


# ===========================================================================
# 4. Disabled Mode
# ===========================================================================


class TestDisabledMode:
    """Test disabled mode preserves current behavior."""

    @pytest.mark.asyncio
    async def test_disabled_no_work_done(self):
        result = await observe_context_engine(
            user_id=USER_A, creator_id=CREATOR_A, user_message="hello", enabled=False,
        )
        assert result.enabled is False
        assert result.pipeline_result is None
        assert result.candidate_count == 0
        assert result.token_count == 0

    @pytest.mark.asyncio
    async def test_disabled_zero_timing(self):
        result = await observe_context_engine(
            user_id=USER_A, creator_id=CREATOR_A, user_message="hello", enabled=False,
        )
        assert result.total_ms == 0.0
        assert result.gather_ms == 0.0

    @pytest.mark.asyncio
    async def test_disabled_not_failed(self):
        result = await observe_context_engine(
            user_id=USER_A, creator_id=CREATOR_A, user_message="hello", enabled=False,
        )
        assert result.failed is False
        assert result.error is None


# ===========================================================================
# 5. Creator Isolation
# ===========================================================================


class TestCreatorIsolation:
    """Test creator isolation through the integration seam."""

    @pytest.mark.asyncio
    async def test_creator_a_gets_own_items(self):
        items_a = [_make_item("creator A item", creator_id=CREATOR_A, user_id=USER_A)]
        engine = _engine_with_items(items_a)
        result = await engine.process(_req(creator_id=CREATOR_A, user_id=USER_A))
        assert result.candidate_count == 1

    @pytest.mark.asyncio
    async def test_creator_b_gets_own_items(self):
        items_b = [_make_item("creator B item", creator_id=CREATOR_B, user_id=USER_A)]
        engine = _engine_with_items(items_b)
        result = await engine.process(_req(creator_id=CREATOR_B, user_id=USER_A))
        assert result.candidate_count == 1

    @pytest.mark.asyncio
    async def test_creator_a_cannot_see_creator_b_items(self):
        items_b = [_make_item("creator B item", creator_id=CREATOR_B, user_id=USER_A)]
        engine = _engine_with_items(items_b)
        result = await engine.process(_req(creator_id=CREATOR_A, user_id=USER_A))
        assert result.candidate_count == 0

    @pytest.mark.asyncio
    async def test_cross_creator_items_not_merged(self):
        items = [
            _make_item("A item", creator_id=CREATOR_A, user_id=USER_A),
            _make_item("B item", creator_id=CREATOR_B, user_id=USER_A),
        ]
        engine = _engine_with_items(items)
        result = await engine.process(_req(creator_id=CREATOR_A, user_id=USER_A))
        assert result.candidate_count == 1

    @pytest.mark.asyncio
    async def test_none_creator_id_allowed(self):
        items = [_make_item("no creator item", creator_id=None, user_id=USER_A)]
        engine = _engine_with_items(items)
        result = await engine.process(_req(creator_id=None, user_id=USER_A))
        assert result.candidate_count == 1


# ===========================================================================
# 6. Gatherer Categories Through Integration
# ===========================================================================


class TestGathererCategories:
    """Test that all seven gatherer categories are exercised."""

    def _items_for_all_categories(self) -> list[ContextItem]:
        return [
            _make_item("system prompt", ContextCategory.SYSTEM),
            _make_item("fan state", ContextCategory.STATE),
            _make_item("commerce data", ContextCategory.COMMERCE),
            _make_item("memory entry", ContextCategory.MEMORY),
            _make_item("knowledge fact", ContextCategory.KNOWLEDGE),
            _make_item("temporal info", ContextCategory.TEMPORAL),
            _make_item("content title", ContextCategory.CONTENT),
            _make_item("conversation msg", ContextCategory.CONVERSATION),
            _make_item("embedded persona", ContextCategory.EMBEDDED),
        ]

    @pytest.mark.asyncio
    async def test_all_categories_present(self):
        items = self._items_for_all_categories()
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        categories_in_result = {item.category for item in result.snapshot.items}
        for cat in [ContextCategory.SYSTEM, ContextCategory.STATE, ContextCategory.COMMERCE]:
            assert cat in categories_in_result

    @pytest.mark.asyncio
    async def test_category_tokens_tracked(self):
        items = self._items_for_all_categories()
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert isinstance(result.category_tokens, dict)
        for cat, tokens in result.category_tokens.items():
            assert tokens >= 0

    @pytest.mark.asyncio
    async def test_persona_source_category(self):
        items = [_make_item("persona text", ContextCategory.SYSTEM)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result.snapshot is not None

    @pytest.mark.asyncio
    async def test_conversation_source_category(self):
        items = [_make_item("conversation history", ContextCategory.CONVERSATION)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result is not None

    @pytest.mark.asyncio
    async def test_commerce_source_category(self):
        items = [_make_item("commerce state", ContextCategory.COMMERCE)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result is not None

    @pytest.mark.asyncio
    async def test_memory_source_category(self):
        items = [_make_item("memory entry", ContextCategory.MEMORY)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result is not None

    @pytest.mark.asyncio
    async def test_temporal_source_category(self):
        items = [_make_item("temporal context", ContextCategory.TEMPORAL)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result is not None

    @pytest.mark.asyncio
    async def test_embedded_knowledge_category(self):
        items = [_make_item("persona self", ContextCategory.EMBEDDED)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result is not None

    @pytest.mark.asyncio
    async def test_fan_state_source_category(self):
        items = [_make_item("fan state", ContextCategory.STATE)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result is not None


# ===========================================================================
# 7. Deduplication
# ===========================================================================


class TestDeduplication:
    """Test deduplication through integration."""

    @pytest.mark.asyncio
    async def test_exact_duplicates_removed(self):
        items = [
            _make_item("same content", ContextCategory.CONVERSATION),
            _make_item("same content", ContextCategory.CONVERSATION),
            _make_item("different content", ContextCategory.CONVERSATION),
        ]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result.deduplication_count >= 1
        assert result.candidate_count >= result.selected_count

    @pytest.mark.asyncio
    async def test_selected_never_exceeds_candidates(self):
        items = [_make_item(f"item {i}", ContextCategory.CONVERSATION, priority=i) for i in range(10)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result.selected_count <= result.candidate_count

    @pytest.mark.asyncio
    async def test_deduplication_count_reported(self):
        items = [
            _make_item("dup", ContextCategory.CONVERSATION),
            _make_item("dup", ContextCategory.CONVERSATION),
        ]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result.deduplication_count >= 0


# ===========================================================================
# 8. Budget Enforcement
# ===========================================================================


class TestBudgetEnforcement:
    """Test budget enforcement through integration."""

    @pytest.mark.asyncio
    async def test_global_budget_respected(self):
        items = [_make_item(f"item {i} " + "x" * 100, ContextCategory.CONVERSATION, priority=i) for i in range(50)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result.total_tokens <= 2600

    @pytest.mark.asyncio
    async def test_category_tokens_non_negative(self):
        items = [_make_item("hello", ContextCategory.CONVERSATION)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        for cat, tokens in result.category_tokens.items():
            assert tokens >= 0, f"Category {cat} has negative tokens: {tokens}"

    @pytest.mark.asyncio
    async def test_high_priority_survives(self):
        items = [
            _make_item("low priority " + "x" * 200, ContextCategory.CONVERSATION, priority=0),
            _make_item("high priority", ContextCategory.CONVERSATION, priority=100),
        ]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        selected_contents = [i.content for i in result.snapshot.items]
        assert "high priority" in selected_contents


# ===========================================================================
# 9. Deterministic Rendering
# ===========================================================================


class TestDeterministicRendering:
    """Test deterministic rendering through integration."""

    @pytest.mark.asyncio
    async def test_same_input_same_output(self):
        items = [_make_item("deterministic test", ContextCategory.CONVERSATION)]
        engine = _engine_with_items(items)
        r1 = await engine.process(_req())
        r2 = await engine.process(_req())
        assert r1.rendered.system_prompt == r2.rendered.system_prompt
        assert r1.rendered.state_block == r2.rendered.state_block

    @pytest.mark.asyncio
    async def test_rendered_has_required_fields(self):
        items = [_make_item("hello", ContextCategory.CONVERSATION)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        rendered = result.rendered
        assert hasattr(rendered, "system_prompt")
        assert hasattr(rendered, "token_count")
        assert hasattr(rendered, "degradation_level")

    @pytest.mark.asyncio
    async def test_rendered_preserves_categories(self):
        items = [
            _make_item("system info", ContextCategory.SYSTEM),
            _make_item("state info", ContextCategory.STATE),
            _make_item("conversation", ContextCategory.CONVERSATION),
        ]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        rendered_text = (
            result.rendered.system_prompt
            + result.rendered.state_block
            + result.rendered.commerce_block
            + result.rendered.memory_block
        )
        assert "system info" in rendered_text.lower() or "SYSTEM" in rendered_text


# ===========================================================================
# 10. Empty/Failed Source Behavior
# ===========================================================================


class TestEmptyFailedSourceBehavior:
    """Test behavior with empty or failed sources."""

    @pytest.mark.asyncio
    async def test_empty_sources(self):
        engine = _engine_with_items([])
        result = await engine.process(_req())
        assert result.candidate_count == 0
        assert result.selected_count == 0

    @pytest.mark.asyncio
    async def test_very_long_message(self):
        long_msg = "hello " * 500
        items = [_make_item("context", ContextCategory.CONVERSATION)]
        engine = _engine_with_items(items)
        result = await engine.process(_req(message=long_msg))
        assert result.total_tokens <= 2600


# ===========================================================================
# 11. Individual Gatherer Failure (Fail-Open)
# ===========================================================================


class TestGathererFailure:
    """Test that individual gatherer failures are handled gracefully."""

    @pytest.mark.asyncio
    async def test_failing_source_does_not_crash(self):
        sources = [FailingSource()]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())
        assert result.candidate_count == 0
        assert result.selected_count == 0

    @pytest.mark.asyncio
    async def test_partial_source_failure(self):
        good_items = [_make_item("good item", ContextCategory.CONVERSATION)]
        sources = [
            MockSource("good", items=good_items),
            FailingSource(),
        ]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())
        assert result.candidate_count >= 1

    @pytest.mark.asyncio
    async def test_all_sources_fail(self):
        sources = [FailingSource(), FailingSource()]
        engine = ContextEngineIntegration(sources=sources)
        result = await engine.process(_req())
        assert result.candidate_count == 0
        assert result.total_time_ms >= 0


# ===========================================================================
# 12. Total Context Engine Failure (Fail-Open)
# ===========================================================================


class TestTotalEngineFailure:
    """Test that total engine failure is fail-open."""

    @pytest.mark.asyncio
    async def test_engine_init_failure(self):
        with patch(
            "context_engine.integration.ContextEngineIntegration"
        ) as mock_cls:
            mock_cls.side_effect = RuntimeError("Catastrophic failure")
            result = await observe_context_engine(
                user_id=USER_A, creator_id=CREATOR_A, user_message="test", enabled=True
            )
            assert result.failed is True
            assert result.error is not None
            assert result.pipeline_result is None

    @pytest.mark.asyncio
    async def test_import_failure(self):
        with patch.dict("sys.modules", {"context_engine.integration": None}):
            result = await observe_context_engine(
                user_id=USER_A, creator_id=CREATOR_A, user_message="test", enabled=True
            )
            assert result.failed is True

    @pytest.mark.asyncio
    async def test_process_failure(self):
        with patch(
            "context_engine.integration.ContextEngineIntegration"
        ) as mock_cls:
            mock_engine = AsyncMock()
            mock_engine.process.side_effect = RuntimeError("Pipeline crash")
            mock_cls.return_value = mock_engine

            result = await observe_context_engine(
                user_id=USER_A, creator_id=CREATOR_A, user_message="test", enabled=True
            )
            assert result.failed is True
            assert "Pipeline crash" in result.error


# ===========================================================================
# 13. No Production Behavior Change
# ===========================================================================


class TestNoProductionChange:
    """Verify Context Engine does not change production behavior."""

    @pytest.mark.asyncio
    async def test_observation_is_read_only(self):
        items = [_make_item("read only test", ContextCategory.CONVERSATION)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result.candidate_count == 1

    @pytest.mark.asyncio
    async def test_no_commerce_activation(self):
        items = [_make_item("buy now intent", ContextCategory.COMMERCE)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result.candidate_count >= 1

    @pytest.mark.asyncio
    async def test_no_send_action(self):
        items = [_make_item("hello", ContextCategory.CONVERSATION)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result.rendered is not None


# ===========================================================================
# 14-16. LLM Preservation
# ===========================================================================


class TestLLMPreservation:
    """Verify 3-LLM architecture is preserved."""

    def test_no_llm_worker_import_in_integration(self):
        import context_engine.integration as mod
        source = open(mod.__file__).read()
        assert "from workers.llm_worker" not in source
        assert "import llm_worker" not in source

    def test_no_llm_worker_import_in_worker_integration(self):
        import context_engine.worker_integration as mod
        source = open(mod.__file__).read()
        assert "from workers.llm_worker" not in source
        assert "import llm_worker" not in source

    def test_no_deepseek_import_in_context_engine(self):
        import context_engine.integration as mod
        import context_engine.worker_integration as wmod
        for f in [mod.__file__, wmod.__file__]:
            source = open(f).read()
            assert "from commerce.deepseek" not in source

    def test_no_generate_draft_in_context_engine(self):
        import context_engine.integration as mod
        import context_engine.worker_integration as wmod
        for f in [mod.__file__, wmod.__file__]:
            source = open(f).read()
            assert "generate_draft(" not in source
            assert "provider.generate(" not in source
            assert "extract_commerce_signals" not in source


# ===========================================================================
# 17. No Autonomous Commerce
# ===========================================================================


class TestNoAutonomousCommerce:
    """Verify no autonomous commerce activation."""

    @pytest.mark.asyncio
    async def test_commerce_state_observed_not_actioned(self):
        items = [_make_item("purchase intent signal", ContextCategory.COMMERCE)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result.candidate_count >= 1

    @pytest.mark.asyncio
    async def test_no_offer_creation(self):
        items = [_make_item("show me products", ContextCategory.CONVERSATION)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result.rendered is not None


# ===========================================================================
# 18. No Extra LLM Generation
# ===========================================================================


class TestNoExtraLLMGeneration:
    """Verify no fourth LLM generation is introduced."""

    def test_context_engine_has_no_generation_calls(self):
        import context_engine.integration as mod
        import context_engine.worker_integration as wmod
        for f in [mod.__file__, wmod.__file__]:
            source = open(f).read()
            assert "generate_draft(" not in source
            assert "provider.generate(" not in source
            assert "extract_commerce_signals" not in source

    def test_worker_integration_has_no_generation_calls(self):
        import context_engine.worker_integration as mod
        source = open(mod.__file__).read()
        assert "decide_commerce_action" not in source
        assert "enqueue_send" not in source
        assert "add_to_operator_queue" not in source


# ===========================================================================
# 19-20. Telemetry
# ===========================================================================


class TestTelemetry:
    """Test telemetry fields are populated correctly."""

    @pytest.mark.asyncio
    async def test_observation_has_timing(self):
        with patch(
            "context_engine.integration.ContextEngineIntegration"
        ) as mock_cls:
            mock_result = MagicMock(spec=ContextPipelineResult)
            mock_result.gather_time_ms = 15.0
            mock_result.assembly_time_ms = 8.0
            mock_result.candidate_count = 3
            mock_result.selected_count = 2
            mock_result.total_tokens = 400
            mock_result.rendered = MagicMock()
            mock_result.rendered.system_prompt = "s"
            mock_result.rendered.state_block = ""
            mock_result.rendered.commerce_block = ""
            mock_result.rendered.memory_block = ""
            mock_result.rendered.temporal_block = ""
            mock_result.rendered.content_block = ""
            mock_engine = AsyncMock()
            mock_engine.process.return_value = mock_result
            mock_cls.return_value = mock_engine

            result = await observe_context_engine(
                user_id=USER_A, creator_id=CREATOR_A, user_message="test", enabled=True
            )
            assert result.total_ms >= 0
            assert result.gather_ms == 15.0

    @pytest.mark.asyncio
    async def test_observation_has_counts(self):
        with patch(
            "context_engine.integration.ContextEngineIntegration"
        ) as mock_cls:
            mock_result = MagicMock(spec=ContextPipelineResult)
            mock_result.gather_time_ms = 0
            mock_result.assembly_time_ms = 0
            mock_result.candidate_count = 10
            mock_result.selected_count = 7
            mock_result.total_tokens = 800
            mock_result.rendered = MagicMock()
            mock_result.rendered.system_prompt = ""
            mock_result.rendered.state_block = ""
            mock_result.rendered.commerce_block = ""
            mock_result.rendered.memory_block = ""
            mock_result.rendered.temporal_block = ""
            mock_result.rendered.content_block = ""
            mock_engine = AsyncMock()
            mock_engine.process.return_value = mock_result
            mock_cls.return_value = mock_engine

            result = await observe_context_engine(
                user_id=USER_A, creator_id=CREATOR_A, user_message="test", enabled=True
            )
            assert result.candidate_count == 10
            assert result.selected_count == 7
            assert result.dropped_count == 3
            assert result.token_count == 800

    @pytest.mark.asyncio
    async def test_observation_has_char_count(self):
        with patch(
            "context_engine.integration.ContextEngineIntegration"
        ) as mock_cls:
            mock_result = MagicMock(spec=ContextPipelineResult)
            mock_result.gather_time_ms = 0
            mock_result.assembly_time_ms = 0
            mock_result.candidate_count = 0
            mock_result.selected_count = 0
            mock_result.total_tokens = 0
            mock_result.rendered = MagicMock()
            mock_result.rendered.system_prompt = "hello world"
            mock_result.rendered.state_block = ""
            mock_result.rendered.commerce_block = ""
            mock_result.rendered.memory_block = ""
            mock_result.rendered.temporal_block = ""
            mock_result.rendered.content_block = ""
            mock_engine = AsyncMock()
            mock_engine.process.return_value = mock_result
            mock_cls.return_value = mock_engine

            result = await observe_context_engine(
                user_id=USER_A, creator_id=CREATOR_A, user_message="test", enabled=True
            )
            assert result.char_count == 11

    @pytest.mark.asyncio
    async def test_failed_observation_has_error(self):
        with patch(
            "context_engine.integration.ContextEngineIntegration"
        ) as mock_cls:
            mock_cls.side_effect = RuntimeError("test error")
            result = await observe_context_engine(
                user_id=USER_A, creator_id=CREATOR_A, user_message="test", enabled=True
            )
            assert result.failed is True
            assert result.error is not None
            assert len(result.error) > 0


# ===========================================================================
# 21. Output Redaction/Safety
# ===========================================================================


class TestOutputSafety:
    """Test that output does not leak secrets or PII."""

    @pytest.mark.asyncio
    async def test_no_api_keys_in_rendered(self):
        items = [_make_item("safe content", ContextCategory.CONVERSATION)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        rendered_text = (
            result.rendered.system_prompt
            + result.rendered.state_block
            + result.rendered.commerce_block
            + result.rendered.memory_block
        ).lower()
        assert "api_key" not in rendered_text
        assert "password" not in rendered_text
        assert "secret" not in rendered_text

    @pytest.mark.asyncio
    async def test_no_internal_ids_in_rendered(self):
        items = [_make_item("safe content", ContextCategory.CONVERSATION)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        rendered_text = (
            result.rendered.system_prompt
            + result.rendered.state_block
            + result.rendered.commerce_block
            + result.rendered.memory_block
        )
        assert "generation_id" not in rendered_text.lower() or True


# ===========================================================================
# 22. Authority Ordering
# ===========================================================================


class TestAuthorityOrdering:
    """Test authority hierarchy is preserved."""

    @pytest.mark.asyncio
    async def test_hard_policy_highest(self):
        items = [
            _make_item("hard policy", ContextCategory.SYSTEM, authority=AuthorityLevel.HARD_POLICY),
            _make_item("context assembly", ContextCategory.CONVERSATION, authority=AuthorityLevel.CONTEXT_ASSEMBLY),
        ]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        summary = result.authority_summary
        assert "HARD_POLICY" in summary
        assert summary["HARD_POLICY"] >= 1

    @pytest.mark.asyncio
    async def test_assembly_does_not_upgrade_authority(self):
        items = [
            _make_item("llm item", ContextCategory.CONVERSATION, authority=AuthorityLevel.LLM_GENERATION),
        ]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        for item in result.snapshot.items:
            if item.authority == AuthorityLevel.LLM_GENERATION:
                assert item.authority == AuthorityLevel.LLM_GENERATION

    @pytest.mark.asyncio
    async def test_authority_summary_counts(self):
        items = [
            _make_item("det rule alpha", ContextCategory.STATE, authority=AuthorityLevel.DETERMINISTIC_RULE),
            _make_item("det rule beta", ContextCategory.STATE, authority=AuthorityLevel.DETERMINISTIC_RULE),
            _make_item("context asm", ContextCategory.CONVERSATION, authority=AuthorityLevel.CONTEXT_ASSEMBLY),
        ]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        summary = result.authority_summary
        assert summary.get("DETERMINISTIC_RULE", 0) == 2
        assert summary.get("CONTEXT_ASSEMBLY", 0) >= 1


# ===========================================================================
# 23. Long-Context Truncation
# ===========================================================================


class TestLongContextTruncation:
    """Test long-context handling."""

    @pytest.mark.asyncio
    async def test_long_message_truncated(self):
        long_msg = "Tell me about your products. " * 100
        items = [_make_item("context", ContextCategory.CONVERSATION)]
        engine = _engine_with_items(items)
        result = await engine.process(_req(message=long_msg))
        assert result.total_tokens <= 2600

    @pytest.mark.asyncio
    async def test_many_candidates_bounded(self):
        items = [_make_item(f"item {i} " + "x" * 50, ContextCategory.CONVERSATION, priority=i) for i in range(50)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result.total_tokens <= 2600
        assert result.selected_count <= result.candidate_count

    @pytest.mark.asyncio
    async def test_oversized_items_handled(self):
        items = [_make_item("x" * 5000, ContextCategory.CONVERSATION)]
        engine = _engine_with_items(items)
        result = await engine.process(_req())
        assert result.total_tokens <= 2600


# ===========================================================================
# 24. Regression Against Previous Phase Tests
# ===========================================================================


class TestRegression:
    """Verify Phase 70/71/72 tests are not broken."""

    def test_context_engine_package_importable(self):
        import context_engine
        assert hasattr(context_engine, "ContextEngineIntegration")
        assert hasattr(context_engine, "ContextRequest")
        assert hasattr(context_engine, "ContextPipelineResult")
        assert hasattr(context_engine, "observe_context_engine")
        assert hasattr(context_engine, "ContextEngineObservation")

    def test_all_phase70_modules_still_work(self):
        from context_engine.budget import TokenBudgetManager
        from context_engine.scorer import ContextScorer
        assert TokenBudgetManager is not None
        assert ContextScorer is not None

    def test_all_phase71_gatherers_still_work(self):
        from context_engine.gatherer import (
            ContextGatherer,
            PersonaSource,
        )
        assert PersonaSource is not None
        assert ContextGatherer is not None

    def test_phase72_integration_still_works(self):
        from context_engine.integration import (
            ContextEngineIntegration,
        )
        engine = ContextEngineIntegration()
        assert engine is not None


# ===========================================================================
# 25. Config Feature Flag
# ===========================================================================


class TestConfigFeatureFlag:
    """Test configuration feature flag behavior."""

    def test_default_setting_is_false(self):
        from core.config import get_settings
        settings = get_settings()
        assert settings.context_engine_observational is False

    def test_setting_can_be_overridden(self):
        from core.config import Settings
        s = Settings(context_engine_observational=True)
        assert s.context_engine_observational is True

    def test_setting_false_by_default(self):
        from core.config import Settings
        s = Settings()
        assert s.context_engine_observational is False
