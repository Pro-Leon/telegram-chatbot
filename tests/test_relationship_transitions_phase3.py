"""Phase 3 relationship-state transition tests (AUDIT → CHARACTERIZE → IMPLEMENT).

Pins the deterministic transition policy of
``commerce.relationship_trajectory`` exactly as implemented:

* independent per-dimension transitions from counter-derived candidates
* committed-band baseline with at-most-one upward step per accumulation
* no invented durable downward transitions (existing candidate behavior
  characterized, never redesigned here)
* trend entirely derived, never persisted as an authoritative band
* LLM promotion-inert under every transition path
* single-turn protection on every persisted dimension
* replay/idempotency through the existing atomic Phase 2 mutation
* zero new durable fields, schema v1 unchanged
* (creator_id, user_id) isolation, fail-open behavior preserved

Conventions follow the Phase 1/2 suites: ``pytestmark =
[pytest.mark.unit]``, explicit ``@pytest.mark.asyncio`` for async paths,
``AsyncMock``/``patch`` for DB boundaries (no live infra).

These tests were written to pass on the unmodified implementation first
(characterization); the Phase 3 production change is a behavior-preserving
extraction of the explicit transition policy plus this suite.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit]

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _ev(**overrides):
    from commerce.relationship_trajectory import RelationshipTurnEvidence

    base = {"user_sent_message": True}
    base.update(overrides)
    return RelationshipTurnEvidence(**base)


def _bands_at(anchors, moment):
    from commerce.relationship_trajectory import derive_relationship_snapshot

    snap = derive_relationship_snapshot(anchors, None, moment)
    return (
        snap.familiarity.value,
        snap.engagement.value,
        snap.reciprocity.value,
        snap.continuity.value,
        snap.trend.value,
    )


def _make_fake_mutate(store: dict):
    async def _fake_mutate(user_id, mutator):
        facts = store.setdefault(int(user_id), {})
        try:
            should_write = mutator(facts)
        except Exception:
            return False
        return bool(should_write)

    return _fake_mutate


# ---------------------------------------------------------------------------
# Familiarity transitions
# ---------------------------------------------------------------------------


class TestFamiliarityTransitions:
    def test_unknown_to_new_on_first_turn(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        anchors = accumulate_turn(neutral_anchors(T0), _ev(), T0)
        assert anchors.bands.get("familiarity") == "new"
        assert anchors.transitions["familiarity"]["from"] == "unknown"
        assert anchors.transitions["familiarity"]["to"] == "new"

    def test_below_familiar_threshold_stays_new(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # 4 interactions with returns: below FAMILIAR_MIN_INTERACTIONS (5).
        anchors = neutral_anchors(T0)
        moment = T0
        for _ in range(4):
            moment = moment + timedelta(hours=1)
            anchors = accumulate_turn(anchors, _ev(session_returned=True), moment)
        assert _bands_at(anchors, moment)[0] == "new"

    def test_exactly_at_familiar_threshold(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # 5 interactions (>=5) + >=3 messages + >=1 return -> FAMILIAR.
        anchors = neutral_anchors(T0)
        moment = T0
        for _ in range(5):
            moment = moment + timedelta(hours=1)
            anchors = accumulate_turn(anchors, _ev(session_returned=True), moment)
        assert _bands_at(anchors, moment)[0] == "familiar"
        assert anchors.transitions["familiarity"]["from"] == "new"
        assert anchors.transitions["familiarity"]["to"] == "familiar"

    def test_just_above_familiar_threshold_holds(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        anchors = neutral_anchors(T0)
        moment = T0
        for _ in range(6):
            moment = moment + timedelta(hours=1)
            anchors = accumulate_turn(anchors, _ev(session_returned=True), moment)
        assert _bands_at(anchors, moment)[0] == "familiar"

    def test_volume_without_span_or_return_never_familiar(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # 20 rapid neutral turns: volume gates pass but span/return gates fail.
        anchors = neutral_anchors(T0)
        moment = T0
        for _ in range(20):
            moment = moment + timedelta(minutes=5)
            anchors = accumulate_turn(anchors, _ev(), moment)
        assert _bands_at(anchors, moment)[0] == "new"

    def test_established_below_thresholds(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # 19 interactions with returns and wide span: below 20 minimum.
        anchors = neutral_anchors(T0)
        moment = T0
        for i in range(19):
            moment = T0 + timedelta(hours=12 * (i + 1))
            anchors = accumulate_turn(anchors, _ev(session_returned=True), moment)
        assert _bands_at(anchors, moment)[0] == "familiar"

    def test_established_exact_thresholds(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # 20 interactions + returns, span 9.5d (>= 7.0) -> ESTABLISHED.
        anchors = neutral_anchors(T0)
        moment = T0
        for i in range(20):
            moment = T0 + timedelta(hours=12 * (i + 1))
            anchors = accumulate_turn(anchors, _ev(session_returned=True), moment)
        assert _bands_at(anchors, moment)[0] == "established"
        assert anchors.transitions["familiarity"]["from"] == "familiar"
        assert anchors.transitions["familiarity"]["to"] == "established"

    def test_established_requires_span(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # 20 interactions + returns but no span -> not ESTABLISHED.
        anchors = neutral_anchors(T0)
        moment = T0
        for _ in range(20):
            moment = moment + timedelta(minutes=5)
            anchors = accumulate_turn(anchors, _ev(session_returned=True), moment)
        assert _bands_at(anchors, moment)[0] != "established"

    def test_established_requires_returns(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # 20 interactions over 9.5d but only 2 returns (< 3) -> not ESTABLISHED.
        anchors = neutral_anchors(T0)
        moment = T0
        for i in range(20):
            moment = T0 + timedelta(hours=12 * (i + 1))
            anchors = accumulate_turn(anchors, _ev(session_returned=(i < 2)), moment)
        assert anchors.session_return_count == 2
        assert _bands_at(anchors, moment)[0] != "established"

    def test_span_boundary_7d(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # 20 interactions + returns; span 167h (< 7d) vs 168h (== 7d).
        for span_h, expected in ((167, "familiar"), (168, "established")):
            anchors = neutral_anchors(T0)
            for i in range(20):
                frac = i / 19
                anchors = accumulate_turn(
                    anchors, _ev(session_returned=True), T0 + timedelta(hours=frac * span_h)
                )
            assert _bands_at(anchors, T0 + timedelta(hours=span_h))[0] == expected, span_h

    def test_sequential_path_never_skips(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        order = ["unknown", "new", "familiar", "established"]
        anchors = neutral_anchors(T0)
        moment = T0
        prev_idx = 0
        for i in range(21):
            moment = T0 + timedelta(hours=12 * (i + 1))
            anchors = accumulate_turn(anchors, _ev(session_returned=True), moment)
            idx = order.index(anchors.bands.get("familiarity", "unknown"))
            assert idx - prev_idx <= 1, f"turn {i + 1} jumped familiarity"
            assert idx >= prev_idx, "committed familiarity must not regress on volume"
            prev_idx = idx
        assert anchors.bands.get("familiarity") == "established"


# ---------------------------------------------------------------------------
# Engagement transitions
# ---------------------------------------------------------------------------


class TestEngagementTransitions:
    def test_first_turn_is_low(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        anchors = accumulate_turn(
            neutral_anchors(T0),
            _ev(
                user_asked_question=True,
                user_shared_information=True,
                user_continued_topic=True,
            ),
            T0,
        )
        assert anchors.bands.get("engagement") == "low"

    def test_steady_below_message_threshold(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # 4 messages with signals: below STEADY minimum (5 messages).
        anchors = neutral_anchors(T0)
        moment = T0
        for _ in range(4):
            moment = moment + timedelta(hours=1)
            anchors = accumulate_turn(anchors, _ev(user_shared_information=True), moment)
        assert _bands_at(anchors, moment)[1] == "low"

    def test_steady_below_signal_threshold(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # 5 messages but only 2 participation signals (< 3).
        anchors = neutral_anchors(T0)
        moment = T0
        for i in range(5):
            moment = moment + timedelta(hours=1)
            anchors = accumulate_turn(anchors, _ev(user_asked_question=(i < 2)), moment)
        assert _bands_at(anchors, moment)[1] == "low"

    def test_steady_exact_threshold(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # 5 messages + 3 signals -> STEADY.
        anchors = neutral_anchors(T0)
        moment = T0
        for i in range(5):
            moment = moment + timedelta(hours=1)
            anchors = accumulate_turn(
                anchors,
                _ev(user_asked_question=(i < 2), user_answered_question=(i == 2)),
                moment,
            )
        assert _bands_at(anchors, moment)[1] == "steady"
        assert anchors.transitions["engagement"]["from"] == "low"
        assert anchors.transitions["engagement"]["to"] == "steady"

    def test_deep_below_thresholds(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # 14 messages with disclosure+continuation: below 15-message minimum.
        anchors = neutral_anchors(T0)
        moment = T0
        for _ in range(14):
            moment = moment + timedelta(hours=1)
            anchors = accumulate_turn(
                anchors,
                _ev(user_shared_information=True, user_continued_topic=True),
                moment,
            )
        assert _bands_at(anchors, moment)[1] == "steady"

    def test_deep_exact_thresholds(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # 15 messages + 5 shared + 3 continuations -> DEEP.
        anchors = neutral_anchors(T0)
        moment = T0
        for i in range(15):
            moment = moment + timedelta(hours=1)
            anchors = accumulate_turn(
                anchors,
                _ev(
                    user_shared_information=(i < 5),
                    user_continued_topic=(i < 3),
                    user_asked_question=True,
                ),
                moment,
            )
        assert anchors.user_message_count == 15
        assert anchors.user_shared_info_count == 5
        assert anchors.user_topic_continuation_count == 3
        assert _bands_at(anchors, moment)[1] == "deep"
        assert anchors.transitions["engagement"]["from"] == "steady"
        assert anchors.transitions["engagement"]["to"] == "deep"

    def test_sequential_path_low_steady_deep(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        anchors = neutral_anchors(T0)
        moment = T0
        seen = []
        for _ in range(16):
            moment = moment + timedelta(hours=1)
            anchors = accumulate_turn(
                anchors,
                _ev(
                    user_shared_information=True,
                    user_continued_topic=True,
                    user_asked_question=True,
                ),
                moment,
            )
            seen.append(anchors.bands.get("engagement"))
        # Entry, then steady at 5, then deep at 15 — never skipping.
        assert seen[0] == "low"
        assert seen[4] == "steady"
        assert seen[14] == "deep"
        order = ["unknown", "low", "steady", "deep"]
        for prev, cur in zip(["unknown"] + seen, seen):
            assert order.index(cur) - order.index(prev) <= 1


# ---------------------------------------------------------------------------
# Reciprocity transitions
# ---------------------------------------------------------------------------


class TestReciprocityTransitions:
    def test_zero_moves_stays_unknown(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        anchors = neutral_anchors(T0)
        moment = T0
        for _ in range(8):
            moment = moment + timedelta(hours=1)
            anchors = accumulate_turn(anchors, _ev(), moment)
        assert _bands_at(anchors, moment)[2] == "unknown"
        assert "reciprocity" not in anchors.transitions

    def test_assistant_only_needs_an_interaction_first(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # A lone assistant turn with zero interactions reads UNKNOWN
        # (interaction gate); once any user interaction exists, the same
        # assistant-only evidence reads LOW (creator side carries it).
        moment = T0
        anchors = accumulate_turn(
            neutral_anchors(T0),
            _ev(user_sent_message=False, assistant_asked_question=True),
            moment,
        )
        assert anchors.interaction_count == 0
        # The clamp loop records every band each accumulation, so UNKNOWN is
        # committed explicitly — with no transition entry (nothing changed).
        assert anchors.bands.get("reciprocity") == "unknown"
        assert anchors.transitions == {}
        moment = moment + timedelta(hours=1)
        anchors = accumulate_turn(anchors, _ev(), moment)
        moment = moment + timedelta(hours=1)
        anchors = accumulate_turn(
            anchors,
            _ev(user_sent_message=False, assistant_asked_question=True),
            moment,
        )
        assert anchors.bands.get("reciprocity") == "low"

    def test_share_exactly_one_quarter_is_balanced(self):
        from commerce.relationship_trajectory import RelationshipAnchors, accumulate_turn

        # share == 0.25 is not < 0.25 -> BALANCED, stays (no transition).
        base = RelationshipAnchors(
            interaction_count=2,
            user_message_count=2,
            user_question_count=1,
            assistant_question_count=3,
            first_seen_at=T0.isoformat(),
            last_seen_at=T0.isoformat(),
            bands={"reciprocity": "balanced"},
            observation_count=2,
        )
        updated = accumulate_turn(base, _ev(), T0 + timedelta(hours=1))
        assert updated.bands.get("reciprocity") == "balanced"
        assert "reciprocity" not in updated.transitions

    def test_share_below_one_quarter_moves_low(self):
        from commerce.relationship_trajectory import RelationshipAnchors, accumulate_turn

        base = RelationshipAnchors(
            interaction_count=2,
            user_message_count=2,
            user_question_count=1,
            assistant_question_count=4,
            first_seen_at=T0.isoformat(),
            last_seen_at=T0.isoformat(),
            bands={"reciprocity": "balanced"},
            observation_count=2,
        )
        updated = accumulate_turn(base, _ev(), T0 + timedelta(hours=1))
        assert updated.bands.get("reciprocity") == "low"
        assert updated.transitions["reciprocity"]["from"] == "balanced"
        assert updated.transitions["reciprocity"]["to"] == "low"

    def test_share_exactly_three_quarters_is_balanced(self):
        from commerce.relationship_trajectory import RelationshipAnchors, accumulate_turn

        # share == 0.75 is not > 0.75 -> BALANCED, stays.
        base = RelationshipAnchors(
            interaction_count=5,
            user_message_count=5,
            user_question_count=3,
            assistant_question_count=1,
            first_seen_at=T0.isoformat(),
            last_seen_at=T0.isoformat(),
            bands={"reciprocity": "balanced"},
            observation_count=5,
        )
        updated = accumulate_turn(base, _ev(), T0 + timedelta(hours=1))
        assert updated.bands.get("reciprocity") == "balanced"
        assert "reciprocity" not in updated.transitions

    def test_high_requires_five_user_messages(self):
        from commerce.relationship_trajectory import RelationshipAnchors, accumulate_turn

        # share == 1.0 but only 4 user messages: an assistant-only turn
        # keeps message volume at 4 -> BALANCED, not HIGH.
        base = RelationshipAnchors(
            interaction_count=4,
            user_message_count=4,
            user_question_count=4,
            first_seen_at=T0.isoformat(),
            last_seen_at=T0.isoformat(),
            bands={"reciprocity": "balanced"},
            observation_count=4,
        )
        updated = accumulate_turn(
            base,
            _ev(user_sent_message=False, assistant_asked_question=True),
            T0 + timedelta(hours=1),
        )
        assert updated.user_message_count == 4
        assert updated.bands.get("reciprocity") == "balanced"
        assert "reciprocity" not in updated.transitions

    def test_high_exact_gate(self):
        from commerce.relationship_trajectory import RelationshipAnchors, accumulate_turn

        base = RelationshipAnchors(
            interaction_count=5,
            user_message_count=5,
            user_question_count=5,
            first_seen_at=T0.isoformat(),
            last_seen_at=T0.isoformat(),
            bands={"reciprocity": "balanced"},
            observation_count=5,
        )
        updated = accumulate_turn(base, _ev(), T0 + timedelta(hours=1))
        assert updated.bands.get("reciprocity") == "high"
        assert updated.transitions["reciprocity"]["from"] == "balanced"
        assert updated.transitions["reciprocity"]["to"] == "high"

    def test_natural_path_to_high_is_one_step_at_a_time(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # HIGH additionally gates on >= 5 user messages, so the natural
        # all-question path reads LOW, BALANCED x3, then HIGH on turn 5.
        anchors = neutral_anchors(T0)
        moment = T0
        seen = []
        for _ in range(5):
            moment = moment + timedelta(hours=1)
            anchors = accumulate_turn(anchors, _ev(user_asked_question=True), moment)
            seen.append(anchors.bands.get("reciprocity"))
        assert seen == ["low", "balanced", "balanced", "balanced", "high"]
        order = ["unknown", "low", "balanced", "high"]
        for prev, cur in zip(["unknown"] + seen, seen):
            assert order.index(cur) - order.index(prev) <= 1

    def test_downward_high_to_balanced_when_share_crosses(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # 10 user-heavy turns -> HIGH; assistant turns dilute share to 0.75.
        anchors = neutral_anchors(T0)
        moment = T0
        for _ in range(10):
            moment = moment + timedelta(hours=1)
            anchors = accumulate_turn(
                anchors,
                _ev(
                    user_asked_question=True,
                    user_answered_question=True,
                    user_shared_information=True,
                ),
                moment,
            )
        assert anchors.bands.get("reciprocity") == "high"
        for _ in range(5):
            moment = moment + timedelta(hours=1)
            anchors = accumulate_turn(
                anchors,
                _ev(
                    user_sent_message=False,
                    assistant_asked_question=True,
                    assistant_shared_information=True,
                ),
                moment,
            )
        # 30 user moves / (30 + 10) assistant moves == 0.75 -> BALANCED.
        assert anchors.bands.get("reciprocity") == "balanced"
        assert anchors.transitions["reciprocity"]["from"] == "high"
        assert anchors.transitions["reciprocity"]["to"] == "balanced"

    def test_downward_follows_candidate_directly(self):
        from commerce.relationship_trajectory import RelationshipAnchors, accumulate_turn

        # Constructed HIGH baseline whose counters read LOW: the committed
        # band follows the candidate in one accumulation (downward movement
        # is intentionally unclamped per the module contract).
        base = RelationshipAnchors(
            interaction_count=5,
            user_message_count=5,
            user_question_count=1,
            assistant_question_count=10,
            first_seen_at=T0.isoformat(),
            last_seen_at=T0.isoformat(),
            bands={"reciprocity": "high"},
            observation_count=5,
        )
        updated = accumulate_turn(base, _ev(), T0 + timedelta(hours=1))
        assert updated.bands.get("reciprocity") == "low"
        assert updated.transitions["reciprocity"] == {
            "from": "high",
            "to": "low",
            "at": updated.transitions["reciprocity"]["at"],
            "reason": updated.transitions["reciprocity"]["reason"],
        }


# ---------------------------------------------------------------------------
# Continuity transitions
# ---------------------------------------------------------------------------


class TestContinuityTransitions:
    def test_single_anchor_event_commits_sparse(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # Candidate reads ANCHORED (1 kind) but the committed band advances
        # only one step from UNKNOWN -> SPARSE.
        anchors = accumulate_turn(neutral_anchors(T0), _ev(session_returned=True), T0)
        assert anchors.bands.get("continuity") == "sparse"
        assert anchors.transitions["continuity"]["from"] == "unknown"
        assert anchors.transitions["continuity"]["to"] == "sparse"

    def test_each_anchor_kind_contributes(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        cases = [
            {"session_returned": True},
            {"open_loop_continued": True},
            {"open_loop_resolved": True},
            {"user_referenced_previous_context": True},
        ]
        for kwargs in cases:
            anchors = accumulate_turn(neutral_anchors(T0), _ev(**kwargs), T0)
            assert anchors.bands.get("continuity") == "sparse", kwargs

    def test_single_continuation_alone_is_not_a_kind(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # Topic continuation kind requires >= 2 continuations.
        anchors = accumulate_turn(neutral_anchors(T0), _ev(user_continued_topic=True), T0)
        assert anchors.user_topic_continuation_count == 1
        assert anchors.bands.get("continuity") == "sparse"

    def test_second_continuation_reaches_anchored(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        anchors = neutral_anchors(T0)
        moment = T0
        for _ in range(2):
            moment = moment + timedelta(hours=1)
            anchors = accumulate_turn(anchors, _ev(user_continued_topic=True), moment)
        assert anchors.bands.get("continuity") == "anchored"
        assert anchors.transitions["continuity"]["from"] == "sparse"
        assert anchors.transitions["continuity"]["to"] == "anchored"

    def test_rich_needs_three_turns_minimum(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # Even with 3 anchor kinds in one turn, the clamp allows only
        # SPARSE on turn 1, ANCHORED on turn 2, RICH on turn 3.
        anchors = neutral_anchors(T0)
        moment = T0
        seen = []
        for _ in range(4):
            moment = moment + timedelta(hours=1)
            anchors = accumulate_turn(
                anchors,
                _ev(
                    session_returned=True,
                    open_loop_continued=True,
                    user_referenced_previous_context=True,
                ),
                moment,
            )
            seen.append(anchors.bands.get("continuity"))
        assert seen == ["sparse", "anchored", "rich", "rich"]

    def test_rich_via_returns_rule(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # >= 3 returns + >= 2 kinds (return + loop) -> RICH.
        anchors = neutral_anchors(T0)
        moment = T0
        for _ in range(3):
            moment = moment + timedelta(hours=25)
            anchors = accumulate_turn(
                anchors,
                _ev(session_returned=True, open_loop_continued=True),
                moment,
            )
        assert anchors.session_return_count == 3
        assert anchors.bands.get("continuity") == "rich"


# ---------------------------------------------------------------------------
# One-step clamp, isolated from thresholds
# ---------------------------------------------------------------------------


class TestOneStepClampIsolated:
    def test_divergent_baseline_advances_exactly_one_step_per_band(self):
        from commerce.relationship_trajectory import RelationshipAnchors, accumulate_turn

        # Counters already satisfy top bands, but committed levels lag:
        # each band must advance exactly one step, never jump.
        base = RelationshipAnchors(
            interaction_count=30,
            user_message_count=30,
            user_shared_info_count=10,
            user_topic_continuation_count=10,
            user_question_count=10,
            user_answer_count=10,
            first_seen_at=T0.isoformat(),
            last_seen_at=(T0 + timedelta(days=10)).isoformat(),
            bands={"familiarity": "new", "engagement": "low"},
            observation_count=30,
        )
        updated = accumulate_turn(base, _ev(), T0 + timedelta(days=10, hours=1))
        assert updated.bands.get("familiarity") == "familiar"  # not established
        assert updated.bands.get("engagement") == "steady"  # not deep
        assert updated.bands.get("reciprocity") == "low"  # not high/balanced
        assert updated.bands.get("continuity") == "sparse"  # not anchored/rich
        assert updated.transitions["familiarity"] == {
            "from": "new",
            "to": "familiar",
            "at": updated.transitions["familiarity"]["at"],
            "reason": updated.transitions["familiarity"]["reason"],
        }
        assert updated.transitions["engagement"]["from"] == "low"
        assert updated.transitions["engagement"]["to"] == "steady"

    def test_dimensions_transition_independently(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # Continuity-only evidence (returns + loops, no questions/disclosure):
        # continuity advances while reciprocity stays UNKNOWN and engagement
        # stays LOW — one dimension reaching threshold never drags another.
        anchors = neutral_anchors(T0)
        moment = T0
        for _ in range(8):
            moment = moment + timedelta(hours=1)
            anchors = accumulate_turn(
                anchors,
                _ev(session_returned=True, open_loop_continued=True),
                moment,
            )
        assert anchors.bands.get("continuity") in ("anchored", "rich")
        assert anchors.bands.get("reciprocity") == "unknown"
        assert anchors.bands.get("engagement") == "low"


# ---------------------------------------------------------------------------
# Downward behavior characterization (no invented decline)
# ---------------------------------------------------------------------------


class TestDownwardCharacterization:
    def test_familiarity_engagement_continuity_never_regress_on_volume(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        order = {
            "familiarity": ["unknown", "new", "familiar", "established"],
            "engagement": ["unknown", "low", "steady", "deep"],
            "continuity": ["unknown", "sparse", "anchored", "rich"],
        }
        evidences = [
            _ev(user_shared_information=True, user_asked_question=True),
            _ev(),
            _ev(user_sent_message=False, assistant_asked_question=True),
            _ev(session_returned=True, user_continued_topic=True),
        ]
        anchors = neutral_anchors(T0)
        moment = T0
        prev = {k: 0 for k in order}
        for i in range(40):
            moment = moment + timedelta(hours=3)
            anchors = accumulate_turn(anchors, evidences[i % 4], moment)
            for dim, levels in order.items():
                idx = levels.index(anchors.bands.get(dim, levels[0]))
                assert idx >= prev[dim], f"{dim} regressed on turn {i + 1}"
                prev[dim] = idx

    def test_trend_decline_is_readout_only(self):
        from commerce.relationship_trajectory import (
            derive_relationship_snapshot,
            serialize_relationship_anchors,
        )

        anchors, moment = _accumulate_rich(4)
        before = serialize_relationship_anchors(anchors)
        stale = derive_relationship_snapshot(anchors, None, moment + timedelta(days=60))
        assert stale.trend.value == "declining"
        assert stale.decay_applied is True
        # The declining readout mutated nothing durable.
        assert serialize_relationship_anchors(anchors) == before
        assert "declining" not in anchors.bands.values()


def _accumulate_rich(n, start=T0, step_hours=25):
    from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

    anchors = neutral_anchors(start)
    moment = start
    for _ in range(n):
        moment = moment + timedelta(hours=step_hours)
        anchors = accumulate_turn(
            anchors,
            _ev(
                user_asked_question=True,
                user_shared_information=True,
                user_continued_topic=True,
            ),
            moment,
        )
    return anchors, moment


# ---------------------------------------------------------------------------
# Trend stays derived
# ---------------------------------------------------------------------------


class TestTrendDerived:
    def test_streak_progression_stable_then_growing(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        anchors = neutral_anchors(T0)
        moment = T0
        seen = []
        for _ in range(4):
            moment = moment + timedelta(hours=25)
            anchors = accumulate_turn(
                anchors,
                _ev(user_asked_question=True, user_shared_information=True),
                moment,
            )
            seen.append(derive_trend_value(anchors, moment))
        assert seen == ["stable", "stable", "growing", "growing"]

    def test_neutral_turn_resets_streak_to_stable(self):
        from commerce.relationship_trajectory import accumulate_turn

        anchors, moment = _accumulate_rich(3)
        assert derive_trend_value(anchors, moment) == "growing"
        moment = moment + timedelta(hours=25)
        anchors = accumulate_turn(anchors, _ev(), moment)
        assert anchors.positive_streak == 0
        assert derive_trend_value(anchors, moment) == "stable"

    def test_bare_message_never_grows(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        anchors = neutral_anchors(T0)
        moment = T0
        for _ in range(6):
            moment = moment + timedelta(hours=25)
            anchors = accumulate_turn(anchors, _ev(), moment)
        assert derive_trend_value(anchors, moment) != "growing"

    def test_dormancy_reads_declining(self):
        anchors, moment = _accumulate_rich(4)
        assert derive_trend_value(anchors, moment + timedelta(days=45)) == "declining"

    def test_return_rewarm_exact_behavior(self):
        from commerce.relationship_trajectory import accumulate_turn

        anchors, moment = _accumulate_rich(3)
        assert anchors.positive_streak == 3
        # Dormancy preserves the durable streak counter.
        assert derive_trend_value(anchors, moment + timedelta(days=60)) == "declining"
        # A qualifying return turn extends the preserved streak: GROWING.
        rewarmed = accumulate_turn(
            anchors,
            _ev(session_returned=True, user_shared_information=True),
            moment + timedelta(days=60, hours=1),
        )
        assert rewarmed.positive_streak == 4
        assert derive_trend_value(rewarmed, moment + timedelta(days=60, hours=1)) == "growing"

    def test_trend_never_in_committed_bands(self):
        anchors, _ = _accumulate_rich(6)
        assert "trend" not in anchors.bands
        assert anchors.transitions.get("trend", {}).get("to") == "growing"

    def test_trend_level_not_serialized(self):
        from commerce.relationship_trajectory import anchors_to_dict

        anchors, _ = _accumulate_rich(6)
        assert "trend" not in anchors_to_dict(anchors)["bands"]

    def test_snapshot_reread_does_not_mutate(self):
        from commerce.relationship_trajectory import (
            derive_relationship_snapshot,
            serialize_relationship_anchors,
        )

        anchors, moment = _accumulate_rich(4)
        before = serialize_relationship_anchors(anchors)
        first = derive_relationship_snapshot(anchors, None, moment)
        second = derive_relationship_snapshot(anchors, None, moment)
        assert first == second
        assert serialize_relationship_anchors(anchors) == before


def derive_trend_value(anchors, moment):
    from commerce.relationship_trajectory import derive_relationship_snapshot

    return derive_relationship_snapshot(anchors, None, moment).trend.value


# ---------------------------------------------------------------------------
# LLM immutability
# ---------------------------------------------------------------------------


class TestLLMImmutability:
    def test_high_llm_without_behavior_is_fully_identical(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        llm_only = _ev(llm_relationship_engagement=0.95)
        plain = _ev()
        with_llm = accumulate_turn(neutral_anchors(T0), llm_only, T0)
        without_llm = accumulate_turn(neutral_anchors(T0), plain, T0)
        assert with_llm == without_llm

    def test_high_llm_with_behavior_changes_only_counter(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        llm = _ev(user_shared_information=True, llm_relationship_engagement=0.95)
        plain = _ev(user_shared_information=True)
        with_llm = accumulate_turn(neutral_anchors(T0), llm, T0)
        without_llm = accumulate_turn(neutral_anchors(T0), plain, T0)
        assert with_llm.bands == without_llm.bands
        assert with_llm.transitions == without_llm.transitions
        assert with_llm.corroborated_engagement_observations == 1
        assert without_llm.corroborated_engagement_observations == 0

    def test_invalid_llm_values_are_identical_to_absent(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        plain = accumulate_turn(neutral_anchors(T0), _ev(), T0)
        for bad in (True, "0.95", float("nan"), 1.5, -0.1):
            tampered = _ev()
            object.__setattr__(tampered, "llm_relationship_engagement", bad)
            assert accumulate_turn(neutral_anchors(T0), tampered, T0) == plain, bad

    def test_repeated_llm_signals_never_promote(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        anchors_llm = neutral_anchors(T0)
        anchors_plain = neutral_anchors(T0)
        moment = T0
        for _ in range(6):
            moment = moment + timedelta(hours=25)
            anchors_llm = accumulate_turn(
                anchors_llm,
                _ev(user_shared_information=True, llm_relationship_engagement=0.99),
                moment,
            )
            anchors_plain = accumulate_turn(
                anchors_plain, _ev(user_shared_information=True), moment
            )
        assert anchors_llm.bands == anchors_plain.bands
        assert anchors_llm.transitions == anchors_plain.transitions
        assert anchors_llm.corroborated_engagement_observations == 6


# ---------------------------------------------------------------------------
# Single-turn protection
# ---------------------------------------------------------------------------


class TestSingleTurnProtection:
    def test_maximal_turn_lands_on_entry_levels(self):
        from commerce.relationship_trajectory import RelationshipTurnEvidence, accumulate_turn

        max_ev = RelationshipTurnEvidence(
            user_sent_message=True,
            user_asked_question=True,
            user_answered_question=True,
            user_shared_information=True,
            user_continued_topic=True,
            user_referenced_previous_context=True,
            assistant_asked_question=True,
            assistant_shared_information=True,
            session_returned=True,
            open_loop_continued=True,
            open_loop_resolved=True,
        )
        anchors = accumulate_turn(None, max_ev, T0)
        assert anchors.bands.get("familiarity") == "new"
        assert anchors.bands.get("engagement") == "low"
        assert anchors.bands.get("reciprocity") == "low"
        assert anchors.bands.get("continuity") == "sparse"

    def test_single_turn_cannot_reach_top_bands(self):
        from commerce.relationship_trajectory import RelationshipTurnEvidence, accumulate_turn

        max_ev = RelationshipTurnEvidence(
            user_sent_message=True,
            user_asked_question=True,
            user_answered_question=True,
            user_shared_information=True,
            user_continued_topic=True,
            user_referenced_previous_context=True,
            assistant_asked_question=True,
            assistant_shared_information=True,
            session_returned=True,
            open_loop_continued=True,
            open_loop_resolved=True,
        )
        anchors = accumulate_turn(None, max_ev, T0)
        assert anchors.bands.get("familiarity") != "established"
        assert anchors.bands.get("engagement") != "deep"
        assert anchors.bands.get("continuity") != "rich"
        assert anchors.bands.get("reciprocity") != "high"

    def test_single_continuity_event_cannot_make_rich(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        anchors = accumulate_turn(
            neutral_anchors(T0),
            _ev(
                session_returned=True,
                open_loop_continued=True,
                open_loop_resolved=True,
                user_referenced_previous_context=True,
                user_continued_topic=True,
            ),
            T0,
        )
        assert anchors.bands.get("continuity") == "sparse"

    def test_returning_session_gets_no_bonus_jump(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # A RETURNING turn from cold start lands on entry levels only:
        # the return contributes counters/kinds, never an arbitrary jump.
        anchors = accumulate_turn(neutral_anchors(T0), _ev(session_returned=True), T0)
        assert anchors.bands.get("familiarity") == "new"
        assert anchors.bands.get("familiarity") != "familiar"
        assert anchors.bands.get("continuity") == "sparse"
        assert anchors.transitions["familiarity"]["to"] == "new"


# ---------------------------------------------------------------------------
# Neutral behavior
# ---------------------------------------------------------------------------


class TestNeutralBehavior:
    def test_neutral_turn_after_rich_turns_changes_no_signal_band(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        anchors = neutral_anchors(T0)
        moment = T0
        for _ in range(4):
            moment = moment + timedelta(hours=1)
            anchors = accumulate_turn(
                anchors, _ev(user_shared_information=True, user_asked_question=True), moment
            )
        before_counts = (
            anchors.user_question_count,
            anchors.user_shared_info_count,
            anchors.user_topic_continuation_count,
        )
        moment = moment + timedelta(hours=1)
        after = accumulate_turn(anchors, _ev(), moment)
        # The neutral 5th turn carries no signals: streak resets, signal
        # counters freeze. Volume gates still apply (5th message crosses the
        # message-count thresholds), while familiarity still needs span/return
        # and continuity still needs anchor kinds.
        assert after.positive_streak == 0
        assert (
            after.user_question_count,
            after.user_shared_info_count,
            after.user_topic_continuation_count,
        ) == before_counts
        assert after.interaction_count == 5
        assert after.observation_count == 5
        assert after.bands.get("familiarity") == "new"
        assert after.bands.get("continuity") == "sparse"
        # Volume-driven promotions on the 5th message are allowed and exact:
        assert after.bands.get("engagement") == "steady"
        assert after.bands.get("reciprocity") == "high"

    def test_twenty_neutral_turns_stay_at_entry(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        anchors = neutral_anchors(T0)
        moment = T0
        for _ in range(20):
            moment = moment + timedelta(minutes=5)
            anchors = accumulate_turn(anchors, _ev(), moment)
        assert _bands_at(anchors, moment)[:4] == ("new", "low", "unknown", "sparse")

    def test_bare_message_resets_positive_streak(self):
        from commerce.relationship_trajectory import accumulate_turn

        anchors, moment = _accumulate_rich(3)
        assert anchors.positive_streak == 3
        moment = moment + timedelta(hours=25)
        anchors = accumulate_turn(anchors, _ev(), moment)
        assert anchors.positive_streak == 0


# ---------------------------------------------------------------------------
# Replay / idempotency at the transition level
# ---------------------------------------------------------------------------


class TestTransitionReplay:
    @pytest.mark.asyncio
    async def test_same_generation_twice_no_double_transition(self):
        from commerce.relationship_evidence import accumulate_relationship_turn_idempotent
        from commerce.relationship_trajectory import RelationshipTurnEvidence

        store: dict = {}
        evidence = RelationshipTurnEvidence(
            user_sent_message=True,
            user_asked_question=True,
            user_shared_information=True,
        )
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=_make_fake_mutate(store)),
        ):
            first, did_first = await accumulate_relationship_turn_idempotent(
                user_id=1, creator_id=7, generation_id="gen-A", evidence=evidence, now=T0
            )
            second, did_second = await accumulate_relationship_turn_idempotent(
                user_id=1, creator_id=7, generation_id="gen-A", evidence=evidence, now=T0
            )
        assert (did_first, did_second) == (True, False)
        assert first is not None and second is not None
        assert second.interaction_count == 1
        assert second.transitions == first.transitions

    @pytest.mark.asyncio
    async def test_same_generation_three_times(self):
        from commerce.relationship_evidence import accumulate_relationship_turn_idempotent
        from commerce.relationship_trajectory import RelationshipTurnEvidence

        store: dict = {}
        evidence = RelationshipTurnEvidence(user_sent_message=True, user_shared_information=True)
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=_make_fake_mutate(store)),
        ):
            results = [
                await accumulate_relationship_turn_idempotent(
                    user_id=1, creator_id=7, generation_id="gen-A", evidence=evidence, now=T0
                )
                for _ in range(3)
            ]
        assert [did for _, did in results] == [True, False, False]
        assert results[-1][0] is not None
        assert results[-1][0].interaction_count == 1
        assert results[-1][0].positive_streak == results[0][0].positive_streak

    @pytest.mark.asyncio
    async def test_a_b_a_generation_replay(self):
        from commerce.relationship_evidence import accumulate_relationship_turn_idempotent
        from commerce.relationship_trajectory import RelationshipTurnEvidence

        store: dict = {}
        ev_a = RelationshipTurnEvidence(user_sent_message=True, user_asked_question=True)
        ev_b = RelationshipTurnEvidence(user_sent_message=True, user_shared_information=True)
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=_make_fake_mutate(store)),
        ):
            _, did_a1 = await accumulate_relationship_turn_idempotent(
                user_id=1, creator_id=7, generation_id="gen-A", evidence=ev_a, now=T0
            )
            b1, did_b1 = await accumulate_relationship_turn_idempotent(
                user_id=1, creator_id=7, generation_id="gen-B", evidence=ev_b, now=T0
            )
            a2, did_a2 = await accumulate_relationship_turn_idempotent(
                user_id=1, creator_id=7, generation_id="gen-A", evidence=ev_a, now=T0
            )
        assert (did_a1, did_b1, did_a2) == (True, True, False)
        assert a2 is not None and b1 is not None
        # The replay changed nothing: same counters, same transitions as post-B.
        assert a2.interaction_count == 2
        assert a2.user_question_count == 1
        assert a2.user_shared_info_count == 1
        assert a2.transitions == b1.transitions

    @pytest.mark.asyncio
    async def test_identical_text_different_generations_both_accumulate(self):
        from commerce.relationship_evidence import accumulate_relationship_turn_idempotent
        from commerce.relationship_trajectory import RelationshipTurnEvidence

        store: dict = {}
        evidence = RelationshipTurnEvidence(user_sent_message=True, user_asked_question=True)
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=_make_fake_mutate(store)),
        ):
            _, did_x = await accumulate_relationship_turn_idempotent(
                user_id=1, creator_id=7, generation_id="gen-X", evidence=evidence, now=T0
            )
            anchors, did_y = await accumulate_relationship_turn_idempotent(
                user_id=1, creator_id=7, generation_id="gen-Y", evidence=evidence, now=T0
            )
        # Idempotency keys on generation, never on content.
        assert (did_x, did_y) == (True, True)
        assert anchors is not None
        assert anchors.interaction_count == 2
        assert anchors.user_question_count == 2

    @pytest.mark.asyncio
    async def test_persistence_failure_then_retry_success(self):
        from commerce.relationship_evidence import accumulate_relationship_turn_idempotent
        from commerce.relationship_trajectory import RelationshipTurnEvidence

        store: dict = {}
        calls = {"n": 0}

        async def _fail_once_then_ok(user_id, mutator):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("db down")
            return await _make_fake_mutate(store)(user_id, mutator)

        evidence = RelationshipTurnEvidence(user_sent_message=True, user_shared_information=True)
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=_fail_once_then_ok),
        ):
            failed, did_failed = await accumulate_relationship_turn_idempotent(
                user_id=1, creator_id=7, generation_id="gen-R", evidence=evidence, now=T0
            )
            retried, did_retried = await accumulate_relationship_turn_idempotent(
                user_id=1, creator_id=7, generation_id="gen-R", evidence=evidence, now=T0
            )
        # Fail-open first attempt persisted nothing; the retry with the same
        # generation accumulates exactly once.
        assert (failed, did_failed) == (None, False)
        assert did_retried is True
        assert retried is not None
        assert retried.interaction_count == 1
        assert retried.user_shared_info_count == 1

    @pytest.mark.asyncio
    async def test_empty_generation_id_never_accumulates(self):
        from commerce.relationship_evidence import accumulate_relationship_turn_idempotent
        from commerce.relationship_trajectory import RelationshipTurnEvidence

        store: dict = {}
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=_make_fake_mutate(store)),
        ):
            anchors, did = await accumulate_relationship_turn_idempotent(
                user_id=1,
                creator_id=7,
                generation_id="",
                evidence=RelationshipTurnEvidence(user_sent_message=True),
                now=T0,
            )
        assert (anchors, did) == (None, False)
        assert store == {}


# ---------------------------------------------------------------------------
# Creator isolation, interleaved with transitions
# ---------------------------------------------------------------------------


class TestCreatorIsolationInterleaved:
    @pytest.mark.asyncio
    async def test_interleaved_generations_and_replays_stay_isolated(self):
        from commerce.relationship_evidence import accumulate_relationship_turn_idempotent
        from commerce.relationship_trajectory import (
            RELATIONSHIP_TRAJECTORY_KEY,
            RelationshipTurnEvidence,
            get_relationship_anchors,
        )

        store: dict = {}
        ev_a = RelationshipTurnEvidence(
            user_sent_message=True,
            user_asked_question=True,
            user_shared_information=True,
        )
        ev_b = RelationshipTurnEvidence(user_sent_message=True, session_returned=True)
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=_make_fake_mutate(store)),
        ):
            a1, did_a1 = await accumulate_relationship_turn_idempotent(
                user_id=1, creator_id=7, generation_id="gen-A1", evidence=ev_a, now=T0
            )
            b1, did_b1 = await accumulate_relationship_turn_idempotent(
                user_id=1, creator_id=9, generation_id="gen-B1", evidence=ev_b, now=T0
            )
            a1r, did_a1r = await accumulate_relationship_turn_idempotent(
                user_id=1, creator_id=7, generation_id="gen-A1", evidence=ev_a, now=T0
            )
            b1r, did_b1r = await accumulate_relationship_turn_idempotent(
                user_id=1, creator_id=9, generation_id="gen-B1", evidence=ev_b, now=T0
            )
        assert (did_a1, did_b1, did_a1r, did_b1r) == (True, True, False, False)
        anchors_a = get_relationship_anchors(store[1], 7)
        anchors_b = get_relationship_anchors(store[1], 9)
        # No cross-creator counters, bands, markers, or transition records.
        assert anchors_a.interaction_count == 1
        assert anchors_b.interaction_count == 1
        assert anchors_a.user_question_count == 1
        assert anchors_b.user_question_count == 0
        assert anchors_b.session_return_count == 1
        assert anchors_a.session_return_count == 0
        assert a1r is not None and b1r is not None
        assert a1r.transitions == (a1.transitions if a1 else {})
        assert b1r.transitions == (b1.transitions if b1 else {})
        markers_a = store[1][RELATIONSHIP_TRAJECTORY_KEY]["7"]["processed_generation_ids"]
        markers_b = store[1][RELATIONSHIP_TRAJECTORY_KEY]["9"]["processed_generation_ids"]
        assert markers_a == ["gen-A1"]
        assert markers_b == ["gen-B1"]

    @pytest.mark.asyncio
    async def test_user_isolation_for_transitions(self):
        from commerce.relationship_evidence import accumulate_relationship_turn_idempotent
        from commerce.relationship_trajectory import (
            RelationshipTurnEvidence,
            get_relationship_anchors,
        )

        store: dict = {}
        evidence = RelationshipTurnEvidence(user_sent_message=True, user_shared_information=True)
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=_make_fake_mutate(store)),
        ):
            await accumulate_relationship_turn_idempotent(
                user_id=1, creator_id=7, generation_id="gen-U", evidence=evidence, now=T0
            )
            await accumulate_relationship_turn_idempotent(
                user_id=2, creator_id=7, generation_id="gen-U", evidence=evidence, now=T0
            )
        assert get_relationship_anchors(store[1], 7).user_shared_info_count == 1
        assert get_relationship_anchors(store[2], 7).user_shared_info_count == 1


# ---------------------------------------------------------------------------
# Persistence contract (zero changes)
# ---------------------------------------------------------------------------


class TestPersistenceContractUnchanged:
    def test_schema_version_stays_v1(self):
        from commerce.relationship_trajectory import SCHEMA_VERSION, anchors_to_dict

        anchors, _ = _accumulate_rich(3)
        assert SCHEMA_VERSION == 1
        assert anchors_to_dict(anchors)["schema_version"] == 1

    def test_serialized_field_set_unchanged(self):
        from commerce.relationship_trajectory import anchors_to_dict

        anchors, _ = _accumulate_rich(6)
        assert set(anchors_to_dict(anchors).keys()) == {
            "schema_version",
            "first_seen_at",
            "last_seen_at",
            "last_provenance",
            "last_source",
            "interaction_count",
            "user_message_count",
            "user_question_count",
            "user_answer_count",
            "user_shared_info_count",
            "user_topic_continuation_count",
            "user_context_reference_count",
            "assistant_question_count",
            "assistant_shared_info_count",
            "session_return_count",
            "open_loop_observed_count",
            "open_loop_resolved_count",
            "corroborated_engagement_observations",
            "positive_streak",
            "observation_count",
            "bands",
            "transitions",
        }

    def test_marker_key_lives_outside_trajectory_serialization(self):
        # The Phase 2 processed-marker is added by the evidence layer, never
        # by trajectory serialization itself.
        from commerce.relationship_trajectory import anchors_to_dict

        anchors, _ = _accumulate_rich(2)
        assert "processed_generation_ids" not in anchors_to_dict(anchors)

    def test_reload_preserves_bands_and_transitions(self):
        from commerce.relationship_trajectory import (
            anchors_from_dict,
            anchors_to_dict,
            serialize_relationship_anchors,
        )

        anchors, _ = _accumulate_rich(6)
        reloaded = anchors_from_dict(anchors_to_dict(anchors))
        assert reloaded.bands == anchors.bands
        assert reloaded.transitions == anchors.transitions
        assert reloaded.interaction_count == anchors.interaction_count
        assert reloaded.positive_streak == anchors.positive_streak
        assert serialize_relationship_anchors(reloaded) == serialize_relationship_anchors(anchors)

    def test_no_raw_text_in_transitions_or_block(self):
        import json

        from commerce.relationship_trajectory import anchors_to_dict

        anchors, _ = _accumulate_rich(4)
        blob = json.dumps(anchors_to_dict(anchors), sort_keys=True)
        for token in ("reason",):
            assert token in blob  # reason keys exist...
        # ...but every reason uses only the bounded evidence-field vocabulary.
        import re

        for entry in anchors_to_dict(anchors)["transitions"].values():
            assert re.fullmatch(r"accumulated:(turn|[a-z_]+(\+[a-z_]+)*)", entry["reason"]), entry


# ---------------------------------------------------------------------------
# Time boundaries (existing semantics only)
# ---------------------------------------------------------------------------


class TestTimeBoundaries:
    def test_naive_now_matches_aware_utc(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        naive = datetime(2026, 1, 1)  # noqa: DTZ001 - naive input is the point: coerce to UTC
        # naive is coerced to UTC, matching T0
        assert accumulate_turn(neutral_anchors(T0), _ev(), naive) == accumulate_turn(
            neutral_anchors(T0), _ev(), T0
        )

    def test_same_now_is_deterministic(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        moment = T0 + timedelta(days=3, hours=4, minutes=5)
        first = accumulate_turn(neutral_anchors(T0), _ev(user_shared_information=True), moment)
        second = accumulate_turn(neutral_anchors(T0), _ev(user_shared_information=True), moment)
        assert first == second

    def test_dormancy_boundary_30d(self):
        from commerce.relationship_trajectory import derive_relationship_snapshot

        anchors, moment = _accumulate_rich(4)
        last_seen = moment
        before = derive_relationship_snapshot(
            anchors, None, last_seen + timedelta(hours=29 * 24 + 23)
        )
        assert before.decay_applied is False
        assert before.trend.value != "declining"
        at = derive_relationship_snapshot(anchors, None, last_seen + timedelta(hours=30 * 24))
        assert at.decay_applied is True
        assert at.trend.value == "declining"
        after = derive_relationship_snapshot(
            anchors, None, last_seen + timedelta(hours=30 * 24 + 1)
        )
        assert after.decay_applied is True

    def test_no_second_session_definition_in_trajectory(self):
        import inspect

        import commerce.relationship_trajectory as rt

        source = inspect.getsource(rt)
        # Session/return authority lives in conversation_state + Phase 2;
        # the trajectory module must not redefine it.
        assert "derive_lifecycle" not in source
        assert "ConversationLifecycle" not in source
        assert "RETURNING" not in source
        assert "48" not in source


# ---------------------------------------------------------------------------
# Transition reasons
# ---------------------------------------------------------------------------


class TestTransitionReasons:
    def test_same_inputs_same_reasons(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        first = accumulate_turn(neutral_anchors(T0), _ev(user_shared_information=True), T0)
        second = accumulate_turn(neutral_anchors(T0), _ev(user_shared_information=True), T0)
        assert first.transitions == second.transitions

    def test_reason_timestamp_matches_accumulation_moment(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        moment = T0 + timedelta(days=2, hours=3)
        anchors = accumulate_turn(neutral_anchors(T0), _ev(user_shared_information=True), moment)
        for entry in anchors.transitions.values():
            assert entry["at"] == moment.astimezone(UTC).isoformat()

    def test_reason_vocabulary_is_bounded_field_names(self):
        import re

        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        anchors = accumulate_turn(
            neutral_anchors(T0),
            _ev(
                user_asked_question=True,
                user_shared_information=True,
                session_returned=True,
            ),
            T0,
        )
        for entry in anchors.transitions.values():
            assert set(entry.keys()) == {"from", "to", "at", "reason"}
            assert re.fullmatch(r"accumulated:(turn|[a-z_]+(\+[a-z_]+)*)", entry["reason"]), entry

    def test_reasons_carry_no_message_content(self):
        import json

        from commerce.relationship_trajectory import anchors_to_dict

        anchors, _ = _accumulate_rich(5)
        blob = json.dumps(anchors_to_dict(anchors))
        # Field-name tokens only; sentence-like content can never appear
        # because reasons are built from evidence field names, not text.
        assert ". " not in blob
        assert "?" not in blob


# ---------------------------------------------------------------------------
# Explicit invariants A–I
# ---------------------------------------------------------------------------


class TestInvariants:
    def test_invariant_a_one_generation_at_most_one_accumulation(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        # Pure-function level: one call folds exactly one evidence object
        # (each boolean contributes at most +1 to its counter).
        anchors = accumulate_turn(
            neutral_anchors(T0),
            _ev(
                user_asked_question=True,
                user_shared_information=True,
            ),
            T0,
        )
        assert anchors.interaction_count == 1
        assert anchors.observation_count == 1
        assert anchors.user_question_count == 1
        assert anchors.user_shared_info_count == 1

    def test_invariant_b_one_step_per_dimension_per_accumulation(self):
        from commerce.relationship_trajectory import RelationshipAnchors, accumulate_turn

        order = {
            "familiarity": ["unknown", "new", "familiar", "established"],
            "engagement": ["unknown", "low", "steady", "deep"],
            "reciprocity": ["unknown", "low", "balanced", "high"],
            "continuity": ["unknown", "sparse", "anchored", "rich"],
        }
        base = RelationshipAnchors(
            interaction_count=50,
            user_message_count=50,
            user_question_count=25,
            user_answer_count=25,
            user_shared_info_count=25,
            user_topic_continuation_count=25,
            user_context_reference_count=25,
            assistant_question_count=25,
            assistant_shared_info_count=25,
            session_return_count=10,
            open_loop_observed_count=10,
            open_loop_resolved_count=10,
            first_seen_at=T0.isoformat(),
            last_seen_at=(T0 + timedelta(days=30)).isoformat(),
            bands={k: v[0] for k, v in order.items()},
            observation_count=50,
        )
        updated = accumulate_turn(base, _ev(), T0 + timedelta(days=30, hours=1))
        for dim, levels in order.items():
            step = levels.index(updated.bands[dim]) - levels.index(levels[0])
            assert step <= 1, f"{dim} jumped more than one step"

    def test_invariant_c_llm_cannot_change_bands(self):
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        plain = accumulate_turn(neutral_anchors(T0), _ev(user_shared_information=True), T0)
        with_llm = accumulate_turn(
            neutral_anchors(T0),
            _ev(user_shared_information=True, llm_relationship_engagement=1.0),
            T0,
        )
        assert with_llm.bands == plain.bands
        assert with_llm.transitions == plain.transitions

    def test_invariant_d_trend_never_persisted_as_band(self):
        from commerce.relationship_trajectory import anchors_to_dict

        anchors, _ = _accumulate_rich(8)
        assert "trend" not in anchors.bands
        assert "trend" not in anchors_to_dict(anchors)["bands"]

    def test_invariant_e_decay_never_mutates_anchors(self):
        from commerce.relationship_trajectory import (
            derive_relationship_snapshot,
            serialize_relationship_anchors,
        )

        anchors, moment = _accumulate_rich(6)
        before = serialize_relationship_anchors(anchors)
        for days in (31, 90, 365):
            derive_relationship_snapshot(anchors, None, moment + timedelta(days=days))
        assert serialize_relationship_anchors(anchors) == before

    def test_invariant_f_determinism(self):
        from commerce.relationship_trajectory import (
            accumulate_turn,
            derive_relationship_snapshot,
            neutral_anchors,
            serialize_relationship_anchors,
        )

        moment = T0 + timedelta(days=2)
        runs = [
            accumulate_turn(neutral_anchors(T0), _ev(user_shared_information=True), moment)
            for _ in range(3)
        ]
        assert runs[0] == runs[1] == runs[2]
        assert serialize_relationship_anchors(runs[0]) == serialize_relationship_anchors(runs[1])
        assert derive_relationship_snapshot(runs[0], None, moment) == derive_relationship_snapshot(
            runs[1], None, moment
        )

    def test_invariant_g_creator_isolation(self):
        from commerce.relationship_trajectory import (
            RELATIONSHIP_TRAJECTORY_KEY,
            anchors_to_dict,
            get_relationship_anchors,
        )

        anchors, _ = _accumulate_rich(10)
        profile = {RELATIONSHIP_TRAJECTORY_KEY: {"7": anchors_to_dict(anchors)}}
        assert get_relationship_anchors(profile, 7).interaction_count == 10
        assert get_relationship_anchors(profile, 9).interaction_count == 0
        assert get_relationship_anchors(profile, 9).bands == {}

    def test_invariant_h_no_raw_text_persisted(self):
        import inspect

        from commerce.relationship_trajectory import accumulate_turn

        params = list(inspect.signature(accumulate_turn).parameters)
        # No text parameter exists on the accumulation boundary at all.
        assert params == ["anchors", "evidence", "now", "source"]

    def test_invariant_i_independent_from_commerce_lifecycle(self):
        from commerce.relationship import RelationshipState

        # Commerce lifecycle vocabulary is frozen; trajectory bands use a
        # disjoint vocabulary and neither module imports the other.
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
        import inspect

        import commerce.relationship_trajectory as rt

        source = inspect.getsource(rt)
        assert "from commerce" not in source
        assert "import commerce" not in source
        assert "relationship_score" not in source


# ---------------------------------------------------------------------------
# Explicit policy seam (behavior-preserving extraction)
# ---------------------------------------------------------------------------


class TestApplyTransitionPolicySeam:
    def test_policy_output_matches_accumulate_commit(self):
        from commerce.relationship_trajectory import (
            accumulate_turn,
            apply_transition_policy,
            neutral_anchors,
        )

        base = neutral_anchors(T0)
        evidence = _ev(user_shared_information=True, user_asked_question=True)
        committed = accumulate_turn(base, evidence, T0)
        # Re-applying the policy to the committed result is a fixed point:
        # counters are final, so candidates, clamp, and records agree.
        bands, transitions = apply_transition_policy(base, committed, evidence, T0)
        assert bands == committed.bands
        assert transitions == committed.transitions

    def test_policy_is_pure_and_deterministic(self):
        from commerce.relationship_trajectory import apply_transition_policy, neutral_anchors

        base = neutral_anchors(T0)
        evidence = _ev(session_returned=True)
        first = apply_transition_policy(base, base, evidence, T0)
        second = apply_transition_policy(base, base, evidence, T0)
        assert first == second


# ---------------------------------------------------------------------------
# Roadmap sketch vocabulary must not leak in
# ---------------------------------------------------------------------------


class TestNoRoadmapSketchVocabulary:
    def test_implemented_contract_supersedes_stale_sketches(self):
        import inspect

        import commerce.relationship_trajectory as rt

        source = inspect.getsource(rt)
        for banned in (
            "rapport",
            "COOLING",
            "RECOVERING",
            "UNCERTAIN",
            "USER_INITIATED_INTIMACY",
            "BOUNDARY_EVENT",
            "relationship_score",
            "affinity",
            "trust_score",
        ):
            assert banned not in source, f"stale sketch vocabulary leaked: {banned}"
