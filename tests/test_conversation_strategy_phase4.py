"""Phase 4 conversational strategy tests (IMPLEMENT → VERIFY).

Pins the deterministic selector in ``commerce.conversation_strategy``:

* same inputs → identical strategy (pure, no I/O, no randomness)
* abstain (None) on missing / cold-start / unusable inputs
* current-turn requirements outrank historical bands
* unanswered-question protection and question fatigue survive
* callbacks require real prior-context evidence (never bands alone)
* exploration requires budget + persona allowance (else downgrade)
* low reciprocity never produces question spam
* trend alone never triggers RECOVER or any durable change
* maximal bands never produce teasing / intimate / commerce moves
* commerce inputs do not exist on the selector contract
* persona vetoes win over relationship-driven moves
* creator-scoped reads only; no writes anywhere; render fail-open

Conventions follow the Phase 1–3 suites: ``pytestmark =
[pytest.mark.unit]``, real trajectory / state / persona / contract
types (no hypothetical behavior), deterministic unit tests only.
"""

import inspect
import pathlib

import pytest

pytestmark = [pytest.mark.unit]

from commerce.conversation_strategy import (  # noqa: E402
    TurnEvidenceSummary,
    _ALLOWED_HINTS,
    _MOVES,
    _REASON_CODES,
    detect_farewell,
    render_conversation_strategy,
    select_conversational_strategy,
    select_for_turn,
)
from commerce.relationship_trajectory import (  # noqa: E402
    RELATIONSHIP_TRAJECTORY_KEY,
    ContinuityBand,
    EngagementBand,
    FamiliarityBand,
    ReciprocityBand,
    RelationshipAnchors,
    RelationshipSnapshot,
    TrendDirection,
    anchors_to_dict,
    neutral_snapshot,
)
from core.conversation_state import ConversationState  # noqa: E402
from core.conversation_contract import ConversationContract  # noqa: E402
from commerce.persona_behavior import PersonaBehaviorState  # noqa: E402


# ---------------------------------------------------------------------------
# Builders over the actual contracts
# ---------------------------------------------------------------------------


def _snap(fam="unknown", eng="unknown", rec="unknown", con="unknown", trend="unknown"):
    return RelationshipSnapshot(
        familiarity=FamiliarityBand(fam),
        engagement=EngagementBand(eng),
        reciprocity=ReciprocityBand(rec),
        continuity=ContinuityBand(con),
        trend=TrendDirection(trend),
    )


def _max_snap():
    return _snap("established", "deep", "high", "rich", "growing")


def _mid_snap():
    return _snap("familiar", "steady", "balanced", "anchored", "stable")


def _new_snap():
    return _snap("new", "low", "low", "sparse", "stable")


def _state(**overrides):
    base = {
        "lifecycle": "established",
        "identity_already_established": True,
        "current_topic": None,
        "recent_topics": (),
        "open_threads": (),
        "last_question": None,
        "last_question_answered": True,
        "consecutive_questions": 0,
        "tone": "warm",
        "last_user_fact": None,
        "questions_in_last_3": 0,
    }
    base.update(overrides)
    return ConversationState(**base)


def _persona(**overrides):
    base = {
        "emotional_state": "warm",
        "confidence": "LOW",
        "conversation_mode": "react",
        "question_allowed": True,
        "question_policy": "ONE_NATURAL_QUESTION",
        "disagreement_available": False,
        "teasing_allowed": False,
        "sincerity_required": False,
        "verbosity_target": "short_medium",
        "emoji_policy": "occasional",
        "lowercase_policy": "neutral",
        "naturalness_mode": "normal",
        "persona_version": None,
        "creator_id": None,
        "generation_id": None,
    }
    base.update(overrides)
    return PersonaBehaviorState(**base)


def _contract(**overrides):
    base = {"answer_required": False}
    base.update(overrides)
    return ConversationContract(**base)


def _select(snapshot, state=None, contract=None, persona=None, turn=None):
    return select_conversational_strategy(
        snapshot=snapshot,
        conversation_state=state if state is not None else _state(),
        contract=contract if contract is not None else _contract(),
        persona=persona if persona is not None else _persona(),
        turn=turn if turn is not None else TurnEvidenceSummary(),
    )


# ---------------------------------------------------------------------------
# A. Determinism
# ---------------------------------------------------------------------------


class TestADeterminism:
    def test_same_inputs_identical_strategy(self):
        snap = _mid_snap()
        state = _state(current_topic="movies", open_threads=("movies",))
        turn = TurnEvidenceSummary(user_continued_topic=True)
        first = _select(snap, state, turn=turn)
        second = _select(snap, state, turn=turn)
        assert first is not None and first == second

    def test_render_deterministic(self):
        strategy = _select(_mid_snap())
        assert strategy is not None
        assert render_conversation_strategy(strategy) == render_conversation_strategy(strategy)


# ---------------------------------------------------------------------------
# B. Abstention
# ---------------------------------------------------------------------------


class TestBAbstention:
    def test_missing_snapshot_abstains(self):
        assert _select(None) is None

    def test_cold_start_abstains(self):
        assert _select(neutral_snapshot()) is None
        assert _select(_snap()) is None

    def test_garbage_inputs_abstain(self):
        assert (
            select_conversational_strategy(
                snapshot="nonsense",
                conversation_state=42,
                contract=[],
                persona="x",
                turn="y",
            )
            is None
        )


# ---------------------------------------------------------------------------
# C. Current-question priority
# ---------------------------------------------------------------------------


class TestCCurrentQuestionPriority:
    def test_max_bands_question_still_acknowledges(self):
        strategy = _select(_max_snap(), turn=TurnEvidenceSummary(user_asked_question=True))
        assert strategy is not None
        assert strategy.move == "ACKNOWLEDGE"
        assert strategy.realization_hint == "answer"
        assert strategy.question_policy == "NO_QUESTION"
        assert "CURRENT_QUESTION" in strategy.reason_codes
        assert strategy.confidence == "HIGH"

    def test_contract_answer_required_counts_as_question(self):
        strategy = _select(_mid_snap(), contract=_contract(answer_required=True))
        assert strategy is not None
        assert strategy.move == "ACKNOWLEDGE"
        assert strategy.realization_hint == "answer"


# ---------------------------------------------------------------------------
# D. Unanswered-question handling
# ---------------------------------------------------------------------------


def _explore_setup(**state_overrides):
    base = {
        "last_question": "what did you do today?",
        "last_question_answered": True,
        "consecutive_questions": 0,
        "questions_in_last_3": 0,
    }
    base.update(state_overrides)
    return _state(**base)


class TestDUnansweredQuestion:
    def test_answered_question_explores_with_budget(self):
        strategy = _select(
            _mid_snap(),
            _explore_setup(),
            turn=TurnEvidenceSummary(user_answered_question=True),
        )
        assert strategy is not None
        assert strategy.move == "EXPLORE"
        assert strategy.realization_hint == "explore"
        assert strategy.question_policy == "ONE_NATURAL_QUESTION"

    def test_unanswered_question_blocks_explore(self):
        strategy = _select(
            _mid_snap(),
            _explore_setup(last_question_answered=False),
            turn=TurnEvidenceSummary(user_answered_question=False),
        )
        assert strategy is not None
        assert strategy.move != "EXPLORE"
        assert strategy.question_policy == "NO_QUESTION"


# ---------------------------------------------------------------------------
# E. Question fatigue
# ---------------------------------------------------------------------------


class TestEQuestionFatigue:
    def test_consecutive_limit_blocks_explore(self):
        strategy = _select(
            _mid_snap(),
            _explore_setup(consecutive_questions=1),
            turn=TurnEvidenceSummary(user_answered_question=True),
        )
        assert strategy is not None
        assert strategy.move != "EXPLORE"
        assert strategy.question_policy == "NO_QUESTION"
        # Reciprocity evidence without a question budget reciprocates
        # without interrogating.
        assert strategy.move == "SHARE"

    def test_per_three_turn_limit_blocks_explore(self):
        strategy = _select(
            _mid_snap(),
            _explore_setup(questions_in_last_3=1),
            turn=TurnEvidenceSummary(user_answered_question=True),
        )
        assert strategy is not None
        assert strategy.move == "SHARE"
        assert strategy.question_policy == "NO_QUESTION"
        assert "QUESTION_BUDGET" in strategy.reason_codes


# ---------------------------------------------------------------------------
# F. Topic continuation
# ---------------------------------------------------------------------------


class TestFTopicContinuation:
    def test_continued_topic_continues_without_bands(self):
        strategy = _select(
            _new_snap(),
            _state(current_topic="movies", recent_topics=("movies",)),
            turn=TurnEvidenceSummary(user_continued_topic=True),
        )
        assert strategy is not None
        assert strategy.move == "CONTINUE"
        assert strategy.realization_hint == "react"
        assert strategy.question_policy == "NO_QUESTION"
        assert "CURRENT_TOPIC" in strategy.reason_codes
        assert "CONTINUED_TOPIC" in strategy.reason_codes


# ---------------------------------------------------------------------------
# G. Callback
# ---------------------------------------------------------------------------


class TestGCallback:
    def test_rich_band_alone_never_callbacks(self):
        strategy = _select(_max_snap())
        assert strategy is not None
        assert strategy.move != "CALLBACK"

    def test_callback_needs_threads_and_evidence(self):
        strategy = _select(
            _max_snap(),
            _state(current_topic="movies", open_threads=("movies",)),
            turn=TurnEvidenceSummary(user_referenced_previous_context=True),
        )
        assert strategy is not None
        assert strategy.move == "CALLBACK"
        assert strategy.realization_hint == "callback"
        assert strategy.question_policy == "ONE_NATURAL_QUESTION"
        assert "PRIOR_CONTEXT" in strategy.reason_codes
        assert "OPEN_THREADS" in strategy.reason_codes

    def test_continuity_without_evidence_never_callbacks(self):
        strategy = _select(
            _max_snap(),
            _state(current_topic="movies", open_threads=("movies",)),
        )
        assert strategy is not None
        assert strategy.move != "CALLBACK"

    def test_sparse_continuity_never_callbacks(self):
        strategy = _select(
            _snap("established", "deep", "high", "sparse", "stable"),
            _state(current_topic="movies", open_threads=("movies",)),
            turn=TurnEvidenceSummary(user_referenced_previous_context=True),
        )
        assert strategy is not None
        assert strategy.move != "CALLBACK"


# ---------------------------------------------------------------------------
# H. Exploration
# ---------------------------------------------------------------------------


class TestHExploration:
    def test_low_engagement_never_explores(self):
        strategy = _select(
            _snap("familiar", "low", "balanced", "anchored", "stable"),
            _explore_setup(),
            turn=TurnEvidenceSummary(user_answered_question=True),
        )
        assert strategy is not None
        assert strategy.move != "EXPLORE"
        assert strategy.question_policy == "NO_QUESTION"


# ---------------------------------------------------------------------------
# I. Reciprocity
# ---------------------------------------------------------------------------


class TestIReciprocity:
    def test_low_reciprocity_no_question_spam(self):
        strategy = _select(
            _snap("familiar", "steady", "low", "anchored", "stable"),
            _explore_setup(),
            turn=TurnEvidenceSummary(user_answered_question=True),
        )
        assert strategy is not None
        assert strategy.move not in ("EXPLORE", "SHARE")
        assert strategy.question_policy == "NO_QUESTION"


# ---------------------------------------------------------------------------
# J. Continuity gate (sparse covered in G; anchored path here)
# ---------------------------------------------------------------------------


class TestJContinuity:
    def test_anchored_supports_callback_with_evidence(self):
        strategy = _select(
            _mid_snap(),
            _state(current_topic="movies", open_threads=("movies",)),
            turn=TurnEvidenceSummary(user_referenced_previous_context=True),
        )
        assert strategy is not None
        assert strategy.move == "CALLBACK"
        assert "CONTINUITY_ANCHORED" in strategy.reason_codes


# ---------------------------------------------------------------------------
# K. Trend
# ---------------------------------------------------------------------------


class TestKTrend:
    def test_declining_trend_alone_never_recovers(self):
        strategy = _select(_snap("familiar", "steady", "balanced", "anchored", "declining"))
        assert strategy is not None
        assert strategy.move != "RECOVER"

    def test_growing_trend_alone_changes_nothing_structural(self):
        strategy = _select(_snap("familiar", "steady", "balanced", "anchored", "growing"))
        assert strategy is not None
        assert strategy.move in ("CONTINUE", "ACKNOWLEDGE")
        assert strategy.question_policy == "NO_QUESTION"


# ---------------------------------------------------------------------------
# L. Relationship max-state safety (STATE is not escalation)
# ---------------------------------------------------------------------------


_FORBIDDEN_CONCEPTS = (
    "tease",
    "sexual",
    "sexy",
    "intim",
    "erotic",
    "permission",
    "content",
    "offer",
    "purchase",
    "price",
    "product",
)


class TestLMaxStateSafety:
    def test_max_bands_plain_turn_stays_plain(self):
        strategy = _select(_max_snap())
        assert strategy is not None
        assert strategy.move in ("CONTINUE", "ACKNOWLEDGE")
        assert strategy.realization_hint in ("react", "answer", "share", "explore", "callback", "close")
        assert strategy.realization_hint != "tease"
        assert strategy.question_policy == "NO_QUESTION"

    def test_max_bands_never_emit_forbidden_concepts(self):
        turns = [
            TurnEvidenceSummary(),
            TurnEvidenceSummary(user_asked_question=True),
            TurnEvidenceSummary(user_answered_question=True),
            TurnEvidenceSummary(user_continued_topic=True),
            TurnEvidenceSummary(user_referenced_previous_context=True),
            TurnEvidenceSummary(farewell=True),
        ]
        states = [
            _state(),
            _explore_setup(),
            _state(current_topic="movies", open_threads=("movies",)),
        ]
        for turn in turns:
            for state in states:
                strategy = _select(_max_snap(), state, turn=turn)
                assert strategy is not None
                blob = " ".join(
                    [
                        strategy.move,
                        strategy.realization_hint,
                        strategy.question_policy,
                        ",".join(strategy.reason_codes),
                        strategy.confidence,
                    ]
                ).lower()
                for concept in _FORBIDDEN_CONCEPTS:
                    assert concept not in blob, (concept, strategy)
                assert strategy.realization_hint in _ALLOWED_HINTS
                assert strategy.move in _MOVES

    def test_hint_vocabulary_closed(self):
        assert "tease" not in _ALLOWED_HINTS
        assert "clarify" not in _ALLOWED_HINTS
        assert _MOVES == frozenset(
            {"ACKNOWLEDGE", "CONTINUE", "EXPLORE", "CALLBACK", "SHARE", "RECOVER", "CLOSE"}
        )


# ---------------------------------------------------------------------------
# M. Commerce independence (no commerce inputs exist)
# ---------------------------------------------------------------------------


class TestMCommerceIndependence:
    def test_selector_signature_has_no_commerce_params(self):
        params = set(inspect.signature(select_conversational_strategy).parameters)
        assert params == {"snapshot", "conversation_state", "contract", "persona", "turn"}
        forbidden = {
            "desire",
            "temperature",
            "offer_readiness",
            "sales_window",
            "purchase_intent",
            "content_interest",
            "next_best_action",
            "objective",
            "commerce_suppress",
        }
        assert params.isdisjoint(forbidden)

    def test_reason_codes_contain_no_commerce_concepts(self):
        blob = " ".join(sorted(_REASON_CODES)).lower()
        for concept in ("offer", "purchase", "price", "product", "desire", "tease"):
            assert concept not in blob


# ---------------------------------------------------------------------------
# N. Persona constraints
# ---------------------------------------------------------------------------


class TestNPersonaConstraints:
    def test_persona_question_veto_wins(self):
        strategy = _select(
            _mid_snap(),
            _explore_setup(),
            persona=_persona(question_allowed=False),
            turn=TurnEvidenceSummary(user_answered_question=True),
        )
        assert strategy is not None
        assert strategy.move != "EXPLORE"
        assert strategy.question_policy == "NO_QUESTION"
        assert "PERSONA_CONSTRAINT" in strategy.reason_codes

    def test_sincerity_requirement_acknowledges(self):
        strategy = _select(
            _mid_snap(), persona=_persona(sincerity_required=True, emotional_state="serious")
        )
        assert strategy is not None
        assert strategy.move == "ACKNOWLEDGE"
        assert strategy.question_policy == "NO_QUESTION"
        assert "SINCERITY_REQUIRED" in strategy.reason_codes

    def test_annoyed_repairs_without_question(self):
        strategy = _select(_mid_snap(), persona=_persona(emotional_state="annoyed"))
        assert strategy is not None
        assert strategy.move == "RECOVER"
        assert strategy.realization_hint == "react"
        assert strategy.question_policy == "NO_QUESTION"
        assert "REPAIR_EVIDENCE" in strategy.reason_codes


# ---------------------------------------------------------------------------
# O. Creator isolation
# ---------------------------------------------------------------------------


def _profile_for(creator_id, bands):
    anchors = RelationshipAnchors(interaction_count=6, bands=dict(bands))
    return {RELATIONSHIP_TRAJECTORY_KEY: {str(creator_id): anchors_to_dict(anchors)}}


class TestOCreatorIsolation:
    def test_only_supplied_creator_namespace_read(self):
        profile = _profile_for(
            7,
            {
                "familiarity": "familiar",
                "engagement": "steady",
                "reciprocity": "balanced",
                "continuity": "anchored",
            },
        )
        own = select_for_turn(profile=profile, creator_id=7)
        foreign = select_for_turn(profile=profile, creator_id=9)
        assert own is not None
        # Creator 9 has no block in this profile: cold start abstains.
        assert foreign is None

    def test_missing_profile_abstains(self):
        assert select_for_turn(profile=None, creator_id=7) is None
        assert select_for_turn(profile={}, creator_id=7) is None
        assert select_for_turn(profile={}, creator_id=None) is None


# ---------------------------------------------------------------------------
# P. No persistence
# ---------------------------------------------------------------------------


class TestPNoPersistence:
    def test_module_performs_no_writes(self):
        from unittest.mock import patch

        profile = _profile_for(
            7,
            {
                "familiarity": "familiar",
                "engagement": "steady",
                "reciprocity": "balanced",
                "continuity": "anchored",
            },
        )
        before = dict(profile[RELATIONSHIP_TRAJECTORY_KEY]["7"])
        with patch(
            "commerce.relationship_trajectory.store_relationship_anchors",
            side_effect=AssertionError("must not persist"),
        ):
            strategy = select_for_turn(profile=profile, creator_id=7)
            assert strategy is not None
            for _ in range(3):
                assert select_conversational_strategy(
                    snapshot=_mid_snap(), conversation_state=_state()
                ) is not None
        assert profile[RELATIONSHIP_TRAJECTORY_KEY]["7"] == before

    def test_module_source_has_no_persistence_or_commerce_hooks(self):
        src = pathlib.Path("commerce/conversation_strategy.py").read_text()
        lowered = src.lower()
        for token in (
            "mutate_",
            "get_redis",
            "postgres",
            "sqlite",
            "insert ",
            "update ",
            "create table",
            "delete ",
            ".execute(",
            "publish_event",
            "enqueue_",
        ):
            assert token not in lowered, token


# ---------------------------------------------------------------------------
# Q. Fail-open
# ---------------------------------------------------------------------------


class TestQFailOpen:
    def test_render_none_is_empty(self):
        assert render_conversation_strategy(None) == ""

    def test_render_garbage_is_empty(self):
        assert render_conversation_strategy("CALLBACK") == ""
        assert render_conversation_strategy(None.__class__) == ""

    def test_render_format_exact(self):
        strategy = _select(
            _max_snap(),
            _state(current_topic="movies", open_threads=("movies",)),
            turn=TurnEvidenceSummary(user_referenced_previous_context=True),
        )
        assert strategy is not None
        assert render_conversation_strategy(strategy) == (
            "CONVERSATION STRATEGY:\n"
            f"move={strategy.move}\n"
            f"realization={strategy.realization_hint}\n"
            f"question={strategy.question_policy}\n"
            f"reasons={','.join(strategy.reason_codes)}"
        )


# ---------------------------------------------------------------------------
# R. Farewell close
# ---------------------------------------------------------------------------


class TestRFarewell:
    def test_farewell_detection(self):
        assert detect_farewell("ok bye, talk later!") is True
        assert detect_farewell("goodnight!") is True
        assert detect_farewell("hello there, how are you?") is False
        assert detect_farewell("") is False
        assert detect_farewell(None) is False
        assert detect_farewell(42) is False

    def test_farewell_closes_even_at_max_bands(self):
        strategy = _select(_max_snap(), turn=TurnEvidenceSummary(farewell=True))
        assert strategy is not None
        assert strategy.move == "CLOSE"
        assert strategy.realization_hint == "close"
        assert strategy.question_policy == "NO_QUESTION"
        assert strategy.reason_codes == ("FAREWELL",)


# ---------------------------------------------------------------------------
# S. Share downgrade (reciprocate without interrogating)
# ---------------------------------------------------------------------------


class TestSShareDowngrade:
    def test_share_when_question_disallowed_but_reciprocity_high(self):
        strategy = _select(
            _snap("familiar", "deep", "high", "anchored", "stable"),
            _explore_setup(consecutive_questions=1),
            turn=TurnEvidenceSummary(user_answered_question=True),
        )
        assert strategy is not None
        assert strategy.move == "SHARE"
        assert strategy.realization_hint == "share"
        assert strategy.question_policy == "NO_QUESTION"
