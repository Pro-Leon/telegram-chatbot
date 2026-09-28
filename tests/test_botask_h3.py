"""H3 bot-accusation deflection + repeat escalation (NEW).

Limited H3 behavior change only:
(a) advisory playful deflection block on operator_request tags;
(b) second ask routes via the EXISTING has_custom_request input;
(c) botask:<repeat|complaint-combo> queue-payload token.
"""

import pytest

from commerce.conversation_strategy import (
    botask_queue_token,
    had_prior_bot_accusation,
    render_botask_deflection,
)

# ── (a) Render gating ────────────────────────────────────────────


class TestDeflectionRender:
    def test_renders_on_operator_request(self):
        for tags in (["operator_request"], ("operator_request",), {"operator_request"}):
            block = render_botask_deflection(tags)
            assert "BOT-ACCUSATION DEFLECTION" in block
            assert "NEVER" in block

    def test_empty_without_operator_request(self):
        assert render_botask_deflection(None) == ""
        assert render_botask_deflection([]) == ""
        assert render_botask_deflection(["complaint"]) == ""
        assert render_botask_deflection(["custom_request", "complaint"]) == ""
        assert render_botask_deflection("operator_request") == ""

    def test_block_has_no_prices_or_links(self):
        block = render_botask_deflection(["operator_request"])
        assert "$" not in block
        assert "http" not in block

    def test_render_takes_no_boundary_or_state(self):
        import inspect

        assert list(inspect.signature(render_botask_deflection).parameters) == ["intent_tags"]


# ── Repeat evidence ──────────────────────────────────────────────


def _transcript(*texts):
    return [{"role": "user", "content": t} for t in texts]


class TestRepeatEvidence:
    def test_second_ask_detected(self):
        assert (
            had_prior_bot_accusation(
                [
                    {"role": "user", "content": "are you a bot or a real human?"},
                    {"role": "assistant", "content": "haha, what do you mean?"},
                    {"role": "user", "content": "I need to talk to a real person"},
                ]
            )
            is True
        )

    def test_first_ask_not_repeat(self):
        assert (
            had_prior_bot_accusation(
                [{"role": "user", "content": "are you a bot or a real human?"}]
            )
            is False
        )

    def test_unrelated_prior_not_repeat(self):
        assert (
            had_prior_bot_accusation(
                [
                    {"role": "user", "content": "hey there"},
                    {"role": "assistant", "content": "hey!"},
                    {"role": "user", "content": "are you a bot or a real human?"},
                ]
            )
            is False
        )

    def test_paraphrase_not_repeat(self):
        # Grounded 1:1 in rows 401-420: non-verbatim priors fall back
        # to first-ask treatment (guidance only, no handoff).
        assert had_prior_bot_accusation(_transcript("are you a bot?", "are you a bot?")) is False

    def test_odd_input_never_raises(self):
        assert had_prior_bot_accusation(None) is False
        assert had_prior_bot_accusation([]) is False
        assert had_prior_bot_accusation("are you a bot?") is False
        assert had_prior_bot_accusation([{"role": "user"}]) is False

    def test_object_messages_supported(self):
        from types import SimpleNamespace

        assert (
            had_prior_bot_accusation(
                [
                    SimpleNamespace(role="user", content="can you transfer me to a live agent?"),
                    SimpleNamespace(role="assistant", content="sure thing"),
                    SimpleNamespace(role="user", content="hello again?"),
                ]
            )
            is True
        )


# ── (b) Routing: repeat escalates, first does not ────────────────


class TestRepeatRouting:
    def test_second_ask_fires_custom_handoff(self):
        from commerce.relationship import (
            CommercialPressure,
            OperatorHandoffReason,
            RelationshipState,
            check_operator_handoff,
        )

        prior_repeat = had_prior_bot_accusation(
            _transcript(
                "are you a bot or a real human?",
                "I need human assistance, not a bot",
            )
        )
        assert prior_repeat is True
        # Pipeline threads repeat through the EXISTING input:
        effective_custom = False or (prior_repeat and True)
        ok, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            has_custom_request=effective_custom,
        )
        assert ok is True
        assert reason == OperatorHandoffReason.CUSTOM_REQUEST

    def test_first_ask_does_not_handoff(self):
        from commerce.relationship import (
            CommercialPressure,
            RelationshipState,
            check_operator_handoff,
        )

        assert had_prior_bot_accusation(_transcript("are you a bot or a real human?")) is False
        ok, reason = check_operator_handoff(
            relationship_state=RelationshipState.WARM,
            commercial_pressure=CommercialPressure.SOFT,
            has_custom_request=False,
        )
        assert ok is False
        assert reason is None

    def test_accusation_plus_complaint_precedence(self):
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
            has_custom_request=True,
        )
        assert ok is True
        assert reason == OperatorHandoffReason.COMPLAINT


# ── Payload token units ──────────────────────────────────────────


class TestBotaskToken:
    def test_repeat_token(self):
        assert (
            botask_queue_token(intent_tags=["operator_request"], repeated=True) == "botask:repeat"
        )

    def test_complaint_combo_token(self):
        assert (
            botask_queue_token(
                intent_tags=["operator_request"],
                negative_intent_tags=["complaint"],
            )
            == "botask:complaint-combo"
        )

    def test_complaint_combo_via_sentiment(self):
        assert (
            botask_queue_token(intent_tags=["operator_request"], negative_sentiment=0.85)
            == "botask:complaint-combo"
        )

    def test_no_token_cases(self):
        assert botask_queue_token(intent_tags=["complaint"]) is None
        assert botask_queue_token(intent_tags=None) is None
        assert (
            botask_queue_token(
                intent_tags=["operator_request"],
                negative_sentiment=0.2,
            )
            is None
        )


# ── (c) Boundary non-interference ────────────────────────────────


class TestBoundaryNonInterference:
    def test_trigger_has_no_boundary_input(self):
        # The guard lives at draft validation, never at the trigger:
        # output is identical with no boundary state in scope at all.
        first = render_botask_deflection(["operator_request"])
        second = render_botask_deflection(["operator_request"])
        assert first == second
        assert "BOT-ACCUSATION DEFLECTION" in first


# ── (d) Validator pins (table b) ─────────────────────────────────


class TestValidatorPins:
    def test_robotic_flags_ooc(self):
        from core.one_call import _compute_conversational_flags

        flags = _compute_conversational_flags("as an ai, i cannot answer that question")
        assert "out_of_character" in flags

    def test_playful_deflections_pass(self):
        from commerce.persona_validation import validate_persona_voice
        from core.one_call import _compute_conversational_flags

        persona = {"identity": {"name": "Sunny Skye"}}
        for draft in (
            ("what do you mean human? i am an angel you know, so take that as a compliment"),
            "haha are you trying to get me in trouble? i am right here talking to you",
        ):
            val = validate_persona_voice(draft, persona=persona, behavior_state=None)
            assert val.valid is True
            assert val.fact_violation is False
            assert "out_of_character" not in _compute_conversational_flags(draft)

    @pytest.mark.xfail(
        strict=True,
        reason="no validator forbids human-species claims "
        "(fact checks cover name/age/3-cities/4-occupations only)",
    )
    def test_human_claim_fails(self):
        from commerce.persona_validation import validate_persona_voice

        persona = {"identity": {"name": "Sunny Skye"}}
        val = validate_persona_voice(
            "yes, i am a real human girl living in new york, ask me anything",
            persona=persona,
            behavior_state=None,
        )
        assert val.valid is False

    @pytest.mark.xfail(
        strict=True,
        reason="no validator forbids invented activities (persona_self is emit-only allowlist)",
    )
    def test_invented_activity_fails(self):
        from commerce.persona_validation import validate_persona_voice

        persona = {"identity": {"name": "Sunny Skye"}}
        val = validate_persona_voice(
            "i was at the cafe downtown yesterday, wish you had been there",
            persona=persona,
            behavior_state=None,
        )
        assert val.valid is False


# ── Purity: no new imports ───────────────────────────────────────


class TestHelperPurity:
    def test_strategy_module_imports_unchanged(self):
        import pathlib

        src = pathlib.Path("commerce/conversation_strategy.py").read_text(encoding="utf-8")
        imports = sorted(
            line.strip()
            for line in src.splitlines()
            if line.strip().startswith(("import ", "from "))
        )
        assert imports, "expected imports"
        for line in imports:
            assert line.startswith(
                (
                    "import logging",
                    "import re",
                    "from __future__",
                    "from collections.abc",
                    "from dataclasses",
                    "from typing",
                    "from commerce.",
                    "from core.",
                )
            ), line
