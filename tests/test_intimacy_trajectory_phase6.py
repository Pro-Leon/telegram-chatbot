"""Phase 6 intimacy trajectory tests (IMPLEMENT -> VERIFY).

Pins the deterministic durable domain in
``commerce/intimacy_trajectory.py``:

* five descriptive dimensions, four bands each, no aggregate score
* unknown -> low -> steady -> deep with single-step upward clamp
* no multi-band jumps; neutral turns move/erase nothing
* readout-only decay; counters/timestamps/provenance only (no text)
* schema version; creator isolation; atomic persistence contract
* fail-open on malformed input
* no permission/consent/commerce/adult semantics anywhere

Conventions follow the Phase 1-5 suites: deterministic unit tests
only, real domain objects, no live LLM/DB/Redis (persistence is
tested through a mocked atomic-mutate helper).
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.unit]

from commerce.intimacy_trajectory import (  # noqa: E402
    DECAY_DORMANT_DAYS,
    INTIMACY_DEEP_MIN_OBSERVATIONS,
    INTIMACY_DIMENSIONS,
    INTIMACY_LOW_MIN_OBSERVATIONS,
    INTIMACY_STEADY_MIN_OBSERVATIONS,
    INTIMACY_TRAJECTORY_KEY,
    IntimacyAnchors,
    IntimacyBand,
    IntimacyTurnEvidence,
    accumulate_intimacy_turn,
    derive_intimacy_snapshot,
    get_intimacy_anchors,
    intimacy_anchors_from_dict,
    intimacy_anchors_to_dict,
    neutral_intimacy_anchors,
    neutral_intimacy_snapshot,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _romantic_turn() -> IntimacyTurnEvidence:
    return IntimacyTurnEvidence(romantic_signal=True, user_initiated_intimacy=True)


def _accumulate(evidence: IntimacyTurnEvidence, turns: int) -> IntimacyAnchors:
    anchors: IntimacyAnchors | None = None
    moment = _now()
    for _ in range(turns):
        anchors = accumulate_intimacy_turn(anchors, evidence, moment)
    assert anchors is not None
    return anchors


class TestDimensionsAndBands:
    def test_exactly_five_dimensions(self):
        assert INTIMACY_DIMENSIONS == (
            "romantic",
            "playful",
            "emotional",
            "sexual_conversation",
            "intimate_continuity",
        )

    def test_band_vocabulary_closed(self):
        assert [m.value for m in IntimacyBand] == ["unknown", "low", "steady", "deep"]

    def test_cold_start_all_unknown(self):
        snap = neutral_intimacy_snapshot()
        assert snap.romantic == IntimacyBand.UNKNOWN
        assert snap.playful == IntimacyBand.UNKNOWN
        assert snap.emotional == IntimacyBand.UNKNOWN
        assert snap.sexual_conversation == IntimacyBand.UNKNOWN
        assert snap.intimate_continuity == IntimacyBand.UNKNOWN
        assert snap.decay_applied is False

    def test_dimensions_independent(self):
        anchors = _accumulate(_romantic_turn(), INTIMACY_STEADY_MIN_OBSERVATIONS)
        assert anchors.bands["romantic"] == "steady"
        for dim in ("playful", "emotional", "sexual_conversation", "intimate_continuity"):
            assert anchors.bands[dim] == "unknown"


class TestTransitions:
    def test_unknown_to_low(self):
        anchors = _accumulate(_romantic_turn(), INTIMACY_LOW_MIN_OBSERVATIONS)
        assert anchors.bands["romantic"] == "low"

    def test_single_observation_stays_unknown(self):
        anchors = _accumulate(_romantic_turn(), 1)
        assert anchors.bands["romantic"] == "unknown"

    def test_low_to_steady(self):
        anchors = _accumulate(_romantic_turn(), INTIMACY_STEADY_MIN_OBSERVATIONS)
        assert anchors.bands["romantic"] == "steady"

    def test_steady_to_deep(self):
        anchors = _accumulate(_romantic_turn(), INTIMACY_DEEP_MIN_OBSERVATIONS)
        assert anchors.bands["romantic"] == "deep"

    def test_no_multi_band_jump(self):
        # Hand-built high counters with UNKNOWN committed bands: the
        # clamp must advance exactly one step per accumulation.
        anchors = neutral_intimacy_anchors()
        anchors.romantic_count = INTIMACY_DEEP_MIN_OBSERVATIONS + 40
        updated = accumulate_intimacy_turn(anchors, _romantic_turn(), _now())
        assert updated.bands["romantic"] == "low"
        updated = accumulate_intimacy_turn(updated, _romantic_turn(), _now())
        assert updated.bands["romantic"] == "steady"

    def test_neutral_turn_moves_nothing_erases_nothing(self):
        anchors = _accumulate(_romantic_turn(), INTIMACY_STEADY_MIN_OBSERVATIONS)
        before_counts = (anchors.romantic_count, anchors.observation_count)
        neutral = IntimacyTurnEvidence(user_initiated_intimacy=True)
        updated = accumulate_intimacy_turn(anchors, neutral, _now())
        assert updated.romantic_count == before_counts[0]
        assert updated.observation_count == before_counts[1] + 1
        assert updated.bands["romantic"] == "steady"

    def test_no_artificial_regression(self):
        anchors = _accumulate(_romantic_turn(), INTIMACY_DEEP_MIN_OBSERVATIONS)
        assert anchors.bands["romantic"] == "deep"
        for _ in range(10):
            anchors = accumulate_intimacy_turn(
                anchors, IntimacyTurnEvidence(user_initiated_intimacy=True), _now()
            )
        assert anchors.bands["romantic"] == "deep"
        assert anchors.romantic_count == INTIMACY_DEEP_MIN_OBSERVATIONS

    def test_multiple_observations_same_turn_count_once(self):
        # Boolean evidence: one turn contributes at most one per dimension.
        ev = IntimacyTurnEvidence(
            romantic_signal=True,
            playful_signal=True,
            emotional_signal=True,
            sexual_conversation_signal=True,
            intimate_continuity_signal=True,
            user_initiated_intimacy=True,
        )
        anchors = accumulate_intimacy_turn(None, ev, _now())
        assert anchors.romantic_count == 1
        assert anchors.sexual_conversation_count == 1
        assert anchors.observation_count == 1

    def test_transition_record_bounded_vocabulary(self):
        anchors = _accumulate(_romantic_turn(), INTIMACY_LOW_MIN_OBSERVATIONS)
        entry = anchors.transitions["romantic"]
        assert entry["from"] == "unknown"
        assert entry["to"] == "low"
        assert set(entry) == {"from", "to", "at", "reason"}
        assert "accumulated:" in entry["reason"]


class TestDecay:
    def test_dormant_readout_decays_one_step(self):
        anchors = _accumulate(_romantic_turn(), INTIMACY_STEADY_MIN_OBSERVATIONS)
        assert anchors.bands["romantic"] == "steady"
        old = _now() - timedelta(days=DECAY_DORMANT_DAYS + 5)
        anchors.last_seen_at = old.isoformat()
        snap = derive_intimacy_snapshot(anchors, None, _now())
        assert snap.decay_applied is True
        assert snap.romantic == IntimacyBand.LOW
        # Anchors untouched by readout decay.
        assert anchors.bands["romantic"] == "steady"
        assert anchors.romantic_count == INTIMACY_STEADY_MIN_OBSERVATIONS

    def test_fresh_readout_no_decay(self):
        anchors = _accumulate(_romantic_turn(), INTIMACY_LOW_MIN_OBSERVATIONS)
        snap = derive_intimacy_snapshot(anchors, None, _now())
        assert snap.decay_applied is False
        assert snap.romantic == IntimacyBand.LOW

    def test_snapshot_exposes_no_internals(self):
        anchors = _accumulate(_romantic_turn(), 3)
        snap = derive_intimacy_snapshot(anchors, _romantic_turn(), _now())
        assert not hasattr(snap, "romantic_count")
        assert not hasattr(snap, "last_provenance")
        assert not hasattr(snap, "processed_generation_ids")
        assert snap.active_signals_this_turn == 1


class TestPersistenceContract:
    def test_namespace_key(self):
        assert INTIMACY_TRAJECTORY_KEY == "intimacy_trajectory_by_creator"

    def test_roundtrip_preserves_counters_bands_timestamps(self):
        anchors = _accumulate(_romantic_turn(), 3)
        payload = intimacy_anchors_to_dict(anchors)
        assert payload["schema_version"] == 1
        assert payload["romantic_count"] == 3
        assert payload["romantic_last_observed_at"] is not None
        assert payload["playful_last_observed_at"] is None
        restored = intimacy_anchors_from_dict(payload)
        assert restored.romantic_count == 3
        assert restored.bands == anchors.bands
        assert restored.romantic_last_observed_at == anchors.romantic_last_observed_at

    def test_no_raw_text_in_payload(self):
        anchors = _accumulate(_romantic_turn(), 3)
        import json

        blob = json.dumps(intimacy_anchors_to_dict(anchors)).lower()
        for token in ("miss you", "sexy", "kiss", "permission", "consent", "offer", "ppv"):
            assert token not in blob, token

    def test_malformed_input_fails_safe(self):
        assert intimacy_anchors_from_dict(None).bands == {}
        assert intimacy_anchors_from_dict("nonsense").bands == {}
        assert intimacy_anchors_from_dict({"bands": {"romantic": "deep"}}).bands == {
            "romantic": "deep"
        }
        # Unknown band values dropped, never interpreted.
        assert intimacy_anchors_from_dict({"bands": {"romantic": "extreme"}}).bands == {}
        assert get_intimacy_anchors(None, 7).bands == {}
        assert get_intimacy_anchors({"other": {}}, 7).bands == {}

    def test_creator_isolation(self):
        anchors = _accumulate(_romantic_turn(), INTIMACY_LOW_MIN_OBSERVATIONS)
        profile = {INTIMACY_TRAJECTORY_KEY: {"7": intimacy_anchors_to_dict(anchors)}}
        own = get_intimacy_anchors(profile, 7)
        foreign = get_intimacy_anchors(profile, 9)
        assert own.bands["romantic"] == "low"
        assert foreign.bands == {}
        # int keys tolerated on read.
        int_keyed = {INTIMACY_TRAJECTORY_KEY: {7: intimacy_anchors_to_dict(anchors)}}
        assert get_intimacy_anchors(int_keyed, 7).bands["romantic"] == "low"

    def test_assembly_mutates_nothing(self):
        anchors = _accumulate(_romantic_turn(), 2)
        profile = {INTIMACY_TRAJECTORY_KEY: {"7": intimacy_anchors_to_dict(anchors)}}
        before = copy.deepcopy(profile)
        get_intimacy_anchors(profile, 7)
        derive_intimacy_snapshot(get_intimacy_anchors(profile, 7))
        assert profile == before

    def test_only_own_namespace_written(self):
        seen: dict = {}

        async def _fake_mutate(uid: int, fn) -> bool:
            facts = seen.setdefault(uid, {"relationship_trajectory_by_creator": {"7": {"x": 1}}})
            facts_copy = copy.deepcopy(facts)
            changed = fn(facts_copy)
            if changed:
                seen[uid] = facts_copy
            return changed

        async def _run() -> None:
            from commerce.intimacy_trajectory import store_intimacy_anchors

            with patch(
                "db.postgres.mutate_user_profile_atomically", side_effect=_fake_mutate
            ):
                anchors = _accumulate(_romantic_turn(), 2)
                assert await store_intimacy_anchors(1, 7, anchors) is True

        import asyncio

        asyncio.run(_run())
        assert seen[1]["relationship_trajectory_by_creator"] == {"7": {"x": 1}}
        assert seen[1][INTIMACY_TRAJECTORY_KEY]["7"]["romantic_count"] == 2


class TestNoForbiddenSemantics:
    def test_dataclass_fields_contain_no_permission_commerce_adult(self):
        import dataclasses

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
            "desire",
            "temperature",
            "readiness",
            "offer",
            "price",
        }
        for cls in (IntimacyAnchors, IntimacyTurnEvidence):
            names = {f.name for f in dataclasses.fields(cls)}
            assert names.isdisjoint(forbidden), names & forbidden

    def test_module_imports_no_commerce_authority(self):
        import pathlib

        src = pathlib.Path("commerce/intimacy_trajectory.py").read_text()
        for token in (
            "from commerce.desire",
            "from commerce.temperature",
            "from commerce.readiness",
            "from commerce.offer_readiness",
            "from commerce.relationship import",
            "from commerce.signals",
            "derive_desire_stage",
            "derive_commercial_temperature",
            "evaluate_offer_readiness",
        ):
            assert token not in src, token

    def test_module_source_has_no_writes(self):
        import pathlib

        src = pathlib.Path("commerce/intimacy_trajectory.py").read_text().lower()
        for token in ("publish_event", "enqueue_", "xadd", "setex", ".execute("):
            assert token not in src, token
