"""H1 complaint triage — evidence, routing, promise pin (NEW).

Covers the limited H1 behavior change only:
(a) experience-only complaints no longer auto-handoff (technical keeps
handoff); (b) queue flags gain a triage label; (c) refund/credit/free
promises in drafts flag like other unauthorized CTAs.
"""

from commerce.complaint_triage_evidence import (
    extract_complaint_triage_evidence as triage,
)

# ── Evidence hits ────────────────────────────────────────────────────


class TestEvidenceHits:
    def test_charged_twice_is_technical(self):
        ev = triage("I was charged twice for the same video")
        assert ev.technical_payment_signal is True
        assert ev.triage_label() == "payment_technical"
        assert ev.has_payment_signal() is True
        assert ev.is_experience_only() is False

    def test_link_doesnt_work_is_technical(self):
        ev = triage("the link you sent me doesn't work")
        assert ev.technical_payment_signal is True
        assert ev.triage_label() == "payment_technical"

    def test_payment_without_delivery_is_technical(self):
        ev = triage("my payment went through but I haven't received anything")
        assert ev.technical_payment_signal is True

    def test_scam_accusation_is_claim(self):
        ev = triage("this feels like a scam, I got nothing after paying")
        assert ev.payment_claim is True
        assert ev.triage_label() == "payment_claim"
        assert ev.has_payment_signal() is True
        assert ev.is_experience_only() is False

    def test_refund_demand_is_claim(self):
        ev = triage("I want a refund, this was not worth the money")
        assert ev.payment_claim is True

    def test_free_demand_is_claim(self):
        ev = triage("this is a scam, give me free stuff")
        assert ev.payment_claim is True

    def test_you_never_send_is_claim(self):
        ev = triage("you never send me anything")
        assert ev.payment_claim is True

    def test_rude_ignoring_is_experience_only(self):
        ev = triage("you're rude and boring, you're ignoring me and this is frustrating")
        assert ev.chat_experience_complaint is True
        assert ev.technical_payment_signal is False
        assert ev.payment_claim is False
        assert ev.is_experience_only() is True
        assert ev.triage_label() == "experience"

    def test_technical_beats_experience_precedence(self):
        ev = triage("you're ignoring me and I was charged twice")
        assert ev.technical_payment_signal is True
        assert ev.chat_experience_complaint is True
        assert ev.is_experience_only() is False
        assert ev.triage_label() == "payment_technical"

    def test_neutral_input_is_empty(self):
        assert triage("hey, how are you today?").is_empty() is True
        assert triage("").is_empty() is True
        assert triage(None).is_empty() is True
        assert triage(None).triage_label() is None


# ── FP guards ────────────────────────────────────────────────────────


class TestEvidenceFPGuards:
    def test_someone_in_charge_does_not_fire(self):
        assert triage("I need to speak to someone in charge").is_empty() is True

    def test_bare_free_tier_does_not_fire(self):
        assert triage("what's included in the free tier?").is_empty() is True

    def test_legit_reassurance_is_not_scam(self):
        assert triage("is this legit?").is_empty() is True
        assert triage("can I trust you with my payment details?").is_empty() is True

    def test_blurry_without_payment_does_not_fire_technical(self):
        ev = triage("the video was blurry and cut off halfway through")
        assert ev.technical_payment_signal is False
        assert ev.payment_claim is False

    def test_attributed_quote_does_not_fire(self):
        ev = triage('my friend said "this is a scam"')
        assert ev.payment_claim is False

    def test_paid_for_value_complaint_is_not_technical(self):
        # Expectation mismatch, not a payment failure.
        ev = triage("this is not what I paid for at all")
        assert ev.technical_payment_signal is False


# ── Handoff triage (relationship layer) ──────────────────────────────


class TestHandoffTriage:
    def test_technical_complaint_still_handoffs(self):
        from commerce.relationship import (
            CommercialPressure,
            OperatorHandoffReason,
            RelationshipState,
            check_operator_handoff,
        )

        ok, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            has_complaint=True,
            complaint_is_experience_only=False,
        )
        assert ok is True
        assert reason == OperatorHandoffReason.COMPLAINT

    def test_legacy_default_still_handoffs(self):
        from commerce.relationship import (
            CommercialPressure,
            OperatorHandoffReason,
            RelationshipState,
            check_operator_handoff,
        )

        ok, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            has_complaint=True,
        )
        assert ok is True
        assert reason == OperatorHandoffReason.COMPLAINT

    def test_experience_only_does_not_handoff_on_complaint(self):
        from commerce.relationship import (
            CommercialPressure,
            RelationshipState,
            check_operator_handoff,
        )

        ok, _ = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            has_complaint=True,
            complaint_is_experience_only=True,
        )
        assert ok is False

    def test_experience_only_does_not_handoff_on_sentiment(self):
        from commerce.relationship import (
            CommercialPressure,
            RelationshipState,
            check_operator_handoff,
        )

        ok, _ = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            negative_sentiment=0.85,
            complaint_is_experience_only=True,
        )
        assert ok is False

    def test_high_sentiment_default_still_handoffs(self):
        from commerce.relationship import (
            CommercialPressure,
            OperatorHandoffReason,
            RelationshipState,
            check_operator_handoff,
        )

        ok, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            negative_sentiment=0.85,
        )
        assert ok is True
        assert reason == OperatorHandoffReason.COMPLAINT

    def test_payment_claim_still_handoffs(self):
        from commerce.relationship import (
            CommercialPressure,
            OperatorHandoffReason,
            RelationshipState,
            check_operator_handoff,
        )

        # payment_claim is a payment signal: never experience-only.
        assert triage("this is a scam, give me free stuff").is_experience_only() is False
        ok, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            has_complaint=True,
            complaint_is_experience_only=False,
        )
        assert ok is True
        assert reason == OperatorHandoffReason.COMPLAINT

    def test_custom_request_intact_under_exemption(self):
        from commerce.relationship import (
            CommercialPressure,
            OperatorHandoffReason,
            RelationshipState,
            check_operator_handoff,
        )

        ok, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            has_custom_request=True,
            complaint_is_experience_only=True,
        )
        assert ok is True
        assert reason == OperatorHandoffReason.CUSTOM_REQUEST


# ── RECOVER / sincerity advisory for experience text ─────────────────


def _snap():
    return {
        "familiarity": "familiar",
        "engagement": "steady",
        "reciprocity": "balanced",
        "continuity": "anchored",
        "trend": "stable",
    }


class TestRecoverAdvisoryForExperience:
    def test_annoyed_experience_message_advises_recover(self):
        from commerce.conversation_strategy import select_conversational_strategy

        strategy = select_conversational_strategy(
            snapshot=_snap(),
            persona={"emotional_state": "annoyed", "sincerity_required": False},
        )
        assert strategy is not None
        assert strategy.move == "RECOVER"
        assert strategy.question_policy == "NO_QUESTION"

    def test_serious_message_advises_sincerity_acknowledge(self):
        from commerce.conversation_strategy import select_conversational_strategy

        strategy = select_conversational_strategy(
            snapshot=_snap(),
            persona={"emotional_state": "serious", "sincerity_required": True},
        )
        assert strategy is not None
        assert strategy.move == "ACKNOWLEDGE"
        assert "SINCERITY_REQUIRED" in strategy.reason_codes


# ── Freebie + complaint suppression preserved ────────────────────────


class TestFreebieSuppressionPreserved:
    def test_freebie_complaint_suppresses_commerce(self):
        from commerce.decision import decide_commerce_action
        from commerce.models import PolicyDecision
        from commerce.signals import CommerceSignals, signals_to_context

        s = CommerceSignals(
            purchase_intent=0.9,
            content_interest=0.8,
            relationship_engagement=0.7,
            price_interest=0.2,
            explicit_purchase_request=False,
            explicit_content_request=False,
            requested_price=None,
            declined_recent_offer=False,
            negative_sentiment=0.4,
            confidence=0.9,
            evidence=["give me free stuff"],
            model_uncertainty=0.1,
            primary_intent="complaint",
            intent_tags=["purchase_intent"],
            negative_intent_tags=["complaint"],
            fan_asks_question=False,
            asks_for_free_content=True,
        )
        ctx = signals_to_context(
            s,
            user_id=1,
            creator_id=1,
            eligibility=PolicyDecision(allowed=True),
            relationship_state="warm",
            has_relevant_product=True,
            creator_sales_enabled=True,
        )
        assert ctx.has_commercial_intent is False
        decision = decide_commerce_action(ctx)
        assert decision.action.value in ("relationship_building", "no_offer")


# ── Draft-promise pin (CTA family) ───────────────────────────────────


class TestPromisePin:
    def test_refund_promise_flags(self):
        from commerce.commercial_cta import detect_unauthorized_commercial_cta

        hit, name = detect_unauthorized_commercial_cta(
            "I'm sorry about that, I'll give you a refund"
        )
        assert hit is True
        assert name == "refund_promise"

    def test_credit_promise_flags(self):
        from commerce.commercial_cta import detect_unauthorized_commercial_cta

        hit, name = detect_unauthorized_commercial_cta("I'll credit your account right away")
        assert hit is True
        assert name == "credit_promise"

    def test_credit_card_is_not_a_promise(self):
        from commerce.commercial_cta import detect_unauthorized_commercial_cta

        hit, _ = detect_unauthorized_commercial_cta("we'll give you a credit card statement copy")
        assert hit is False

    def test_free_content_promise_flags(self):
        from commerce.commercial_cta import detect_unauthorized_commercial_cta

        hit, name = detect_unauthorized_commercial_cta("here's a free video on me tonight")
        assert hit is True
        assert name == "free_content_promise"

    def test_authorized_commerce_exempt(self):
        from commerce.commercial_cta import detect_unauthorized_commercial_cta

        hit, _ = detect_unauthorized_commercial_cta("I'll give you a refund", is_authorized=True)
        assert hit is False

    def test_benign_free_tier_mention_passes(self):
        from commerce.commercial_cta import detect_unauthorized_commercial_cta

        hit, _ = detect_unauthorized_commercial_cta("the free tier includes early access")
        assert hit is False

    def test_existing_cta_still_fires(self):
        from commerce.commercial_cta import detect_unauthorized_commercial_cta

        hit, name = detect_unauthorized_commercial_cta("want to see my new set?")
        assert hit is True
        assert name == "want_to_see"


# ── Purity: evidence module imports ──────────────────────────────────


class TestEvidencePurity:
    def test_module_imports_only_allowed_stdlib(self):
        import pathlib

        src = pathlib.Path("commerce/complaint_triage_evidence.py").read_text(encoding="utf-8")
        imports = sorted(
            line.strip()
            for line in src.splitlines()
            if line.strip().startswith(("import ", "from "))
        )
        assert imports, "expected stdlib imports"
        for line in imports:
            assert line.startswith(
                (
                    "import re",
                    "from dataclasses",
                    "from typing",
                    "from collections.abc",
                    "from __future__",
                )
            ), line
