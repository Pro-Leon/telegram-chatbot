"""Humanness scoring: flag split, pair completion, prompt rules.

Pins the research-backed contract: short-clean replies are sendable,
templates and ungrounded replies need review, placeholders never count
as known names.
"""

import inspect

import pytest

from core.scoring_deterministic import (
    _pair_completion_flags,
    score_draft_deterministic,
    validate_draft_quality,
)

pytestmark = [pytest.mark.unit]


class TestFlagSplit:
    def test_two_word_clean_gets_short_reply_not_generic(self):
        _score, flags = score_draft_deterministic("lol nice", "hey")
        assert "short_reply" in flags
        assert "too_generic" not in flags

    def test_one_word_stays_generic(self):
        for text in ("ok", "hi", "x", ""):
            score, flags = score_draft_deterministic(text, "hey")
            assert "too_generic" in flags, text
            assert score <= 0.5, text

    def test_three_word_clean_has_no_length_flag(self):
        score, flags = score_draft_deterministic("Red? Solid choice", "hey")
        assert "short_reply" not in flags
        assert "too_generic" not in flags
        assert score >= 0.80

    def test_template_still_generic(self):
        _score, flags = score_draft_deterministic(
            "Thank you for reaching out. I appreciate it. Let me know how I can help.",
            "hey",
        )
        assert "too_generic" in flags

    def test_every_template_opener_flags(self):
        from core.scoring_deterministic import TEMPLATE_OPENER_PHRASES

        assert len(TEMPLATE_OPENER_PHRASES) >= 10  # unified list, not a stub
        for phrase in TEMPLATE_OPENER_PHRASES:
            _score, flags = score_draft_deterministic(
                phrase + " and here is the rest of a longer reply for length",
                "hey",
            )
            assert "too_generic" in flags, phrase

    def test_mid_draft_politeness_stays_clean(self):
        _score, flags = score_draft_deterministic(
            "Sounds good! I appreciate your message and the trip sounds amazing",
            "hey",
        )
        assert "too_generic" not in flags


class TestPairCompletion:
    def test_ignored_question_flags_no_grounding(self):
        flags = _pair_completion_flags("ok", "what did you get up to today?")
        assert "no_grounding" in flags

    def test_stopword_only_question_abstains(self):
        # "how are you?" leaves no substantive tokens to ground to —
        # the check abstains (fail-open) rather than guessing.
        assert _pair_completion_flags("ok", "how are you?") == []

    def test_question_back_is_engaged_not_ignored(self):
        assert _pair_completion_flags("Doing great, you?", "How are you?") == []

    def test_long_reply_gets_benefit_of_doubt(self):
        flags = _pair_completion_flags(
            "That is a really interesting story about your trip to the mountains last summer",
            "what did you do?",
        )
        assert "no_grounding" not in flags

    def test_non_question_needs_no_grounding(self):
        assert _pair_completion_flags("lol nice", "hey") == []

    def test_luna_name_fumble_flags(self):
        flags = _pair_completion_flags(
            "hi, that is my name. What is yours?",
            "Red, what is yours",
            fan_name="Luna",
        )
        assert "name_request_known" in flags

    def test_name_request_unknown_name_clean(self):
        assert _pair_completion_flags("What is your name?", "hey there", fan_name=None) == []

    def test_placeholder_names_never_count(self):
        for placeholder in ("Fan", "there", "user", "", None):
            assert (
                _pair_completion_flags("What is your name?", "hey", fan_name=placeholder) == []
            ), placeholder

    def test_preference_whats_yours_without_nametalk_clean(self):
        assert (
            _pair_completion_flags(
                "Blue is mine, what is yours?", "Red, what is yours", fan_name="Luna"
            )
            == []
        )

    def test_helpers_never_raise(self):
        assert _pair_completion_flags(None, None) == []
        assert _pair_completion_flags("", "", fan_name=123) == []


class TestApprovalWiring:
    def test_short_clean_queues_with_documented_reason(self):
        approved, _score, flags, _ = validate_draft_quality("lol nice", "hey")
        assert approved is False
        assert "short_reply" in flags

    def test_grounded_short_sends(self):
        approved, _score, flags, _ = validate_draft_quality("Red? Solid choice", "hey")
        assert approved is True
        assert flags == []

    def test_pair_flags_route_to_review(self):
        _, _, flags, _ = validate_draft_quality(
            "ok", "what did you get up to today?", fan_name="Luna"
        )
        assert "no_grounding" in flags
        _, _, flags2, _ = validate_draft_quality(
            "hi, that is my name. What is yours?",
            "Red, what is yours",
            fan_name="Luna",
        )
        assert "name_request_known" in flags2


class TestPromptRules:
    def test_filler_allowed_and_length_matched(self):
        from memory import context as ctx

        qwen_src = inspect.getsource(ctx.build_qwen3_system_prompt)
        assert "one-liners" in qwen_src
        assert "No filler words" not in qwen_src
        assert "something specific" in qwen_src
        assert "hollow template" in qwen_src
        assert "no shared history" in qwen_src

    def test_onecall_prompt_carries_specificity_rules(self):
        from core.one_call import ONE_CALL_SYSTEM_PROMPT

        assert "something specific" in ONE_CALL_SYSTEM_PROMPT
        assert "hollow template" in ONE_CALL_SYSTEM_PROMPT
        assert "no shared history" in ONE_CALL_SYSTEM_PROMPT
