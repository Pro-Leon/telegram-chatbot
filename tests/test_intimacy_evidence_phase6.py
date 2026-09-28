"""Phase 6 intimacy evidence tests (IMPLEMENT -> VERIFY).

Pins the deterministic extractor in
``commerce/intimacy_evidence.py`` plus the idempotent accumulator:

* per-category lexical evidence (romantic/playful/emotional/sexual)
* word boundaries; ``photo`` never becomes ``hot``
* ambiguous-compliment handling (hot/beautiful/gorgeous/babe neutral)
* single soft token never sets sexual_conversation_signal
* history alone manufactures nothing; continuity needs current + prior
* LLM-only signal cannot move a band
* content-interest / explicit-content-request isolation
* deduplication; provenance; creator isolation; fail-open

Conventions follow the Phase 1-5 suites: deterministic unit tests
only, no live LLM/DB/Redis (accumulation via mocked atomic-mutate).
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from commerce.intimacy_evidence import (  # noqa: E402
    INTIMACY_PROCESSED_IDS_FIELD,
    INTIMACY_TRAJECTORY_KEY,
    accumulate_intimacy_turn_idempotent,
    extract_intimacy_evidence,
)
from commerce.intimacy_trajectory import (  # noqa: E402
    IntimacyTurnEvidence,
    accumulate_intimacy_turn,
)


def _ev(text: str, **kwargs) -> IntimacyTurnEvidence:
    return extract_intimacy_evidence(user_message=text, **kwargs)


class TestCategoryEvidence:
    def test_romantic_signal(self):
        assert _ev("I miss you so much").romantic_signal is True
        assert _ev("thinking of you tonight").romantic_signal is True

    def test_playful_signal(self):
        assert _ev("haha you are ridiculous").playful_signal is True
        assert _ev("stop teasing me").playful_signal is True

    def test_emotional_signal(self):
        assert _ev("I trust you with this").emotional_signal is True
        assert _ev("I feel safe talking to you").emotional_signal is True

    def test_sexual_strong_single_hit(self):
        assert _ev("kiss me").sexual_conversation_signal is True
        assert _ev("I want to make love").sexual_conversation_signal is True

    def test_sexual_soft_requires_two_distinct(self):
        assert _ev("you are sexy").sexual_conversation_signal is False
        assert _ev("feeling horny today").sexual_conversation_signal is False
        assert _ev("you are so sexy and horny").sexual_conversation_signal is True
        # Same token repeated is one distinct token -> still False.
        assert _ev("sexy sexy sexy").sexual_conversation_signal is False

    def test_neutral_conversation_is_neutral(self):
        ev = _ev("what time is the game tonight?")
        assert ev == IntimacyTurnEvidence()
        assert ev.dimension_signals() == 0


class TestFalsePositives:
    def test_ambiguous_compliments_neutral(self):
        for text in ("you are hot", "you are beautiful", "gorgeous pic", "hey babe"):
            ev = _ev(text)
            assert ev.dimension_signals() == 0, text

    def test_photo_never_becomes_hot(self):
        # Substring guard: "photo" contains "hot" but must not match.
        for text in ("send me a photo", "nice photography", "hotdog for dinner"):
            ev = _ev(text)
            assert ev.dimension_signals() == 0, text

    def test_word_boundaries(self):
        # "kissing" / "kissed" are not the "kiss me" proposition.
        assert _ev("the baby is kissing the dog").sexual_conversation_signal is False

    def test_unrelated_history_manufactures_nothing(self):
        history = [
            {"direction": "inbound", "content": "kiss me"},
            {"direction": "outbound", "content": "miss you too"},
        ]
        ev = _ev("what time is the game tonight?", history=history)
        assert ev.dimension_signals() == 0
        assert ev.intimate_continuity_signal is False
        assert ev.prior_intimate_context_reference is False


class TestNegation:
    def test_negated_strong_propositions(self):
        for text in (
            "don't kiss me",
            "do not kiss me",
            "never kiss me",
            "don't touch me",
            "do not touch me",
            "never touch me",
        ):
            ev = _ev(text)
            assert ev.sexual_conversation_signal is False, text
            assert ev.dimension_signals() == 0, text

    def test_negation_variants(self):
        assert _ev("never, ever kiss me").sexual_conversation_signal is False
        assert _ev("please don't kiss me").sexual_conversation_signal is False
        assert _ev("DON'T KISS ME").sexual_conversation_signal is False

    def test_positive_controls_still_fire(self):
        for text in (
            "kiss me",
            "please kiss me",
            "I want to make love",
            "oh please, kiss me",
            "you are so sexy and horny",
        ):
            assert _ev(text).sexual_conversation_signal is True, text

    def test_unrelated_negation_does_not_suppress(self):
        # Clause boundary: "not" governs "tired", not the proposition.
        assert _ev("I am not tired, kiss me").sexual_conversation_signal is True

    def test_negated_soft_token_does_not_contribute(self):
        # "sexy" governed by "not" leaves only "horny": single soft
        # token behavior (False) is preserved, not weakened.
        assert _ev("not sexy but horny").sexual_conversation_signal is False
        assert _ev("sexy and not horny").sexual_conversation_signal is False
        # Two unnegated soft tokens still corroborate across a
        # contrast boundary.
        assert _ev("not sexy but horny and naughty").sexual_conversation_signal is True

    def test_negation_does_not_create_evidence(self):
        ev = _ev("don't kiss me")
        assert ev == IntimacyTurnEvidence()
        anchors = accumulate_intimacy_turn(None, ev)
        assert anchors.sexual_conversation_count == 0
        assert anchors.observation_count == 1


class TestContinuity:
    def _history(self):
        return [{"direction": "inbound", "content": "I miss you"}]

    def test_current_plus_prior_sets_continuity(self):
        ev = _ev("I miss you too", history=self._history())
        assert ev.intimate_continuity_signal is True
        assert ev.prior_intimate_context_reference is True

    def test_unrelated_cooccurrence_is_not_reference(self):
        # "I miss you" history + playful-but-unlinked present:
        # continuity (co-occurrence counter input) may hold, but the
        # reference requires substantive token overlap ("miss" shared
        # here is absent: {lol, funny} vs {miss}).
        ev = _ev("lol that is so funny", history=self._history())
        assert ev.playful_signal is True
        assert ev.prior_intimate_context_reference is False

    def test_linked_reference_shares_substantive_tokens(self):
        ev = _ev("kiss me tomorrow", history=[{"direction": "inbound", "content": "kiss me tonight"}])
        assert ev.sexual_conversation_signal is True
        assert ev.prior_intimate_context_reference is True

    def test_stopwords_alone_do_not_link(self):
        # Current playful signal shares only stopwords ("you", "too")
        # with the prior intimate text: without stopword filtering this
        # would link; with it, reference stays False.
        ev = _ev("haha you too", history=[{"direction": "inbound", "content": "I miss you"}])
        assert ev.playful_signal is True
        assert ev.prior_intimate_context_reference is False

    def test_current_without_prior_no_continuity(self):
        ev = _ev("I miss you", history=[{"direction": "inbound", "content": "hi"}])
        assert ev.intimate_continuity_signal is False
        assert ev.prior_intimate_context_reference is False

    def test_user_initiation_and_assistant_continuation(self):
        ev = _ev("I miss you", history=self._history())
        assert ev.user_initiated_intimacy is True
        assert ev.assistant_intimacy_continuation is False
        ev2 = _ev(
            "I miss you",
            history=self._history(),
            assistant_texts=["I miss you too, always"],
        )
        assert ev2.assistant_intimacy_continuation is True

    def test_current_intimate_topic(self):
        ev = _ev(
            "tell me more",
            conversation_state={"current_topic": "miss you", "open_threads": ()},
        )
        assert ev.current_intimate_topic is True
        ev2 = _ev(
            "tell me more",
            conversation_state={"current_topic": "movies", "open_threads": ("movies",)},
        )
        assert ev2.current_intimate_topic is False


class TestLLMIsolation:
    def test_llm_only_signal_moves_nothing(self):
        ev = extract_intimacy_evidence(
            user_message="what time is the game?",
            llm_content_interest=0.95,
            llm_explicit_content=True,
        )
        assert ev.dimension_signals() == 0
        anchors = accumulate_intimacy_turn(None, ev)
        assert anchors.observation_count == 1
        assert anchors.romantic_count == 0
        assert anchors.sexual_conversation_count == 0
        assert set(anchors.bands.values()) == {"unknown"}

    def test_content_interest_alone_not_sexual(self):
        ev = extract_intimacy_evidence(
            user_message="do you have new content?",
            llm_content_interest=0.9,
        )
        assert ev.sexual_conversation_signal is False

    def test_explicit_content_alone_not_intimacy(self):
        # explicit_content_request is commerce/content-request plumbing;
        # without deterministic current-turn evidence it creates nothing.
        ev = extract_intimacy_evidence(
            user_message="send me the video",
            llm_explicit_content=True,
        )
        assert ev.sexual_conversation_signal is False
        assert ev.dimension_signals() == 0

    def test_explicit_content_corroborates_deterministic_only(self):
        ev = extract_intimacy_evidence(
            user_message="kiss me",
            llm_explicit_content=True,
        )
        assert ev.sexual_conversation_signal is True
        anchors = accumulate_intimacy_turn(None, ev)
        assert anchors.corroborated_observations == 1

    def test_invalid_llm_values_rejected(self):
        ev = extract_intimacy_evidence(
            user_message="hi",
            llm_content_interest="high",  # type: ignore[arg-type]
        )
        assert ev.llm_content_interest is None
        ev2 = extract_intimacy_evidence(user_message="hi", llm_content_interest=7.5)
        assert ev2.llm_content_interest is None

    def test_signals_object_accepted(self):
        from commerce.signals import CommerceSignals

        signals = CommerceSignals(
            purchase_intent=0.0,
            content_interest=0.9,
            relationship_engagement=0.0,
            price_interest=0.0,
            explicit_purchase_request=False,
            explicit_content_request=True,
            confidence=0.8,
            evidence=[],
        )
        ev = extract_intimacy_evidence(user_message="hello there", llm_signals=signals)
        assert ev.llm_content_interest == 0.9
        assert ev.llm_explicit_content is True
        assert ev.dimension_signals() == 0


class TestDedupProvenanceFailOpen:
    def test_repeated_hits_count_once_per_turn(self):
        ev = _ev("sexy and horny, so sexy and horny")
        assert ev.sexual_conversation_signal is True
        anchors = accumulate_intimacy_turn(None, ev)
        assert anchors.sexual_conversation_count == 1

    def test_provenance_explicit_for_direct_signal(self):
        anchors = accumulate_intimacy_turn(None, _ev("kiss me"))
        assert anchors.last_provenance == 1.0

    def test_fail_open_neutral(self):
        assert extract_intimacy_evidence(user_message=None) == IntimacyTurnEvidence()
        assert extract_intimacy_evidence(user_message=42) == IntimacyTurnEvidence()
        assert extract_intimacy_evidence(user_message="hi", history="garbage").dimension_signals() == 0  # type: ignore[arg-type]

    def test_reason_token_bounded(self):
        token = _ev("I miss you, kiss me").reason_token()
        assert token.startswith("accumulated:")
        assert "miss you" not in token


class TestIdempotentAccumulation:
    @pytest.mark.asyncio
    async def test_exactly_once_per_generation(self):
        from unittest.mock import patch

        store: dict = {}

        async def _fake_mutate(uid: int, fn) -> bool:
            import copy

            facts = store.setdefault(uid, {})
            working = copy.deepcopy(facts)
            changed = fn(working)
            if changed:
                store[uid] = working
            return changed

        with patch(
            "db.postgres.mutate_user_profile_atomically", side_effect=_fake_mutate
        ):
            ev = IntimacyTurnEvidence(romantic_signal=True, user_initiated_intimacy=True)
            first, did_first = await accumulate_intimacy_turn_idempotent(
                user_id=1, creator_id=7, generation_id="gen-1", evidence=ev
            )
            assert did_first is True
            assert first is not None and first.romantic_count == 1
            second, did_second = await accumulate_intimacy_turn_idempotent(
                user_id=1, creator_id=7, generation_id="gen-1", evidence=ev
            )
            assert did_second is False
            assert second is not None and second.romantic_count == 1
            # Marker bounded + creator-scoped.
            block = store[1][INTIMACY_TRAJECTORY_KEY]["7"]
            assert block[INTIMACY_PROCESSED_IDS_FIELD] == ["gen-1"]

    @pytest.mark.asyncio
    async def test_bad_inputs_fail_open(self):
        from unittest.mock import patch

        with patch(
            "db.postgres.mutate_user_profile_atomically",
            side_effect=AssertionError("must not persist"),
        ):
            anchors, did = await accumulate_intimacy_turn_idempotent(
                user_id=1, creator_id=7, generation_id="",
                evidence=IntimacyTurnEvidence(),
            )
            assert (anchors, did) == (None, False)
            anchors, did = await accumulate_intimacy_turn_idempotent(
                user_id="bad", creator_id="worse", generation_id="g",  # type: ignore[arg-type]
                evidence=IntimacyTurnEvidence(),  # type: ignore[arg-type]
            )
            assert (anchors, did) == (None, False)

    @pytest.mark.asyncio
    async def test_markers_isolated_per_creator(self):
        from unittest.mock import patch

        store: dict = {}

        async def _fake_mutate(uid: int, fn) -> bool:
            import copy

            facts = store.setdefault(uid, {})
            working = copy.deepcopy(facts)
            changed = fn(working)
            if changed:
                store[uid] = working
            return changed

        with patch(
            "db.postgres.mutate_user_profile_atomically", side_effect=_fake_mutate
        ):
            ev = IntimacyTurnEvidence(romantic_signal=True, user_initiated_intimacy=True)
            _, did_a = await accumulate_intimacy_turn_idempotent(
                user_id=1, creator_id=7, generation_id="gen-x", evidence=ev
            )
            _, did_b = await accumulate_intimacy_turn_idempotent(
                user_id=1, creator_id=9, generation_id="gen-x", evidence=ev
            )
            assert did_a is True and did_b is True
            by_creator = store[1][INTIMACY_TRAJECTORY_KEY]
            assert by_creator["7"]["romantic_count"] == 1
            assert by_creator["9"]["romantic_count"] == 1
