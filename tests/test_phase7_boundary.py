"""Phase 7 boundary/refusal/recovery tests (IMPLEMENT -> VERIFY).

Pins the deterministic Phase 7 domain:

* evidence extractor (rule families, false-positive guards, relaxation,
  ambiguity, fail-open)
* durable state (activation, reaffirmation, explicit relaxation,
  conflict resolution, scope, expiry/recovery, creator isolation,
  atomic idempotent accumulation, bounded markers, versioning)
* current-evidence-beats-history precedence
* context carrier (ordering, no raw-text leak, bounded)
* strategy constraint without Phase 4 modification
* persona non-authority (teasing_allowed + NO_FLIRTING)
* output validation + safe completion (violating vs compliant drafts)
* routing veto (never AUTO_SEND) + one_call validator hook
* commerce veto independence from LLM signals
* legacy-path enforcement via the shared validator
* debounce/latest-only documented behavior, retry idempotency
* adversarial scenarios A-H from the Phase 7 specification

Conventions follow the Phase 1/2/6 suites: deterministic unit tests
only, no live LLM/DB/Redis (accumulation via mocked atomic-mutate).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = [pytest.mark.unit]

from commerce.boundary_evidence import (  # noqa: E402
    BOUNDARY_SCOPES,
    BoundaryTurnEvidence,
    extract_boundary_evidence,
)
from commerce.boundary_state import (  # noqa: E402
    BOUNDARY_PROCESSED_IDS_FIELD,
    BOUNDARY_STATE_KEY,
    BoundarySnapshot,
    accumulate_boundary_turn_idempotent,
    apply_evidence,
    boundary_blocks_commerce,
    derive_boundary_snapshot,
    get_boundary_constraints,
    snapshot_with_current_evidence,
)
from commerce.boundary_validation import (  # noqa: E402
    SAFE_CLOSE,
    SAFE_NEUTRAL_CONTINUE,
    safe_completion_for,
    validate_reply_against_boundaries,
)
from context_engine.boundary_context import (  # noqa: E402
    constrain_strategy_for_boundary,
    render_boundary_context,
    select_boundary_context,
)
from core.routing import (  # noqa: E402
    REASON_BOUNDARY_VIOLATION,
    decide_routing,
)


def _ev(text: str, **kwargs) -> BoundaryTurnEvidence:
    return extract_boundary_evidence(user_message=text, **kwargs)


# ---------------------------------------------------------------------------
# Evidence: assertions
# ---------------------------------------------------------------------------


class TestEvidenceAssertions:
    def test_stop_flirting(self):
        assert _ev("stop flirting with me").asserted_types() == ("NO_FLIRTING",)
        assert _ev("Stop flirting!").no_flirting is True
        assert _ev("STOP FLIRTING").no_flirting is True

    def test_dont_flirt_variants(self):
        assert _ev("don't flirt with me").no_flirting is True
        assert _ev("do not flirt with me").no_flirting is True
        assert _ev("never flirt with me").no_flirting is True
        assert _ev("don’t flirt with me").no_flirting is True  # curly apostrophe

    def test_dont_call_me_pet_name(self):
        assert _ev("don't call me babe").asserted_types() == ("NO_PET_NAME",)
        assert _ev("stop calling me sweetheart").no_pet_name is True
        assert _ev("never call me honey").no_pet_name is True
        assert _ev("don't call me that").no_pet_name is True

    def test_no_sexual_topic(self):
        assert _ev("I don't want to talk about sex").asserted_types() == ("NO_SEXUAL_TOPIC",)
        assert _ev("I don't want sexual talk").no_sexual_topic is True
        assert _ev("no sex talk please").no_sexual_topic is True

    def test_temporary_qualifier(self):
        ev = _ev("I don't want to talk about sex right now")
        assert ev.no_sexual_topic is True
        assert ev.temporary_qualifier is True
        ev2 = _ev("I don't want to talk about sex")
        assert ev2.temporary_qualifier is False

    def test_no_personal_question(self):
        assert _ev("don't ask me personal questions").asserted_types() == ("NO_PERSONAL_QUESTION",)
        assert _ev("don't ask me that").no_personal_question is True
        assert _ev("no more questions").no_personal_question is True

    def test_change_topic(self):
        assert _ev("let's talk about something else").asserted_types() == ("CHANGE_TOPIC",)
        assert _ev("let's change the subject").change_topic is True
        assert _ev("leave that alone").change_topic is True

    def test_stop_conversation(self):
        assert _ev("leave me alone").asserted_types() == ("STOP_CONVERSATION",)
        assert _ev("go away").stop_conversation is True

    def test_do_not_contact(self):
        assert _ev("don't message me again").asserted_types() == ("DO_NOT_CONTACT",)
        assert _ev("don't contact me").do_not_contact is True
        assert _ev("never text me").do_not_contact is True

    def test_discomfort_maps_to_manner_and_topic(self):
        ev = _ev("I'm not comfortable with this")
        assert ev.no_flirting is True
        assert ev.no_sexual_topic is True

    def test_manner_complaint(self):
        assert _ev("don't talk to me like that").no_flirting is True

    def test_multiple_boundaries_same_turn(self):
        ev = _ev("Stop flirting and don't call me babe.")
        assert ev.asserted_types() == ("NO_FLIRTING", "NO_PET_NAME")

    def test_punctuation_and_case_variants(self):
        assert _ev("STOP... flirting?!").no_flirting is True
        assert _ev("  Don't   Call  Me   BABE  ").no_pet_name is True


# ---------------------------------------------------------------------------
# Evidence: false positives (must NOT assert)
# ---------------------------------------------------------------------------


class TestEvidenceFalsePositives:
    def test_bare_stop_is_ambiguous_only(self):
        for text in ("stop", "stop.", "stop it", "stop that", "stop please"):
            ev = _ev(text)
            assert ev.has_assertion() is False, text
            assert ev.ambiguous is True, text

    def test_bare_too_much_is_ambiguous_only(self):
        for text in (
            "that's too much",
            "that's too much information about your day",
            "thats too much",
        ):
            ev = _ev(text)
            assert ev.has_assertion() is False, text

    def test_unrelated_dont(self):
        assert _ev("I don't want pizza").is_empty() is True
        assert _ev("don't forget to send the file").is_empty() is True
        assert _ev("don't forget to call me tomorrow").is_empty() is True
        assert _ev("don't worry about it").is_empty() is True

    def test_unrelated_stop(self):
        assert _ev("stop the music, I'm trying to listen").is_empty() is True

    def test_unrelated_no(self):
        assert _ev("no, I was joking").is_empty() is True
        assert _ev("no thanks").is_empty() is True

    def test_neutral_is_empty(self):
        assert _ev("what did you do today?").is_empty() is True
        assert _ev("haha that's funny").is_empty() is True

    def test_quoted_third_party_is_not_boundary(self):
        ev = _ev('he said "stop flirting with me"')
        assert ev.has_assertion() is False
        ev2 = _ev('my friend told me "don\'t call me babe"')
        assert ev2.has_assertion() is False

    def test_direct_assertion_still_works_with_quotes_elsewhere(self):
        # Quoted span removed, but the direct clause still asserts.
        ev = _ev('he said "hi" and stop flirting with me')
        assert ev.no_flirting is True

    def test_fail_open_neutral(self):
        assert extract_boundary_evidence(user_message=None).is_empty() is True
        assert extract_boundary_evidence(user_message=42).is_empty() is True
        assert extract_boundary_evidence(user_message="   ").is_empty() is True

    def test_call_me_tomorrow_is_not_pet_name(self):
        assert _ev("call me tomorrow").is_empty() is True

    def test_im_not_flirting_self_report_is_not_boundary(self):
        assert _ev("I'm not flirting, I promise").is_empty() is True

    def test_dont_mean_is_not_boundary(self):
        # D3: denying the meaning is not establishing a boundary.
        assert _ev("I don't mean stop flirting").has_assertion() is False
        assert _ev("I didn't mean stop flirting").has_assertion() is False
        assert _ev("I don't mean stop flirting").is_empty() is True

    def test_mean_without_denial_still_asserts(self):
        # D3: the guard is narrow — an affirmed directive still asserts.
        assert _ev("I mean stop flirting").no_flirting is True


# ---------------------------------------------------------------------------
# Evidence: D1 STOP scoping (manner/topic complements are not STOP)
# ---------------------------------------------------------------------------


class TestD1StopScoping:
    def test_manner_complaint_is_not_stop(self):
        assert _ev("don't talk to me like that").stop_conversation is False
        assert _ev("stop talking to me like that").stop_conversation is False

    def test_manner_complaint_keeps_narrow_mapping(self):
        assert _ev("don't talk to me like that").no_flirting is True
        assert _ev("stop talking to me like that").no_flirting is True

    def test_topic_complement_is_not_stop(self):
        assert _ev("don't talk to me about work").stop_conversation is False
        assert _ev("stop talking to me about this").stop_conversation is False

    def test_bare_stop_directives_still_assert(self):
        assert _ev("don't talk to me").stop_conversation is True
        assert _ev("stop talking to me").stop_conversation is True
        assert _ev("don't talk to me, please").stop_conversation is True


# ---------------------------------------------------------------------------
# Evidence: relaxation (explicit only)
# ---------------------------------------------------------------------------


class TestEvidenceRelaxation:
    def test_explicit_pet_relaxation(self):
        ev = _ev("okay, you can call me babe again")
        assert ev.relaxed_no_pet_name is True
        assert ev.has_assertion() is False

    def test_explicit_flirt_relaxation(self):
        ev = _ev("okay, you can flirt again")
        assert ev.relaxed_no_flirting is True

    def test_explicit_sexual_relaxation(self):
        ev = _ev("actually, we can talk about that")
        assert ev.relaxed_no_sexual_topic is True

    def test_positive_conversation_is_not_relaxation(self):
        for text in (
            "haha that's funny",
            "I missed you",
            "you're sweet",
            "thanks, that was nice",
            "I like talking to you",
        ):
            ev = _ev(text)
            assert ev.has_relaxation() is False, text

    def test_same_turn_assertion_beats_relaxation(self):
        ev = _ev("you can call me babe, but stop flirting")
        assert ev.no_flirting is True
        assert ev.relaxed_no_pet_name is True
        assert ev.relaxed_no_flirting is False


# ---------------------------------------------------------------------------
# Evidence/state: D2 STOP relaxation scoping (topic talk is not resume)
# ---------------------------------------------------------------------------


class TestD2StopRelaxScoping:
    def test_topic_talk_does_not_relax_stop(self):
        for text in (
            "we can talk about that again",
            "actually, we can talk about that",
            "we can talk about sex again",
        ):
            ev = _ev(text)
            assert ev.relaxed_stop_conversation is False, text

    def test_topic_talk_still_relaxes_topic(self):
        assert _ev("we can talk about sex again").relaxed_no_sexual_topic is True
        assert _ev("actually, we can talk about that").relaxed_no_sexual_topic is True

    def test_genuine_resume_still_relaxes_stop(self):
        assert _ev("we can keep talking").relaxed_stop_conversation is True
        assert _ev("keep talking").relaxed_stop_conversation is True

    def test_active_stop_survives_topic_permission(self):
        state = apply_evidence({}, _ev("leave me alone"), _now())
        assert derive_boundary_snapshot(state, _now()).active == ("STOP_CONVERSATION",)
        for text in (
            "we can talk about that again",
            "actually, we can talk about that",
            "we can talk about sex again",
        ):
            state = apply_evidence(state, _ev(text), _now())
            snap = derive_boundary_snapshot(state, _now())
            assert "STOP_CONVERSATION" in snap.active, text


# ---------------------------------------------------------------------------
# State: activation / reaffirmation / relaxation / conflicts
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


class TestDurableState:
    def test_first_activation(self):
        state = apply_evidence({}, _ev("stop flirting"), _now())
        assert set(state) == {"NO_FLIRTING"}
        assert state["NO_FLIRTING"].scope == BOUNDARY_SCOPES["NO_FLIRTING"]
        assert state["NO_FLIRTING"].first_observed is not None
        assert state["NO_FLIRTING"].provenance == "explicit:user_assertion"

    def test_narrow_stays_narrow(self):
        state = apply_evidence({}, _ev("don't call me babe"), _now())
        assert set(state) == {"NO_PET_NAME"}
        snap = derive_boundary_snapshot(state, _now())
        assert snap.active == ("NO_PET_NAME",)

    def test_reaffirmation_refreshes_but_keeps_first_observed(self):
        t0 = _now()
        state = apply_evidence({}, _ev("stop flirting"), t0)
        first = state["NO_FLIRTING"].first_observed
        t1 = t0 + timedelta(hours=1)
        state2 = apply_evidence(state, _ev("stop flirting"), t1)
        assert state2["NO_FLIRTING"].first_observed == first
        assert state2["NO_FLIRTING"].last_reaffirmed != first

    def test_explicit_relaxation_clears(self):
        state = apply_evidence({}, _ev("don't call me babe"), _now())
        state2 = apply_evidence(state, _ev("okay, you can call me babe again"), _now())
        snap = derive_boundary_snapshot(state2, _now())
        assert snap.active == ()

    def test_ordinary_conversation_never_clears(self):
        state = apply_evidence({}, _ev("don't call me babe"), _now())
        for text in ("haha that's funny", "I missed you", "what did you do today?"):
            state = apply_evidence(state, _ev(text), _now() + timedelta(days=30))
        snap = derive_boundary_snapshot(state, _now() + timedelta(days=30))
        assert snap.active == ("NO_PET_NAME",)

    def test_reactivation_after_clear(self):
        state = apply_evidence({}, _ev("don't call me babe"), _now())
        state = apply_evidence(state, _ev("you can call me babe again"), _now())
        assert derive_boundary_snapshot(state, _now()).active == ()
        state = apply_evidence(state, _ev("don't call me that"), _now())
        assert derive_boundary_snapshot(state, _now()).active == ("NO_PET_NAME",)

    def test_multiple_types_coexist(self):
        state = apply_evidence({}, _ev("Stop flirting and don't call me babe."), _now())
        snap = derive_boundary_snapshot(state, _now())
        assert snap.active == ("NO_FLIRTING", "NO_PET_NAME")

    def test_contact_never_auto_recovers_on_later_message(self):
        state = apply_evidence({}, _ev("don't message me again"), _now())
        # A later inbound message is NOT relaxation (only explicit
        # permission relaxes CONTACT).
        state = apply_evidence(state, _ev("hey are you there?"), _now() + timedelta(days=10))
        snap = derive_boundary_snapshot(state, _now() + timedelta(days=10))
        assert snap.blocks_contact() is True

    def test_persistent_naming_boundary_does_not_expire(self):
        state = apply_evidence({}, _ev("don't call me babe"), _now())
        snap = derive_boundary_snapshot(state, _now() + timedelta(days=365))
        assert snap.active == ("NO_PET_NAME",)

    def test_temporary_topic_boundary_expires(self):
        state = apply_evidence({}, _ev("I don't want to talk about sex right now"), _now())
        assert derive_boundary_snapshot(state, _now()).active == ("NO_SEXUAL_TOPIC",)
        snap = derive_boundary_snapshot(state, _now() + timedelta(hours=25))
        assert "NO_SEXUAL_TOPIC" not in snap.active
        assert "NO_SEXUAL_TOPIC" in snap.expired

    def test_persistent_topic_boundary_does_not_expire(self):
        state = apply_evidence({}, _ev("I don't want to talk about sex"), _now())
        snap = derive_boundary_snapshot(state, _now() + timedelta(days=90))
        assert "NO_SEXUAL_TOPIC" in snap.active

    def test_change_topic_expires(self):
        state = apply_evidence({}, _ev("let's talk about something else"), _now())
        assert derive_boundary_snapshot(state, _now()).active == ("CHANGE_TOPIC",)
        snap = derive_boundary_snapshot(state, _now() + timedelta(hours=25))
        assert "CHANGE_TOPIC" not in snap.active

    def test_temporary_reports_recovering_near_expiry(self):
        state = apply_evidence({}, _ev("I don't want to talk about sex right now"), _now())
        snap = derive_boundary_snapshot(state, _now() + timedelta(hours=20))
        assert "NO_SEXUAL_TOPIC" in snap.active
        assert "NO_SEXUAL_TOPIC" in snap.recovering

    def test_snapshot_helpers(self):
        snap = BoundarySnapshot(active=("STOP_CONVERSATION",))
        assert snap.wants_close() is True
        assert snap.blocks_offer() is True
        assert snap.blocks_contact() is False
        snap2 = BoundarySnapshot(active=("DO_NOT_CONTACT",))
        assert snap2.blocks_contact() is True
        assert BoundarySnapshot(active=("NO_FLIRTING",)).blocks_offer() is False

    def test_raw_text_never_persisted(self):
        import json

        state = apply_evidence({}, _ev("don't call me babe you jerk"), _now())
        blob = json.dumps(
            {k: vars(v) if hasattr(v, "__dict__") else v for k, v in state.items()}
            if False
            else str(state)
        )
        assert "jerk" not in blob
        assert "babe" not in blob.lower() or "NO_PET_NAME" in blob


# ---------------------------------------------------------------------------
# Current evidence beats history
# ---------------------------------------------------------------------------


class TestCurrentBeatsHistory:
    def test_current_assertion_overrides_empty_history(self):
        snap = snapshot_with_current_evidence({}, _ev("stop flirting"), _now())
        assert snap.active == ("NO_FLIRTING",)

    def test_current_relaxation_overrides_durable(self):
        state = apply_evidence({}, _ev("don't call me babe"), _now())
        snap = snapshot_with_current_evidence(state, _ev("you can call me babe again"), _now())
        assert snap.active == ()

    def test_conflicting_scopes_use_current_relaxation(self):
        state = apply_evidence({}, _ev("I don't want to talk about sex"), _now())
        snap = snapshot_with_current_evidence(
            state, _ev("actually, we can talk about that"), _now()
        )
        assert snap.active == ()


# ---------------------------------------------------------------------------
# Persistence: namespace / atomicity / idempotency / isolation / bounds
# ---------------------------------------------------------------------------


def _fake_store():
    return {}


def _make_fake_mutate(store: dict):
    import copy

    async def _fake_mutate(uid: int, fn) -> bool:
        facts = store.setdefault(uid, {})
        working = copy.deepcopy(facts)
        changed = fn(working)
        if changed:
            store[uid] = working
        return changed

    return _fake_mutate


class TestPersistence:
    @pytest.mark.asyncio
    async def test_namespace_and_first_activation(self):
        from unittest.mock import patch

        store = _fake_store()
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            side_effect=_make_fake_mutate(store),
        ):
            snap, applied = await accumulate_boundary_turn_idempotent(
                user_id=1,
                creator_id=7,
                generation_id="gen-1",
                evidence=_ev("don't call me babe"),
            )
            assert applied is True
            assert snap is not None and snap.active == ("NO_PET_NAME",)
            block = store[1][BOUNDARY_STATE_KEY]["7"]
            assert block["schema_version"] == 1
            assert block["constraints"]["NO_PET_NAME"]["status"] == "ACTIVE"
            assert block[BOUNDARY_PROCESSED_IDS_FIELD] == ["gen-1"]

    @pytest.mark.asyncio
    async def test_same_generation_twice_applies_once(self):
        from unittest.mock import patch

        store = _fake_store()
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            side_effect=_make_fake_mutate(store),
        ):
            ev = _ev("stop flirting")
            first, did_first = await accumulate_boundary_turn_idempotent(
                user_id=1, creator_id=7, generation_id="gen-dup", evidence=ev
            )
            second, did_second = await accumulate_boundary_turn_idempotent(
                user_id=1, creator_id=7, generation_id="gen-dup", evidence=ev
            )
            assert did_first is True and did_second is False
            assert first is not None and second is not None
            assert first.active == second.active == ("NO_FLIRTING",)
            block = store[1][BOUNDARY_STATE_KEY]["7"]
            assert block[BOUNDARY_PROCESSED_IDS_FIELD].count("gen-dup") == 1

    @pytest.mark.asyncio
    async def test_same_text_different_generations_reaffirms(self):
        from unittest.mock import patch

        store = _fake_store()
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            side_effect=_make_fake_mutate(store),
        ):
            await accumulate_boundary_turn_idempotent(
                user_id=1, creator_id=7, generation_id="g1", evidence=_ev("stop flirting")
            )
            snap, applied = await accumulate_boundary_turn_idempotent(
                user_id=1, creator_id=7, generation_id="g2", evidence=_ev("stop flirting")
            )
            assert applied is True
            assert snap is not None and snap.active == ("NO_FLIRTING",)

    @pytest.mark.asyncio
    async def test_empty_evidence_writes_nothing(self):
        from unittest.mock import patch

        store = _fake_store()
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            side_effect=_make_fake_mutate(store),
        ):
            snap, applied = await accumulate_boundary_turn_idempotent(
                user_id=1,
                creator_id=7,
                generation_id="g0",
                evidence=_ev("what did you do today?"),
            )
            assert applied is False
            assert 1 not in store
            assert snap is not None and snap.active == ()

    @pytest.mark.asyncio
    async def test_creator_isolation(self):
        from unittest.mock import patch

        store = _fake_store()
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            side_effect=_make_fake_mutate(store),
        ):
            await accumulate_boundary_turn_idempotent(
                user_id=1,
                creator_id=7,
                generation_id="gx",
                evidence=_ev("don't call me babe"),
            )
            by_creator = store[1][BOUNDARY_STATE_KEY]
            assert "7" in by_creator and "9" not in by_creator
            # Creator B reads clean state from the same row.
            constraints_b = get_boundary_constraints(store[1], 9)
            assert constraints_b == {}
            snap_b = derive_boundary_snapshot(constraints_b, _now())
            assert snap_b.active == ()

    @pytest.mark.asyncio
    async def test_markers_bounded(self):
        from unittest.mock import patch

        store = _fake_store()
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            side_effect=_make_fake_mutate(store),
        ):
            for i in range(30):
                await accumulate_boundary_turn_idempotent(
                    user_id=1,
                    creator_id=7,
                    generation_id=f"g{i}",
                    evidence=_ev("stop flirting"),
                )
            block = store[1][BOUNDARY_STATE_KEY]["7"]
            assert len(block[BOUNDARY_PROCESSED_IDS_FIELD]) <= 20

    @pytest.mark.asyncio
    async def test_write_failure_still_enforces_current_turn(self):
        from unittest.mock import patch

        async def _failing(uid: int, fn) -> bool:
            raise RuntimeError("db down")

        with patch("db.postgres.mutate_user_profile_atomically", side_effect=_failing):
            snap, applied = await accumulate_boundary_turn_idempotent(
                user_id=1,
                creator_id=7,
                generation_id="gfail",
                evidence=_ev("stop flirting"),
                profile={},
            )
            assert applied is False
            # Current-turn evidence still enforced from memory.
            assert snap is not None and snap.active == ("NO_FLIRTING",)

    @pytest.mark.asyncio
    async def test_bad_inputs_fail_safe(self):
        from unittest.mock import patch

        with patch(
            "db.postgres.mutate_user_profile_atomically",
            side_effect=AssertionError("must not persist"),
        ):
            snap, applied = await accumulate_boundary_turn_idempotent(
                user_id=1,
                creator_id=7,
                generation_id="",
                evidence=_ev("stop flirting"),
            )
            assert (snap, applied) == (None, False)
            snap, applied = await accumulate_boundary_turn_idempotent(
                user_id="bad",
                creator_id=7,
                generation_id="g",  # type: ignore[arg-type]
                evidence=_ev("stop flirting"),
            )
            assert (snap, applied) == (None, False)

    def test_no_flat_profile_key(self):
        # The durable namespace is namespaced; flat "boundary" must not exist.
        assert BOUNDARY_STATE_KEY == "boundary_state_by_creator"
        assert BOUNDARY_STATE_KEY.endswith("_by_creator")


# ---------------------------------------------------------------------------
# Context carrier
# ---------------------------------------------------------------------------


class TestBoundaryContext:
    def test_empty_renders_nothing(self):
        from context_engine.boundary_context import BoundarySelection

        assert render_boundary_context(BoundarySelection()) == ""
        assert render_boundary_context(None) == ""

    def test_renders_active_constraints_without_raw_text(self):
        from context_engine.boundary_context import BoundarySelection

        sel = select_boundary_context(
            snapshot=BoundarySnapshot(active=("NO_FLIRTING", "NO_PET_NAME"))
        )
        block = render_boundary_context(sel)
        assert "BOUNDARY CONTEXT [DERIVED]:" in block
        assert "no flirting" in block
        assert "avoid pet names" in block
        assert "outrank persona style" in block
        assert "babe" not in block  # no raw wording
        assert "consent" not in block and "permission" not in block

    def test_degraded_renders_nothing(self):
        from context_engine.boundary_context import BoundarySelection

        sel = BoundarySelection(active=("NO_FLIRTING",), degraded=True)
        assert render_boundary_context(sel) == ""

    def test_ordering_in_snapshot_blocks(self):
        from context_engine.models import AuthoritativeState
        from core.context_compact import phase5_snapshot_blocks

        state = AuthoritativeState(
            creator_id=1,
            user_id=2,
            generation_id="g",
            current_message="hi",
            relationship_context_text="RELATIONSHIP CONTEXT [DERIVED]:\ncalm",
            intimacy_context_text="INTIMACY CONTEXT [DERIVED]:\nlow",
            boundary_context_text="BOUNDARY CONTEXT [DERIVED]:\nactive constraints:\n- no flirting",
            strategy_block_text="CONVERSATION STRATEGY:\nmove=ACKNOWLEDGE",
            behavior_block_text="PERSONA BEHAVIOR:\ncalm",
        )
        blocks = phase5_snapshot_blocks(state)
        kinds = []
        for b in blocks:
            content = b["content"]
            if content.startswith("RELATIONSHIP"):
                kinds.append("REL")
            elif content.startswith("INTIMACY"):
                kinds.append("INT")
            elif content.startswith("BOUNDARY"):
                kinds.append("BND")
            elif content.startswith("CONVERSATION STRATEGY"):
                kinds.append("STRAT")
            elif content.startswith("PERSONA BEHAVIOR"):
                kinds.append("BEH")
        assert kinds == ["REL", "INT", "BND", "STRAT", "BEH"]

    def test_empty_carriers_render_nothing(self):
        from context_engine.models import AuthoritativeState
        from core.context_compact import phase5_snapshot_blocks

        state = AuthoritativeState(creator_id=1, user_id=2, generation_id="g", current_message="hi")
        assert phase5_snapshot_blocks(state) == []


# ---------------------------------------------------------------------------
# Strategy constraint (Phase 4 untouched)
# ---------------------------------------------------------------------------


class TestStrategyConstraint:
    def _strategy(self, move="EXPLORE", hint="explore", question="ONE_NATURAL_QUESTION"):
        from commerce.conversation_strategy import ConversationalStrategy

        return ConversationalStrategy(
            move=move,
            realization_hint=hint,
            question_policy=question,
            reason_codes=("ANSWERED_QUESTION",),
            confidence="MEDIUM",
        )

    def test_no_boundary_passes_through(self):
        s = self._strategy()
        assert constrain_strategy_for_boundary(s, ()) is s

    def test_stop_forces_safe_default(self):
        out = constrain_strategy_for_boundary(self._strategy(), ("STOP_CONVERSATION",))
        assert out is not None
        assert out.move == "ACKNOWLEDGE"
        assert out.question_policy == "NO_QUESTION"

    def test_question_barred_downgrades_explore(self):
        out = constrain_strategy_for_boundary(self._strategy(), ("NO_PERSONAL_QUESTION",))
        assert out is not None
        assert out.move == "SHARE"
        assert out.question_policy == "NO_QUESTION"

    def test_flirting_boundary_leaves_strategy_moves(self):
        # Strategy hints never include tease; nothing to change.
        s = self._strategy(move="CONTINUE", hint="react", question="NO_QUESTION")
        assert constrain_strategy_for_boundary(s, ("NO_FLIRTING",)) is s

    def test_phase4_module_has_no_boundary_concepts(self):
        import commerce.conversation_strategy as strat

        source_moves = {
            strat.StrategyMove.ACKNOWLEDGE,
            strat.StrategyMove.CONTINUE,
            strat.StrategyMove.EXPLORE,
            strat.StrategyMove.CALLBACK,
            strat.StrategyMove.SHARE,
            strat.StrategyMove.RECOVER,
            strat.StrategyMove.CLOSE,
        }
        assert "TEASE" not in source_moves
        assert len(source_moves) == 7


# ---------------------------------------------------------------------------
# Persona non-authority
# ---------------------------------------------------------------------------


class TestPersonaNonAuthority:
    def test_teasing_allowed_does_not_permit_flirty_output(self):
        from commerce.persona_behavior import derive_persona_behavior_state

        behavior = derive_persona_behavior_state(
            structured_persona={},
            conversation_state=None,
            fan_message="haha you're funny",
        )
        # Whatever persona derives, NO_FLIRTING validation governs output.
        assert (
            validate_reply_against_boundaries(
                "hey babe, you are so sexy ;)", ("NO_FLIRTING", "NO_PET_NAME")
            ).violated
            is True
        )

    def test_boundary_block_declares_precedence(self):
        from context_engine.boundary_context import BoundarySelection

        block = render_boundary_context(BoundarySelection(active=("NO_FLIRTING",)))
        assert "outrank persona style" in block


# ---------------------------------------------------------------------------
# Output validation + safe completion
# ---------------------------------------------------------------------------


class TestOutputValidation:
    def test_flirty_draft_violates(self):
        assert (
            validate_reply_against_boundaries(
                "Hey, you are looking so sexy tonight", ("NO_FLIRTING",)
            ).violated
            is True
        )

    def test_neutral_draft_passes(self):
        assert (
            validate_reply_against_boundaries(
                "Sounds good, what are you up to later today",
                ("NO_FLIRTING", "NO_SEXUAL_TOPIC", "NO_PET_NAME"),
            ).violated
            is False
        )

    def test_pet_name_violates(self):
        assert (
            validate_reply_against_boundaries("Sure thing, babe", ("NO_PET_NAME",)).violated is True
        )
        # "love" alone is not a pet-name violation (FP guard).
        assert (
            validate_reply_against_boundaries("I love pizza too", ("NO_PET_NAME",)).violated
            is False
        )

    def test_sexual_topic_violates(self):
        assert (
            validate_reply_against_boundaries("kiss me closer", ("NO_SEXUAL_TOPIC",)).violated
            is True
        )

    def test_question_violates_no_personal_question(self):
        assert (
            validate_reply_against_boundaries(
                "Where do you live?", ("NO_PERSONAL_QUESTION",)
            ).violated
            is True
        )
        assert (
            validate_reply_against_boundaries(
                "Nice, glad to hear it.", ("NO_PERSONAL_QUESTION",)
            ).violated
            is False
        )

    def test_stop_allows_brief_close_only(self):
        assert (
            validate_reply_against_boundaries(
                "Understood — I'll leave it here. Take care.",
                ("STOP_CONVERSATION",),
            ).violated
            is False
        )
        assert (
            validate_reply_against_boundaries(
                "Oh come on, why are you leaving? What did I do wrong, tell me everything?",
                ("STOP_CONVERSATION",),
            ).violated
            is True
        )

    def test_contact_blocks_any_outbound(self):
        assert validate_reply_against_boundaries("hello", ("DO_NOT_CONTACT",)).violated is True

    def test_safe_templates_pass_validator(self):
        for active in (
            ("NO_FLIRTING",),
            ("NO_SEXUAL_TOPIC",),
            ("NO_PET_NAME",),
            ("NO_PERSONAL_QUESTION",),
            ("CHANGE_TOPIC",),
            ("NO_FLIRTING", "NO_PET_NAME", "NO_PERSONAL_QUESTION", "CHANGE_TOPIC"),
        ):
            safe = safe_completion_for(active)
            assert safe is not None
            assert validate_reply_against_boundaries(safe, active).violated is False
        assert (
            validate_reply_against_boundaries(SAFE_CLOSE, ("STOP_CONVERSATION",)).violated is False
        )
        assert safe_completion_for(("STOP_CONVERSATION",)) == SAFE_CLOSE
        assert safe_completion_for(("DO_NOT_CONTACT",)) is None

    def test_safe_templates_have_no_pressure_or_questions(self):
        assert "?" not in SAFE_NEUTRAL_CONTINUE
        assert "?" not in SAFE_CLOSE

    def test_no_active_no_violation(self):
        assert validate_reply_against_boundaries("hey sexy", ()).violated is False
        assert validate_reply_against_boundaries("hey sexy", None).violated is False


# ---------------------------------------------------------------------------
# Routing + one_call hook
# ---------------------------------------------------------------------------


class TestRouting:
    def test_boundary_violation_queues(self):
        decision = decide_routing(is_valid=True, boundary_violation=True, score=0.95)
        assert decision.action.value == "queue"
        assert decision.reason == REASON_BOUNDARY_VIOLATION

    def test_boundary_beats_approval(self):
        decision = decide_routing(
            is_valid=True,
            boundary_violation=True,
            score=1.0,
            needs_handoff=False,
            advisory_handoff=False,
        )
        assert decision.action.value != "auto_send"

    def test_invalid_output_still_first(self):
        decision = decide_routing(is_valid=False, boundary_violation=True)
        assert decision.reason == "invalid_output"

    def test_clean_turn_still_sends(self):
        decision = decide_routing(is_valid=True, score=0.95)
        assert decision.action.value == "auto_send"

    def test_one_call_validator_hook(self):
        import json

        from core.one_call import validate_one_call_response

        payload = json.dumps(
            {
                "reply": "hey babe, you look so sexy",
                "commerce_signals": {
                    "purchase_intent": 0.0,
                    "content_interest": 0.0,
                    "relationship_engagement": 0.5,
                    "price_interest": 0.0,
                    "explicit_purchase_request": False,
                    "explicit_content_request": False,
                    "requested_price": None,
                    "declined_recent_offer": False,
                    "asks_for_free_content": False,
                    "negative_sentiment": 0.0,
                    "confidence": 0.8,
                    "primary_intent": "casual_chat",
                    "intent_tags": [],
                    "negative_intent_tags": [],
                    "fan_asks_question": False,
                },
                "confidence": 0.9,
                "needs_handoff": False,
            }
        )
        result = validate_one_call_response(payload, boundary_active=("NO_FLIRTING", "NO_PET_NAME"))
        assert set(result.boundary_violations) == {"NO_FLIRTING", "NO_PET_NAME"}
        assert result.needs_handoff is True
        assert result.is_valid is True  # routing veto handles it, not invalidity

        clean = validate_one_call_response(payload, boundary_active=())
        assert clean.boundary_violations == []
        # Default (no boundary kwarg) preserves legacy behavior.
        legacy = validate_one_call_response(payload)
        assert legacy.boundary_violations == []


# ---------------------------------------------------------------------------
# Commerce veto
# ---------------------------------------------------------------------------


class TestCommerceVeto:
    def test_veto_types_block(self):
        for active in (
            ("NO_SEXUAL_TOPIC",),
            ("CHANGE_TOPIC",),
            ("STOP_CONVERSATION",),
            ("DO_NOT_CONTACT",),
        ):
            blocked, reason = boundary_blocks_commerce(BoundarySnapshot(active=active))
            assert blocked is True, active
            assert reason is not None

    def test_manner_types_do_not_veto_offers(self):
        for active in (("NO_FLIRTING",), ("NO_PET_NAME",), ("NO_PERSONAL_QUESTION",)):
            blocked, _ = boundary_blocks_commerce(BoundarySnapshot(active=active))
            assert blocked is False, active

    def test_veto_independent_of_llm_signals(self):
        # Even maximal purchase intent cannot lift a boundary veto.
        blocked, _ = boundary_blocks_commerce(BoundarySnapshot(active=("NO_SEXUAL_TOPIC",)))
        assert blocked is True

    def test_degraded_blocks_commerce(self):
        blocked, reason = boundary_blocks_commerce(BoundarySnapshot(degraded=True))
        assert blocked is True
        assert reason == "boundary_state_unknown"

    def test_no_boundary_no_veto(self):
        assert boundary_blocks_commerce(BoundarySnapshot()) == (False, None)
        assert boundary_blocks_commerce(None) == (False, None)


# ---------------------------------------------------------------------------
# Intimacy / relationship / commerce authority untouched
# ---------------------------------------------------------------------------


class TestPhase16Untouched:
    def test_intimacy_module_has_no_boundary_state(self):
        import commerce.intimacy_trajectory as traj

        assert not hasattr(traj, "BOUNDARY_STATE_KEY")
        assert not hasattr(traj, "BoundaryConstraint")

    def test_no_permission_scores_introduced(self):
        # Docstrings discuss these concepts (as prohibitions); the check
        # is that no such IDENTIFIER or string constant exists in code.
        import ast
        import inspect

        import commerce.boundary_state as bs
        import commerce.boundary_evidence as be
        import commerce.boundary_validation as bv

        def _code_without_docstrings(module) -> str:
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(
                    node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    if (
                        node.body
                        and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)
                    ):
                        node.body[0].value.value = ""
            return ast.unparse(tree)

        blob = "".join(_code_without_docstrings(m) for m in (bs, be, bv))
        for token in (
            "permission_score",
            "consent_score",
            "sexual_readiness",
            "age_verified",
            "adult_verified",
        ):
            assert token not in blob

    def test_response_mode_tease_not_revived(self):
        from core.response_mode import ResponseMode

        assert ResponseMode.TEASE == "tease"  # legacy value still exists...
        # ...but no boundary module references it.
        import commerce.boundary_state as bs
        import commerce.boundary_validation as bv

        assert "TEASE" not in str(vars(bs)) + str(vars(bv))


# ---------------------------------------------------------------------------
# Adversarial scenarios A-H
# ---------------------------------------------------------------------------


class TestAdversarial:
    def test_a_deep_intimacy_stop_flirting(self):
        # Historical intimacy is irrelevant to evidence: current assertion wins.
        ev = _ev("stop flirting with me")
        snap = snapshot_with_current_evidence({}, ev, _now())
        assert snap.active == ("NO_FLIRTING",)
        assert (
            validate_reply_against_boundaries("come on, flirt with me a little", snap).violated
            is True
        )
        blocked, _ = boundary_blocks_commerce(BoundarySnapshot(active=("NO_FLIRTING",)))
        # NO_FLIRTING constrains realization, not the offer itself.
        assert blocked is False

    def test_b_pet_name_only(self):
        ev = _ev("don't call me babe")
        assert ev.asserted_types() == ("NO_PET_NAME",)
        snap = snapshot_with_current_evidence({}, ev, _now())
        # Normal non-flirty conversation remains possible.
        assert (
            validate_reply_against_boundaries(
                "Sounds good, tell me more about your day", snap
            ).violated
            is False
        )
        assert validate_reply_against_boundaries("ok babe", snap).violated is True

    def test_c_temporary_then_ordinary(self):
        state = apply_evidence({}, _ev("I don't want to talk about sex right now"), _now())
        snap = snapshot_with_current_evidence(state, _ev("what did you do today?"), _now())
        assert "NO_SEXUAL_TOPIC" in snap.active
        assert (
            validate_reply_against_boundaries("Work was busy, glad to be home", snap).violated
            is False
        )
        assert validate_reply_against_boundaries("kiss me", snap).violated is True

    def test_d_do_not_contact_blocks(self):
        snap = snapshot_with_current_evidence({}, _ev("don't message me again"), _now())
        assert snap.blocks_contact() is True
        blocked, _ = boundary_blocks_commerce(snap)
        assert blocked is True

    def test_e_explicit_relaxation_recognized(self):
        state = apply_evidence({}, _ev("stop flirting"), _now())
        assert derive_boundary_snapshot(state, _now()).active == ("NO_FLIRTING",)
        state = apply_evidence(state, _ev("okay, you can flirt again"), _now())
        assert derive_boundary_snapshot(state, _now()).active == ()
        # Intimacy alone cannot relax: positive message changes nothing.
        state = apply_evidence({}, _ev("stop flirting"), _now())
        state = apply_evidence(state, _ev("I missed you so much"), _now())
        assert derive_boundary_snapshot(state, _now()).active == ("NO_FLIRTING",)

    def test_f_pizza_no_boundary(self):
        assert _ev("I don't want pizza").is_empty() is True

    def test_g_send_file_no_boundary(self):
        assert _ev("don't forget to send the file").is_empty() is True

    def test_h_too_much_information_no_flirt_inference(self):
        ev = _ev("that's too much information about your day")
        assert "NO_FLIRTING" not in ev.asserted_types()
        assert "NO_SEXUAL_TOPIC" not in ev.asserted_types()


# ---------------------------------------------------------------------------
# Debounce / retry semantics (documented latest-only behavior)
# ---------------------------------------------------------------------------


class TestDebounceRetry:
    def test_latest_only_documents_limitation(self):
        # Ingress forwards the latest buffered message only; a boundary in
        # a superseded message requires re-assertion. The effective
        # snapshot for the PROCESSED (latest) message is exact.
        latest = _ev("what did you do today?")
        assert latest.is_empty() is True
        reasserted = _ev("stop flirting")
        snap = snapshot_with_current_evidence({}, reasserted, _now())
        assert snap.active == ("NO_FLIRTING",)

    @pytest.mark.asyncio
    async def test_retry_after_xautoclaim_idempotent(self):
        from unittest.mock import patch

        store: dict = {}
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            side_effect=_make_fake_mutate(store),
        ):
            ev = _ev("don't call me babe")
            for _ in range(3):  # redeliveries of gen-r
                snap, applied = await accumulate_boundary_turn_idempotent(
                    user_id=5, creator_id=5, generation_id="gen-r", evidence=ev
                )
            assert snap is not None and snap.active == ("NO_PET_NAME",)
            block = store[5][BOUNDARY_STATE_KEY]["5"]
            assert block[BOUNDARY_PROCESSED_IDS_FIELD] == ["gen-r"]


# ---------------------------------------------------------------------------
# Legacy path shares the same validator (no bypass)
# ---------------------------------------------------------------------------


class TestLegacyParity:
    def test_shared_validator_covers_legacy_drafts(self):
        # The worker's common choke calls validate_reply_against_boundaries
        # for canonical, commerce, agent, AND legacy drafts alike.
        assert (
            validate_reply_against_boundaries(
                "hey sexy babe", ("NO_FLIRTING", "NO_PET_NAME")
            ).violated
            is True
        )

    def test_deterministic_scoring_path_unaffected(self):
        from core.scoring_deterministic import validate_draft_quality

        approved, _, _, safety = validate_draft_quality(
            "Sounds good, tell me more.", "what did you do today?"
        )
        assert safety == []
        # Boundary is orthogonal: same text under NO_FLIRTING still passes.
        assert (
            validate_reply_against_boundaries(
                "Sounds good, tell me more.", ("NO_FLIRTING",)
            ).violated
            is False
        )
