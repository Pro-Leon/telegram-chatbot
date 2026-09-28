"""Phase 75B: One-call response contract tests.

Tests the structured JSON response schema for the single Qwen2.5
generation call that replaces the 3-LLM pipeline.
"""

import json
import pytest
from pydantic import ValidationError

from core.one_call import (
    OneCallReply,
    OneCallResult,
    validate_one_call_response,
    _compute_safety_flags,
    _compute_quality_heuristics,
    ONE_CALL_SYSTEM_PROMPT,
    format_one_call_prompt,
)
from commerce.signals import CommerceSignals, INTENT_CATEGORIES


class TestOneCallReplySchema:
    """Tests for OneCallReply Pydantic model."""

    def test_valid_full_response(self):
        """Full valid response with all fields."""
        data = {
            "reply": "Hey! I was just thinking about you 😊",
            "commerce_signals": {
                "purchase_intent": 0.3,
                "content_interest": 0.7,
                "relationship_engagement": 0.8,
                "price_interest": 0.1,
                "explicit_purchase_request": False,
                "explicit_content_request": False,
                "requested_price": None,
                "declined_recent_offer": False,
                "asks_for_free_content": False,
                "negative_sentiment": 0.0,
                "confidence": 0.85,
                "evidence": [],
                "model_uncertainty": 0.1,
                "primary_intent": "casual_chat",
                "intent_tags": ["greeting", "casual_chat"],
                "negative_intent_tags": [],
                "fan_asks_question": False,
            },
            "confidence": 0.82,
            "needs_handoff": False,
        }
        parsed = OneCallReply.model_validate(data)
        assert parsed.reply == "Hey! I was just thinking about you 😊"
        assert parsed.commerce_signals.purchase_intent == 0.3
        assert parsed.confidence == 0.82
        assert parsed.needs_handoff is False

    def test_minimal_response(self):
        """Minimal valid response with defaults."""
        data = {"reply": "Thanks for reaching out!"}
        parsed = OneCallReply.model_validate(data)
        assert parsed.reply == "Thanks for reaching out!"
        assert parsed.commerce_signals.confidence == 0.0
        assert parsed.confidence == 0.5
        assert parsed.needs_handoff is False

    def test_empty_reply_rejected(self):
        """Empty reply should be rejected."""
        data = {"reply": ""}
        with pytest.raises(ValidationError):
            OneCallReply.model_validate(data)

    def test_whitespace_only_reply_rejected(self):
        """Whitespace-only reply should be rejected."""
        data = {"reply": "   "}
        with pytest.raises(ValidationError):
            OneCallReply.model_validate(data)

    def test_reply_too_long(self):
        """Reply exceeding max length should be rejected."""
        data = {"reply": "x" * 2001}
        with pytest.raises(ValidationError):
            OneCallReply.model_validate(data)

    def test_reply_strips_whitespace(self):
        """Reply should be stripped of leading/trailing whitespace."""
        data = {"reply": "  Hello!  "}
        parsed = OneCallReply.model_validate(data)
        assert parsed.reply == "Hello!"

    def test_invalid_commerce_signals_rejected(self):
        """Invalid commerce signals should be rejected."""
        data = {
            "reply": "Hello!",
            "commerce_signals": {
                "purchase_intent": 1.5,  # Out of range
            },
        }
        with pytest.raises(ValidationError):
            OneCallReply.model_validate(data)

    def test_extra_fields_rejected(self):
        """Extra fields should be rejected (extra=forbid)."""
        data = {
            "reply": "Hello!",
            "extra_field": "value",
        }
        with pytest.raises(ValidationError):
            OneCallReply.model_validate(data)

    def test_confidence_bounds(self):
        """Confidence must be between 0.0 and 1.0."""
        data = {"reply": "Hello!", "confidence": 1.5}
        with pytest.raises(ValidationError):
            OneCallReply.model_validate(data)

        data["confidence"] = -0.1
        with pytest.raises(ValidationError):
            OneCallReply.model_validate(data)

    def test_needs_handoff_boolean(self):
        """needs_handoff must be boolean (Pydantic coerces strings by default)."""
        data = {"reply": "Hello!", "needs_handoff": "yes"}
        # Pydantic v2 coerces "yes" to True by default (lax validation)
        parsed = OneCallReply.model_validate(data)
        assert parsed.needs_handoff is True


class TestValidateOneCallResponse:
    """Tests for validate_one_call_response function."""

    def test_valid_json_response(self):
        """Valid JSON response should parse successfully."""
        raw = json.dumps({
            "reply": "Hey there!",
            "commerce_signals": {
                "purchase_intent": 0.2,
                "content_interest": 0.5,
                "relationship_engagement": 0.7,
                "price_interest": 0.1,
                "explicit_purchase_request": False,
                "explicit_content_request": False,
                "requested_price": None,
                "declined_recent_offer": False,
                "asks_for_free_content": False,
                "negative_sentiment": 0.0,
                "confidence": 0.8,
                "evidence": [],
                "model_uncertainty": 0.2,
                "primary_intent": "casual_chat",
                "intent_tags": [],
                "negative_intent_tags": [],
                "fan_asks_question": False,
            },
            "confidence": 0.85,
            "needs_handoff": False,
        })
        result = validate_one_call_response(raw)
        assert result.is_valid is True
        assert result.reply == "Hey there!"
        assert result.confidence == 0.85
        assert result.needs_handoff is False
        assert result.validation_error is None

    def test_invalid_json_returns_fallback(self):
        """Invalid JSON should return low_information fallback."""
        result = validate_one_call_response("not valid json {{{")
        assert result.is_valid is False
        assert result.reply == ""
        assert result.needs_handoff is True
        assert result.confidence == 0.0
        assert "JSON parse failed" in result.validation_error

    def test_none_input_returns_fallback(self):
        """None input should return low_information fallback."""
        result = validate_one_call_response(None)
        assert result.is_valid is False
        assert result.needs_handoff is True

    def test_invalid_schema_returns_fallback(self):
        """Invalid schema should return low_information fallback."""
        raw = json.dumps({"invalid": "schema"})
        result = validate_one_call_response(raw)
        assert result.is_valid is False
        assert result.needs_handoff is True

    def test_safety_flags_trigger_handoff(self):
        """Safety flags should trigger handoff."""
        raw = json.dumps({
            "reply": "I'm feeling depressed and alone",
            "confidence": 0.8,
        })
        result = validate_one_call_response(raw)
        assert result.is_valid is True
        assert result.needs_handoff is True
        assert "distress_signal" in result.safety_flags

    def test_price_mention_flagged(self):
        """Price mentions should be flagged."""
        raw = json.dumps({
            "reply": "The price is $50",
            "confidence": 0.8,
        })
        result = validate_one_call_response(raw)
        assert "price_mention" in result.safety_flags

    def test_price_mention_authorized_commerce(self):
        """Price mentions in authorized commerce should not be flagged."""
        raw = json.dumps({
            "reply": "The price is $50",
            "confidence": 0.8,
        })
        result = validate_one_call_response(
            raw,
            is_authorized_commerce=True,
            authorized_price_minor=5000,
        )
        assert "price_mention" not in result.safety_flags

    def test_quality_heuristics_score(self):
        """Quality heuristics should produce reasonable scores."""
        raw = json.dumps({
            "reply": "Hey! I was just thinking about you. How's your day going?",
            "confidence": 0.85,
        })
        result = validate_one_call_response(raw)
        assert result.is_valid is True
        assert 0.0 <= result.quality_score <= 1.0
        assert isinstance(result.quality_flags, list)

    def test_generic_reply_flagged(self):
        """Generic replies should be flagged."""
        raw = json.dumps({
            "reply": "Thank you for reaching out. I appreciate your message. Let me know how I can help.",
            "confidence": 0.7,
        })
        result = validate_one_call_response(raw)
        assert "too_generic" in result.quality_flags

    def test_low_quality_triggers_handoff(self):
        """Low quality score should trigger handoff."""
        # Empty or invalid replies should trigger handoff
        raw = json.dumps({
            "reply": "   ",  # Whitespace only - will fail validation
            "confidence": 0.3,
        })
        result = validate_one_call_response(raw)
        # Invalid reply triggers handoff
        assert result.needs_handoff is True

    def test_missing_commerce_signals_uses_default(self):
        """Missing commerce signals should use low_information default."""
        raw = json.dumps({
            "reply": "Hello!",
            "confidence": 0.8,
        })
        result = validate_one_call_response(raw)
        assert result.is_valid is True
        assert result.signals == CommerceSignals.low_information()


class TestSafetyFlags:
    """Tests for _compute_safety_flags."""

    def test_distress_signal(self):
        """Distress keywords should be detected."""
        flags = _compute_safety_flags("I'm feeling depressed")
        assert "distress_signal" in flags

    def test_legal_mention(self):
        """Legal keywords should be detected."""
        flags = _compute_safety_flags("I'll report this to the police")
        assert "legal_mention" in flags

    def test_personal_info_request(self):
        """Personal info keywords should be detected."""
        flags = _compute_safety_flags("Can I have your email address?")
        assert "personal_info_request" in flags

    def test_clean_reply_no_flags(self):
        """Clean reply should have no flags."""
        flags = _compute_safety_flags("Hey! How's your day going?")
        assert len(flags) == 0

    def test_price_mention_authorized(self):
        """Authorized price mention should not be flagged."""
        flags = _compute_safety_flags(
            "The price is $50",
            is_authorized_commerce=True,
            authorized_price_minor=5000,
        )
        assert "price_mention" not in flags


class TestQualityHeuristics:
    """Tests for _compute_quality_heuristics."""

    def test_good_quality_reply(self):
        """Good quality reply should score high."""
        score, flags = _compute_quality_heuristics(
            "Hey! I was just thinking about you. How's your day going? I'd love to hear about it!"
        )
        assert score >= 0.7
        assert "too_generic" not in flags

    def test_too_short_reply(self):
        """Very short reply should be flagged."""
        score, flags = _compute_quality_heuristics("x")
        assert score <= 0.5
        assert "too_generic" in flags

    def test_formal_reply(self):
        """Formal reply should be flagged."""
        score, flags = _compute_quality_heuristics(
            "Dear Sir, I respectfully inform you that furthermore, therefore, consequently, moreover, I appreciate your inquiry."
        )
        assert "too_formal" in flags

    def test_repetitive_reply(self):
        """Repetitive reply should be flagged."""
        score, flags = _compute_quality_heuristics(
            "hello hello hello hello hello hello hello hello hello hello hello hello"
        )
        assert "repetitive" in flags

    def test_empty_reply(self):
        """Empty reply should return default score."""
        score, flags = _compute_quality_heuristics("")
        assert 0.0 <= score <= 1.0


class TestFormatPrompt:
    """Tests for format_one_call_prompt."""

    def test_basic_prompt_format(self):
        """Basic prompt should include system, context, and user message."""
        messages = format_one_call_prompt(
            system_prompt="You are a friendly assistant.",
            context_messages=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ],
            user_message="How are you?",
        )
        assert len(messages) == 4
        assert messages[0]["role"] == "system"
        assert "You are Sunny, chatting directly with one fan" in messages[0]["content"]
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "How are you?"

    def test_context_messages_limited(self):
        """Only last 10 context messages should be included."""
        context = [
            {"role": "user", "content": f"Message {i}"}
            for i in range(20)
        ]
        messages = format_one_call_prompt(
            system_prompt="Test",
            context_messages=context,
            user_message="Final",
        )
        # 1 system + 10 context + 1 user = 12
        assert len(messages) == 12


class TestCommerceSignalsIntegration:
    """Tests for CommerceSignals integration with one-call."""

    def test_commerce_signals_valid(self):
        """Valid commerce signals should parse correctly."""
        raw = json.dumps({
            "reply": "Hello!",
            "commerce_signals": {
                "purchase_intent": 0.5,
                "content_interest": 0.6,
                "relationship_engagement": 0.7,
                "price_interest": 0.3,
                "explicit_purchase_request": True,
                "explicit_content_request": False,
                "requested_price": 25.0,
                "declined_recent_offer": False,
                "asks_for_free_content": False,
                "negative_sentiment": 0.1,
                "confidence": 0.8,
                "evidence": ["Fan asked about pricing"],
                "model_uncertainty": 0.2,
                "primary_intent": "purchase_intent",
                "intent_tags": ["purchase_intent"],
                "negative_intent_tags": [],
                "fan_asks_question": False,
            },
            "confidence": 0.9,
        })
        result = validate_one_call_response(raw)
        assert result.is_valid is True
        assert result.signals.explicit_purchase_request is True
        assert result.signals.requested_price == 25.0

    def test_commerce_signals_low_information_default(self):
        """Missing commerce signals should use low_information default."""
        raw = json.dumps({
            "reply": "Hello!",
            "confidence": 0.8,
        })
        result = validate_one_call_response(raw)
        assert result.signals == CommerceSignals.low_information()

    def test_commerce_signals_invalid_intent_rejected(self):
        """Invalid primary_intent should be rejected."""
        raw = json.dumps({
            "reply": "Hello!",
            "commerce_signals": {
                "purchase_intent": 0.0,
                "content_interest": 0.0,
                "relationship_engagement": 0.0,
                "price_interest": 0.0,
                "explicit_purchase_request": False,
                "explicit_content_request": False,
                "requested_price": None,
                "declined_recent_offer": False,
                "asks_for_free_content": False,
                "negative_sentiment": 0.0,
                "confidence": 0.0,
                "primary_intent": "invalid_intent",
                "intent_tags": [],
                "negative_intent_tags": [],
                "fan_asks_question": False,
            },
            "confidence": 0.5,
        })
        result = validate_one_call_response(raw)
        assert result.is_valid is False


class TestOneCallResult:
    """Tests for OneCallResult dataclass."""

    def test_default_values(self):
        """Default values should be set correctly."""
        result = OneCallResult(
            reply="Hello",
            signals=CommerceSignals.low_information(),
            confidence=0.8,
            needs_handoff=False,
        )
        assert result.quality_score == 0.0
        assert result.quality_flags == []
        assert result.safety_flags == []
        assert result.is_valid is True
        assert result.validation_error is None
