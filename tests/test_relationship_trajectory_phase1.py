"""Phase 1 canonical relationship trajectory tests (AUDIT → DESIGN → IMPLEMENT).

Covers the deterministic ``commerce.relationship_trajectory`` domain only:
contract, derivation, decay, provenance discipline, persistence adapter,
creator isolation, failure behavior, versioning, and commerce invariance.

Conventions follow existing suites (e.g. ``test_phase10_lifecycle.py``):
``pytestmark = [pytest.mark.unit]``, explicit ``@pytest.mark.asyncio`` for
async paths, ``AsyncMock``/``patch`` for DB boundaries (no live infra).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit]

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _rich_evidence(**overrides):
    from commerce.relationship_trajectory import RelationshipTurnEvidence

    base = {
        "user_sent_message": True,
        "user_asked_question": True,
        "user_answered_question": True,
        "user_shared_information": True,
        "user_continued_topic": True,
        "user_referenced_previous_context": True,
        "assistant_asked_question": True,
        "assistant_shared_information": True,
        "session_returned": True,
        "open_loop_continued": True,
        "open_loop_resolved": True,
    }
    base.update(overrides)
    return RelationshipTurnEvidence(**base)


def _accumulate_many(n, evidence=None, start=T0, step_hours=25):
    """Fold ``n`` identical turns; returns final anchors (pure, deterministic)."""
    from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

    anchors = neutral_anchors(start)
    moment = start
    for _ in range(n):
        moment = moment + timedelta(hours=step_hours)
        anchors = accumulate_turn(anchors, evidence or _rich_evidence(), moment)
    return anchors, moment


class TestDeterminism:
    def test_same_input_same_timestamp_identical_snapshot(self):
        from commerce.relationship_trajectory import (
            accumulate_turn,
            derive_relationship_snapshot,
            serialize_relationship_anchors,
        )

        ev = _rich_evidence()
        first = accumulate_turn(None, ev, T0)
        second = accumulate_turn(None, ev, T0)
        assert serialize_relationship_anchors(first) == serialize_relationship_anchors(second)
        snap_a = derive_relationship_snapshot(first, ev, T0)
        snap_b = derive_relationship_snapshot(second, ev, T0)
        assert snap_a == snap_b

    def test_evidence_order_in_reason_token_is_stable(self):
        from commerce.relationship_trajectory import accumulate_turn

        first = accumulate_turn(None, _rich_evidence(), T0)
        second = accumulate_turn(None, _rich_evidence(), T0)
        assert first.transitions == second.transitions


class TestColdStart:
    def test_no_history_neutral_anchors(self):
        from commerce.relationship_trajectory import (
            FamiliarityBand,
            derive_relationship_snapshot,
            neutral_anchors,
        )

        anchors = neutral_anchors(T0)
        assert anchors.interaction_count == 0
        assert anchors.schema_version == 1
        snapshot = derive_relationship_snapshot(anchors, None, T0)
        assert snapshot.familiarity == FamiliarityBand.UNKNOWN
        assert snapshot.trend.value == "unknown"
        assert snapshot.decay_applied is False
        assert snapshot.days_since_last_seen is None

    def test_none_anchors_derive_neutral(self):
        from commerce.relationship_trajectory import derive_relationship_snapshot

        snapshot = derive_relationship_snapshot(None, None, T0)
        assert snapshot.familiarity.value == "unknown"
        assert snapshot.engagement.value == "unknown"
        assert snapshot.reciprocity.value == "unknown"
        assert snapshot.continuity.value == "unknown"

    def test_cold_start_is_not_rejection_or_coldness(self):
        # Neutral semantics are documented on the constructor; the snapshot
        # carries no commercial/permission meaning — assert the shape that
        # guarantees it (no score below a neutral floor, no denial flags).
        from commerce.relationship_trajectory import neutral_snapshot

        snapshot = neutral_snapshot(T0)
        assert snapshot.decay_applied is False
        assert snapshot.active_signals_this_turn == 0


class TestFamiliarityAccumulation:
    def test_repeated_interactions_progress_familiarity(self):
        from commerce.relationship_trajectory import (
            FamiliarityBand,
            derive_relationship_snapshot,
        )

        anchors, moment = _accumulate_many(6)
        snapshot = derive_relationship_snapshot(anchors, None, moment)
        assert snapshot.familiarity in (FamiliarityBand.FAMILIAR, FamiliarityBand.ESTABLISHED)

    def test_single_message_cannot_jump_multiple_levels(self):
        from commerce.relationship_trajectory import (
            FamiliarityBand,
            accumulate_turn,
            derive_relationship_snapshot,
            neutral_anchors,
        )

        # Maximal single-turn evidence must still land on the entry level.
        anchors = accumulate_turn(neutral_anchors(T0), _rich_evidence(), T0)
        snapshot = derive_relationship_snapshot(anchors, None, T0)
        assert snapshot.familiarity == FamiliarityBand.NEW
        assert snapshot.engagement.value in ("low", "unknown")
        assert snapshot.continuity.value in ("sparse", "unknown")

    def test_established_requires_span_and_returns(self):
        from commerce.relationship_trajectory import (
            FamiliarityBand,
            accumulate_turn,
            derive_relationship_snapshot,
            neutral_anchors,
        )

        # 25 rapid turns with no session returns and no time span.
        anchors = neutral_anchors(T0)
        moment = T0
        for _ in range(25):
            moment = moment + timedelta(minutes=5)
            anchors = accumulate_turn(anchors, _rich_evidence(session_returned=False), moment)
        snapshot = derive_relationship_snapshot(anchors, None, moment)
        assert snapshot.familiarity != FamiliarityBand.ESTABLISHED


class TestRecencyDecay:
    def test_long_inactivity_decays_readout_without_destroying_anchors(self):
        from commerce.relationship_trajectory import (
            derive_relationship_snapshot,
        )

        anchors, moment = _accumulate_many(25)
        fresh = derive_relationship_snapshot(anchors, None, moment)
        stale_moment = moment + timedelta(days=60)
        stale = derive_relationship_snapshot(anchors, None, stale_moment)
        assert stale.decay_applied is True
        assert stale.days_since_last_seen is not None and stale.days_since_last_seen >= 60
        # Readout decayed at least one step on familiarity or engagement…
        assert (stale.familiarity, stale.engagement) != (
            fresh.familiarity,
            fresh.engagement,
        )
        # …but durable anchors are untouched (history survives decay).
        assert anchors.interaction_count == 25
        assert anchors.user_message_count == 25

    def test_decay_floors_do_not_reach_unknown(self):
        from commerce.relationship_trajectory import derive_relationship_snapshot

        anchors, moment = _accumulate_many(6)
        stale = derive_relationship_snapshot(anchors, None, moment + timedelta(days=365))
        assert stale.familiarity.value != "unknown"
        assert stale.engagement.value != "unknown"

    def test_returning_fan_rewarms_through_accumulation(self):
        from commerce.relationship_trajectory import (
            RelationshipTurnEvidence,
            accumulate_turn,
            derive_relationship_snapshot,
        )

        anchors, moment = _accumulate_many(6)
        stale_moment = moment + timedelta(days=60)
        stale = derive_relationship_snapshot(anchors, None, stale_moment)
        assert stale.decay_applied is True
        rewarmed = accumulate_turn(
            anchors,
            RelationshipTurnEvidence(user_sent_message=True, session_returned=True),
            stale_moment + timedelta(hours=1),
        )
        # last_seen advanced → dormancy window shrinks for the new readout.
        assert rewarmed.last_seen_at is not None


class TestTrend:
    def test_repeated_positive_evidence_grows(self):
        from commerce.relationship_trajectory import derive_relationship_snapshot

        # >=2 behavioral signals per turn for 3+ turns → streak → GROWING.
        anchors, moment = _accumulate_many(4)
        snapshot = derive_relationship_snapshot(anchors, None, moment)
        assert snapshot.trend.value == "growing"

    def test_disengagement_declines_via_dormancy(self):
        from commerce.relationship_trajectory import derive_relationship_snapshot

        anchors, moment = _accumulate_many(4)
        snapshot = derive_relationship_snapshot(anchors, None, moment + timedelta(days=45))
        assert snapshot.trend.value == "declining"

    def test_mixed_evidence_stable_or_unknown(self):
        from commerce.relationship_trajectory import (
            RelationshipTurnEvidence,
            accumulate_turn,
            derive_relationship_snapshot,
            neutral_anchors,
        )

        anchors = neutral_anchors(T0)
        moment = T0
        # Single-signal turns: streak never reaches the growing threshold.
        for _ in range(6):
            moment = moment + timedelta(hours=25)
            anchors = accumulate_turn(
                anchors, RelationshipTurnEvidence(user_sent_message=True), moment
            )
        snapshot = derive_relationship_snapshot(anchors, None, moment)
        assert snapshot.trend.value in ("stable", "unknown")
        assert snapshot.trend.value != "growing"

    def test_single_turn_never_grows(self):
        from commerce.relationship_trajectory import (
            accumulate_turn,
            derive_relationship_snapshot,
            neutral_anchors,
        )

        anchors = accumulate_turn(neutral_anchors(T0), _rich_evidence(), T0)
        snapshot = derive_relationship_snapshot(anchors, None, T0)
        assert snapshot.trend.value != "growing"


class TestReciprocity:
    def test_user_participation_moves_reciprocity(self):
        from commerce.relationship_trajectory import derive_relationship_snapshot

        anchors, moment = _accumulate_many(8)
        snapshot = derive_relationship_snapshot(anchors, None, moment)
        assert snapshot.reciprocity.value in ("balanced", "high")

    def test_assistant_only_turns_cannot_produce_high_reciprocity(self):
        from commerce.relationship_trajectory import (
            RelationshipTurnEvidence,
            accumulate_turn,
            derive_relationship_snapshot,
            neutral_anchors,
        )

        anchors = neutral_anchors(T0)
        moment = T0
        for _ in range(10):
            moment = moment + timedelta(hours=25)
            anchors = accumulate_turn(
                anchors,
                RelationshipTurnEvidence(
                    assistant_asked_question=True, assistant_shared_information=True
                ),
                moment,
            )
        snapshot = derive_relationship_snapshot(anchors, None, moment)
        assert snapshot.reciprocity.value in ("unknown", "low")
        assert snapshot.reciprocity.value != "high"
        assert snapshot.reciprocity.value != "balanced"


class TestLLMNeutrality:
    def test_strong_llm_engagement_alone_does_not_promote(self):
        from commerce.relationship_trajectory import (
            RelationshipTurnEvidence,
            accumulate_turn,
            derive_relationship_snapshot,
            neutral_anchors,
        )

        llm_only = RelationshipTurnEvidence(
            user_sent_message=True, llm_relationship_engagement=0.95
        )
        plain = RelationshipTurnEvidence(user_sent_message=True)
        with_llm = accumulate_turn(neutral_anchors(T0), llm_only, T0)
        without_llm = accumulate_turn(neutral_anchors(T0), plain, T0)
        assert derive_relationship_snapshot(with_llm, None, T0) == derive_relationship_snapshot(
            without_llm, None, T0
        )
        # Corroboration counter stays zero: a bare message carries no
        # behavioral signal for the high LLM value to corroborate with.
        assert with_llm.corroborated_engagement_observations == 0
        assert derive_relationship_snapshot(with_llm, None, T0).familiarity.value == "new"

    def test_llm_high_with_behavior_only_increments_informational_counter(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        ev = _rich_evidence(llm_relationship_engagement=0.95)
        anchors = accumulate_turn(neutral_anchors(T0), ev, T0)
        assert anchors.corroborated_engagement_observations == 1
        # …and still exactly one upward step from neutral (clamp holds).
        assert anchors.interaction_count == 1


class TestSingleTurnProtection:
    def test_one_compliment_like_turn_does_not_progress(self):
        # A warm but thin turn: message + shared info only (one signal).
        from commerce.relationship_trajectory import (
            RelationshipTurnEvidence,
            accumulate_turn,
            derive_relationship_snapshot,
            neutral_anchors,
        )

        anchors = accumulate_turn(
            neutral_anchors(T0),
            RelationshipTurnEvidence(user_sent_message=True, user_shared_information=True),
            T0,
        )
        snapshot = derive_relationship_snapshot(anchors, None, T0)
        assert snapshot.familiarity.value == "new"
        assert snapshot.trend.value != "growing"

    def test_one_content_request_like_turn_does_not_progress(self):
        # Content curiosity is commerce evidence, not relationship evidence:
        # a bare message carries no participation signals at all.
        from commerce.relationship_trajectory import (
            RelationshipTurnEvidence,
            accumulate_turn,
            derive_relationship_snapshot,
            neutral_anchors,
        )

        anchors = accumulate_turn(
            neutral_anchors(T0), RelationshipTurnEvidence(user_sent_message=True), T0
        )
        snapshot = derive_relationship_snapshot(anchors, None, T0)
        assert snapshot.engagement.value in ("low", "unknown")
        assert snapshot.continuity.value in ("sparse", "unknown")


class TestContinuity:
    def test_returns_and_references_build_continuity(self):
        from commerce.relationship_trajectory import derive_relationship_snapshot

        anchors, moment = _accumulate_many(6)
        snapshot = derive_relationship_snapshot(anchors, None, moment)
        assert snapshot.continuity.value in ("anchored", "rich")

    def test_interaction_without_anchors_stays_sparse(self):
        from commerce.relationship_trajectory import (
            RelationshipTurnEvidence,
            accumulate_turn,
            derive_relationship_snapshot,
            neutral_anchors,
        )

        anchors = neutral_anchors(T0)
        moment = T0
        for _ in range(4):
            moment = moment + timedelta(minutes=10)
            anchors = accumulate_turn(
                anchors, RelationshipTurnEvidence(user_sent_message=True), moment
            )
        snapshot = derive_relationship_snapshot(anchors, None, moment)
        assert snapshot.continuity.value == "sparse"


class TestCreatorIsolation:
    def test_namespaces_are_independent(self):
        from commerce.relationship_trajectory import (
            RELATIONSHIP_TRAJECTORY_KEY,
            anchors_to_dict,
            get_relationship_anchors,
        )

        profile = {
            RELATIONSHIP_TRAJECTORY_KEY: {
                "7": anchors_to_dict(_accumulate_many(10)[0]),
            }
        }
        creator_a = get_relationship_anchors(profile, 7)
        creator_b = get_relationship_anchors(profile, 9)
        assert creator_a.interaction_count == 10
        assert creator_b.interaction_count == 0

    def test_int_creator_key_tolerance(self):
        from commerce.relationship_trajectory import (
            RELATIONSHIP_TRAJECTORY_KEY,
            anchors_to_dict,
            get_relationship_anchors,
        )

        profile = {RELATIONSHIP_TRAJECTORY_KEY: {7: anchors_to_dict(_accumulate_many(3)[0])}}
        assert get_relationship_anchors(profile, 7).interaction_count == 3


class TestNamespacePreservation:
    @pytest.mark.asyncio
    async def test_store_preserves_unrelated_profile_json(self):
        from commerce.relationship_trajectory import (
            RELATIONSHIP_TRAJECTORY_KEY,
            anchors_to_dict,
            neutral_anchors,
            store_relationship_anchors,
        )

        facts = {
            "interests": ["hiking"],
            "long_term_memory_by_creator": {"7": [{"subject": "trip"}]},
            "fan_knowledge_by_creator": {"7": [{"subject": "city"}]},
            "commercial_preferences_by_creator": {"7": {"red lace": {}}},
            RELATIONSHIP_TRAJECTORY_KEY: {"9": anchors_to_dict(neutral_anchors(T0))},
        }

        async def _fake_mutate(user_id, mutator):
            assert mutator(facts) is True
            return True

        with patch(
            "db.postgres.mutate_user_profile_atomically", new=AsyncMock(side_effect=_fake_mutate)
        ):
            assert await store_relationship_anchors(3, 7, neutral_anchors(T0)) is True

        assert facts["interests"] == ["hiking"]
        assert facts["long_term_memory_by_creator"] == {"7": [{"subject": "trip"}]}
        assert facts["fan_knowledge_by_creator"] == {"7": [{"subject": "city"}]}
        assert facts["commercial_preferences_by_creator"] == {"7": {"red lace": {}}}
        # Other creator's relationship namespace preserved; ours written.
        assert set(facts[RELATIONSHIP_TRAJECTORY_KEY].keys()) == {"9", "7"}

    @pytest.mark.asyncio
    async def test_store_failure_is_fail_open(self):
        from commerce.relationship_trajectory import neutral_anchors, store_relationship_anchors

        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            assert await store_relationship_anchors(3, 7, neutral_anchors(T0)) is False


class TestAtomicMutationContract:
    @pytest.mark.asyncio
    async def test_store_uses_atomic_helper_not_blind_write(self):
        from commerce.relationship_trajectory import neutral_anchors, store_relationship_anchors

        seen = {}

        async def _fake_mutate(user_id, mutator):
            seen["user_id"] = user_id
            facts: dict = {}
            assert mutator(facts) is True
            seen["facts"] = facts
            return True

        with patch(
            "db.postgres.mutate_user_profile_atomically", new=AsyncMock(side_effect=_fake_mutate)
        ):
            assert await store_relationship_anchors(42, 7, neutral_anchors(T0)) is True
        assert seen["user_id"] == 42
        assert "relationship_trajectory_by_creator" in seen["facts"]


class TestCorruptAndMissingState:
    def test_missing_namespace_is_neutral(self):
        from commerce.relationship_trajectory import get_relationship_anchors

        assert get_relationship_anchors({}, 7).interaction_count == 0
        assert get_relationship_anchors(None, 7).interaction_count == 0  # type: ignore[arg-type]
        assert get_relationship_anchors("nonsense", 7).interaction_count == 0  # type: ignore[arg-type]

    def test_malformed_block_is_neutral(self):
        from commerce.relationship_trajectory import (
            RELATIONSHIP_TRAJECTORY_KEY,
            get_relationship_anchors,
        )

        for bad in ({"7": "garbage"}, {"7": {"interaction_count": -5}}, {"7": None}, {"7": []}):
            profile = {RELATIONSHIP_TRAJECTORY_KEY: bad}
            anchors = get_relationship_anchors(profile, 7)
            assert anchors.interaction_count == 0

    def test_wrong_types_coerce_safely(self):
        from commerce.relationship_trajectory import anchors_from_dict

        anchors = anchors_from_dict(
            {
                "interaction_count": True,  # bool must not count as 1
                "user_message_count": "many",
                "last_provenance": 99.0,
                "first_seen_at": 12345,
                "transitions": {"familiarity": "oops"},
            }
        )
        assert anchors.interaction_count == 0
        assert anchors.first_seen_at is None
        assert anchors.transitions == {}


class TestVersioning:
    def test_unknown_future_fields_ignored(self):
        from commerce.relationship_trajectory import anchors_from_dict, anchors_to_dict

        anchors, _ = _accumulate_many(4)
        payload = anchors_to_dict(anchors)
        payload["future_field_xyz"] = {"nested": [1, 2, 3]}
        payload["transitions"]["future_band"] = {"from": "a", "to": "b"}
        restored = anchors_from_dict(payload)
        assert restored.interaction_count == 4
        assert "future_band" not in restored.transitions

    def test_serialization_is_deterministic_and_bounded(self):
        from commerce.relationship_trajectory import serialize_relationship_anchors

        anchors, _ = _accumulate_many(10)
        first = serialize_relationship_anchors(anchors)
        second = serialize_relationship_anchors(anchors)
        assert first == second
        # Fixed scalar fields + ≤5 bounded transitions: comfortably small,
        # and never carries raw message text (no free-text inputs exist).
        assert len(first) < 4096
        assert "content" not in first

    def test_schema_version_stamped(self):
        from commerce.relationship_trajectory import SCHEMA_VERSION, anchors_to_dict

        anchors, _ = _accumulate_many(2)
        assert anchors_to_dict(anchors)["schema_version"] == SCHEMA_VERSION


class TestProvenance:
    def test_ltm_compatible_vocabulary(self):
        from commerce import long_term_memory as ltm
        from commerce import relationship_trajectory as rt

        assert rt.PROVENANCE_EXPLICIT == ltm.EXPLICIT
        assert rt.PROVENANCE_SYSTEM_EVENT == ltm.SYSTEM_EVENT
        assert rt.PROVENANCE_STRONG_INFERENCE == ltm.STRONG_INFERENCE
        assert rt.PROVENANCE_WEAK_INFERENCE == ltm.WEAK_INFERENCE

    def test_transitions_carry_deterministic_reasons(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        anchors = accumulate_turn(neutral_anchors(T0), _rich_evidence(), T0)
        assert anchors.transitions  # at least one clamped promotion recorded
        for entry in anchors.transitions.values():
            assert set(entry.keys()) == {"from", "to", "at", "reason"}
            assert entry["reason"].startswith("accumulated:")


class TestCommerceInvariance:
    def test_new_module_imports_no_commerce_authority(self):
        import inspect

        import commerce.relationship_trajectory as rt

        source = inspect.getsource(rt)
        for banned in (
            "commerce.decision",
            "commerce.ranking",
            "commerce.sealing",
            "commerce.execution",
            "commerce/selection",
            "commerce/sealing",
            "commerce/execution",
            "decide_commerce_action",
            "seal_",
            "execute_ppv",
            "price_minor",
            "product_id",
            "offer_id",
        ):
            assert banned not in source, f"banned coupling: {banned}"

    def test_new_module_reuses_no_banned_enum_names(self):
        import commerce.relationship_trajectory as rt

        for name in (
            "RelationshipState",
            "DesireStage",
            "Temperature",
            "WarmingLevel",
            "ReadinessLevel",
            "SalesWindow",
            "ResponseMode",
        ):
            assert not hasattr(rt, name), f"banned name reuse: {name}"

    def test_snapshot_carries_no_commerce_authority_fields(self):
        import dataclasses

        from commerce.relationship_trajectory import RelationshipSnapshot

        field_names = {f.name for f in dataclasses.fields(RelationshipSnapshot)}
        for banned in ("price", "product", "offer", "eligibility", "purchase", "payment"):
            assert not any(banned in name for name in field_names), banned

    def test_existing_commerce_relationship_state_untouched(self):
        from commerce.relationship import RelationshipState

        assert {m.value for m in RelationshipState} == {
            "cold",
            "new",
            "engaged",
            "warm",
            "buying_signal",
            "purchased",
            "repeat_buyer",
            "vip",
            "cooling_down",
            "do_not_push",
            "operator_required",
        }

    def test_no_sexual_or_permission_fields(self):
        import dataclasses

        from commerce.relationship_trajectory import RelationshipAnchors, RelationshipSnapshot

        names = {f.name for f in dataclasses.fields(RelationshipAnchors)} | {
            f.name for f in dataclasses.fields(RelationshipSnapshot)
        }
        blob = " ".join(names).lower()
        for banned in (
            "sexual",
            "intimacy",
            "intimate",
            "arousal",
            "erotic",
            "consent",
            "permission",
            "escalat",
            "tension",
        ):
            assert banned not in blob, f"banned domain leak: {banned}"
