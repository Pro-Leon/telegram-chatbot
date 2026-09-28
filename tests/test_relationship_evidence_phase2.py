"""Phase 2 relationship evidence tests (AUDIT → IMPLEMENT).

Covers the deterministic ``commerce.relationship_evidence`` normalization
layer, its idempotent runtime accumulation, and the fail-open worker
integration contract:

* one canonical question detector (conversation_state semantics)
* answer preservation (existing ConversationState semantics)
* disclosure OR-semantics over narrow explicit extractors only
* provisional overlap rules for topic continuation / prior context
* assistant evidence from actual outbound text / share-mode proxy
* session-returned exclusively via ConversationLifecycle.RETURNING
* open loops exclusively via real long_term_memory (dead path never used)
* strict LLM boundary (validated CommerceSignals only, advisory only)
* per-turn deduplication (one message → one evidence object)
* generation-keyed idempotent accumulation (atomic, bounded, isolated)
* failure / fail-open behavior
* Phase 1 contract invariance (no silent redesign)

Conventions follow ``test_relationship_trajectory_phase1.py``:
``pytestmark = [pytest.mark.unit]``, explicit ``@pytest.mark.asyncio`` for
async paths, ``AsyncMock``/``patch`` for DB boundaries (no live infra).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit]

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _ev(**overrides):
    from commerce.relationship_evidence import extract_turn_evidence

    base: dict = {
        "user_message": "hello there",
        "history": [],
        "conversation_state": None,
    }
    base.update(overrides)
    return extract_turn_evidence(**base)


def _state(**overrides):
    """Minimal ConversationState-shaped dict for extractor tests."""
    base: dict = {
        "lifecycle": "established",
        "current_topic": None,
        "recent_topics": (),
        "open_threads": (),
        "last_question": None,
        "last_question_answered": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Canonical question detector
# ---------------------------------------------------------------------------


class TestCanonicalQuestionDetector:
    def test_question_mark(self):
        assert _ev(user_message="How are you?").user_asked_question is True

    def test_qless_interrogative(self):
        assert _ev(user_message="How are you").user_asked_question is True
        assert _ev(user_message="What time is it").user_asked_question is True
        assert _ev(user_message="Could you help me").user_asked_question is True

    def test_non_question(self):
        assert _ev(user_message="Tell me about work").user_asked_question is False
        assert _ev(user_message="Hello there").user_asked_question is False
        assert _ev(user_message="").user_asked_question is False
        assert _ev(user_message="   ").user_asked_question is False
        assert _ev(user_message=None).user_asked_question is False

    def test_boundary_trailing_whitespace_question(self):
        assert _ev(user_message="Are you coming?   ").user_asked_question is True

    def test_canonical_matches_conversation_state_exactly(self):
        from commerce.relationship_evidence import is_canonical_question
        from core.conversation_state import _is_question as _cs_is_question

        cases = [
            "How are you?",
            "What time is it",
            "Tell me about work",
            "Are you coming?",
            "Could you help me",
            "could you help?",
            "are we done",
            "is this working",
            "will you come",
            "would you help",
            "did you go",
            "When is it",
            "where are you from",
            "who are you",
            "how about you",
            "and you?",
            "Hello there",
            "",
            "   ",
        ]
        for text in cases:
            assert is_canonical_question(text) is _cs_is_question(text), text

    def test_characterization_divergence_documented(self):
        # Direct source inspection result (see module docstring): the
        # contract detector is widest on bare "?-less" declaratives, the
        # response-mode detector lacks "could you ". The canonical choice
        # (conversation_state) is conservative: bare declaratives stay False.
        from core import conversation_contract as _cc
        from core import response_mode as _rm
        from core import conversation_state as _cs

        assert _cs._is_question("are we done") is False
        assert _cc._is_question("are we done") is True
        assert _cs._is_question("Could you help me") is True
        assert _rm._is_question("Could you help me") is False
        # Canonical follows conversation_state in both cases.
        assert _ev(user_message="are we done").user_asked_question is False
        assert _ev(user_message="Could you help me").user_asked_question is True

    def test_exactly_one_detector_feeds_evidence(self):
        import inspect

        import commerce.relationship_evidence as _re

        source = inspect.getsource(_re)
        # No other detector module is imported or called: the only question
        # predicate defined or invoked here is the canonical one. (Module
        # docstrings may name the alternatives for documentation; what
        # matters is no import and no second predicate definition/call.)
        assert "from core.conversation_state import" not in source
        assert "from core.conversation_contract import" not in source
        assert "from core.response_mode import" not in source
        assert "import core.conversation_state" not in source
        assert "import core.conversation_contract" not in source
        assert "import core.response_mode" not in source
        assert source.count("def is_canonical_question") == 1
        assert "def _is_question" not in source
        # The single canonical detector is the only question predicate used.
        assert source.count("is_canonical_question") >= 3

    def test_llm_flag_corroborates_without_second_vote(self):
        from commerce.relationship_evidence import extract_turn_evidence
        from commerce.signals import CommerceSignals

        corroborating = CommerceSignals(fan_asks_question=True)
        # Deterministic True + LLM True → still one boolean True.
        assert (
            extract_turn_evidence(
                user_message="How are you?",
                history=[],
                conversation_state=None,
                llm_signals=corroborating,
            ).user_asked_question
            is True
        )
        # Deterministic False + LLM True → LLM cannot create a vote.
        assert (
            extract_turn_evidence(
                user_message="Tell me about work",
                history=[],
                conversation_state=None,
                llm_signals=corroborating,
            ).user_asked_question
            is False
        )

    def test_user_sent_message_is_transport_fact_not_llm(self):
        from commerce.signals import CommerceSignals

        assert _ev(user_message="hello").user_sent_message is True
        assert (
            _ev(
                user_message="hello", llm_signals=CommerceSignals.low_information()
            ).user_sent_message
            is True
        )
        assert _ev(user_message=None).user_sent_message is True


# ---------------------------------------------------------------------------
# Answer (existing ConversationState semantics preserved)
# ---------------------------------------------------------------------------


class TestAnswerSemantics:
    def test_answers_previous_assistant_question(self):
        from core.conversation_state import derive_conversation_state

        history = [
            {"direction": "outbound", "content": "How was your day?"},
            {"direction": "inbound", "content": "It was great, thanks"},
        ]
        state = derive_conversation_state(history)
        assert state.last_question is not None
        assert state.last_question_answered is True
        assert (
            _ev(
                user_message="It was great, thanks", history=history[:1], conversation_state=state
            ).user_answered_question
            is True
        )

    def test_no_previous_question(self):
        from core.conversation_state import derive_conversation_state

        history = [
            {"direction": "outbound", "content": "Nice to meet you here."},
            {"direction": "inbound", "content": "Hey there, good evening."},
        ]
        state = derive_conversation_state(history)
        assert state.last_question is None
        assert (
            _ev(
                user_message="Hey there", history=history, conversation_state=state
            ).user_answered_question
            is False
        )

    def test_unrelated_message_after_question_preserves_existing_semantics(self):
        # Existing semantics: ANY user turn after the last assistant
        # question counts as answered (no new answer detector is created).
        from core.conversation_state import derive_conversation_state

        history = [
            {"direction": "outbound", "content": "What did you have for lunch?"},
            {"direction": "inbound", "content": "I like pizza"},
        ]
        state = derive_conversation_state(history)
        assert state.last_question_answered is True
        assert (
            _ev(
                user_message="I like pizza", history=history, conversation_state=state
            ).user_answered_question
            is True
        )

    def test_missing_state_is_false(self):
        assert _ev(user_message="anything", conversation_state=None).user_answered_question is False


# ---------------------------------------------------------------------------
# Disclosure (deterministic explicit only, OR semantics)
# ---------------------------------------------------------------------------


class TestDisclosure:
    def test_ltm_explicit_hit(self):
        assert _ev(user_message="x", ltm_explicit_hit=True).user_shared_information is True

    def test_fan_knowledge_explicit_hit(self):
        assert (
            _ev(user_message="x", fan_knowledge_explicit_hit=True).user_shared_information is True
        )

    def test_both_hits_one_boolean(self):
        from commerce.relationship_evidence import extract_turn_evidence
        from commerce.relationship_trajectory import accumulate_turn, neutral_anchors

        evidence = extract_turn_evidence(
            user_message="x",
            history=[],
            conversation_state=None,
            ltm_explicit_hit=True,
            fan_knowledge_explicit_hit=True,
        )
        assert evidence.user_shared_information is True
        anchors = accumulate_turn(neutral_anchors(T0), evidence, T0)
        assert anchors.user_shared_info_count == 1

    def test_neither_hit_is_false(self):
        assert _ev(user_message="hey how are you").user_shared_information is False

    def test_real_ltm_extractor_hit_and_weak_excluded(self):
        from commerce.relationship_evidence import has_explicit_ltm_hit

        assert has_explicit_ltm_hit("My favorite color is red", 7, 3) is True
        # Weak-inference movie TOPIC (0.5) never counts as explicit disclosure.
        assert has_explicit_ltm_hit("watching a movie tonight", 7, 3) is False
        assert has_explicit_ltm_hit("", 7, 3) is False

    def test_real_fan_knowledge_hit_and_false_positives(self):
        from commerce.relationship_evidence import has_explicit_fan_knowledge_hit

        assert has_explicit_fan_knowledge_hit("I live in Chicago", 7, 3) is True
        assert has_explicit_fan_knowledge_hit("I love horror movies", 7, 3) is True
        # Known upstream false-positive guards stay False (no disclosure).
        assert has_explicit_fan_knowledge_hit("I need to get back to work soon", 7, 3) is False
        assert has_explicit_fan_knowledge_hit("His name is Max", 7, 3) is False
        assert has_explicit_fan_knowledge_hit("hey how are you doing today", 7, 3) is False

    def test_inferred_profile_information_does_not_count(self):
        # An LLM intent tag suggesting disclosure, without deterministic
        # evidence, creates nothing.
        from commerce.signals import CommerceSignals

        signals = CommerceSignals(
            primary_intent="personal_disclosure", intent_tags=["personal_disclosure"]
        )
        evidence = _ev(user_message="hey there", llm_signals=signals)
        assert evidence.user_shared_information is False

    def test_raw_text_never_persisted(self):
        import json

        from commerce.relationship_evidence import extract_turn_evidence
        from commerce.relationship_trajectory import (
            accumulate_turn,
            anchors_to_dict,
            neutral_anchors,
        )

        secret = "My favorite color is red and I live in Chicago"
        evidence = extract_turn_evidence(
            user_message=secret,
            history=[],
            conversation_state=None,
            ltm_explicit_hit=True,
            fan_knowledge_explicit_hit=True,
        )
        anchors = accumulate_turn(neutral_anchors(T0), evidence, T0)
        blob = json.dumps(anchors_to_dict(anchors), sort_keys=True)
        assert "Chicago" not in blob
        assert "favorite color" not in blob
        assert secret not in blob


# ---------------------------------------------------------------------------
# Topic continuation (narrow provisional overlap; maintain_topic never used)
# ---------------------------------------------------------------------------


class TestTopicContinuation:
    def _history(self):
        return [
            {"direction": "inbound", "content": "I watched a great movie last night"},
            {"direction": "outbound", "content": "Oh nice, what movie was it?"},
        ]

    def test_true_for_valid_overlap(self):
        state = _state(current_topic="movie", recent_topics=("movie",), open_threads=("movie",))
        evidence = _ev(
            user_message="The movie was a sci-fi adventure",
            history=self._history(),
            conversation_state=state,
        )
        assert evidence.user_continued_topic is True

    def test_false_for_unrelated_topic(self):
        state = _state(current_topic="movie", recent_topics=("movie",), open_threads=("movie",))
        evidence = _ev(
            user_message="I need to fix my bicycle tire",
            history=self._history(),
            conversation_state=state,
        )
        assert evidence.user_continued_topic is False

    def test_maintain_topic_is_not_used(self):
        class _Contract:
            def __init__(self):
                self.maintain_topic = True
                self.current_topic = "movie"

            def __getattr__(self, name):
                if name == "maintain_topic":
                    raise AssertionError("maintain_topic must never be read")
                raise AttributeError(name)

        state = _state(current_topic="movie", recent_topics=("movie",), open_threads=("movie",))
        # Unrelated message: True maintain_topic must not force True.
        evidence = _ev(
            user_message="I need to fix my bicycle tire",
            history=self._history(),
            conversation_state=state,
            contract=_Contract(),
        )
        assert evidence.user_continued_topic is False
        # Valid overlap still True even with a contract whose maintain_topic
        # raises on access (proves independence).
        evidence2 = _ev(
            user_message="The movie was a sci-fi adventure",
            history=self._history(),
            conversation_state=state,
            contract=_Contract(),
        )
        assert evidence2.user_continued_topic is True

    def test_single_message_cannot_create_artificial_continuation(self):
        # No prior history: the current message alone cannot continue a
        # topic, even when the state vocabulary contains its token.
        state = _state(current_topic="movie", recent_topics=("movie",), open_threads=("movie",))
        assert (
            _ev(
                user_message="I love that movie", history=[], conversation_state=state
            ).user_continued_topic
            is False
        )
        assert (
            _ev(
                user_message="I love that movie", history=None, conversation_state=None
            ).user_continued_topic
            is False
        )

    def test_system_text_does_not_count_as_prior_presence(self):
        state = _state(current_topic="movie", recent_topics=("movie",), open_threads=("movie",))
        history = [{"role": "system", "content": "You love discussing the movie topic in depth"}]
        assert (
            _ev(
                user_message="Tell me about that movie", history=history, conversation_state=state
            ).user_continued_topic
            is False
        )


# ---------------------------------------------------------------------------
# Prior context (relationship-specific threshold over retrieval output)
# ---------------------------------------------------------------------------


class TestPriorContext:
    def _mem(self, subject="interview", value="interview Friday morning"):
        return {"subject": subject, "value": value, "memory_type": "open_loop"}

    def test_below_threshold_is_false(self):
        evidence = _ev(
            user_message="I need to fix my bicycle tire",
            retrieved_memories=[self._mem()],
            retrieved_knowledge=[],
        )
        assert evidence.user_referenced_previous_context is False

    def test_exactly_threshold_is_true(self):
        evidence = _ev(
            user_message="How did the interview go?",
            retrieved_memories=[self._mem()],
            retrieved_knowledge=[],
        )
        assert evidence.user_referenced_previous_context is True

    def test_above_threshold_is_true(self):
        evidence = _ev(
            user_message="My interview Friday morning went great",
            retrieved_memories=[self._mem()],
            retrieved_knowledge=[{"subject": "city", "value": "Chicago"}],
        )
        assert evidence.user_referenced_previous_context is True

    def test_no_retrieval_evidence_is_false(self):
        assert (
            _ev(user_message="How did the interview go?").user_referenced_previous_context is False
        )
        assert (
            _ev(
                user_message="How did the interview go?",
                retrieved_memories=[],
                retrieved_knowledge=[],
            ).user_referenced_previous_context
            is False
        )
        assert (
            _ev(
                user_message="How did the interview go?",
                retrieved_memories=None,
                retrieved_knowledge=None,
            ).user_referenced_previous_context
            is False
        )

    def test_no_new_semantic_retrieval_infrastructure(self):
        import inspect

        import commerce.relationship_evidence as _re

        source = inspect.getsource(_re).lower()
        assert "minilm" not in source
        assert "from commerce.embedding_model" not in source
        assert "cosine" not in source
        assert "vector_search" not in source

    def test_threshold_is_named_and_distinct_from_commercial(self):
        from commerce.relationship_evidence import (
            RELATIONSHIP_RETRIEVAL_MIN_OVERLAP,
        )

        assert RELATIONSHIP_RETRIEVAL_MIN_OVERLAP == 1
        # Commercial prefilter is a 0.2 float score; ours is an integer
        # token-overlap count — a different kind and value by construction.
        assert isinstance(RELATIONSHIP_RETRIEVAL_MIN_OVERLAP, int)


# ---------------------------------------------------------------------------
# Assistant evidence
# ---------------------------------------------------------------------------


class TestAssistantEvidence:
    def test_actual_assistant_question(self):
        assert (
            _ev(assistant_text="How was your day?", assistant_valid=True).assistant_asked_question
            is True
        )

    def test_failed_generation_is_no_question(self):
        assert (
            _ev(assistant_text="How was your day?", assistant_valid=False).assistant_asked_question
            is False
        )
        assert _ev(assistant_text="", assistant_valid=True).assistant_asked_question is False
        assert _ev(assistant_text=None, assistant_valid=True).assistant_asked_question is False
        assert _ev().assistant_asked_question is False

    def test_non_question_outbound(self):
        assert (
            _ev(
                assistant_text="Thanks for sharing that.", assistant_valid=True
            ).assistant_asked_question
            is False
        )

    def test_share_mode_proxy(self):
        assert _ev(conversation_mode="share").assistant_shared_information is True
        assert _ev(conversation_mode="SHARE").assistant_shared_information is True

    def test_unavailable_mode_is_false(self):
        assert _ev(conversation_mode=None).assistant_shared_information is False
        assert _ev(conversation_mode="").assistant_shared_information is False
        assert _ev(conversation_mode="explore").assistant_shared_information is False
        assert _ev(conversation_mode="react").assistant_shared_information is False


# ---------------------------------------------------------------------------
# Return (ConversationLifecycle.RETURNING only)
# ---------------------------------------------------------------------------


class TestSessionReturned:
    def test_below_48h_is_not_returning(self):
        from core.conversation_state import derive_lifecycle

        now = T0
        last = now - timedelta(hours=47)
        assert derive_lifecycle(10, last, now=now) == "established"
        assert _ev(lifecycle="established").session_returned is False

    def test_exact_48h_boundary_is_returning(self):
        from core.conversation_state import derive_lifecycle

        now = T0
        last = now - timedelta(hours=48)
        assert derive_lifecycle(10, last, now=now) == "returning"
        assert _ev(lifecycle="returning").session_returned is True

    def test_above_48h_is_returning(self):
        from core.conversation_state import derive_lifecycle

        now = T0
        last = now - timedelta(hours=72)
        assert derive_lifecycle(10, last, now=now) == "returning"
        assert _ev(lifecycle="returning").session_returned is True

    def test_state_lifecycle_fallback(self):
        assert _ev(conversation_state=_state(lifecycle="returning")).session_returned is True
        assert _ev(conversation_state=_state(lifecycle="established")).session_returned is False
        assert _ev(conversation_state=None).session_returned is False

    def test_ltm_commitment_return_does_not_substitute(self):
        # "I will come back" is an explicit COMMITMENT disclosure, but the
        # lifecycle is authoritative for session_returned.
        from commerce.relationship_evidence import has_explicit_ltm_hit

        assert has_explicit_ltm_hit("I will come back after payday", 7, 3) is True
        evidence = _ev(
            user_message="I will come back after payday",
            conversation_state=_state(lifecycle="established"),
            lifecycle="established",
            ltm_explicit_hit=True,
        )
        assert evidence.user_shared_information is True
        assert evidence.session_returned is False


# ---------------------------------------------------------------------------
# Open loops (real long_term_memory only; dead path never used)
# ---------------------------------------------------------------------------


class TestOpenLoops:
    def test_continuation_passthrough(self):
        assert _ev(open_loop_continued=True).open_loop_continued is True
        assert _ev(open_loop_continued=False).open_loop_continued is False

    def test_resolution_passthrough(self):
        assert _ev(open_loop_resolved=True).open_loop_resolved is True
        assert _ev(open_loop_resolved=False).open_loop_resolved is False

    def test_no_open_loop_is_false(self):
        evidence = _ev()
        assert evidence.open_loop_continued is False
        assert evidence.open_loop_resolved is False

    def test_dead_commerce_open_loop_path_not_used(self):
        import inspect

        import commerce.relationship_evidence as _re

        with pytest.raises(ImportError):
            __import__("commerce.open_loop")
        source = inspect.getsource(_re)
        assert "from commerce.open_loop import" not in source
        assert "import commerce.open_loop" not in source

    def test_real_resolve_signature(self):
        import inspect

        from commerce.long_term_memory import resolve_open_loop

        params = list(inspect.signature(resolve_open_loop).parameters)
        assert params == ["creator_id", "user_id", "current_message"]


# ---------------------------------------------------------------------------
# LLM boundary
# ---------------------------------------------------------------------------


class TestLLMBoundary:
    def test_invalid_signals_are_absent(self):
        assert _ev(llm_signals=None).llm_relationship_engagement is None
        assert _ev(llm_signals="garbage").llm_relationship_engagement is None
        assert _ev(llm_signals=0.95).llm_relationship_engagement is None
        assert _ev(llm_signals=True).llm_relationship_engagement is None
        assert (
            _ev(llm_signals={"relationship_engagement": 0.95}).llm_relationship_engagement is None
        )

    def test_raw_reply_never_consumed(self):
        from core.one_call import OneCallReply
        from commerce.signals import CommerceSignals

        raw = OneCallReply(
            reply="hello there",
            commerce_signals=CommerceSignals(relationship_engagement=0.95),
            confidence=0.9,
            needs_handoff=False,
        )
        assert _ev(user_message="hello", llm_signals=raw).llm_relationship_engagement is None

    def test_valid_engagement_passes_through_for_phase1_rule(self):
        from commerce.signals import CommerceSignals

        evidence = _ev(
            user_message="How are you?",
            llm_signals=CommerceSignals(relationship_engagement=0.95),
        )
        assert evidence.llm_relationship_engagement == pytest.approx(0.95)

    def test_out_of_range_engagement_is_absent(self):
        from commerce.signals import CommerceSignals

        tampered = CommerceSignals.low_information()
        object.__setattr__(tampered, "relationship_engagement", 1.5)
        assert _ev(llm_signals=tampered).llm_relationship_engagement is None
        tampered2 = CommerceSignals.low_information()
        object.__setattr__(tampered2, "relationship_engagement", float("nan"))
        assert _ev(llm_signals=tampered2).llm_relationship_engagement is None

    def test_engagement_alone_cannot_alter_bands(self):
        from commerce.relationship_evidence import extract_turn_evidence
        from commerce.relationship_trajectory import (
            accumulate_turn,
            derive_relationship_snapshot,
            neutral_anchors,
        )
        from commerce.signals import CommerceSignals

        llm_only = extract_turn_evidence(
            user_message="hello there",
            history=[],
            conversation_state=None,
            llm_signals=CommerceSignals(relationship_engagement=0.95),
        )
        plain = extract_turn_evidence(
            user_message="hello there", history=[], conversation_state=None
        )
        with_llm = accumulate_turn(neutral_anchors(T0), llm_only, T0)
        without_llm = accumulate_turn(neutral_anchors(T0), plain, T0)
        assert derive_relationship_snapshot(with_llm, None, T0) == derive_relationship_snapshot(
            without_llm, None, T0
        )
        assert with_llm.corroborated_engagement_observations == 0

    def test_excluded_content_signals_do_not_enter(self):
        from commerce.signals import CommerceSignals

        signals = CommerceSignals(
            content_interest=0.99,
            explicit_content_request=True,
            primary_intent="content_request",
            intent_tags=["content_request"],
            relationship_engagement=0.0,
            evidence=["some quoted fragment"],
        )
        evidence = _ev(user_message="hey there", llm_signals=signals)
        assert evidence.user_shared_information is False
        assert evidence.user_continued_topic is False
        assert evidence.user_referenced_previous_context is False
        assert evidence.user_asked_question is False
        assert evidence.llm_relationship_engagement == pytest.approx(0.0)

    def test_intent_tags_cannot_create_evidence(self):
        from commerce.signals import CommerceSignals

        signals = CommerceSignals(
            primary_intent="personal_disclosure", intent_tags=["personal_disclosure"]
        )
        evidence = _ev(user_message="hey there", llm_signals=signals)
        assert evidence.user_shared_information is False
        assert evidence.user_continued_topic is False


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_one_message_one_evidence_object(self):
        from commerce.relationship_evidence import extract_turn_evidence
        from commerce.signals import CommerceSignals

        evidence = extract_turn_evidence(
            user_message="How are you? I live in Chicago",
            history=[],
            conversation_state=None,
            ltm_explicit_hit=True,
            fan_knowledge_explicit_hit=True,
            llm_signals=CommerceSignals(fan_asks_question=True, relationship_engagement=0.9),
        )
        assert evidence.user_asked_question is True
        assert evidence.user_shared_information is True

    def test_distinct_fields_may_coexist(self):
        # Topic continuation and open-loop continuation are different Phase 1
        # dimensions; both may be True on one turn without collapsing.
        state = _state(current_topic="movie", recent_topics=("movie",), open_threads=("movie",))
        history = [
            {"direction": "inbound", "content": "I watched a great movie last night"},
        ]
        evidence = _ev(
            user_message="That movie was amazing",
            history=history,
            conversation_state=state,
            open_loop_continued=True,
        )
        assert evidence.user_continued_topic is True
        assert evidence.open_loop_continued is True


# ---------------------------------------------------------------------------
# Idempotency (generation-keyed, atomic, bounded, isolated)
# ---------------------------------------------------------------------------


def _make_fake_mutate(store: dict):
    async def _fake_mutate(user_id, mutator):
        facts = store.setdefault(int(user_id), {})
        try:
            should_write = mutator(facts)
        except Exception:
            return False
        return bool(should_write)

    return _fake_mutate


def _rich_evidence():
    from commerce.relationship_trajectory import RelationshipTurnEvidence

    return RelationshipTurnEvidence(
        user_sent_message=True,
        user_asked_question=True,
        user_shared_information=True,
    )


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_first_generation_accumulates(self):
        from commerce.relationship_evidence import accumulate_relationship_turn_idempotent

        store: dict = {}
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=_make_fake_mutate(store)),
        ):
            anchors, did = await accumulate_relationship_turn_idempotent(
                user_id=1,
                creator_id=7,
                generation_id="gen-1",
                evidence=_rich_evidence(),
                now=T0,
            )
        assert did is True
        assert anchors is not None
        assert anchors.interaction_count == 1
        assert anchors.user_question_count == 1

    @pytest.mark.asyncio
    async def test_same_generation_repeated_does_not_accumulate(self):
        from commerce.relationship_evidence import accumulate_relationship_turn_idempotent

        store: dict = {}
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=_make_fake_mutate(store)),
        ):
            first, did_first = await accumulate_relationship_turn_idempotent(
                user_id=1,
                creator_id=7,
                generation_id="gen-1",
                evidence=_rich_evidence(),
                now=T0,
            )
            second, did_second = await accumulate_relationship_turn_idempotent(
                user_id=1,
                creator_id=7,
                generation_id="gen-1",
                evidence=_rich_evidence(),
                now=T0,
            )
        assert did_first is True
        assert did_second is False
        assert first is not None and second is not None
        assert second.interaction_count == 1
        assert second.user_question_count == 1
        assert second.observation_count == 1

    @pytest.mark.asyncio
    async def test_worker_redelivery_does_not_accumulate_again(self):
        from commerce.relationship_evidence import accumulate_relationship_turn_idempotent

        store: dict = {}
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=_make_fake_mutate(store)),
        ):
            for _ in range(3):
                anchors, did = await accumulate_relationship_turn_idempotent(
                    user_id=1,
                    creator_id=7,
                    generation_id="redelivery-gen",
                    evidence=_rich_evidence(),
                    now=T0,
                )
        assert did is False
        assert anchors is not None
        assert anchors.interaction_count == 1

    @pytest.mark.asyncio
    async def test_two_creators_same_generation_no_collision(self):
        from commerce.relationship_evidence import accumulate_relationship_turn_idempotent
        from commerce.relationship_trajectory import (
            RELATIONSHIP_TRAJECTORY_KEY,
            get_relationship_anchors,
        )

        store: dict = {}
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=_make_fake_mutate(store)),
        ):
            _, did_a = await accumulate_relationship_turn_idempotent(
                user_id=1,
                creator_id=7,
                generation_id="shared-gen",
                evidence=_rich_evidence(),
                now=T0,
            )
            _, did_b = await accumulate_relationship_turn_idempotent(
                user_id=1,
                creator_id=9,
                generation_id="shared-gen",
                evidence=_rich_evidence(),
                now=T0,
            )
        assert did_a is True
        assert did_b is True
        profile = store[1]
        assert get_relationship_anchors(profile, 7).interaction_count == 1
        assert get_relationship_anchors(profile, 9).interaction_count == 1

    @pytest.mark.asyncio
    async def test_two_users_same_generation_no_collision(self):
        from commerce.relationship_evidence import accumulate_relationship_turn_idempotent
        from commerce.relationship_trajectory import (
            RELATIONSHIP_TRAJECTORY_KEY,
            get_relationship_anchors,
        )

        store: dict = {}
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=_make_fake_mutate(store)),
        ):
            _, did_a = await accumulate_relationship_turn_idempotent(
                user_id=1,
                creator_id=7,
                generation_id="shared-gen",
                evidence=_rich_evidence(),
                now=T0,
            )
            _, did_b = await accumulate_relationship_turn_idempotent(
                user_id=2,
                creator_id=7,
                generation_id="shared-gen",
                evidence=_rich_evidence(),
                now=T0,
            )
        assert did_a is True
        assert did_b is True
        assert get_relationship_anchors(store[1], 7).interaction_count == 1
        assert get_relationship_anchors(store[2], 7).interaction_count == 1

    @pytest.mark.asyncio
    async def test_bounded_processed_marker_retention(self):
        from commerce.relationship_evidence import (
            PROCESSED_GENERATION_IDS_FIELD,
            accumulate_relationship_turn_idempotent,
        )
        from commerce.relationship_trajectory import RELATIONSHIP_TRAJECTORY_KEY

        store: dict = {}
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=_make_fake_mutate(store)),
        ):
            for i in range(5):
                await accumulate_relationship_turn_idempotent(
                    user_id=1,
                    creator_id=7,
                    generation_id=f"gen-{i}",
                    evidence=_rich_evidence(),
                    now=T0,
                    max_processed_ids=3,
                )
        block = store[1][RELATIONSHIP_TRAJECTORY_KEY]["7"]
        assert block[PROCESSED_GENERATION_IDS_FIELD] == ["gen-2", "gen-3", "gen-4"]

    @pytest.mark.asyncio
    async def test_atomic_mutation_preserves_unrelated_namespaces(self):
        from commerce.relationship_evidence import accumulate_relationship_turn_idempotent
        from commerce.relationship_trajectory import (
            RELATIONSHIP_TRAJECTORY_KEY,
            anchors_to_dict,
            neutral_anchors,
        )

        store: dict = {
            1: {
                "interests": ["hiking"],
                "long_term_memory_by_creator": {"7": [{"subject": "trip"}]},
                "fan_knowledge_by_creator": {"7": [{"subject": "city"}]},
                "commercial_preferences_by_creator": {"7": {"red lace": {}}},
                RELATIONSHIP_TRAJECTORY_KEY: {"9": anchors_to_dict(neutral_anchors(T0))},
            }
        }
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=_make_fake_mutate(store)),
        ):
            _, did = await accumulate_relationship_turn_idempotent(
                user_id=1,
                creator_id=7,
                generation_id="gen-new",
                evidence=_rich_evidence(),
                now=T0,
            )
        assert did is True
        facts = store[1]
        assert facts["interests"] == ["hiking"]
        assert facts["long_term_memory_by_creator"] == {"7": [{"subject": "trip"}]}
        assert facts["fan_knowledge_by_creator"] == {"7": [{"subject": "city"}]}
        assert facts["commercial_preferences_by_creator"] == {"7": {"red lace": {}}}
        assert set(facts[RELATIONSHIP_TRAJECTORY_KEY].keys()) == {"9", "7"}

    @pytest.mark.asyncio
    async def test_missing_corrupt_anchors_fail_safe(self):
        from commerce.relationship_evidence import accumulate_relationship_turn_idempotent
        from commerce.relationship_trajectory import RELATIONSHIP_TRAJECTORY_KEY

        for bad_block in ("garbage", None, [], {"interaction_count": -5}):
            store: dict = {1: {RELATIONSHIP_TRAJECTORY_KEY: {"7": bad_block}}}
            with patch(
                "db.postgres.mutate_user_profile_atomically",
                new=AsyncMock(side_effect=_make_fake_mutate(store)),
            ):
                anchors, did = await accumulate_relationship_turn_idempotent(
                    user_id=1,
                    creator_id=7,
                    generation_id="gen-x",
                    evidence=_rich_evidence(),
                    now=T0,
                )
            assert did is True
            assert anchors is not None
            assert anchors.interaction_count == 1

    @pytest.mark.asyncio
    async def test_persistence_failure_is_fail_open(self):
        from commerce.relationship_evidence import accumulate_relationship_turn_idempotent

        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            anchors, did = await accumulate_relationship_turn_idempotent(
                user_id=1,
                creator_id=7,
                generation_id="gen-1",
                evidence=_rich_evidence(),
                now=T0,
            )
        assert anchors is None
        assert did is False

    @pytest.mark.asyncio
    async def test_empty_generation_id_skips_safely(self):
        from commerce.relationship_evidence import accumulate_relationship_turn_idempotent

        store: dict = {}
        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=_make_fake_mutate(store)),
        ):
            anchors, did = await accumulate_relationship_turn_idempotent(
                user_id=1,
                creator_id=7,
                generation_id="",
                evidence=_rich_evidence(),
                now=T0,
            )
        assert did is False
        assert anchors is None
        assert store == {}


# ---------------------------------------------------------------------------
# Failure / fail-open
# ---------------------------------------------------------------------------


class TestFailureFailOpen:
    def test_extractor_is_total_on_garbage(self):
        from commerce.relationship_evidence import extract_turn_evidence

        evidence = extract_turn_evidence(
            user_message=None,
            history="not-a-list",  # type: ignore[arg-type]
            conversation_state="garbage",  # type: ignore[arg-type]
            contract="garbage",
            lifecycle=12345,  # type: ignore[arg-type]
            retrieved_memories="garbage",  # type: ignore[arg-type]
            llm_signals="garbage",
            now=None,
        )
        # Neutral-ish: transport fact only, nothing speculative.
        assert evidence.user_sent_message is True
        assert evidence.user_asked_question is False
        assert evidence.user_shared_information is False
        assert evidence.user_continued_topic is False
        assert evidence.user_referenced_previous_context is False
        assert evidence.session_returned is False

    @pytest.mark.asyncio
    async def test_worker_helper_missing_creator_is_fail_open(self):
        from workers.llm_worker import _record_relationship_trajectory

        # Must never raise; simply skips without creator scope.
        await _record_relationship_trajectory(
            user_id=1,
            creator_id=None,
            generation_id="gen-1",
            user_message="hello",
        )

    @pytest.mark.asyncio
    async def test_worker_helper_ltm_failure_still_accumulates(self):
        from workers.llm_worker import _record_relationship_trajectory

        store: dict = {}
        with (
            patch(
                "db.postgres.mutate_user_profile_atomically",
                new=AsyncMock(side_effect=_make_fake_mutate(store)),
            ),
            patch(
                "commerce.long_term_memory.retrieve_relevant_memories",
                new=AsyncMock(side_effect=RuntimeError("ltm down")),
            ),
            patch(
                "commerce.fan_knowledge.retrieve_relevant_knowledge",
                new=AsyncMock(side_effect=RuntimeError("knowledge down")),
            ),
            patch(
                "commerce.long_term_memory.resolve_open_loop",
                new=AsyncMock(side_effect=RuntimeError("resolve down")),
            ),
            patch(
                "commerce.fan_knowledge.get_fan_knowledge",
                new=AsyncMock(side_effect=RuntimeError("get down")),
            ),
        ):
            await _record_relationship_trajectory(
                user_id=1,
                creator_id=7,
                generation_id="gen-ltm-fail",
                user_message="How are you?",
            )
        from commerce.relationship_trajectory import (
            RELATIONSHIP_TRAJECTORY_KEY,
            get_relationship_anchors,
        )

        assert get_relationship_anchors(store[1], 7).interaction_count == 1
        assert get_relationship_anchors(store[1], 7).user_question_count == 1

    @pytest.mark.asyncio
    async def test_worker_helper_persistence_failure_never_raises(self):
        from workers.llm_worker import _record_relationship_trajectory

        with patch(
            "db.postgres.mutate_user_profile_atomically",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            await _record_relationship_trajectory(
                user_id=1,
                creator_id=7,
                generation_id="gen-dbfail",
                user_message="How are you?",
            )

    @pytest.mark.asyncio
    async def test_generation_failure_still_processes_deterministic_evidence(self):
        from workers.llm_worker import _record_relationship_trajectory

        store: dict = {}
        with (
            patch(
                "db.postgres.mutate_user_profile_atomically",
                new=AsyncMock(side_effect=_make_fake_mutate(store)),
            ),
            patch(
                "commerce.long_term_memory.resolve_open_loop",
                new=AsyncMock(return_value=False),
            ),
        ):
            # Generation failed: no LLM signals, no valid assistant output.
            await _record_relationship_trajectory(
                user_id=1,
                creator_id=7,
                generation_id="gen-nollm",
                user_message="How are you? I live in Chicago",
                assistant_text="",
                assistant_valid=False,
                llm_signals=None,
            )
        from commerce.relationship_trajectory import get_relationship_anchors

        anchors = get_relationship_anchors(store[1], 7)
        assert anchors.interaction_count == 1
        assert anchors.user_question_count == 1
        assert anchors.user_shared_info_count == 1
        assert anchors.assistant_question_count == 0


# ---------------------------------------------------------------------------
# Phase 1 contract invariance + commerce/prompt isolation
# ---------------------------------------------------------------------------


class TestPhase1InvarianceAndIsolation:
    def test_relationship_turn_evidence_contract_unchanged(self):
        import dataclasses

        from commerce.relationship_trajectory import RelationshipTurnEvidence

        names = {f.name for f in dataclasses.fields(RelationshipTurnEvidence)}
        assert names == {
            "user_sent_message",
            "user_asked_question",
            "user_answered_question",
            "user_shared_information",
            "user_continued_topic",
            "user_referenced_previous_context",
            "assistant_asked_question",
            "assistant_shared_information",
            "session_returned",
            "open_loop_continued",
            "open_loop_resolved",
            "llm_relationship_engagement",
        }

    def test_no_second_score_or_extra_fields(self):
        import inspect

        import commerce.relationship_evidence as _re

        source = inspect.getsource(_re)
        for banned in (
            "relationship_strength",
            "engagement_score",
            "intimacy",
            "sexual",
            "consent",
            "permission",
            "escalat",
            "content_interest",
            "explicit_content_request",
        ):
            assert banned not in source, f"banned relationship concept: {banned}"

    def test_no_commerce_wiring(self):
        import inspect

        import commerce.relationship_evidence as _re

        source = inspect.getsource(_re)
        for banned in (
            "decide_commerce_action",
            "commerce.decision",
            "commerce.ranking",
            "commerce.sealing",
            "commerce.execution",
            "select_commerce_response",
            "price_minor",
            "product_id",
            "offer_id",
            "build_operation_decision",
            "derive_commercial_objective",
            "plan_response_mode",
        ):
            assert banned not in source, f"banned commerce coupling: {banned}"

    def test_worker_helper_has_no_commerce_authority(self):
        import inspect

        from workers.llm_worker import _record_relationship_trajectory

        source = inspect.getsource(_record_relationship_trajectory)
        for banned in (
            "decide_commerce_action",
            "enqueue_send",
            "add_to_operator_queue",
            "plan_response_mode",
            "derive_commercial_objective",
            "from commerce.open_loop",
            "import commerce.open_loop",
            "maintain_topic",
        ):
            assert banned not in source, f"banned worker coupling: {banned}"

    def test_no_raw_text_in_persisted_block(self):
        import json

        from commerce.relationship_evidence import extract_turn_evidence
        from commerce.relationship_trajectory import (
            accumulate_turn,
            anchors_to_dict,
            neutral_anchors,
        )

        secret = "My deepest secret confession text here"
        evidence = extract_turn_evidence(
            user_message=secret,
            history=[],
            conversation_state=None,
            ltm_explicit_hit=True,
        )
        block = anchors_to_dict(accumulate_turn(neutral_anchors(T0), evidence, T0))
        assert secret not in json.dumps(block, sort_keys=True)
