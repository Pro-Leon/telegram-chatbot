"""H2 custom acknowledgment guidance + queue payload (NEW).

Limited H2 behavior change only:
(a) advisory custom-ack block renders solely on advisory
    custom_request tags (wording guidance, no authority change);
(b) custom-handoff queue flags gain a custom:<need> token.
"""

import pytest

from commerce.conversation_strategy import (
    custom_need_token,
    render_custom_acknowledgment,
)

# ── (a) Guidance-block units ─────────────────────────────────────


class TestGuidanceRender:
    def test_renders_on_custom_tag(self):
        for tags in (["custom_request"], ("custom_request",), {"custom_request"}):
            block = render_custom_acknowledgment(tags)
            assert "CUSTOM ACKNOWLEDGMENT" in block
            assert "at most ONE clarifying question" in block
            assert "NEVER" in block

    def test_empty_without_custom_tag(self):
        assert render_custom_acknowledgment(None) == ""
        assert render_custom_acknowledgment([]) == ""
        assert render_custom_acknowledgment(["price_inquiry"]) == ""
        assert render_custom_acknowledgment(["content_request", "purchase_intent"]) == ""
        assert render_custom_acknowledgment("custom_request") == ""

    def test_block_quotes_no_prices_or_links(self):
        block = render_custom_acknowledgment(["custom_request"])
        assert "$" not in block
        assert "http" not in block
        assert "Pricing stays human-side" in block


# ── (a) Envelope-token units ─────────────────────────────────────


class TestNeedToken:
    def test_token_carries_fan_words(self):
        token = custom_need_token("can you make a custom video for me?", ["custom_request"])
        assert token == "custom:can you make a custom video for me?"

    def test_truncation(self):
        token = custom_need_token("x" * 200, ["custom_request"])
        assert token is not None
        assert len(token) <= len("custom:") + 120

    def test_card_data_redacted(self):
        token = custom_need_token(
            "my card 4111111111111111 for a custom video please",
            ["custom_request"],
        )
        assert token is not None
        assert "4111111111111111" not in token
        assert "[redacted]" in token

    def test_price_inquiry_disambiguation(self):
        # "custom" word in text but no custom_request tag → no token
        # (no text detection here; tags only).
        assert custom_need_token("how much for a custom video?", ["price_inquiry"]) is None

    def test_none_cases(self):
        assert custom_need_token("can you make a custom?", None) is None
        assert custom_need_token("can you make a custom?", []) is None
        assert custom_need_token("", ["custom_request"]) is None
        assert custom_need_token(None, ["custom_request"]) is None
        assert custom_need_token(123, ["custom_request"]) is None


# ── (b) Routing: customs still handoff ───────────────────────────


class TestCustomStillHandoffs:
    def test_pure_custom_ask_handoffs(self):
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
        )
        assert ok is True
        assert reason == OperatorHandoffReason.CUSTOM_REQUEST


# ── (c) Guardrail pins (deterministic parts only) ─────────────────


def _clean_draft(text):
    from commerce.commercial_cta import detect_unauthorized_commercial_cta
    from core.one_call import _compute_quality_heuristics, _compute_safety_flags

    hit, _ = detect_unauthorized_commercial_cta(text)
    safety = _compute_safety_flags(text)
    score, _ = _compute_quality_heuristics(text)
    return (not hit) and not safety and score >= 0.80


class TestGuardrailPins:
    def test_allowed_ack_lines_clean(self):
        assert _clean_draft("That sounds amazing! What kind of video did you have in mind?")
        assert _clean_draft("Love that idea! How long were you thinking, and any outfit you love?")
        assert _clean_draft(
            "Customs usually fall in a comfortable middle band, what works for you?"
        )

    def test_price_line_flags_to_queue(self):
        from core.one_call import _compute_safety_flags
        from core.routing import decide_routing

        safety = _compute_safety_flags("A custom like that is $100, want me to set it up?")
        assert "price_mention" in safety
        decision = decide_routing(is_valid=True, score=0.95, has_blocking_flags=True)
        assert decision.action.value == "queue"

    def test_pic_send_line_flags(self):
        from commerce.commercial_cta import detect_unauthorized_commercial_cta
        from core.one_call import _compute_safety_flags

        hit, _ = detect_unauthorized_commercial_cta("I can send you a pic of the outfit first")
        assert hit is True
        assert "photo_promise" in _compute_safety_flags("I can send you a pic of the outfit first")

    def test_exclusive_cta_flags(self):
        from commerce.commercial_cta import detect_unauthorized_commercial_cta

        hit, _ = detect_unauthorized_commercial_cta(
            "Want to see my exclusive set while you decide?"
        )
        assert hit is True

    def test_refund_pin_flags(self):
        from commerce.commercial_cta import detect_unauthorized_commercial_cta

        hit, name = detect_unauthorized_commercial_cta("I'll give you a refund")
        assert hit is True
        assert name == "refund_promise"


# ── (d) Creation-promise gap (documents honest gap) ───────────────


@pytest.mark.xfail(
    strict=True,
    reason="no validator covers creation/delivery promises "
    "(absent from CTA family, safety flags, _FORBIDDEN, _OFFER_CLAIM)",
)
def test_creation_promise_flags():
    from commerce.commercial_cta import detect_unauthorized_commercial_cta
    from core.one_call import _compute_safety_flags

    hit, _ = detect_unauthorized_commercial_cta("I will make it just for you, ready tomorrow!")
    safety = _compute_safety_flags("I will make it just for you, ready tomorrow!")
    assert hit is True or bool(safety)


# ── Purity: helpers add no imports ─────────────────────────────────


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
