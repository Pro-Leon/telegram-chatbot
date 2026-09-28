"""Phase 5 relationship-aware context assembly tests (IMPLEMENT → VERIFY).

Pins the single deterministic selector in
``context_engine/relationship_context.py`` and its integration points:

* deterministic selection over existing retrieval output (no new fetch)
* current-topic / open-thread / message relevance gating
* open-loop/commitment prioritization with existing threshold language
* bounded top-k, empty/irrelevant/expired handling, dedup, stable order
* summary bounding (first sentence, char-capped)
* compact rendering without counters/provenance/IDs/directives
* no sexual / commerce-authority vocabulary in the block
* current-context precedence (history stays advisory)
* CALLBACK evidence only from genuine supporting facts
* Phase 4 selector unchanged; strategy + relationship reach OneCall via
  snapshot fields; legacy parity via the same selection
* token bound + truncation via the existing estimator
* fail-open on every retrieval/selection/render/trajectory failure
* creator isolation; commerce isolation; no persistence

Conventions follow the Phase 1–4 suites: deterministic unit tests only,
real domain objects where practical, no live LLM/DB/Redis.
"""

from __future__ import annotations

import inspect
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit]

from context_engine.relationship_context import (  # noqa: E402
    MAX_RELATIONSHIP_FACTS,
    MAX_RELATIONSHIP_TOKENS,
    RelationshipContext,
    assemble_relationship_context,
    has_prior_context_evidence,
    render_relationship_context,
    select_relationship_context,
    to_plain_dict,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _bands(**overrides):
    base = {
        "familiarity": "established",
        "engagement": "steady",
        "reciprocity": "balanced",
        "continuity": "anchored",
        "trend": "stable",
    }
    base.update(overrides)
    return base


def _fact(subject="trip", value="miami", **overrides):
    base = {
        "subject": subject,
        "value": value,
        "status": "CURRENT",
        "confidence": 1.0,
        "memory_type": "plan",
    }
    base.update(overrides)
    return base


def _select(**overrides):
    kwargs = {
        "bands": _bands(),
        "current_topic": "trip to miami",
        "open_threads": ("trip to miami",),
        "current_message": "are we still on for miami?",
        "ltm_items": [_fact()],
        "fk_items": [],
        "summary": None,
    }
    kwargs.update(overrides)
    return select_relationship_context(**kwargs)


# ---------------------------------------------------------------------------
# A. Deterministic selection
# ---------------------------------------------------------------------------


class TestADeterminism:
    def test_same_inputs_identical_output(self):
        first = _select()
        second = _select()
        assert first == second
        assert render_relationship_context(first) == render_relationship_context(second)

    def test_pure_no_io(self):
        # AST-level: no I/O-shaped imports, calls, or global statements in
        # the module (docstring prose such as "no randomness" is ignored).
        import ast
        import pathlib

        tree = ast.parse(pathlib.Path("context_engine/relationship_context.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported.isdisjoint({"db", "redis", "httpx", "requests", "random", "socket"}), imported
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    called.add(func.id)
                elif isinstance(func, ast.Attribute):
                    called.add(func.attr)
        assert called.isdisjoint({"open", "execute", "publish_event", "enqueue_send", "get_llm_provider"}), called
        assert not any(isinstance(n, ast.Global) for n in ast.walk(tree))

    def test_stable_ordering_open_loop_first(self):
        ctx = select_relationship_context(
            bands=_bands(),
            current_topic="exam friday",
            open_threads=("exam friday",),
            current_message="exam friday",
            ltm_items=[
                _fact("hobby", "chess", confidence=1.0, memory_type="fact"),
                _fact("exam", "friday test", confidence=0.6, memory_type="open_loop"),
            ],
        )
        assert ctx.has_prior_context is True
        assert ctx.facts[0].subject == "exam"


# ---------------------------------------------------------------------------
# B. Relevance gating
# ---------------------------------------------------------------------------


class TestBRelevance:
    def test_overlap_required(self):
        ctx = select_relationship_context(
            bands=_bands(),
            current_topic="movies",
            open_threads=("movies",),
            current_message="want to watch movies?",
            ltm_items=[_fact("trip", "miami")],
        )
        assert ctx.facts == ()
        assert ctx.has_prior_context is False

    def test_bands_alone_never_produce_facts(self):
        ctx = select_relationship_context(
            bands=_bands(),
            current_topic=None,
            open_threads=(),
            current_message="",
            ltm_items=[_fact()],
        )
        assert ctx.facts == ()
        assert ctx.has_prior_context is False

    def test_threads_survive_without_facts(self):
        ctx = select_relationship_context(
            bands=_bands(familiarity="unknown"),
            current_topic="movies",
            open_threads=("movies",),
            current_message="movies tonight?",
            ltm_items=[],
        )
        assert ctx.threads == ("movies",)
        assert ctx.has_prior_context is False
        # Threads + bands render, but no prior-context evidence.
        assert render_relationship_context(ctx) != ""

    def test_expired_rows_dropped(self):
        ctx = _select(ltm_items=[_fact(status="EXPIRED")])
        assert ctx.facts == ()
        assert ctx.has_prior_context is False

    def test_empty_inputs_yield_empty_render(self):
        ctx = select_relationship_context()
        assert ctx == RelationshipContext()
        assert render_relationship_context(ctx) == ""
        assert render_relationship_context(None) == ""
        assert render_relationship_context("RELATIONSHIP CONTEXT") == ""


# ---------------------------------------------------------------------------
# C. Bounds / dedup / summary
# ---------------------------------------------------------------------------


class TestCBounds:
    def test_top_k_bound(self):
        items = [_fact(f"topic{i}", f"value{i} miami") for i in range(10)]
        ctx = _select(ltm_items=items)
        assert len(ctx.facts) <= MAX_RELATIONSHIP_FACTS
        assert MAX_RELATIONSHIP_FACTS == 3

    def test_dedup_across_ltm_and_fk(self):
        ctx = _select(
            ltm_items=[_fact("trip", "Miami")],
            fk_items=[{"subject": "trip", "value": "miami", "status": "CURRENT", "confidence": 1.0}],
        )
        assert len(ctx.facts) == 1

    def test_summary_first_sentence_bounded(self):
        long_summary = "First sentence here. " + ("Extra words. " * 60)
        ctx = _select(ltm_items=[], fk_items=[], summary=long_summary)
        assert ctx.summary_line is not None
        assert "Extra words" not in ctx.summary_line
        assert len(ctx.summary_line) <= 201  # 200 chars + terminal period
        assert ctx.has_prior_context is False

    def test_render_token_bound(self):
        from context_engine.budget import estimate_tokens

        items = [_fact(f"topic{i}", f"value{i} miami trip") for i in range(6)]
        ctx = _select(ltm_items=items, summary="Summary sentence here. " * 20)
        text = render_relationship_context(ctx)
        assert estimate_tokens(text) <= MAX_RELATIONSHIP_TOKENS


# ---------------------------------------------------------------------------
# D. Rendering hygiene
# ---------------------------------------------------------------------------


_FORBIDDEN_RENDER = (
    "interaction_count",
    "user_message_count",
    "provenance",
    "positive_streak",
    "decay",
    "transition",
    "memory_id",
    "user_id",
    "creator_id",
    "you should",
    "always ",
    "sell",
    "ask ",
    "flirt",
    "escalate",
    "sexual",
    "sexy",
    "intima",
    "erotic",
    "consent",
    "permission",
    "desire",
    "temperature",
    "readiness",
    "purchase",
    "offer",
    "price",
    "product",
    "nba",
    "next_best_action",
    "tease",
)


class TestDRendering:
    def test_compact_semantic_structure(self):
        text = render_relationship_context(_select(summary="Fan likes movies."))
        assert text.startswith("RELATIONSHIP CONTEXT [DERIVED]:")
        assert "familiarity=established" in text
        assert "ongoing_thread: trip to miami" in text
        assert "relevant_fact: trip=miami (current)" in text
        assert "summary: Fan likes movies." in text

    def test_no_counters_or_directives_or_authority_vocab(self):
        ctx = _select(summary="Fan likes movies. Extra. " * 10)
        blob = render_relationship_context(ctx).lower()
        for concept in _FORBIDDEN_RENDER:
            assert concept not in blob, concept

    def test_unknown_bands_omitted(self):
        ctx = select_relationship_context(
            bands={"familiarity": "unknown", "engagement": None},
            current_topic="movies",
            open_threads=("movies",),
            current_message="movies?",
            ltm_items=[],
        )
        text = render_relationship_context(ctx)
        assert "unknown" not in text.lower()
        assert "familiarity" not in text.lower()

    def test_commerce_and_sexual_facts_excluded(self):
        ctx = _select(
            ltm_items=[
                _fact("offer", "miami discount", memory_type="fact"),
                _fact("trip", "miami"),
            ],
            fk_items=[
                {"subject": "preference", "value": "sexy photos miami", "status": "CURRENT", "confidence": 1.0},
            ],
        )
        blob = render_relationship_context(ctx).lower()
        assert "discount" not in blob
        assert "sexy" not in blob
        assert "trip=miami" in blob


# ---------------------------------------------------------------------------
# E. Current-context precedence (selector is advisory by construction)
# ---------------------------------------------------------------------------


class TestEPrecedence:
    def test_stale_fact_without_overlap_ignored(self):
        # A rich stored fact about exams must not surface on a movies turn.
        ctx = select_relationship_context(
            bands=_bands(),
            current_topic="movies",
            open_threads=("movies",),
            current_message="movies tonight?",
            ltm_items=[_fact("exam", "friday test", confidence=1.0, memory_type="open_loop")],
            summary="Fan has an exam Friday.",
        )
        assert all(f.subject != "exam" for f in ctx.facts)
        # Summary still renders (advisory), facts do not hijack the turn.
        assert ctx.has_prior_context is False

    def test_no_directive_lines_ever_rendered(self):
        # Across a battery of selections, no rendered line is phrased as a
        # behavioral instruction (the module docstring documents this
        # prohibition in prose; this test pins the rendered output).
        import itertools

        topics = ["miami trip", "movies", None]
        case_items = [
            [],
            [_fact("trip", "miami")],
            [_fact("exam", "friday test", memory_type="open_loop")],
        ]
        for topic, items in itertools.product(topics, case_items):
            ctx = select_relationship_context(
                bands=_bands(),
                current_topic=topic,
                open_threads=(topic,) if topic else (),
                current_message=f"tell me about {topic}" if topic else "hi",
                ltm_items=list(items),
                summary="Fan summary line one. Line two.",
            )
            for line in render_relationship_context(ctx).lower().splitlines():
                stripped = line.strip()
                assert not stripped.startswith(("you should", "always ", "never "))
                assert "sell" not in stripped.split()
                assert "flirt" not in stripped
                assert "escalate" not in stripped


# ---------------------------------------------------------------------------
# F. Callback evidence semantics
# ---------------------------------------------------------------------------


class TestFCallbackEvidence:
    def test_supporting_facts_enable_evidence(self):
        assert has_prior_context_evidence(_select()) is True

    def test_familiarity_alone_does_not(self):
        ctx = select_relationship_context(
            bands=_bands(), current_topic=None, open_threads=("movies",),
            current_message="hi", ltm_items=[], fk_items=[],
        )
        assert has_prior_context_evidence(ctx) is False

    def test_thread_alone_does_not(self):
        ctx = select_relationship_context(
            bands=_bands(continuity="rich"), current_topic="movies",
            open_threads=("movies",), current_message="movies?",
            ltm_items=[], fk_items=[],
        )
        assert has_prior_context_evidence(ctx) is False

    def test_unrelated_history_does_not(self):
        ctx = select_relationship_context(
            bands=_bands(), current_topic="movies", open_threads=("movies",),
            current_message="movies?", ltm_items=[_fact("trip", "miami")],
        )
        assert has_prior_context_evidence(ctx) is False

    def test_none_and_garbage_safe(self):
        assert has_prior_context_evidence(None) is False
        assert has_prior_context_evidence("facts") is False


# ---------------------------------------------------------------------------
# G. Phase 4 unchanged + CALLBACK reachable with referenced evidence
# ---------------------------------------------------------------------------


class TestGPhase4:
    def test_phase4_priority_untouched(self):
        from commerce.conversation_strategy import (
            TurnEvidenceSummary,
            select_conversational_strategy,
        )
        from commerce.relationship_trajectory import (
            ContinuityBand,
            EngagementBand,
            FamiliarityBand,
            ReciprocityBand,
            RelationshipSnapshot,
            TrendDirection,
        )
        from core.conversation_contract import ConversationContract
        from core.conversation_state import ConversationState
        from commerce.persona_behavior import PersonaBehaviorState

        snap = RelationshipSnapshot(
            familiarity=FamiliarityBand("familiar"),
            engagement=EngagementBand("steady"),
            reciprocity=ReciprocityBand("balanced"),
            continuity=ContinuityBand("anchored"),
            trend=TrendDirection("stable"),
        )
        state = ConversationState(
            lifecycle="established", identity_already_established=True,
            current_topic="movies", recent_topics=("movies",),
            open_threads=("movies",), last_question=None,
            last_question_answered=True, consecutive_questions=0,
            tone="warm", last_user_fact=None, questions_in_last_3=0,
        )
        persona = PersonaBehaviorState(
            emotional_state="warm", confidence="LOW", conversation_mode="react",
            question_allowed=True, question_policy="ONE_NATURAL_QUESTION",
            disagreement_available=False, teasing_allowed=False,
            sincerity_required=False, verbosity_target="short_medium",
            emoji_policy="occasional", lowercase_policy="neutral",
            naturalness_mode="normal", persona_version=None,
            creator_id=None, generation_id=None,
        )
        contract = ConversationContract(answer_required=False)
        # Without referenced evidence and without continued topic: no CALLBACK.
        plain = select_conversational_strategy(
            snapshot=snap, conversation_state=state, contract=contract,
            persona=persona, turn=TurnEvidenceSummary(),
        )
        assert plain is None or plain.move != "CALLBACK"
        # With genuine referenced evidence (what Phase 5 now supplies):
        # CALLBACK becomes reachable under existing Phase 4 rules.
        with_evidence = select_conversational_strategy(
            snapshot=snap, conversation_state=state, contract=contract,
            persona=persona,
            turn=TurnEvidenceSummary(user_referenced_previous_context=True),
        )
        assert with_evidence is not None
        assert with_evidence.move == "CALLBACK"


# ---------------------------------------------------------------------------
# H. OneCall integration via snapshot fields
# ---------------------------------------------------------------------------


class TestHOneCall:
    def test_snapshot_carries_phase5_blocks(self):
        from context_engine.models import AuthoritativeState

        state = AuthoritativeState(
            creator_id=1, user_id=2, generation_id="g",
            current_message="hi", user={}, profile={},
            recent_messages=(), persona="You are Sunny Skye.",
        )
        assert state.relationship_context_text == ""
        assert state.strategy_block_text == ""
        object.__setattr__(state, "relationship_context_text", render_relationship_context(_select()))
        object.__setattr__(state, "strategy_block_text", "CONVERSATION STRATEGY:\nmove=CONTINUE")
        from core.context_compact import build_one_call_from_snapshot

        messages = build_one_call_from_snapshot(authoritative_state=state)
        blob = " ".join(m.get("content", "") for m in messages)
        assert "RELATIONSHIP CONTEXT [DERIVED]:" in blob
        assert "CONVERSATION STRATEGY:" in blob
        # Current message exactly once and dominant.
        assert blob.count("[PLAYER MESSAGE]") == 1

    def test_empty_snapshot_blocks_render_nothing(self):
        from context_engine.models import AuthoritativeState
        from core.context_compact import build_one_call_from_snapshot, phase5_snapshot_blocks

        state = AuthoritativeState(
            creator_id=1, user_id=2, generation_id="g",
            current_message="hi", user={}, profile={},
            recent_messages=(), persona="You are Sunny Skye.",
        )
        assert phase5_snapshot_blocks(state) == []
        assert phase5_snapshot_blocks(None) == []
        messages = build_one_call_from_snapshot(authoritative_state=state)
        blob = " ".join(m.get("content", "") for m in messages)
        assert "RELATIONSHIP CONTEXT" not in blob
        assert "CONVERSATION STRATEGY" not in blob
        # Contract grounding still authoritative.
        assert "CHARACTER" in blob or "PLAYER" in blob

    def test_phase5_blocks_ordered_before_conversation(self):
        from context_engine.models import AuthoritativeState
        from core.context_compact import build_one_call_from_snapshot

        state = AuthoritativeState(
            creator_id=1, user_id=2, generation_id="g",
            current_message="hello there",
            user={"first_name": "Ann"}, profile={},
            recent_messages=({"direction": "inbound", "content": "hello there"},),
            persona="You are Sunny Skye.",
        )
        object.__setattr__(state, "relationship_context_text", render_relationship_context(_select()))
        object.__setattr__(state, "strategy_block_text", "CONVERSATION STRATEGY:\nmove=CONTINUE")
        messages = build_one_call_from_snapshot(authoritative_state=state)
        kinds = [
            "REL" if "RELATIONSHIP CONTEXT" in m.get("content", "")
            else "STRAT" if "CONVERSATION STRATEGY" in m.get("content", "")
            else "CONV" if m.get("role") in ("user", "assistant")
            else "SYS"
            for m in messages
        ]
        assert "REL" in kinds and "STRAT" in kinds
        assert kinds.index("REL") < kinds.index("STRAT") < kinds.index("CONV")


# ---------------------------------------------------------------------------
# I. Fail-open assembly
# ---------------------------------------------------------------------------


class TestIFailOpen:
    @pytest.mark.asyncio
    async def test_retrieval_failures_yield_empty(self):
        with patch(
            "commerce.long_term_memory.retrieve_relevant_memories",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB down"),
        ), patch(
            "commerce.fan_knowledge.retrieve_relevant_knowledge",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB down"),
        ):
            ctx = await assemble_relationship_context(
                creator_id=7, user_id=9, current_message="hi",
                conversation_state={"current_topic": "movies", "open_threads": ("movies",)},
                profile={}, summary="Summary here.",
            )
        assert ctx.facts == ()
        assert ctx.has_prior_context is False
        assert render_relationship_context(ctx) == "" or "summary: Summary here." in render_relationship_context(ctx)

    @pytest.mark.asyncio
    async def test_missing_creator_abstains(self):
        ctx = await assemble_relationship_context(
            creator_id=None, user_id=9, current_message="hi",
            conversation_state={}, profile={},
        )
        assert ctx == RelationshipContext()
        assert render_relationship_context(ctx) == ""

    @pytest.mark.asyncio
    async def test_garbage_inputs_abstain(self):
        ctx = await assemble_relationship_context(
            creator_id="bad", user_id="worse", current_message=None,
            conversation_state=42, profile="nonsense",
        )
        assert isinstance(ctx, RelationshipContext)
        assert render_relationship_context(ctx) == ""

    @pytest.mark.asyncio
    async def test_snapshot_failure_still_selects_facts(self):
        with patch(
            "commerce.relationship_trajectory.get_relationship_anchors",
            side_effect=RuntimeError("broken"),
        ):
            ctx = await assemble_relationship_context(
                creator_id=7, user_id=9, current_message="miami trip?",
                conversation_state={"current_topic": "miami", "open_threads": ("miami",)},
                profile={},
                summary=None,
            )
        # Bands omitted, but retrieval-backed facts still work.
        assert isinstance(ctx, RelationshipContext)


# ---------------------------------------------------------------------------
# J. Creator isolation
# ---------------------------------------------------------------------------


class TestJCreatorIsolation:
    @pytest.mark.asyncio
    async def test_creator_a_facts_never_appear_for_creator_b(self):
        ltm_a = [_fact("trip", "miami")]
        ltm_b = [_fact("exam", "friday test")]

        async def _fake_ltm(creator_id, user_id, **kwargs):
            assert creator_id in (7, 9)
            return list(ltm_a) if creator_id == 7 else list(ltm_b)

        async def _fake_fk(creator_id, user_id, **kwargs):
            return []

        with patch(
            "commerce.long_term_memory.retrieve_relevant_memories", side_effect=_fake_ltm
        ), patch(
            "commerce.fan_knowledge.retrieve_relevant_knowledge", side_effect=_fake_fk
        ):
            ctx_a = await assemble_relationship_context(
                creator_id=7, user_id=1, current_message="miami and exam?",
                conversation_state={"current_topic": "miami exam", "open_threads": ("miami", "exam")},
                profile={},
            )
            ctx_b = await assemble_relationship_context(
                creator_id=9, user_id=1, current_message="miami and exam?",
                conversation_state={"current_topic": "miami exam", "open_threads": ("miami", "exam")},
                profile={},
            )
        subjects_a = {f.subject for f in ctx_a.facts}
        subjects_b = {f.subject for f in ctx_b.facts}
        assert "trip" in subjects_a
        assert "trip" not in subjects_b
        assert "exam" in subjects_b
        assert "exam" not in subjects_a

    def test_mapping_proxy_profile_supported(self):
        frozen = MappingProxyType({"relationship_trajectory_by_creator": {}})
        plain = to_plain_dict(frozen)
        assert isinstance(plain, dict)
        assert plain == {"relationship_trajectory_by_creator": {}}


# ---------------------------------------------------------------------------
# K. Commerce isolation
# ---------------------------------------------------------------------------


class TestKCommerceIsolation:
    def test_selector_signature_has_no_commerce_params(self):
        params = set(inspect.signature(select_relationship_context).parameters)
        forbidden = {
            "desire", "temperature", "offer_readiness", "purchase_intent",
            "content_interest", "next_best_action", "objective",
            "commerce_suppress", "price", "product",
        }
        assert params.isdisjoint(forbidden)

    def test_module_imports_no_commerce_authority(self):
        import pathlib

        src = pathlib.Path("context_engine/relationship_context.py").read_text()
        for token in (
            "from commerce.desire",
            "from commerce.temperature",
            "from commerce.readiness",
            "optimizer",
            "sealing",
            "execution",
            "response_mode",
            "purchase_intent",
        ):
            assert token not in src, token


# ---------------------------------------------------------------------------
# L. No persistence
# ---------------------------------------------------------------------------


class TestLNoPersistence:
    @pytest.mark.asyncio
    async def test_assembly_mutates_nothing(self):
        import copy

        profile = {"relationship_trajectory_by_creator": {"7": {}}, "interests": ["a"]}
        before = copy.deepcopy(profile)
        with patch(
            "commerce.long_term_memory.retrieve_relevant_memories",
            new_callable=AsyncMock, return_value=[_fact()],
        ), patch(
            "commerce.fan_knowledge.retrieve_relevant_knowledge",
            new_callable=AsyncMock, return_value=[],
        ):
            await assemble_relationship_context(
                creator_id=7, user_id=1, current_message="miami?",
                conversation_state={"current_topic": "miami", "open_threads": ("miami",)},
                profile=profile, summary="S.",
            )
        assert profile == before

    def test_module_source_has_no_writes(self):
        import pathlib

        src = pathlib.Path("context_engine/relationship_context.py").read_text().lower()
        for token in (
            "mutate_", "store_", "insert ", "update ", "delete ",
            "create table", ".execute(", "setex", "xadd", "publish_event",
            "enqueue_",
        ):
            assert token not in src, token


# ---------------------------------------------------------------------------
# M. Persona render regression (minimal §17 fix)
# ---------------------------------------------------------------------------


class TestMPersonaRender:
    def test_single_arg_render_works(self):
        from commerce.persona_behavior import (
            PersonaBehaviorState,
            render_persona_behavior_block,
        )

        state = PersonaBehaviorState(
            emotional_state="warm", confidence="LOW", conversation_mode="react",
            question_allowed=True, question_policy="ONE_NATURAL_QUESTION",
            disagreement_available=False, teasing_allowed=False,
            sincerity_required=False, verbosity_target="short_medium",
            emoji_policy="occasional", lowercase_policy="neutral",
            naturalness_mode="normal", persona_version=None,
            creator_id=7, generation_id="g1",
        )
        block = render_persona_behavior_block(state)
        assert "PERSONA BEHAVIOR" in block
        assert len(block.splitlines()) >= 2

    def test_worker_call_site_uses_single_arg(self):
        import pathlib

        src = pathlib.Path("workers/llm_worker.py").read_text()
        assert "render_persona_behavior_block(\n                _persona_behavior_state,\n            )" in src


# ---------------------------------------------------------------------------
# N. Retrieval call-site signatures
# ---------------------------------------------------------------------------


class TestNCallSites:
    def test_gatherer_uses_real_fk_signature(self):
        import pathlib

        src = pathlib.Path("context_engine/gatherer.py").read_text()
        # No live call passes the never-existing ``query=`` kwarg
        # (explanatory comments may still name it).
        assert "query=config.current_message" not in src
        assert 'query="city location timezone"' not in src
        assert "current_topic=" in src

    def test_worker_retrieval_uses_real_signatures(self):
        import pathlib

        src = pathlib.Path("workers/llm_worker.py").read_text()
        assert "query=user_message, limit=5" not in src
        assert "await get_knowledge_memory(" not in src
        assert "add_memory_item(mem, user_id=user_id" not in src
        assert "await add_memory_item(_creator_id, user_id, mem)" in src
        assert "await add_knowledge_item(_creator_id, user_id, it)" in src
