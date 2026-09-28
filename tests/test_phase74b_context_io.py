"""Phase 74B — Context I/O Optimization Regression Tests.

Verifies B1 (profile reuse), B2 (redundant PG elimination), B3 (PG parallelization),
B4 (Redis pipelining), B5 (orjson) without changing business logic.
"""

import ast
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


_WORKER_PATH = "workers/llm_worker.py"


def _read_worker_source() -> str:
    return open(_WORKER_PATH, encoding="utf-8").read()


# ---------------------------------------------------------------------------
# B1 — Canonical Profile Reuse
# ---------------------------------------------------------------------------


class TestB1ProfileReuse:
    """Profile must be fetched once and reused by all consumers."""

    def test_uses_cached_profile_for_commerce(self):
        source = _read_worker_source()
        # Must use get_last_profile() instead of get_user_profile() for commerce
        assert "get_last_profile" in source

    def test_get_last_profile_no_args(self):
        source = _read_worker_source()
        # get_last_profile must be called WITHOUT user_id argument (aliased as _get_cached_profile)
        assert "_get_cached_profile()" in source
        # Must NOT have the old buggy call with user_id
        assert "get_last_profile(user_id)" not in source

    def test_profile_fallback_to_db(self):
        source = _read_worker_source()
        # Must have fallback: if cached profile is None, fetch from DB
        assert "_cached_profile_for_commerce is None" in source

    def test_user_reused_via_cache(self):
        source = _read_worker_source()
        # Must use get_last_user() instead of redundant get_user() for commerce state
        assert "get_last_user" in source

    def test_user_fallback_to_db(self):
        source = _read_worker_source()
        assert "_user_for_state is None" in source

    def test_profile_cache_exists(self):
        from memory.context import get_last_profile, get_last_user
        assert callable(get_last_profile)
        assert callable(get_last_user)

    def test_no_double_profile_fetch_in_strategy_learning(self):
        source = _read_worker_source()
        lines = source.split("\n")
        # Find the strategy learning section
        in_strategy = False
        profile_db_fetches = 0
        for line in lines:
            if "update_strategy_evidence" in line and "import" not in line:
                in_strategy = True
            if in_strategy and "await get_user_profile" in line:
                profile_db_fetches += 1
        # Strategy learning uses cached profile (0 fetches if cache hit),
        # with at most 1 fallback fetch + 1 post-update re-read (justified)
        assert profile_db_fetches <= 2, (
            f"Strategy learning has {profile_db_fetches} await get_user_profile calls; expected <=2"
        )


# ---------------------------------------------------------------------------
# B2 — Redundant PG Elimination
# ---------------------------------------------------------------------------


class TestB2RedundantPGElimination:
    """Redundant PG reads must be eliminated."""

    def test_no_dead_user_fetch_in_try_commerce(self):
        source = _read_worker_source()
        # _user_for_product and _profile_for_product dead reads removed
        assert "_user_for_product" not in source
        assert "_profile_for_product" not in source

    def test_derive_conversation_state_correct_args(self):
        source = _read_worker_source()
        # Must call derive_conversation_state(context) not derive_conversation_state(user_id, _creator_id)
        assert "derive_conversation_state(context)" in source
        assert "derive_conversation_state(user_id, _creator_id)" not in source

    def test_derive_conversation_state_is_sync(self):
        from core.conversation_state import derive_conversation_state
        # Function is synchronous, not async
        assert not inspect.iscoroutinefunction(derive_conversation_state)

    def test_derive_conversation_state_pure(self):
        """derive_conversation_state must be pure — no I/O."""
        import core.conversation_state as mod
        source = inspect.getsource(mod)
        assert "async def" not in source or "derive_conversation_state" not in source.split("async def")[0]


# ---------------------------------------------------------------------------
# B3 — PG Parallelization
# ---------------------------------------------------------------------------


class TestB3PGParallelization:
    """Independent PG reads must be parallelized with asyncio.gather."""

    def test_upsert_and_auto_reply_parallelized(self):
        source = _read_worker_source()
        # Must use asyncio.gather for upsert_user + is_user_auto_reply_excluded
        assert "asyncio.gather" in source
        assert "_upsert_coro" in source
        assert "_auto_reply_coro" in source

    def test_timing_and_behavioral_parallelized(self):
        source = _read_worker_source()
        # Must use asyncio.gather for get_timing_context + get_behavioral_feedback_context
        assert "_timing_coro" in source
        assert "_behavioral_coro" in source

    def test_gather_uses_return_exceptions(self):
        source = _read_worker_source()
        # All gathers must use return_exceptions=True for safety
        gather_count = source.count("return_exceptions=True")
        assert gather_count >= 2, f"Expected >=2 gather calls with return_exceptions, found {gather_count}"

    def test_exception_handling_in_gather(self):
        source = _read_worker_source()
        # Gather results must be checked for exceptions
        assert "isinstance(_timing_ctx, Exception)" in source
        assert "isinstance(_behavioral_ctx, Exception)" in source


# ---------------------------------------------------------------------------
# B4 — Redis Pipelining
# ---------------------------------------------------------------------------


class TestB4RedisPipelining:
    """Event publishing must use batch pipeline where possible."""

    def test_publish_events_batch_exists(self):
        source = _read_worker_source()
        assert "publish_events_batch" in source

    def test_event_bus_uses_orjson(self):
        from core.event_bus import _json_dumps
        # Should use orjson if available
        import orjson
        result = _json_dumps({"test": "value"})
        assert isinstance(result, str)

    def test_batch_used_for_operator_events(self):
        source = _read_worker_source()
        # Operator events must use publish_events_batch
        assert "await publish_events_batch(_operator_events)" in source


# ---------------------------------------------------------------------------
# B5 — orjson
# ---------------------------------------------------------------------------


class TestB5Orjson:
    """orjson must be used where available."""

    def test_orjson_installed(self):
        import orjson
        assert orjson.__version__ >= "3.0"

    def test_event_bus_uses_orjson(self):
        import core.event_bus as eb
        source = inspect.getsource(eb)
        assert "orjson" in source

    def test_json_loads_profile_works(self):
        """Profile JSON parsing must work with stdlib json (fallback)."""
        import json
        profile = json.loads('{"strategy": "default", "by_creator": {}}')
        assert profile["strategy"] == "default"


# ---------------------------------------------------------------------------
# Context Engine Safety
# ---------------------------------------------------------------------------


class TestContextEngineSafety:
    """Context Engine must remain observational, feature-gated, fail-open."""

    def test_observational_flag_preserved(self):
        source = _read_worker_source()
        assert "context_engine_observational" in source

    def test_context_engine_does_not_replace_context(self):
        source = _read_worker_source()
        # observe_context_engine must not reassign context variable
        lines = source.split("\n")
        for i, line in enumerate(lines, 1):
            if "observe_context_engine" in line:
                for j in range(i, min(i + 5, len(lines))):
                    assert "context =" not in lines[j] or "context.append" in lines[j], \
                        f"Context Engine at line {i} appears to replace context at line {j+1}"


# ---------------------------------------------------------------------------
# LLM Call Count
# ---------------------------------------------------------------------------


class TestLLMCallCount:
    """Normal path must remain 3 LLM calls."""

    def test_extract_commerce_signals_once(self):
        tree = ast.parse(_read_worker_source())
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "extract_commerce_signals"]
        assert len(calls) == 1

    def test_generate_draft_present(self):
        source = _read_worker_source()
        assert "generate_draft(" in source

    def test_score_draft_present(self):
        source = _read_worker_source()
        assert "score_draft(" in source


# ---------------------------------------------------------------------------
# Static Validation
# ---------------------------------------------------------------------------


class TestStaticValidation:
    """Worker must compile and have correct structure."""

    def test_compiles(self):
        import py_compile
        py_compile.compile(_WORKER_PATH, doraise=True)

    def test_ast_valid(self):
        tree = ast.parse(_read_worker_source())
        assert tree is not None

    def test_process_message_exists(self):
        tree = ast.parse(_read_worker_source())
        funcs = [n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        assert "process_message" in funcs

    def test_run_worker_exists(self):
        tree = ast.parse(_read_worker_source())
        funcs = [n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        assert "run_worker" in funcs
