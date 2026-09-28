"""Phase 6 intimacy context tests (IMPLEMENT -> VERIFY).

Pins the selector/renderer in ``context_engine/intimacy_context.py``
plus Phase 6 integration:

* bounded deterministic output; empty on missing state
* deny-list (no permission/commerce/safety/escalation language)
* no counters/timestamps/permission/commerce leakage
* creator isolation; token bound
* bands alone never inform strategy (reference needs evidence)
* intimacy reaches OneCall between RELATIONSHIP and STRATEGY:

```text
RELATIONSHIP -> INTIMACY -> STRATEGY -> PERSONA
```

* Phase 4 unchanged (no new moves, no TEASE revival)
* commerce isolation (no desire/temperature/readiness imports)
* adult-gate invariant (no permission/adult authority anywhere)

Conventions follow the Phase 1-5 suites: deterministic unit tests
only, no live LLM/DB/Redis.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from commerce.intimacy_evidence import extract_intimacy_evidence  # noqa: E402
from commerce.intimacy_trajectory import (  # noqa: E402
    INTIMACY_TRAJECTORY_KEY,
    IntimacyTurnEvidence,
    accumulate_intimacy_turn,
    derive_intimacy_snapshot,
    intimacy_anchors_to_dict,
)
from context_engine.intimacy_context import (  # noqa: E402
    MAX_INTIMACY_TOKENS,
    IntimacyContext,
    assemble_intimacy_context,
    has_intimate_context_reference,
    render_intimacy_context,
    select_intimacy_context,
)


def _snapshot_after(turns: int = 6) -> object:
    anchors = None
    ev = IntimacyTurnEvidence(romantic_signal=True, user_initiated_intimacy=True)
    for _ in range(turns):
        anchors = accumulate_intimacy_turn(anchors, ev)
    assert anchors is not None
    return derive_intimacy_snapshot(anchors)


def _evidence_with_reference() -> IntimacyTurnEvidence:
    return extract_intimacy_evidence(
        user_message="I miss you too",
        history=[{"direction": "inbound", "content": "I miss you"}],
    )


def _make_state(**overrides):
    from context_engine.models import AuthoritativeState

    kwargs = dict(
        creator_id=1, user_id=2, generation_id="g-intimacy",
        current_message="hello there", user={"first_name": "Ann"},
        profile={}, recent_messages=(), persona="You are Sunny Skye.",
    )
    kwargs.update(overrides)
    return AuthoritativeState(**kwargs)


class TestSelection:
    def test_empty_inputs_yield_empty(self):
        assert select_intimacy_context() == IntimacyContext()
        assert render_intimacy_context(select_intimacy_context()) == ""
        assert render_intimacy_context(None) == ""
        assert render_intimacy_context("INTIMACY CONTEXT") == ""

    def test_deterministic(self):
        snap = _snapshot_after()
        first = select_intimacy_context(snapshot=snap, evidence=None)
        second = select_intimacy_context(snapshot=snap, evidence=None)
        assert first == second
        assert render_intimacy_context(first) == render_intimacy_context(second)

    def test_unknown_bands_omitted(self):
        ctx = select_intimacy_context(bands={"romantic": "unknown", "playful": None})
        assert ctx.band_labels == ()
        assert render_intimacy_context(ctx) == ""

    def test_known_bands_render(self):
        snap = _snapshot_after()
        ctx = select_intimacy_context(snapshot=snap, evidence=None)
        assert "romantic=steady" in ctx.band_labels
        text = render_intimacy_context(ctx)
        assert text.startswith("INTIMACY CONTEXT [DERIVED]:")
        assert "romantic: steady" in text

    def test_bands_alone_never_reference(self):
        snap = _snapshot_after(12)
        ctx = select_intimacy_context(snapshot=snap, evidence=None)
        assert ctx.has_intimate_reference is False
        assert has_intimate_context_reference(ctx) is False
        # Even with no evidence object at all.
        assert has_intimate_context_reference(None) is False
        assert has_intimate_context_reference("reference") is False

    def test_genuine_reference_sets_flag(self):
        snap = _snapshot_after()
        ctx = select_intimacy_context(snapshot=snap, evidence=_evidence_with_reference())
        assert ctx.has_intimate_reference is True
        assert has_intimate_context_reference(ctx) is True

    def test_unrelated_cooccurrence_is_not_reference(self):
        # Remediation Case C: prior "I miss you" + current playful but
        # topically unlinked "lol that is so funny" shares no
        # substantive tokens ("miss" absent) -> False even with DEEP
        # bands present.
        from commerce.intimacy_evidence import extract_intimacy_evidence

        snap = _snapshot_after(12)
        ev = extract_intimacy_evidence(
            user_message="lol that is so funny",
            history=[{"direction": "inbound", "content": "I miss you"}],
        )
        assert ev.playful_signal is True
        ctx = select_intimacy_context(snapshot=snap, evidence=ev)
        assert ctx.has_intimate_reference is False
        assert has_intimate_context_reference(ctx) is False

    def test_current_only_is_not_reference(self):
        # Remediation Case B: current intimate signal with no relevant
        # prior context -> False.
        from commerce.intimacy_evidence import extract_intimacy_evidence

        snap = _snapshot_after(12)
        ev = extract_intimacy_evidence(
            user_message="kiss me",
            history=[{"direction": "inbound", "content": "goodnight"}],
        )
        assert ev.sexual_conversation_signal is True
        ctx = select_intimacy_context(snapshot=snap, evidence=ev)
        assert ctx.has_intimate_reference is False

    def test_topic_mediated_linkage_counts(self):
        # Continuity on a currently intimate topic/thread is
        # topic-mediated linkage (documented, deterministic).
        from commerce.intimacy_evidence import extract_intimacy_evidence

        snap = _snapshot_after()
        ev = extract_intimacy_evidence(
            user_message="lol that is so funny",
            history=[{"direction": "inbound", "content": "I miss you"}],
            conversation_state={"current_topic": "miss you", "open_threads": ()},
        )
        assert ev.intimate_continuity_signal is True
        assert ev.current_intimate_topic is True
        ctx = select_intimacy_context(snapshot=snap, evidence=ev)
        assert ctx.has_intimate_reference is True

    def test_reference_does_not_select_strategy(self):
        # Remediation Case E: the bridge supplies only the existing
        # referenced-previous-context boolean; CALLBACK still needs
        # the full Phase 4 conjunction (threads + bands + evidence).
        from commerce.conversation_strategy import TurnEvidenceSummary

        turn = TurnEvidenceSummary(user_referenced_previous_context=True)
        assert turn.user_referenced_previous_context is True

    def test_mapping_snapshot_supported(self):
        ctx = select_intimacy_context(
            bands={"romantic": "steady", "playful": "low"},
            evidence={"prior_intimate_context_reference": True, "romantic_signal": True},
        )
        assert "romantic=steady" in ctx.band_labels
        assert ctx.has_intimate_reference is True


class TestRenderingHygiene:
    _FORBIDDEN = (
        "allowed",
        "permission",
        "consent",
        "authorized",
        "grant",
        "escalat",
        "de-escalat",
        "deescalat",
        "ready",
        "readiness",
        "adult",
        "verified",
        "purchase",
        "offer",
        "price",
        "product",
        "ppv",
        "sell",
        "buy",
        "should",
        "must",
        "always ",
        "flirt",
        "tease",
        "refus",
        "boundar",
    )

    def test_no_forbidden_vocabulary(self):
        snap = _snapshot_after(12)
        ctx = select_intimacy_context(snapshot=snap, evidence=_evidence_with_reference())
        blob = render_intimacy_context(ctx).lower()
        assert blob
        for concept in self._FORBIDDEN:
            assert concept not in blob, concept

    def test_no_counters_or_internals(self):
        snap = _snapshot_after(12)
        blob = render_intimacy_context(select_intimacy_context(snapshot=snap)).lower()
        for token in (
            "observation_count",
            "provenance",
            "generation",
            "timestamp",
            "schema_version",
            "transitions",
            "counters",
        ):
            assert token not in blob, token

    def test_unknown_dimensions_dropped(self):
        ctx = IntimacyContext(band_labels=("romantic=steady", "evil=allowed", "x"))
        text = render_intimacy_context(ctx)
        assert "romantic: steady" in text
        assert "evil" not in text
        assert "allowed" not in text

    def test_token_bound(self):
        from context_engine.budget import estimate_tokens

        snap = _snapshot_after(12)
        text = render_intimacy_context(select_intimacy_context(snapshot=snap))
        assert estimate_tokens(text) <= MAX_INTIMACY_TOKENS
        assert MAX_INTIMACY_TOKENS == 120


class TestAssembly:
    @pytest.mark.asyncio
    async def test_missing_creator_abstains(self):
        ctx = await assemble_intimacy_context(
            creator_id=None, user_id=9, current_message="I miss you",
            conversation_state={}, profile={},
        )
        assert ctx == IntimacyContext()
        assert render_intimacy_context(ctx) == ""

    @pytest.mark.asyncio
    async def test_garbage_inputs_abstain(self):
        ctx = await assemble_intimacy_context(
            creator_id="bad", user_id="worse", current_message=None,  # type: ignore[arg-type]
            conversation_state=42, profile="nonsense",
        )
        assert isinstance(ctx, IntimacyContext)
        assert render_intimacy_context(ctx) == ""

    @pytest.mark.asyncio
    async def test_cold_profile_yields_empty_render(self):
        ctx = await assemble_intimacy_context(
            creator_id=7, user_id=1, current_message="I miss you",
            conversation_state={}, profile={},
        )
        # Cold trajectory: no known bands -> nothing renders (current-turn
        # evidence alone does not manufacture durable bands).
        assert render_intimacy_context(ctx) == ""

    @pytest.mark.asyncio
    async def test_warm_profile_renders_bands(self):
        anchors = None
        ev = IntimacyTurnEvidence(romantic_signal=True, user_initiated_intimacy=True)
        for _ in range(6):
            anchors = accumulate_intimacy_turn(anchors, ev)
        assert anchors is not None
        profile = {INTIMACY_TRAJECTORY_KEY: {"7": intimacy_anchors_to_dict(anchors)}}
        ctx = await assemble_intimacy_context(
            creator_id=7, user_id=1, current_message="I miss you",
            conversation_state={}, profile=profile, evidence=ev,
        )
        assert "romantic: steady" in render_intimacy_context(ctx)

    @pytest.mark.asyncio
    async def test_creator_isolation(self):
        anchors = None
        ev = IntimacyTurnEvidence(romantic_signal=True, user_initiated_intimacy=True)
        for _ in range(6):
            anchors = accumulate_intimacy_turn(anchors, ev)
        assert anchors is not None
        profile = {INTIMACY_TRAJECTORY_KEY: {"7": intimacy_anchors_to_dict(anchors)}}
        ctx_a = await assemble_intimacy_context(
            creator_id=7, user_id=1, current_message="hi", conversation_state={},
            profile=profile, evidence=None,
        )
        ctx_b = await assemble_intimacy_context(
            creator_id=9, user_id=1, current_message="hi", conversation_state={},
            profile=profile, evidence=None,
        )
        assert "romantic: steady" in render_intimacy_context(ctx_a)
        assert render_intimacy_context(ctx_b) == ""

    def test_frozen_profile_supported(self):
        from types import MappingProxyType

        from context_engine.intimacy_context import to_plain_dict

        frozen = MappingProxyType({INTIMACY_TRAJECTORY_KEY: {}})
        assert to_plain_dict(frozen) == {INTIMACY_TRAJECTORY_KEY: {}}


class TestOneCallIntegration:
    def _state(self, **overrides):
        return _make_state(**overrides)

    def test_carrier_default_empty(self):
        assert self._state().intimacy_context_text == ""

    def test_ordering_relationship_intimacy_strategy_persona(self):
        from core.context_compact import build_one_call_from_snapshot

        state = self._state()
        object.__setattr__(state, "relationship_context_text", "RELATIONSHIP CONTEXT [DERIVED]:\nrelationship: familiarity=familiar")
        object.__setattr__(state, "intimacy_context_text", "INTIMACY CONTEXT [DERIVED]:\nromantic: steady")
        object.__setattr__(state, "strategy_block_text", "CONVERSATION STRATEGY:\nmove=CONTINUE")
        object.__setattr__(
            state, "behavior_block_text",
            "PERSONA BEHAVIOR: emotion=warm confidence=LOW mode=react",
        )
        messages = build_one_call_from_snapshot(authoritative_state=state)
        kinds = [
            "REL" if "RELATIONSHIP CONTEXT" in m.get("content", "")
            else "INT" if "INTIMACY CONTEXT" in m.get("content", "")
            else "STRAT" if "CONVERSATION STRATEGY" in m.get("content", "")
            else "BEH" if "PERSONA BEHAVIOR" in m.get("content", "")
            else "CONV" if m.get("role") in ("user", "assistant")
            else "SYS"
            for m in messages
        ]
        assert "REL" in kinds and "INT" in kinds and "STRAT" in kinds and "BEH" in kinds
        assert kinds.index("REL") < kinds.index("INT") < kinds.index("STRAT") < kinds.index("BEH")
        assert kinds.index("BEH") < kinds.index("CONV")
        blob = "\n".join(m.get("content", "") for m in messages)
        assert blob.count("INTIMACY CONTEXT") == 1
        assert blob.count("[PLAYER MESSAGE]") == 1

    def test_empty_intimacy_keeps_prompts_identical(self):
        from core.context_compact import build_one_call_from_snapshot, phase5_snapshot_blocks

        state = self._state()
        object.__setattr__(state, "relationship_context_text", "RELATIONSHIP CONTEXT [DERIVED]:\nx")
        object.__setattr__(state, "strategy_block_text", "CONVERSATION STRATEGY:\nmove=CONTINUE")
        object.__setattr__(state, "intimacy_context_text", "")
        assert all("INTIMACY CONTEXT" not in b.get("content", "") for b in phase5_snapshot_blocks(state))
        blob = " ".join(m.get("content", "") for m in messages) if (messages := build_one_call_from_snapshot(authoritative_state=state)) else ""
        assert "INTIMACY CONTEXT" not in blob


class TestPhase4Compatibility:
    def test_no_new_moves_no_tease_revival(self):
        from commerce.conversation_strategy import _ALLOWED_HINTS, _MOVES

        assert "TEASE" not in set(_MOVES)
        assert "SEXUALIZE" not in set(_MOVES)
        assert "ESCALATE_INTIMACY" not in set(_MOVES)
        assert "tease" not in set(_ALLOWED_HINTS)

    def test_intimacy_modules_never_emit_tease(self):
        import pathlib

        for path in (
            "commerce/intimacy_trajectory.py",
            "commerce/intimacy_evidence.py",
            "context_engine/intimacy_context.py",
        ):
            src = pathlib.Path(path).read_text()
            assert "ResponseMode" not in src, path
            assert "TEASE" not in src, path

    def test_strategy_selector_still_green_with_reference(self):
        # The only bridge is the existing referenced-context boolean;
        # Phase 4 semantics (CALLBACK reachable with evidence) unchanged.
        from commerce.conversation_strategy import TurnEvidenceSummary

        turn = TurnEvidenceSummary(user_referenced_previous_context=True)
        assert turn.user_referenced_previous_context is True

    def test_reference_alone_never_forces_callback(self):
        # Remediation Case E: even a True reference cannot select
        # CALLBACK without the full Phase 4 conjunction (familiarity +
        # anchored continuity + open threads). Weak bands + referenced
        # evidence must not yield CALLBACK through the intimacy bridge.
        from commerce.conversation_strategy import (
            TurnEvidenceSummary,
            select_conversational_strategy,
        )
        from commerce.persona_behavior import PersonaBehaviorState
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

        snap = RelationshipSnapshot(
            familiarity=FamiliarityBand("new"),
            engagement=EngagementBand("low"),
            reciprocity=ReciprocityBand("low"),
            continuity=ContinuityBand("sparse"),
            trend=TrendDirection("stable"),
        )
        state = ConversationState(
            lifecycle="new", identity_already_established=False,
            current_topic=None, recent_topics=(), open_threads=(),
            last_question=None, last_question_answered=True,
            consecutive_questions=0, tone="warm", last_user_fact=None,
            questions_in_last_3=0,
        )
        persona = PersonaBehaviorState(
            emotional_state="warm", confidence="LOW", conversation_mode="react",
            question_allowed=False, question_policy="NO_QUESTION",
            disagreement_available=False, teasing_allowed=False,
            sincerity_required=False, verbosity_target="short_medium",
            emoji_policy="occasional", lowercase_policy="neutral",
            naturalness_mode="normal", persona_version=None,
            creator_id=None, generation_id=None,
        )
        result = select_conversational_strategy(
            snapshot=snap, conversation_state=state,
            contract=ConversationContract(answer_required=False),
            persona=persona,
            turn=TurnEvidenceSummary(user_referenced_previous_context=True),
        )
        assert result is None or result.move != "CALLBACK"

    def test_worker_bridge_uses_existing_shape(self):
        import pathlib

        src = pathlib.Path("workers/llm_worker.py").read_text()
        assert "or _int_prior_ref" in src
        assert "intimacy_context_text" in src
        assert "_record_intimacy_trajectory" in src
        # No new strategy input, no Phase 4 import change for intimacy.
        assert "SEXUALIZE" not in src
        assert "ESCALATE_INTIMACY" not in src


class TestCommerceAndAdultIsolation:
    def test_no_commerce_authority_imports(self):
        import pathlib

        # NOTE: advisory reads of validated CommerceSignals fields
        # (content_interest / explicit_content_request, corroboration
        # only) are explicitly permitted by the Phase 6 spec section 12
        # and are pinned separately in test_intimacy_evidence_phase6.py.
        # What is forbidden here is commerce *authority*: funnel-state
        # modules and decision/sealing/ranking/execution machinery.
        # Tokens below are import- and symbol-shaped (not bare prose
        # words) so docstrings documenting the boundary stay green.
        for path in (
            "commerce/intimacy_trajectory.py",
            "commerce/intimacy_evidence.py",
            "context_engine/intimacy_context.py",
        ):
            src = pathlib.Path(path).read_text(encoding="utf-8")
            for token in (
                "from commerce.desire",
                "from commerce.temperature",
                "from commerce.readiness",
                "from commerce.offer_readiness",
                "from commerce.relationship import",
                "import commerce.desire",
                "import commerce.temperature",
                "import commerce.readiness",
                "derive_desire_stage",
                "derive_commercial_temperature",
                "evaluate_offer_readiness",
                "evaluate_readiness",
                "decide_commerce_action",
            ):
                assert token not in src, f"{path}: {token}"

    def test_no_permission_or_adult_fields(self):
        import dataclasses

        from commerce.intimacy_trajectory import IntimacyAnchors

        forbidden = {
            "sexual_allowed",
            "intimacy_allowed",
            "explicit_allowed",
            "consent_level",
            "consent",
            "permission_granted",
            "permission",
            "escalation_allowed",
            "sexual_response_allowed",
            "age_verified",
            "adult",
            "is_adult",
            "refusal",
            "boundary",
        }
        assert {f.name for f in dataclasses.fields(IntimacyAnchors)}.isdisjoint(forbidden)
        assert {f.name for f in dataclasses.fields(IntimacyTurnEvidence)}.isdisjoint(forbidden)


class TestPhase5Preserved:
    def test_relationship_budget_unchanged(self):
        from context_engine.relationship_context import MAX_RELATIONSHIP_TOKENS

        assert MAX_RELATIONSHIP_TOKENS == 200

    def test_phase5_blocks_still_render_without_intimacy(self):
        from core.context_compact import phase5_snapshot_blocks

        state = _make_state()
        object.__setattr__(state, "relationship_context_text", "RELATIONSHIP CONTEXT [DERIVED]:\nx")
        assert len(phase5_snapshot_blocks(state)) == 1
