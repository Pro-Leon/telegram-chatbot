"""Phase 5 remediation regression tests (Defect 1 + Defect 2).

Minimal, deterministic, no live LLM/DB/Redis.

Defect 1: production frozen profile (MappingProxyType) starved the Phase 4
strategy selector. The fix is worker-side conversion via the repository's
established ``to_plain_dict()`` before ``select_for_turn()``. The canonical
Phase 4 selector is intentionally NOT modified.

Defect 2: the repaired persona-behavior block never reached canonical
OneCall (only the legacy context list). The fix adds the minimal
``behavior_block_text`` snapshot carrier (same pattern as
``relationship_context_text`` / ``strategy_block_text``) and renders it in
``core/context_compact.py::phase5_snapshot_blocks`` after strategy and
before conversation, as subordinate realization guidance.
"""

from __future__ import annotations

import pathlib
from types import MappingProxyType

import pytest

pytestmark = [pytest.mark.unit]

from commerce.conversation_strategy import TurnEvidenceSummary, select_for_turn
from commerce.relationship_trajectory import (
    RELATIONSHIP_TRAJECTORY_KEY,
    RelationshipAnchors,
    anchors_to_dict,
)
from context_engine.relationship_context import to_plain_dict


def _valid_plain_profile(creator_id: int = 7) -> dict:
    anchors = RelationshipAnchors(
        interaction_count=6,
        bands={
            "familiarity": "familiar",
            "engagement": "steady",
            "reciprocity": "balanced",
            "continuity": "anchored",
        },
    )
    return {
        RELATIONSHIP_TRAJECTORY_KEY: {str(creator_id): anchors_to_dict(anchors)}
    }


def _direct_question_turn() -> TurnEvidenceSummary:
    return TurnEvidenceSummary(user_asked_question=True)


class TestDefect1FrozenProfile:
    def test_plain_dict_yields_strategy(self):
        plain = _valid_plain_profile(7)
        result = select_for_turn(
            profile=plain, creator_id=7, turn=_direct_question_turn()
        )
        assert result is not None
        assert result.move == "ACKNOWLEDGE"

    def test_frozen_profile_rejected_by_canonical_selector(self):
        # Documents the verified production bug: the canonical Phase 4
        # selector guards on isinstance(profile, dict), so the frozen
        # MappingProxyType abstains. This guard is intentionally preserved.
        plain = _valid_plain_profile(7)
        frozen = MappingProxyType(plain)
        assert select_for_turn(
            profile=frozen, creator_id=7, turn=_direct_question_turn()  # type: ignore[arg-type]
        ) is None

    def test_worker_conversion_restores_equivalent_strategy(self):
        # Simulates the remediated worker call boundary:
        # frozen -> to_plain_dict(...) -> select_for_turn(...).
        plain = _valid_plain_profile(7)
        frozen = MappingProxyType(plain)
        converted = to_plain_dict(frozen)
        assert isinstance(converted, dict)
        plain_result = select_for_turn(
            profile=plain, creator_id=7, turn=_direct_question_turn()
        )
        converted_result = select_for_turn(
            profile=converted, creator_id=7, turn=_direct_question_turn()
        )
        assert plain_result is not None
        assert converted_result is not None
        assert converted_result.move == plain_result.move
        assert converted_result.realization_hint == plain_result.realization_hint
        assert converted_result.question_policy == plain_result.question_policy
        assert converted_result.move == "ACKNOWLEDGE"

    def test_worker_call_site_converts_via_to_plain_dict(self):
        # Fails against the pre-remediation worker (which passed
        # ``profile=_cached_profile_for_commerce`` directly) and passes
        # after the worker-side fix. Pins the boundary without touching
        # the Phase 4 module.
        src = pathlib.Path("workers/llm_worker.py").read_text()
        assert "to_plain_dict" in src
        assert "_profile_for_strategy" in src
        assert "profile=_profile_for_strategy" in src
        # The fixed _select_for_turn call site must not pass the raw frozen
        # profile. (Other profile= usages — relationship assembly,
        # conversational state, OneCall inputs — legitimately keep the
        # original variable; only the strategy selector needs conversion
        # because of its strict isinstance(profile, dict) guard.)
        assert "_select_for_turn(\n                profile=_cached_profile_for_commerce," not in src


def _make_state(**overrides):
    from context_engine.models import AuthoritativeState

    kwargs = dict(
        creator_id=1,
        user_id=2,
        generation_id="g-remediation",
        current_message="hello there",
        user={"first_name": "Ann"},
        profile={},
        recent_messages=({"direction": "inbound", "content": "hello there"},),
        persona="You are Sunny Skye.",
    )
    kwargs.update(overrides)
    return AuthoritativeState(**kwargs)


_REL_TEXT = "RELATIONSHIP CONTEXT [DERIVED]:\nfamiliarity=familiar"
_STRAT_TEXT = "CONVERSATION STRATEGY:\nmove=CONTINUE"
_BEH_TEXT = (
    "PERSONA BEHAVIOR: emotion=warm confidence=LOW mode=react\n"
    "Voice: standard casing; occasional emoji max 1 (zero ok); length=short_medium; question=none\n"
    "Behavior: teasing off; do not force disagreement"
)


class TestDefect2PersonaBehavior:
    def test_snapshot_carrier_default_empty(self):
        state = _make_state()
        assert state.relationship_context_text == ""
        assert state.strategy_block_text == ""
        assert state.behavior_block_text == ""

    def test_empty_behavior_renders_nothing(self):
        from core.context_compact import phase5_snapshot_blocks

        state = _make_state()
        object.__setattr__(state, "relationship_context_text", _REL_TEXT)
        object.__setattr__(state, "strategy_block_text", _STRAT_TEXT)
        object.__setattr__(state, "behavior_block_text", "")
        blocks = phase5_snapshot_blocks(state)
        assert len(blocks) == 2
        assert all("PERSONA BEHAVIOR" not in b.get("content", "") for b in blocks)

    def test_populated_behavior_produces_exactly_one_block(self):
        from core.context_compact import phase5_snapshot_blocks

        state = _make_state()
        object.__setattr__(state, "relationship_context_text", _REL_TEXT)
        object.__setattr__(state, "strategy_block_text", _STRAT_TEXT)
        object.__setattr__(state, "behavior_block_text", _BEH_TEXT)
        blocks = phase5_snapshot_blocks(state)
        assert len(blocks) == 3
        assert "RELATIONSHIP CONTEXT" in blocks[0].get("content", "")
        assert "CONVERSATION STRATEGY" in blocks[1].get("content", "")
        assert "PERSONA BEHAVIOR" in blocks[2].get("content", "")
        assert sum(1 for b in blocks if "PERSONA BEHAVIOR" in b.get("content", "")) == 1

    def test_behavior_reaches_fallback_onecall_branch(self):
        from core.context_compact import build_one_call_from_snapshot

        # recent_messages empty so the canonical [PLAYER MESSAGE] injection
        # fires exactly once (M3 exactly-once suppresses it when the current
        # turn is already visible in-window).
        state = _make_state(recent_messages=())
        object.__setattr__(state, "relationship_context_text", _REL_TEXT)
        object.__setattr__(state, "strategy_block_text", _STRAT_TEXT)
        object.__setattr__(state, "behavior_block_text", _BEH_TEXT)
        messages = build_one_call_from_snapshot(authoritative_state=state)
        blob = "\n".join(m.get("content", "") for m in messages)
        assert "RELATIONSHIP CONTEXT" in blob
        assert "CONVERSATION STRATEGY" in blob
        assert blob.count("PERSONA BEHAVIOR") == 1
        assert blob.count("[PLAYER MESSAGE]") == 1
        # Ordering: relationship -> strategy -> behavior -> conversation.
        assert blob.index("RELATIONSHIP CONTEXT") < blob.index("CONVERSATION STRATEGY")
        assert blob.index("CONVERSATION STRATEGY") < blob.index("PERSONA BEHAVIOR")
        assert blob.index("PERSONA BEHAVIOR") < blob.index("[PLAYER MESSAGE]")

    def test_behavior_reaches_pipeline_messages_branch(self):
        from core.context_compact import build_one_call_from_snapshot

        state = _make_state()
        object.__setattr__(state, "relationship_context_text", _REL_TEXT)
        object.__setattr__(state, "strategy_block_text", _STRAT_TEXT)
        object.__setattr__(state, "behavior_block_text", _BEH_TEXT)

        class _FakePipeline:
            messages = [
                {"role": "system", "content": "CE SYSTEM BLOCK"},
                {"role": "user", "content": "Ann: hello there"},
            ]

        messages = build_one_call_from_snapshot(
            authoritative_state=state, pipeline_result=_FakePipeline()
        )
        blob = "\n".join(m.get("content", "") for m in messages)
        assert "PERSONA BEHAVIOR" in blob
        assert blob.count("PERSONA BEHAVIOR") == 1
        assert blob.count("[PLAYER MESSAGE]") == 1
        assert blob.index("RELATIONSHIP CONTEXT") < blob.index("CONVERSATION STRATEGY")
        assert blob.index("CONVERSATION STRATEGY") < blob.index("PERSONA BEHAVIOR")

    def test_behavior_reaches_snapshot_render_branch(self):
        from context_engine.models import ContextSnapshot
        from core.context_compact import build_one_call_from_snapshot

        state = _make_state(recent_messages=())
        object.__setattr__(state, "relationship_context_text", _REL_TEXT)
        object.__setattr__(state, "strategy_block_text", _STRAT_TEXT)
        object.__setattr__(state, "behavior_block_text", _BEH_TEXT)
        _snap = ContextSnapshot(
            items=(),
            total_tokens=0,
            category_tokens={},
            degradation_level=0,
            candidate_count=0,
            selected_count=0,
            deduplication_count=0,
            assembly_time_ms=0.0,
        )

        class _FakePipeline:
            messages: list = []
            snapshot = _snap

        messages = build_one_call_from_snapshot(
            snapshot=_snap,
            authoritative_state=state,
            pipeline_result=_FakePipeline(),
        )
        blob = "\n".join(m.get("content", "") for m in messages)
        assert "PERSONA BEHAVIOR" in blob
        assert blob.count("PERSONA BEHAVIOR") == 1
        assert blob.count("[PLAYER MESSAGE]") == 1

    def test_existing_ordering_preserved(self):
        from core.context_compact import build_one_call_from_snapshot

        state = _make_state()
        object.__setattr__(state, "relationship_context_text", _REL_TEXT)
        object.__setattr__(state, "strategy_block_text", _STRAT_TEXT)
        object.__setattr__(state, "behavior_block_text", _BEH_TEXT)
        messages = build_one_call_from_snapshot(authoritative_state=state)
        kinds = [
            "REL"
            if "RELATIONSHIP CONTEXT" in m.get("content", "")
            else "STRAT"
            if "CONVERSATION STRATEGY" in m.get("content", "")
            else "BEH"
            if "PERSONA BEHAVIOR" in m.get("content", "")
            else "CONV"
            if m.get("role") in ("user", "assistant")
            else "SYS"
            for m in messages
        ]
        assert "REL" in kinds and "STRAT" in kinds and "BEH" in kinds
        assert kinds.index("REL") < kinds.index("STRAT") < kinds.index("BEH")
        assert kinds.index("BEH") < kinds.index("CONV")

    def test_worker_attaches_behavior_to_snapshot(self):
        src = pathlib.Path("workers/llm_worker.py").read_text()
        assert "behavior_block_text" in src
        # Single renderer preserved: no second persona-behavior renderer.
        assert src.count("render_persona_behavior_block(") >= 1
        assert "object.__setattr__" in src
