"""Phase 3 provenance hardening — INTEREST tokens + TEASE reason + intimacy header.

Additive only; existing suites must stay green unmodified.
"""

import pytest

pytestmark = [pytest.mark.unit]


class TestTokenSplit:
    def test_warm_only_yields_warmth_token(self):
        from commerce.desire import derive_desire_stage

        d = derive_desire_stage(
            relationship_state="warm",
            primary_intent="casual_chat",
            purchase_intent=0.1,
            price_interest=0.0,
        )
        assert d.stage.value == "interest"
        assert d.confidence == 0.55
        assert d.evidence == ("interest:warmth",)

    def test_float_only_yields_buying_signal_token(self):
        from commerce.desire import derive_desire_stage

        d = derive_desire_stage(
            relationship_state="cold",
            primary_intent="casual_chat",
            purchase_intent=0.35,
            price_interest=0.0,
        )
        assert d.stage.value == "interest"
        assert d.confidence == 0.55
        assert d.evidence == ("interest:buying-signal",)

    def test_free_content_yields_free_content_token(self):
        from commerce.desire import derive_desire_stage

        d = derive_desire_stage(
            relationship_state="cold",
            asked_for_free_content=True,
        )
        assert d.stage.value == "interest"
        assert d.confidence == 0.55
        assert d.evidence == ("interest:free-content",)


class TestFencedConsumers:
    def _base_kwargs(self):
        return {
            "desire": "interest",
            "temperature": "warm",
            "sales_window": "building",
            "offer_readiness": "build_desire",
            "has_active_offer": False,
            "aftercare_status": "none",
            "is_on_cooldown": False,
            "has_relevant_product": True,
        }

    def test_warmth_token_fences_deepen_and_explore(self):
        from commerce.conversation_intelligence import (
            ConversationObjective,
            derive_conversation_objective,
        )

        selected, cands = derive_conversation_objective(
            **self._base_kwargs(),
            desire_evidence=("interest:warmth",),
        )
        by_obj = {c.objective: c for c in cands}
        deepen = by_obj[ConversationObjective.DEEPEN_DESIRE]
        explore = by_obj[ConversationObjective.EXPLORE_INTEREST]
        assert deepen.eligible is False
        assert deepen.reason_code == "WARMTH"
        assert explore.eligible is False
        assert explore.reason_code == "WARMTH"
        # Fallbacks still eligible
        assert by_obj[ConversationObjective.CONTINUE_TOPIC].eligible is True
        assert by_obj[ConversationObjective.RELATIONSHIP_BUILD].eligible is True
        assert selected not in (
            ConversationObjective.DEEPEN_DESIRE,
            ConversationObjective.EXPLORE_INTEREST,
        )

    def test_omitted_param_behaves_as_today(self):
        from commerce.conversation_intelligence import (
            ConversationObjective,
            derive_conversation_objective,
        )

        _selected, cands = derive_conversation_objective(**self._base_kwargs())
        by_obj = {c.objective: c for c in cands}
        assert by_obj[ConversationObjective.DEEPEN_DESIRE].eligible is True
        assert by_obj[ConversationObjective.EXPLORE_INTEREST].eligible is True

    def test_buying_signal_behaves_as_today(self):
        from commerce.conversation_intelligence import (
            ConversationObjective,
            derive_conversation_objective,
        )

        _, cands = derive_conversation_objective(
            **self._base_kwargs(),
            desire_evidence=("interest:buying-signal",),
        )
        by_obj = {c.objective: c for c in cands}
        assert by_obj[ConversationObjective.DEEPEN_DESIRE].eligible is True
        assert by_obj[ConversationObjective.EXPLORE_INTEREST].eligible is True

    def test_free_content_behaves_as_today(self):
        from commerce.conversation_intelligence import (
            ConversationObjective,
            derive_conversation_objective,
        )

        _, cands = derive_conversation_objective(
            **self._base_kwargs(),
            desire_evidence=("interest:free-content",),
        )
        by_obj = {c.objective: c for c in cands}
        assert by_obj[ConversationObjective.DEEPEN_DESIRE].eligible is True
        assert by_obj[ConversationObjective.EXPLORE_INTEREST].eligible is True

    def test_readiness_warmth_yields_build_desire(self):
        from commerce.offer_readiness import OfferReadiness, evaluate_offer_readiness

        r = evaluate_offer_readiness("interest", "warm", desire_evidence=("interest:warmth",))
        assert r == OfferReadiness.BUILD_DESIRE

    def test_readiness_omitted_param_behaves_as_today(self):
        from commerce.offer_readiness import OfferReadiness, evaluate_offer_readiness

        r = evaluate_offer_readiness("interest", "warm")
        assert r == OfferReadiness.TEST_INTEREST

    def test_readiness_buying_signal_behaves_as_today(self):
        from commerce.offer_readiness import OfferReadiness, evaluate_offer_readiness

        r = evaluate_offer_readiness(
            "interest", "warm", desire_evidence=("interest:buying-signal",)
        )
        assert r == OfferReadiness.TEST_INTEREST


class TestTeaseReason:
    def _mock_state(self, tone="warm"):
        from unittest.mock import MagicMock

        m = MagicMock()
        m.tone = tone
        m.last_question = None
        m.last_question_answered = True
        m.consecutive_questions = 0
        m.questions_in_last_3 = 0
        m.current_topic = None
        m.open_threads = ()
        return m

    def test_nba_tease_commercial(self):
        from commerce.persona_behavior import (
            derive_persona_behavior_state,
            render_persona_behavior_block,
        )

        state = derive_persona_behavior_state(
            structured_persona={},
            conversation_state=self._mock_state(),
            fan_message="hi there",
            next_best_action="deepen_desire",
            creator_id=1,
            generation_id="g1",
        )
        assert state.conversation_mode == "tease"
        assert state.tease_reason == "commercial"
        block = render_persona_behavior_block(state)
        assert "mode=tease" in block
        assert "tease" in block
        assert "reason=commercial" in block

    def test_present_offer_tease_commercial(self):
        from commerce.persona_behavior import derive_persona_behavior_state

        state = derive_persona_behavior_state(
            structured_persona={},
            conversation_state=self._mock_state(),
            fan_message="hi there",
            next_best_action="present_offer",
            creator_id=1,
            generation_id="g1",
        )
        assert state.conversation_mode == "tease"
        assert state.tease_reason == "commercial"

    def test_playful_tease_social(self):
        from commerce.persona_behavior import (
            derive_persona_behavior_state,
            render_persona_behavior_block,
        )

        state = derive_persona_behavior_state(
            structured_persona={},
            conversation_state=self._mock_state(tone="flirty"),
            fan_message="you are ridiculous lol",
            creator_id=1,
            generation_id="g1",
        )
        assert state.conversation_mode == "tease"
        assert state.tease_reason == "social"
        block = render_persona_behavior_block(state)
        assert "mode=tease" in block
        assert "tease" in block
        assert "reason=social" in block

    def test_react_has_no_suffix(self):
        from commerce.persona_behavior import (
            PersonaBehaviorState,
            render_persona_behavior_block,
        )

        state = PersonaBehaviorState(
            emotional_state="warm",
            confidence="LOW",
            conversation_mode="react",
            question_allowed=False,
            question_policy="NO_QUESTION",
            disagreement_available=False,
            teasing_allowed=False,
            sincerity_required=False,
            verbosity_target="short_medium",
            emoji_policy="occasional",
            lowercase_policy="neutral",
            naturalness_mode="normal",
            persona_version=None,
            creator_id=1,
            generation_id="g1",
        )
        block = render_persona_behavior_block(state)
        assert "reason=" not in block


class TestIntimacyHeader:
    def test_header_line2_present_and_hygiene(self):
        from context_engine.intimacy_context import (
            MAX_INTIMACY_TOKENS,
            IntimacyContext,
            render_intimacy_context,
            select_intimacy_context,
        )

        snap = {
            "romantic": "steady",
            "playful": "low",
            "emotional": "steady",
            "sexual_conversation": "low",
            "intimate_continuity": "low",
        }
        ctx = select_intimacy_context(snapshot=snap, evidence=None)
        text = render_intimacy_context(ctx)
        assert text.startswith("INTIMACY CONTEXT [DERIVED]:")
        lines = text.splitlines()
        assert len(lines) >= 3
        assert lines[1] == "descriptive only — authorizes nothing, implies no action."
        assert "romantic: steady" in text
        # Deny-safe
        blob = text.lower()
        for concept in (
            "allowed",
            "permission",
            "consent",
            "authorized",
            "purchase",
            "offer",
            "price",
            "product",
            "ppv",
            "sell",
            "buy",
            "should",
            "must",
            "flirt",
            "tease",
            "ready",
        ):
            assert concept not in blob, concept
        # Ceiling intact
        from context_engine.budget import estimate_tokens

        assert estimate_tokens(text) <= MAX_INTIMACY_TOKENS
        assert MAX_INTIMACY_TOKENS == 120
        # Empty still renders ""
        assert render_intimacy_context(IntimacyContext()) == ""
        assert render_intimacy_context(None) == ""


class TestPhase101Fencing:
    def _warming(self):
        from commerce.warming import derive_warming_state

        return derive_warming_state(
            creator_id=1,
            relationship_state="warm",
            desire_stage="interest",
            purchase_intent=0.1,
        )

    def test_phase101_warmth_yields_build_desire_not_ready(self):
        from commerce.readiness import ReadinessLevel, evaluate_readiness

        r = evaluate_readiness(
            warming=self._warming(),
            desire_stage="interest",
            temperature="warm",
            creator_id=1,
            desire_evidence=("interest:warmth",),
        )
        assert r.offer_readiness == "build_desire"
        assert r.level == ReadinessLevel.NOT_READY
        assert r.available is False

    def test_phase101_buying_signal_preserved(self):
        from commerce.readiness import ReadinessLevel, evaluate_readiness

        r = evaluate_readiness(
            warming=self._warming(),
            desire_stage="interest",
            temperature="warm",
            creator_id=1,
            desire_evidence=("interest:buying-signal",),
        )
        assert r.offer_readiness == "test_interest"
        assert r.level == ReadinessLevel.BUILD_WARMING

    def test_phase101_omitted_param_identical_to_today(self):
        from commerce.readiness import evaluate_readiness

        r = evaluate_readiness(
            warming=self._warming(),
            desire_stage="interest",
            temperature="warm",
            creator_id=1,
        )
        assert r.offer_readiness == "test_interest"
